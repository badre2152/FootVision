import logging
import random
import re
import time
from datetime import datetime

import pandas as pd

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

_PLAYER_URL = "https://www.flashscore.com/player/{slug}/{flashscore_id}/"

_VIEWPORT = {"width": 1366, "height": 850}
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_RATING_BADGE_SELECTOR = "[data-testid='wcl-badgeRating']"


def _extract_rating_from_text(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"\b([0-9](?:\.\d{1,2})?)\b", text.strip())
    if not match:
        return None
    value = float(match.group(1))
    if 0.0 < value <= 10.0:
        return value
    return None


def fetch_flashscore_player(flashscore_id: str, slug: str) -> dict:
    """
    Renders one FlashScore player page and extracts per-match ratings.
    Returns dict with flashscore_rating_avg, flashscore_form_5,
    flashscore_matches_rated, or {} on failure.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        logger.error(
            "Playwright not installed. Run: pip install playwright "
            "&& playwright install chromium"
        )
        return {}

    time.sleep(random.uniform(1.5, 3.0))

    url = _PLAYER_URL.format(slug=slug, flashscore_id=flashscore_id)
    result: dict = {}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport=_VIEWPORT, user_agent=_USER_AGENT)
            try:
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(random.randint(1500, 2500))

                badges = page.locator(_RATING_BADGE_SELECTOR).all()
                ratings: list[float] = []
                for badge in badges:
                    try:
                        text = badge.inner_text(timeout=2000)
                    except PWTimeout:
                        continue
                    value = _extract_rating_from_text(text)
                    if value is not None:
                        ratings.append(value)

                if ratings:
                    result["flashscore_rating_avg"] = round(sum(ratings) / len(ratings), 2)
                    result["flashscore_matches_rated"] = len(ratings)
                    last_5 = ratings[:5]
                    result["flashscore_form_5"] = round(sum(last_5) / len(last_5), 2)

            except PWTimeout:
                logger.warning("FlashScore page load timed out for id=%s", flashscore_id)
            finally:
                browser.close()

    except Exception as e:
        logger.warning("FlashScore fetch failed for id=%s: %s", flashscore_id, e)
        return {}

    if not result:
        logger.info("FlashScore: no rating badges found in DOM for id=%s (url=%s)", flashscore_id, url)

    return result


def load_flashscore_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Fetches FlashScore ratings for all players in seed_df and writes raw
    results to MinIO s3://football-bronze/flashscore/.

    seed_df must contain: 'player_id', 'flashscore_id', 'flashscore_slug'.

    Returns status string: "OK (N/49 players with data)" or "FAILED".
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "flashscore_id", "flashscore_slug"], source="flashscore")

    records = []
    for _, row in seed_df.iterrows():
        fid = row.get("flashscore_id")
        slug = row.get("flashscore_slug")

        if pd.isna(fid) or fid in (None, "") or pd.isna(slug) or not slug:
            records.append({"player_id": row["player_id"]})
            continue

        stats = fetch_flashscore_player(str(fid), str(slug))
        records.append({"player_id": row["player_id"], **stats})

    assert_min_coverage(records, min_pct=0.20, source="flashscore")

    object_name = f"flashscore/players_flashscore_{run_date}.json"
    success = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"