# ingestion/loaders/flashscore_loader.py
# Source 15: FlashScore — Player Rating Fallback #2 (extends ADR-018 chain)
#
# Why this exists:
#   SofaScore (ADR-013) → 403 Forbidden on every endpoint, confirmed across all
#   49 players, all leagues, all retries (see logs/bronze_20260618_114528.log).
#   Fotmob (ADR-018 / Source 14) → search endpoint returning 404/403 as of v18 testing.
#   Both unofficial JSON APIs are currently unusable. FlashScore is added as a
#   THIRD fallback in the same COALESCE chain — not a replacement for either.
#
# IMPORTANT — read before relying on this in production:
#   FlashScore has no public JSON API. Player rating data lives inside a
#   JS-rendered React app — a plain `requests.get()` returns an empty shell,
#   not the rating. This loader uses Playwright (headless Chromium) to render
#   the page and read the rating out of the DOM. That makes it:
#     - Slower than the JSON-API loaders (~3-6s/player vs ~1-2s)
#     - More fragile: DOM selectors break on any FlashScore front-end redeploy,
#       where a JSON API loader would more likely keep working until the
#       endpoint itself changes shape.
#     - Subject to anti-bot defenses (Cloudflare/DataDome-style). No bypass
#       attempted here beyond a realistic header/viewport and randomised
#       delays — if FlashScore starts hard-blocking headless Chromium the
#       same way it may have blocked SofaScore, this loader will degrade the
#       same way: low coverage, not a crash (assert_min_coverage logs it).
#   Treat this as a best-effort fallback, not a guaranteed-stable source.
#
# Requires: playwright (pip install playwright && playwright install chromium)

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

# Confirmed via live DevTools inspection (2026-06-18, real Hakimi page):
#   <div data-testid="wcl-badgeRating" data-color="F3A000"
#        class="wcl-badgeRating_lqrln wcl-large_PdEZL lmTable__playerRating"
#        title="Rating" style="background-color: rgb(243, 160, 0);">
#     <span data-testid="wcl-scores-caption-05">6.7</span>
#   </div>
# Critical finding that changed the original design entirely: FlashScore's
# player page has NO single "season average rating" value anywhere. What
# exists is a "Last Matches" table with one rating badge PER MATCH (e.g.
# 7.1, 6.7, 7.1, ...). There's no average to scrape — it has to be computed
# here from the per-match values.
#
# Also critical: the inner <span data-testid="wcl-scores-caption-05"> is a
# generic "numeric caption" component FlashScore reuses for BOTH the rating
# badge AND the match score (e.g. the "1" in a 1-1 scoreline carries the
# exact same data-testid). Selecting on that span alone pulls scores too.
# The badge only becomes unique at its parent:
# [data-testid='wcl-badgeRating'] — confirmed via manual inspection of both
# a rating badge and a score number side by side on the same page.
_RATING_BADGE_SELECTOR = "[data-testid='wcl-badgeRating']"


def _extract_rating_from_text(text: str) -> float | None:
    """
    FlashScore shows ratings as a bare decimal like '7.1' inside the badge.
    Pull the first plausible 1-10 decimal.
    """
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
    Renders one FlashScore player page in headless Chromium and reads
    per-match ratings out of the "Last Matches" table (data-testid=
    'wcl-badgeRating'), since FlashScore exposes no single season-average
    value. Computes:
      - flashscore_rating_avg: mean of all rating badges found on the page
        (typically the most recent ~10-15 matches FlashScore renders by
        default before any "load more" interaction)
      - flashscore_form_5: mean of the first 5 badges (badges appear in
        reverse-chronological order in the table, most recent match first)
      - flashscore_matches_rated: count of rating badges found

    Returns dict with flashscore_* columns, or {} on failure / not found /
    Playwright not installed.
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
                # Let the JS app hydrate. FlashScore lazy-loads the matches table.
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
    Fetches FlashScore ratings for all players in seed_df via headless
    Chromium, then writes raw results to MinIO s3://football-bronze/flashscore/.

    seed_df must contain: 'player_id', 'flashscore_id', 'flashscore_slug'.
    Both id and slug are REQUIRED — confirmed via manual testing that
    FlashScore's player route validates slug+id together; a wrong or
    placeholder slug 404s even when the id is exactly right. Use
    scripts/collect_ids.py --source flashscore to get real slugs (it
    click-throughs each candidate to read the actual resolved URL, since
    FlashScore's search results don't expose the slug directly).

    This is a fallback source — expect lower and less predictable coverage
    than a working JSON-API loader. Threshold set lower than SofaScore's
    (0.30) to reflect that.

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
            # No placeholder fallback here: manual testing confirmed
            # FlashScore's player route 404s on a wrong/fake slug even with
            # the correct id (slug+id are validated together server-side).
            # A real slug is required — see scripts/collect_ids.py.
            records.append({"player_id": row["player_id"]})
            continue

        stats = fetch_flashscore_player(str(fid), str(slug))
        records.append({"player_id": row["player_id"], **stats})

    # Lower bar than SofaScore (0.30) — this is a third-line fallback running
    # against a site with no stable contract for this data.
    assert_min_coverage(records, min_pct=0.20, source="flashscore")

    object_name = f"flashscore/players_flashscore_{run_date}.json"
    success = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"
