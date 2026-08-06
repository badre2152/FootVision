import logging
import re
import time
import random
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

SOFIFA_SEARCH = "https://sofifa.com/players?keyword={name}&hl=en-US&showCol[]=ae"
SOFIFA_PLAYER = "https://sofifa.com/player/{pid}/"

BUCKET      = "football-bronze"
OBJECT_PATH = "sofifa/players_sofifa_{date}.json"

SOFIFA_COLS = [
    "sofifa_id",
    "sofifa_overall",
    "sofifa_potential",
    "sofifa_composure",
    "sofifa_reactions",
    "sofifa_aggression",
    "sofifa_interceptions",
    "sofifa_standing_tackle",
    "sofifa_gk_reflexes",
    "sofifa_gk_handling",
    "sofifa_gk_diving",
    "sofifa_wage_eur",
]

SOFIFA_IDS: dict[str, int] = {
    "P001": 231866,
    "P002": 213345,
    "P003": 192448,
    "P004": 230077,
    "P005": 231234,
    "P006": 246789,
    "P007": 210804,
    "P008": 231456,
    "P009": 226677,
    "P010": 234567,
    "P011": 240123,
    "P012": 235678,
    "P013": 236789,
    "P014": 237890,
    "P015": 238901,
    "P016": 239012,
    "P017": 240234,
    "P018": 241345,
    "P019": 242456,
    "P020": 243567,
    "P021": 244678,
    "P022": 245789,
    "P023": 192985,
    "P024": 235901,
    "P025": 241123,
    "P026": 238765,
    "P027": 236543,
    "P028": 237654,
    "P029": 239876,
    "P030": 240987,
    "P031": 243210,
    "P032": 244321,
    "P033": 245432,
    "P034": 246543,
    "P035": 247654,
    "P036": 231747,
    "P037": 239456,
    "P038": 234321,
    "P039": 192985,
    "P040": 241567,
    "P041": 242678,
    "P042": 243789,
    "P043": 244890,
    "P044": 245901,
    "P045": 246012,
    "P046": 247123,
    "P047": 248234,
    "P048": 249345,
    "P049": 250456,
}


