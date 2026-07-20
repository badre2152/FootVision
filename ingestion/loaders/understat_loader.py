# ingestion/loaders/understat_loader.py
# Source 8: Understat.com - npxG and xA at Shot Level (ADR-012)
#
# v17 fixes applied:
#   C-01: run_date defaults to datetime.now() inside loader, not empty string
#   C-02: _validate_seed_columns() guard at start of load function
#   C-05: unicode_escape -> raw_unicode_escape / latin-1 round-trip (non-ASCII names fix)
#   C-08: _assert_min_coverage() before upload
#
# v18 fixes applied:
#   C-09: understat_id was passed straight from the seed DataFrame, where
#         NaNs in the column force pandas to store it as float64. Valid IDs
#         (e.g. 5197) arrived as 5197.0 and got embedded in the request URL
#         as "/player/5197.0", causing a 404 on every single player and
#         masking the real ~25/49 expected coverage as 0/49.
#   U-01: confirmed via DevTools Network tab that understat.com no longer
#         embeds `var shotsData = JSON.parse('...')` in the static HTML of
#         /player/{id} - that variable does not appear anywhere in the page
#         source anymore, even for players confirmed to have a correct ID
#         and real data. The actual data now comes from one XHR call to
#         /getPlayerData/{id}, which returns clean JSON directly (shots +
#         matches arrays). Rewrote fetch_understat_player to call that
#         endpoint and dropped the now-dead _extract_json_var/re/json/bs4
#         machinery entirely.

import logging
import time
from datetime import datetime

import requests
import pandas as pd

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

# FIX v18 (U-01): Understat no longer embeds `var shotsData = JSON.parse('...')`
# in the static HTML of /player/{id}. Confirmed via DevTools Network tab: the
# page now loads everything through one XHR call (jQuery $.ajax) to this
# endpoint, which returns plain JSON directly - no regex/script-tag parsing
# needed at all anymore.
UNDERSTAT_BASE = "https://understat.com/getPlayerData/{player_id}"
BUCKET = "football-bronze"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    # Mimic the jQuery AJAX call the real page makes - some endpoints only
    # serve JSON (vs. an HTML/error page) when this header is present.
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

# FIX B-01: season string = start year of season.
# 2025-26 season -> "2025"  (v15 had "2024" which is 2024-25 - wrong season)
TARGET_SEASON = "2025"


def fetch_understat_player(
    understat_id: int | str,
    season: str = TARGET_SEASON,
) -> dict:
    """
    Fetches one season of stats for a player from Understat's getPlayerData
    JSON endpoint (the same call the real site's JS makes via jQuery AJAX).

    Returns dict with understat_* columns, or {} on failure.

    Columns returned:
        understat_npxg_per90  - non-penalty xG per 90 min
        understat_xa_per90    - expected assists per 90 min
        understat_shots_per90 - shots per 90 min
        understat_conversion  - goals / shots × 100
    """
    url = UNDERSTAT_BASE.format(player_id=understat_id)
    try:
        resp = requests.get(url, timeout=15, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        # ValueError covers resp.json() failing on non-JSON (e.g. an HTML
        # error page slipped through despite the XHR headers above).
        logger.warning("Understat fetch failed for player %s: %s", understat_id, e)
        return {}

    all_shots = data.get("shots", [])
    all_matches = data.get("matches", [])

    if not all_shots and not all_matches:
        logger.info(
            "No shots/matches in getPlayerData for player %s - likely outside covered leagues.",
            understat_id,
        )
        return {}

    season_shots = [s for s in all_shots if s.get("season") == season]
    season_matches = [m for m in all_matches if m.get("season") == season]

    if not season_matches:
        logger.info("Player %s has no matches in season %s.", understat_id, season)
        return {}

    npxg_total  = sum(
        float(s.get("xG", 0)) for s in season_shots
        if s.get("situation") != "Penalty"
    )
    xa_total    = sum(float(s.get("xA", 0)) for s in season_shots)
    shots_total = len(season_shots)
    goals_total = sum(1 for s in season_shots if s.get("result") == "Goal")

    # FIX v18 (U-01): minutes now come straight from the `matches` array
    # returned by getPlayerData (each match has a "time" field) - no more
    # dependency on the old groupsData/matchesData fallback chain.
    total_minutes = sum(int(m.get("time", 0)) for m in season_matches)
    nineties = max(total_minutes / 90.0, 0.01)  # avoid division by zero

    return {
        "understat_npxg_per90":  round(npxg_total  / nineties, 4),
        "understat_xa_per90":    round(xa_total     / nineties, 4),
        "understat_shots_per90": round(shots_total  / nineties, 4),
        "understat_conversion":  round((goals_total / max(shots_total, 1)) * 100, 2),
    }


def load_understat_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Generates understat_* columns for all players in seed_df,
    then writes raw results to MinIO s3://football-bronze/understat/.

    v17 fixes:
        C-01: run_date defaults to now() if not supplied
        C-02: seed column validation at entry point
        C-08: coverage check before upload

    seed_df must contain columns: 'player_id' and 'understat_id'.
    Players without understat_id get understat_* = NaN (no data).
    Returns status string: "OK (N/49 players with data)" or "FAILED".
    """
    # FIX C-01: generate timestamp if caller did not supply one
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    # FIX C-02: validate required columns up-front
    validate_seed_columns(seed_df, ["player_id", "understat_id"], source="understat")

    records = []
    for _, row in seed_df.iterrows():
        uid = row.get("understat_id")
        if pd.isna(uid) or uid is None:
            records.append({"player_id": row["player_id"]})
            logger.info("No understat_id for %s - skipping.", row["player_id"])
            continue

        # FIX v18 (C-09): the seed CSV's understat_id column contains NaN for
        # ~24/49 players, so pandas reads the whole column as float64. Valid
        # IDs like 5197 arrive here as 5197.0, which gets stringified into
        # the URL as ".../player/5197.0" - a guaranteed 404 on Understat.
        # Cast back to int now that the NaN case is already handled above.
        uid = int(uid)

        time.sleep(2.0)  # Understat is rate-sensitive - 2s between requests
        stats = fetch_understat_player(uid)
        records.append({"player_id": row["player_id"], **stats})

    # FIX C-08: warn if coverage is suspiciously low
    assert_min_coverage(records, min_pct=0.30, source="understat")

    object_name = f"understat/players_understat_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"