import logging
import time
from datetime import datetime

import requests
import pandas as pd

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

UNDERSTAT_BASE = "https://understat.com/getPlayerData/{player_id}"
BUCKET = "football-bronze"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

TARGET_SEASON = "2025"


def fetch_understat_player(
    understat_id: int | str,
    season: str = TARGET_SEASON,
) -> dict:
    url = UNDERSTAT_BASE.format(player_id=understat_id)
    try:
        resp = requests.get(url, timeout=15, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
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

    total_minutes = sum(int(m.get("time", 0)) for m in season_matches)
    nineties = max(total_minutes / 90.0, 0.01)

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
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "understat_id"], source="understat")

    records = []
    for _, row in seed_df.iterrows():
        uid = row.get("understat_id")
        if pd.isna(uid) or uid is None:
            records.append({"player_id": row["player_id"]})
            logger.info("No understat_id for %s - skipping.", row["player_id"])
            continue

        uid = int(uid)

        time.sleep(2.0)
        stats = fetch_understat_player(uid)
        records.append({"player_id": row["player_id"], **stats})

    assert_min_coverage(records, min_pct=0.30, source="understat")

    object_name = f"understat/players_understat_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"