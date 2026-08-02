# ingestion/loaders/arctic_shift_collector.py
# Source: Arctic Shift (arctic-shift.photon-reddit.com) — Reddit data
# without needing Reddit's own API/OAuth.
#
# Added 2026-07-23, REPLACING ingestion/loaders/reddit_collector.py (PRAW-
# based). Reason: as of 2026, Reddit's official Data API closed self-
# service OAuth app registration — new access requires manual approval
# under Reddit's "Responsible Builder Policy", and small/personal
# projects are reportedly the most-rejected category, often with no
# response at all. reddit_collector.py is left in the codebase for
# reference/if official access is ever granted, but this is the
# practical path for this project.
#
# ✅ UNUSUALLY WELL-VERIFIED FOR THIS SESSION ✅
# Unlike every other new source built this session, this one's actual
# HTTP response was fetched LIVE and inspected during development:
#   GET https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=worldnews&title=wuhan&limit=10
#   -> real JSON confirmed: {"data": [{"title": ..., "selftext": ...,
#      "id": ..., "num_comments": ..., "author": ..., ...}, ...]}
# The response schema and field names below are NOT guessed — they match
# a real, live API response fetched 2026-07-23. What ISN'T verified: real
# result relevance/volume for a "player name + subreddit" query
# specifically (the live test used a generic "wuhan" keyword in
# r/worldnews to confirm the schema, not a football player name) — see
# the checklist at the bottom before trusting real coverage numbers.
#
# NO AUTHENTICATION NEEDED — no client_id/secret/API key required at all,
# unlike every other source in this project. Just plain HTTP GET requests.
#
# Rate limiting: soft (no documented daily cap, just "don't hammer it" —
# the API returns an X-RateLimit-Remaining header). This loader adds a
# small delay between requests to be a considerate free-service user, per
# the project's own documentation: "this is a free service, so be
# considerate."

import logging
import time
from datetime import datetime

import pandas as pd
import requests

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import (
    validate_seed_columns, assert_min_coverage, mentions_player as _mentions_player,
)

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"
BASE_URL = "https://arctic-shift.photon-reddit.com"

# Subreddits most likely to discuss Morocco national team players — same
# starting guess as the retired reddit_collector.py. Arctic Shift's
# keyword search ("query" param) is documented to only work reliably when
# combined with a "subreddit" (or "author") parameter — there's no
# working global cross-subreddit keyword search — so this loader queries
# each subreddit separately per player, not all of Reddit at once.
SUBREDDITS = ["soccer", "MoroccoNationalTeam", "Morocco"]

MAX_POSTS_PER_SUBREDDIT = 10
MAX_COMMENTS_PER_POST = 10
REQUEST_TIMEOUT_SECONDS = 15
POLITE_DELAY_SECONDS = 0.5  # between requests, to be a considerate free-service user


