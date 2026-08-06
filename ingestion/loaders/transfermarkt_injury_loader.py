import logging
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"
_TM_BASE = "https://www.transfermarkt.com"
_INJURY_URL = "{base}/{slug}/verletzungen/spieler/{tm_id}"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.transfermarkt.com/",
    "Accept-Language": "en-GB,en;q=0.9",
}


def _parse_tm_date(date_str: str) -> datetime | None:
    for fmt in ("%b %d, %Y", "%d/%m/%Y", "%Y-%m-%d", "%b %d %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def fetch_injury_history(
    tm_id: int | str,
    slug: str = "-",
    as_of: "datetime | None" = None,
) -> dict:
    if as_of is None:
        from config import DECISION_DATE
        as_of = datetime.combine(DECISION_DATE, datetime.min.time())

    two_years_ago = as_of - timedelta(days=730)

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

    soup = BeautifulSoup(resp.content, "html.parser")
    table = soup.select_one("table.items")

    if not table:
        logger.info(
            "No injury table found for tm_id=%s (player may have 0 injuries).", tm_id
        )
        return {
            "inj_count_2y": 0,
            "inj_days_missed_2y": 0,
            "inj_last_type": None,
            "inj_last_date": None,
        }

    rows = table.select("tbody tr")
    injuries_2y: list[dict] = []

    for tr in rows:
        cols = tr.find_all("td")
        if len(cols) < 5:
            continue

        inj_type = cols[1].get_text(strip=True)
        date_from = _parse_tm_date(cols[2].get_text(strip=True))

        date_to_text = cols[3].get_text(strip=True) if len(cols) > 3 else ""
        date_to = _parse_tm_date(date_to_text)
        days_text = cols[4].get_text(strip=True).replace(" days", "").strip()

        if days_text.isdigit():
            days = int(days_text)
        elif date_from and (not date_to or date_to_text in ("-", "", "present", "-")):
            days = (as_of - date_from).days
            logger.info(
                "Ongoing injury detected for tm_id=%s (%s since %s) — estimated %d days as of %s.",
                tm_id, inj_type, date_from.strftime("%Y-%m-%d"), days, as_of.date(),
            )
        else:
            logger.debug(
                "Could not parse days for tm_id=%s, injury '%s'. days_text='%s'",
                tm_id, inj_type, days_text,
            )
            days = 0

        if date_from and date_from > as_of:
            logger.debug(
                "Excluding injury for tm_id=%s dated %s — after as_of=%s (temporal cutoff).",
                tm_id, date_from.strftime("%Y-%m-%d"), as_of.date(),
            )
            continue

        if date_from and date_from >= two_years_ago:
            injuries_2y.append({
                "type": inj_type,
                "date": date_from,
                "days": max(0, days),
            })

    injuries_2y.sort(key=lambda x: x["date"], reverse=True)

    last_type = injuries_2y[0]["type"] if injuries_2y else None
    last_date = injuries_2y[0]["date"].strftime("%Y-%m-%d") if injuries_2y else None

    return {
        "inj_count_2y": len(injuries_2y),
        "inj_days_missed_2y": sum(i["days"] for i in injuries_2y),
        "inj_last_type": last_type,
        "inj_last_date": last_date,
    }


def load_injury_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
    as_of: "datetime | None" = None,
) -> str:
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "tm_id"], source="transfermarkt_injury")

    records = []
    for _, row in seed_df.iterrows():
        tm_id = row.get("tm_id")
        if pd.isna(tm_id) or tm_id is None:
            records.append({"player_id": row["player_id"]})
            continue

        slug = row.get("tm_slug") or "-"
        time.sleep(1.5)
        stats = fetch_injury_history(tm_id=int(tm_id), slug=str(slug), as_of=as_of)
        records.append({"player_id": row["player_id"], **stats})

    assert_min_coverage(records, min_pct=0.50, source="transfermarkt_injury")

    object_name = f"transfermarkt_injury/players_injury_{run_date}.json"
    success = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"