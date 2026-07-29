# ingestion/loaders/reddit_collector.py
# Source: Reddit (PRAW) — raw text for SentimentScore
#
# Added 2026-07-22 (Sentiment session). Reddit's free tier (100 queries/
# minute per OAuth client, non-commercial/academic use) is the primary
# real-time source for SentimentScore — see MASTER_SPEC_ADDENDUM_2026-07-14.md
# §4 for why Twitter/X was ruled out (no free tier since Feb 2026) and why
# Facebook/Instagram were ruled out (App Review + business verification
# required for arbitrary public content — not practical for this project).
#
# ⚠️ UNTESTED AGAINST LIVE REDDIT ⚠️
# reddit.com is not reachable from the sandbox this was written in (only
# github/pypi/npm-family domains are whitelisted). The REQUEST-BUILDING
# and TEXT-EXTRACTION logic below is unit-tested against mocked PRAW
# objects (see tests/test_reddit_collector.py) — praw itself IS installed
# and its class shapes were inspected to write this correctly, but no
# live OAuth call has ever succeeded from this environment.
#
# BEFORE TRUSTING THIS IN A REAL RUN:
#   1. Register a Reddit "script" app at https://reddit.com/prefs/apps
#      (free, instant — no approval wait, unlike Meta/X).
#   2. Add to .env: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
#      (user agent should look like "football-atlas/1.0 by u/yourusername"
#      per Reddit's API rules — generic user agents get rate-limited harder).
#   3. Run this loader for 2-3 well-known players (e.g. Hakimi, En-Nesyri)
#      and confirm non-empty text is actually returned — Reddit search can
#      return 0 results for a query that looks fine but doesn't match how
#      people actually spell/discuss the player.
#   4. Check the coverage report (assert_min_coverage) — Reddit discussion
#      volume for most of the 49 candidates (many play in Botola Pro /
#      lesser-known leagues) will likely be LOW. Don't be surprised if
#      real coverage is under 30% — that's a volume reality, not a bug.

import logging
import os
from datetime import datetime

import pandas as pd

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

# Subreddits most likely to discuss Morocco national team players.
# r/soccer is huge/general (English), the others are more targeted.
# This list is a starting guess, NOT verified against real subreddit
# activity levels — check actual hit counts on first real run and prune
# subreddits that return nothing.
SUBREDDITS = "soccer+MoroccoNationalTeam+Morocco"

MAX_SUBMISSIONS_PER_PLAYER = 15
MAX_COMMENTS_PER_SUBMISSION = 10
SEARCH_TIME_FILTER = "year"  # "all", "year", "month", "week" — PRAW's search() param


def _try_import_praw():
    try:
        import praw
        return praw
    except ImportError:
        logger.warning("praw not installed — run: pip install praw")
        return None


def _build_reddit_client(praw_module):
    """
    Builds a read-only PRAW client from .env credentials.
    Returns None (not a raised exception) if credentials are missing —
    matches the existing pattern in this codebase (statsbomb_loader.py's
    _try_import_statsbomb) of degrading gracefully rather than crashing
    the whole Bronze run over one missing optional source.
    """
    client_id     = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent    = os.getenv("REDDIT_USER_AGENT")

    if not all([client_id, client_secret, user_agent]):
        logger.warning(
            "Reddit credentials missing from .env (REDDIT_CLIENT_ID/"
            "REDDIT_CLIENT_SECRET/REDDIT_USER_AGENT) — skipping Reddit source. "
            "Register a free app at https://reddit.com/prefs/apps to enable it."
        )
        return None

    reddit = praw_module.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )
    reddit.read_only = True
    return reddit


def _collect_player_texts(reddit, player_name: str) -> list[str]:
    """
    Searches SUBREDDITS for `player_name` and collects submission titles +
    top comments as raw text for downstream sentiment scoring.

    Returns a flat list of strings (titles and comment bodies mixed) —
    the NLP layer (analytics/nlp/sentiment_analyzer.py) is responsible for
    per-language routing and scoring, this function only collects text.
    """
    texts: list[str] = []
    subreddit = reddit.subreddit(SUBREDDITS)

    for submission in subreddit.search(
        player_name, limit=MAX_SUBMISSIONS_PER_PLAYER, time_filter=SEARCH_TIME_FILTER
    ):
        if submission.title:
            texts.append(submission.title)
        if submission.selftext:
            texts.append(submission.selftext)

        # Flatten comment tree without expanding "load more comments"
        # (replace_more(limit=0) drops those nodes instead of making more
        # API calls per submission — keeps this loader's request budget
        # predictable: ~1 search + 1 comment-tree fetch per player).
        submission.comments.replace_more(limit=0)
        for comment in submission.comments.list()[:MAX_COMMENTS_PER_SUBMISSION]:
            if comment.body:
                texts.append(comment.body)

    return texts


def load_reddit_bronze(seed_df: pd.DataFrame, run_date: str = "") -> str:
    """
    Collects raw text mentioning each of the 49 candidates from Reddit,
    for downstream sentiment scoring.

    Args:
        seed_df:  Must contain columns: player_id, player_name
        run_date: Timestamp string; auto-generated if empty

    Returns:
        Status string, e.g. "OK (12/49 players with data)",
        "OK (0/49 — praw not installed)", or "OK (0/49 — credentials missing)"
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "player_name"], source="reddit")

    praw_module = _try_import_praw()
    if praw_module is None:
        records = [{"player_id": row["player_id"]} for _, row in seed_df.iterrows()]
        upload_json(BUCKET, f"reddit/players_reddit_{run_date}.json", records)
        return "OK (0/49 — praw not installed)"

    reddit = _build_reddit_client(praw_module)
    if reddit is None:
        records = [{"player_id": row["player_id"]} for _, row in seed_df.iterrows()]
        upload_json(BUCKET, f"reddit/players_reddit_{run_date}.json", records)
        return "OK (0/49 — credentials missing)"

    records = []
    for _, row in seed_df.iterrows():
        try:
            texts = _collect_player_texts(reddit, row["player_name"])
        except Exception as e:
            logger.debug("Reddit collection failed for %s: %s", row["player_name"], e)
            texts = []

        record = {"player_id": row["player_id"]}
        if texts:
            record["reddit_texts"] = texts
            record["reddit_text_count"] = len(texts)
        records.append(record)

    assert_min_coverage(records, min_pct=0.15, source="reddit")
    # min_pct deliberately lower than other sources (0.30 default) — Reddit
    # discussion volume for a 49-player pool skewed toward non-European
    # leagues is realistically going to be sparse for most candidates.
    # See the module-level "BEFORE TRUSTING" note above.

    object_name = f"reddit/players_reddit_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"