def _search_posts(subreddit: str, query: str, before_ts: int | None = None) -> list[dict]:
    """
    Searches one subreddit for `query` using Arctic Shift's
    /api/posts/search endpoint. No auth needed.

    Args:
        before_ts: TEMPORAL CUTOFF FIX (01_temporal_cutoff.md) — Unix
            timestamp (seconds) passed as Arctic Shift's `before` param.
            Without this, this collector had ZERO date bound at all, so
            a live run could pull in Reddit posts from any time,
            including well after the actual decision date, straight into
            SentimentScore.

    Returns a list of raw post dicts (Arctic Shift's native Reddit post
    schema — same shape as PRAW/the official API would return) or an
    empty list on any failure.
    """
    params = {
        "subreddit": subreddit,
        "query": query,
        "limit": MAX_POSTS_PER_SUBREDDIT,
        "sort": "desc",
        "fields": "title,selftext,id,num_comments",
    }
    if before_ts is not None:
        params["before"] = before_ts  # TEMPORAL CUTOFF FIX
    try:
        resp = requests.get(
            f"{BASE_URL}/api/posts/search",
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.debug("Arctic Shift post search failed (subreddit=%s, query=%s): %s", subreddit, query, e)
        return []
    except ValueError as e:
        logger.debug("Arctic Shift returned non-JSON (subreddit=%s, query=%s): %s", subreddit, query, e)
        return []

    return data.get("data", [])


def _search_comments(post_id: str, before_ts: int | None = None) -> list[dict]:
    """
    Fetches top comments for a post via /api/comments/search, filtered by
    link_id. No auth needed. Requests "author" too (needed to filter out
    AutoModerator/bot noise — see _is_bot_or_removed_text()).

    Args:
        before_ts: TEMPORAL CUTOFF FIX (01_temporal_cutoff.md). A post
            that predates the cutoff can still pick up NEW comments after
            it — this bounds comment fetch time the same way _search_posts()
            bounds post fetch time, so a stale-but-still-open thread can't
            leak post-cutoff sentiment in through its comment section.
    """
    params = {
        "link_id": post_id,
        "limit": MAX_COMMENTS_PER_POST,
        "fields": "body,author",
    }
    if before_ts is not None:
        params["before"] = before_ts  # TEMPORAL CUTOFF FIX
    try:
        resp = requests.get(
            f"{BASE_URL}/api/comments/search",
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.debug("Arctic Shift comment search failed (post_id=%s): %s", post_id, e)
        return []
    except ValueError as e:
        logger.debug("Arctic Shift returned non-JSON comments (post_id=%s): %s", post_id, e)
        return []

    return data.get("data", [])


# Bot/mod accounts whose comments are never real fan sentiment. Found via
# live testing (2026-07-23): AutoModerator messages like "Your post has
# been removed under the Submission Guidelines: Shitposting" were showing
# up in real results, polluting the sentiment signal with moderation
# boilerplate.
BOT_AUTHORS = {"automoderator", "[deleted]"}
BOT_TEXT_PATTERNS = ("has been removed under", "your submission was removed")


def _is_bot_or_removed(text: str, author: str | None) -> bool:
    if author and author.lower() in BOT_AUTHORS:
        return True
    text_lower = (text or "").lower()
    return any(pattern in text_lower for pattern in BOT_TEXT_PATTERNS)


# _mentions_player is now imported from ingestion.loaders._bronze_utils
# (moved there 2026-07-24 to share with newsapi_collector.py, which needed
# the exact same fix independently — see _bronze_utils.py's docstring).


def _collect_player_texts(player_name: str, as_of: "date | None" = None) -> list[str]:
    """
    Searches all SUBREDDITS for `player_name`, collecting post titles +
    selftext (always relevant — they came directly from a name-matched
    search) + comments, but comments are FILTERED to only those that also
    mention the player by name (or surname) — see _mentions_player() for
    why this filter was added after live testing surfaced irrelevant
    megathread comments in real results.

    Args:
        as_of: TEMPORAL CUTOFF FIX (01_temporal_cutoff.md). Defaults to
            config.DECISION_DATE. Converted to a Unix timestamp (end of
            that day, UTC) and passed as `before` to both the post and
            comment searches, so nothing dated after the decision date —
            including new comments on an old, still-open post — can leak
            into SentimentScore.
    """
    if as_of is None:
        from config import DECISION_DATE
        as_of = DECISION_DATE
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    before_ts = int((_dt.combine(as_of, _dt.min.time(), tzinfo=_tz.utc) + _td(days=1)).timestamp())

    texts: list[str] = []

    for subreddit in SUBREDDITS:
        posts = _search_posts(subreddit, player_name, before_ts=before_ts)
        time.sleep(POLITE_DELAY_SECONDS)

        for post in posts:
            title = post.get("title")
            selftext = post.get("selftext")
            post_id = post.get("id")

            # FIX (round 2, 2026-07-23): titles/selftext are NOT
            # automatically relevant just because the post matched the
            # search — live testing found a post matching "Achraf Hakimi"
            # whose selftext was entirely about Messi (his name likely
            # appeared once, deep in the text, or the match was fuzzy/
            # relevance-based rather than a strict phrase match). Apply
            # the same _mentions_player() check used for comments.
            if title and _mentions_player(title, player_name):
                texts.append(title)
            if (selftext and selftext not in ("[deleted]", "[removed]", "")
                    and _mentions_player(selftext, player_name)):
                texts.append(selftext)

            if post_id and post.get("num_comments", 0) > 0:
                comments = _search_comments(post_id, before_ts=before_ts)
                time.sleep(POLITE_DELAY_SECONDS)
                for comment in comments:
                    body = comment.get("body")
                    author = comment.get("author")
                    if not body or body in ("[deleted]", "[removed]", ""):
                        continue
                    if _is_bot_or_removed(body, author):
                        continue
                    if not _mentions_player(body, player_name):
                        # A comment on a matched post is only kept if IT
                        # ITSELF mentions the player — not just because
                        # the post's title/selftext did.
                        continue
                    texts.append(body)

    return texts


def load_arctic_shift_bronze(seed_df: pd.DataFrame, run_date: str = "", as_of: "date | None" = None) -> str:
    """
    Collects raw text mentioning each of the 49 candidates from Reddit via
    Arctic Shift (no auth needed), for downstream sentiment scoring.

    Writes to the SAME bronze/reddit/ path and "reddit_texts" field as
    the retired reddit_collector.py, so silver_pipeline.py's
    join_sentiment_texts() needs NO changes to pick this up — it's a
    drop-in replacement at the Bronze level.

    Args:
        seed_df:  Must contain columns: player_id, player_name
        run_date: Timestamp string; auto-generated if empty
        as_of: TEMPORAL CUTOFF FIX (01_temporal_cutoff.md) — decision-date
            cutoff passed through to _collect_player_texts(). Defaults to
            config.DECISION_DATE.

    Returns:
        Status string, e.g. "OK (14/49 players with data)"
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    if as_of is None:
        from config import DECISION_DATE
        as_of = DECISION_DATE

    validate_seed_columns(seed_df, ["player_id", "player_name"], source="arctic_shift")

    records = []
    for _, row in seed_df.iterrows():
        try:
            texts = _collect_player_texts(row["player_name"], as_of=as_of)
        except Exception as e:
            logger.debug("Arctic Shift collection failed for %s: %s", row["player_name"], e)
            texts = []

        record = {"player_id": row["player_id"]}
        if texts:
            record["reddit_texts"] = texts  # same field name as reddit_collector.py
            record["reddit_text_count"] = len(texts)
        records.append(record)

    assert_min_coverage(records, min_pct=0.15, source="arctic_shift")
    # Same low threshold as reddit_collector.py — Reddit discussion volume
    # for a 49-player pool skewed toward non-European leagues is
    # realistically sparse for most candidates, not a bug if coverage is low.

    object_name = f"reddit/players_reddit_{run_date}.json"  # same folder as reddit_collector.py
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"