# ingestion/loaders/transfermarkt_injury_loader.py
# Source 10: Transfermarkt Injury Scraper (ADR-014)
# Automates ADR-009 (manual injury enrichment) for all players with tm_id.
#
# v17 fixes applied:
#   C-01: run_date defaults to datetime.now() inside loader
#   C-02: validate_seed_columns() guard
#   C-06: ongoing injury days now estimated (today - date_from) instead of 0
#   C-08: assert_min_coverage() before upload

import logging
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET    = "football-bronze"
_TM_BASE  = "https://www.transfermarkt.com"
_INJURY_URL = "{base}/{slug}/verletzungen/spieler/{tm_id}"

# Referer header is MANDATORY — without it TM returns HTTP 403
_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer":         "https://www.transfermarkt.com/",
    "Accept-Language": "en-GB,en;q=0.9",
}


def _parse_tm_date(date_str: str) -> datetime | None:
    """Converts Transfermarkt date string to datetime. Tries multiple formats."""
    for fmt in ("%b %d, %Y", "%d/%m/%Y", "%Y-%m-%d", "%b %d %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def fetch_injury_history(tm_id: int | str, slug: str = "-") -> dict:
    """
    Fetches last 2 years of injury history from Transfermarkt for one player.

    v16 fix:
        B-05: _TWO_YEARS_AGO was computed at import time (stale on long runs).
              Now computed inside this function on each call.

    v17 fix:
        C-06: v16 used days_text.isdigit() which returns False for ongoing injuries
              (TM shows "- days" or blank). Ongoing injuries — often the most severe —
              were silently counted as 0 days, severely underestimating risk.
              Fix: detect ongoing injuries by checking date_to; estimate days as
              (today - date_from). This correctly captures ACL-level injuries.

    Returns dict with inj_* columns, or {} on failure.
    Columns:
        inj_count_2y       — number of injuries in last 2 years
        inj_days_missed_2y — total days missed (includes estimated ongoing days)
        inj_last_type      — type of most recent injury (string)
        inj_last_date      — date of most recent injury (YYYY-MM-DD)
    """
    # FIX B-05: compute cutoff inside function, not at module level
    two_years_ago = datetime.now() - timedelta(days=730)

    url = _INJURY_URL.format(base=_TM_BASE, slug=slug, tm_id=tm_id)
    resp = None
    for attempt in range(1, 4):
        try:
            time.sleep(1.5 * attempt)
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 502:
                logger.warning("TM injury 502 for tm_id=%s (attempt %d/3)", tm_id, attempt)
                time.sleep(8 * attempt)
                resp = None
                continue
            resp.raise_for_status()
            break
        except requests.exceptions.Timeout:
            logger.warning("TM injury timeout for tm_id=%s (attempt %d/3)", tm_id, attempt)
            if attempt == 3:
                logger.warning("TM injury fetch failed for tm_id=%s: timeout after 3 attempts", tm_id)
                return {}
            time.sleep(8 * attempt)
        except requests.RequestException as e:
            logger.warning("TM injury fetch failed for tm_id=%s: %s", tm_id, e)
            return {}

    if resp is None:
        logger.warning("TM injury fetch failed after 3 attempts for tm_id=%s", tm_id)
        return {}

    soup  = BeautifulSoup(resp.content, "html.parser")
    table = soup.select_one("table.items")

    if not table:
        logger.info(
            "No injury table found for tm_id=%s (player may have 0 injuries).", tm_id
        )
        return {
            "inj_count_2y":       0,
            "inj_days_missed_2y": 0,
            "inj_last_type":      None,
            "inj_last_date":      None,
        }

    rows          = table.select("tbody tr")
    injuries_2y: list[dict] = []

    for tr in rows:
        cols = tr.find_all("td")
        if len(cols) < 5:
            continue

        inj_type  = cols[1].get_text(strip=True)
        date_from = _parse_tm_date(cols[2].get_text(strip=True))

        # FIX C-06 (v17): detect ongoing injuries via date_to column
        date_to_text = cols[3].get_text(strip=True) if len(cols) > 3 else ""
        date_to      = _parse_tm_date(date_to_text)
        days_text    = cols[4].get_text(strip=True).replace(" days", "").strip()

        if days_text.isdigit():
            # Completed injury with explicit day count
            days = int(days_text)

        elif date_from and (not date_to or date_to_text in ("-", "", "present", "-")):
            # FIX C-06: Ongoing injury — estimate from date_from to today
            days = (datetime.now() - date_from).days
            logger.info(
                "Ongoing injury detected for tm_id=%s (%s since %s) — estimated %d days.",
                tm_id, inj_type, date_from.strftime("%Y-%m-%d"), days,
            )
        else:
            # Unknown format — default to 0 but log it
            logger.debug(
                "Could not parse days for tm_id=%s, injury '%s'. days_text='%s'",
                tm_id, inj_type, days_text,
            )
            days = 0

        if date_from and date_from >= two_years_ago:
            injuries_2y.append({
                "type": inj_type,
                "date": date_from,
                "days": days,
            })

    # Sort by date descending — most recent first
    injuries_2y.sort(key=lambda x: x["date"], reverse=True)

    last_type = injuries_2y[0]["type"]                          if injuries_2y else None
    last_date = injuries_2y[0]["date"].strftime("%Y-%m-%d")     if injuries_2y else None

    return {
        "inj_count_2y":       len(injuries_2y),
        "inj_days_missed_2y": sum(i["days"] for i in injuries_2y),
        "inj_last_type":      last_type,
        "inj_last_date":      last_date,
    }


def load_injury_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Generates inj_* columns for all players in seed_df,
    then writes raw results to MinIO s3://football-bronze/transfermarkt_injury/.

    v17 fixes:
        C-01: run_date defaults to now() if not supplied
        C-02: seed column validation
        C-06: ongoing injury days estimated in fetch_injury_history()
        C-08: coverage check before upload (min_pct=0.50 — TM covers ~45/49)

    seed_df must contain: 'player_id', 'tm_id'.
    'tm_slug' is optional — replaced by '-' if missing (TM accepts it with redirect).
    Returns status string: "OK (N/49 players with data)" or "FAILED".
    """
    # FIX C-01
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    # FIX C-02
    validate_seed_columns(seed_df, ["player_id", "tm_id"], source="transfermarkt_injury")

    records = []
    for _, row in seed_df.iterrows():
        tm_id = row.get("tm_id")
        if pd.isna(tm_id) or tm_id is None:
            records.append({"player_id": row["player_id"]})
            continue

        slug  = row.get("tm_slug") or "-"
        time.sleep(1.5)  # Transfermarkt is very rate-sensitive
        stats = fetch_injury_history(tm_id=int(tm_id), slug=str(slug))
        records.append({"player_id": row["player_id"], **stats})

    # FIX C-08: 50% expected (TM covers ~45/49)
    assert_min_coverage(records, min_pct=0.50, source="transfermarkt_injury")

    object_name = f"transfermarkt_injury/players_injury_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"