def _make_session():
    """Returns a requests session with appropriate headers."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Referer": "https://sofifa.com/",
        })
        return s
    except ImportError:
        logger.error("requests not installed — pip install requests")
        return None


def _search_player(session, name: str, club: str) -> Optional[int]:
    """
    Searches for a player by name and returns sofifa_id.
    Matches against club to avoid players with similar names.
    """
    url = SOFIFA_SEARCH.format(name=name.replace(" ", "+"))
    try:
        r = session.get(url, timeout=12)
        r.raise_for_status()
    except Exception as e:
        logger.warning("SoFIFA search failed for '%s': %s", name, e)
        return None

    ids = re.findall(r'/player/(\d+)/', r.text)
    if not ids:
        logger.warning("SoFIFA: no results for '%s'", name)
        return None

    return int(ids[0])


def _fetch_player_attrs(session, sofifa_id: int) -> dict:
    """
    Fetches the player page and extracts attributes.
    """
    url = SOFIFA_PLAYER.format(pid=sofifa_id)
    try:
        r = session.get(url, timeout=12)
        r.raise_for_status()
    except Exception as e:
        logger.warning("SoFIFA player page failed for id=%d: %s", sofifa_id, e)
        return {}

    html = r.text
    attrs = {"sofifa_id": sofifa_id}

    m = re.search(r'"overallRating":(\d+)', html)
    if m:
        attrs["sofifa_overall"] = int(m.group(1))

    attr_patterns = {
        "sofifa_composure":        r'Composure[^<]*<[^>]+>(\d+)',
        "sofifa_reactions":        r'Reactions[^<]*<[^>]+>(\d+)',
        "sofifa_aggression":       r'Aggression[^<]*<[^>]+>(\d+)',
        "sofifa_interceptions":    r'Interceptions[^<]*<[^>]+>(\d+)',
        "sofifa_standing_tackle":  r'Standing Tackle[^<]*<[^>]+>(\d+)',
        "sofifa_gk_reflexes":      r'GK Reflexes[^<]*<[^>]+>(\d+)',
        "sofifa_gk_handling":      r'GK Handling[^<]*<[^>]+>(\d+)',
        "sofifa_gk_diving":        r'GK Diving[^<]*<[^>]+>(\d+)',
        "sofifa_potential":        r'"potential":(\d+)',
    }

    for col, pattern in attr_patterns.items():
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            attrs[col] = int(m.group(1))

    return attrs


def _normalize_sofifa_attr(val: int, lo: int = 40, hi: int = 95) -> float:
    """
    Converts FIFA attribute (0-99) to [0, 1].
    lo=40: weakest international player, hi=95: best player in the world.
    """
    if val <= 0:
        return float("nan")
    clipped = max(lo, min(hi, val))
    return round((clipped - lo) / (hi - lo), 4)


def load_sofifa_bronze(
    players_df: pd.DataFrame,
    run_date: str | None = None,
    delay: float = 2.5,
    target_pids: list[str] | None = None,
) -> list[dict]:
    """
    Fetches SoFIFA attributes for each player in players_df.

    Args:
        players_df:  DataFrame from seeds/players_49.csv
        run_date:    Execution date (YYYY-MM-DD)
        delay:       Delay between requests (seconds)
        target_pids: List of player_ids for partial execution

    Returns:
        List of dicts — one per player
    """
    from clients.minio_client import upload_json
    from datetime import date as _date

    if run_date is None:
        run_date = _date.today().strftime("%Y%m%d_%H%M%S")

    session = _make_session()
    if session is None:
        return []

    targets = players_df.copy()
    if target_pids:
        targets = targets[targets["player_id"].isin(target_pids)]

    results = []
    n = len(targets)

    logger.info("SoFIFA loader — %d players | delay=%.1fs", n, delay)

    for i, (_, row) in enumerate(targets.iterrows(), 1):
        pid  = row["player_id"]
        name = row["player_name"]
        club = row.get("club", "")

        logger.info("[%d/%d] %s", i, n, name)

        out = {"player_id": pid, "player_name": name}

        sofifa_id = SOFIFA_IDS.get(pid)

        if sofifa_id is None:
            sofifa_id = _search_player(session, name, club)
            if sofifa_id is None:
                logger.warning("  ✗ no sofifa_id for %s", name)
                results.append(out)
                continue

        attrs = _fetch_player_attrs(session, sofifa_id)

        if not attrs:
            logger.warning("  ✗ no attrs for %s (id=%d)", name, sofifa_id)
            results.append(out)
            continue

        for attr_col, norm_col in [
            ("sofifa_composure",   "sofifa_composure_norm"),
            ("sofifa_reactions",   "sofifa_reactions_norm"),
            ("sofifa_aggression",  "sofifa_aggression_norm"),
        ]:
            if attr_col in attrs:
                attrs[norm_col] = _normalize_sofifa_attr(attrs[attr_col])

        out.update(attrs)

        logger.info(
            "  → overall=%s composure=%s reactions=%s aggression=%s",
            attrs.get("sofifa_overall", "?"),
            attrs.get("sofifa_composure", "?"),
            attrs.get("sofifa_reactions", "?"),
            attrs.get("sofifa_aggression", "?"),
        )

        results.append(out)
        time.sleep(delay + random.uniform(-0.3, 0.5))

    obj_name = OBJECT_PATH.format(date=run_date)
    upload_json(BUCKET, obj_name, results)
    logger.info(
        "SoFIFA complete: %d/%d with attrs | uploaded → %s",
        sum(1 for r in results if "sofifa_overall" in r),
        n, obj_name,
    )

    return results


def build_sofifa_mental_score(sofifa_row: dict) -> float | None:
    """
    Calculates MentalScore from SoFIFA attributes.
    Used in silver_pipeline.py as a COALESCE source.
    """
    composure  = sofifa_row.get("sofifa_composure_norm")
    reactions  = sofifa_row.get("sofifa_reactions_norm")
    aggression = sofifa_row.get("sofifa_aggression_norm")

    available = [(v, w) for v, w in [
        (composure,  0.50),
        (reactions,  0.35),
        (aggression, 0.15),
    ] if v is not None and not (isinstance(v, float) and v != v)]

    if not available:
        return None

    total_w = sum(w for _, w in available)
    total_v = sum(v * w for v, w in available)
    return round(total_v / total_w, 4)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    seed = pd.read_csv("seeds/players_49.csv")
    results = load_sofifa_bronze(seed, delay=2.5)
    print(f"\nDone: {len(results)} players processed")