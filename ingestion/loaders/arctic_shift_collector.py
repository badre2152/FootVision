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

SUBREDDITS = ["soccer", "MoroccoNationalTeam", "Morocco"]

MAX_POSTS_PER_SUBREDDIT = 10
MAX_COMMENTS_PER_POST = 10
REQUEST_TIMEOUT_SECONDS = 15
POLITE_DELAY_SECONDS = 0.5


def _search_posts(subreddit: str, query: str, before_ts: int | None = None) -> list[dict]:
    """
    Searches one subreddit for `query` using Arctic Shift's
    /api/posts/search endpoint. No auth needed.

    Args:
        before_ts: Unix timestamp (seconds) passed as Arctic Shift's
            `before` param, bounding results to before this time.

    Returns a list of raw post dicts, or an empty list on any failure.
    """
    params = {
        "subreddit": subreddit,
        "query": query,
        "limit": MAX_POSTS_PER_SUBREDDIT,
        "sort": "desc",
        "fields": "title,selftext,id,num_comments",
    }
    if before_ts is not None:
        params["before"] = before_ts
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
    link_id. No auth needed.

    Args:
        before_ts: Unix timestamp bounding comment fetch time, same as
            in _search_posts().
    """
    params = {
        "link_id": post_id,
        "limit": MAX_COMMENTS_PER_POST,
        "fields": "body,author",
    }
    if before_ts is not None:
        params["before"] = before_ts
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


BOT_AUTHORS = {"automoderator", "[deleted]"}
BOT_TEXT_PATTERNS = ("has been removed under", "your submission was removed")


def _is_bot_or_removed(text: str, author: str | None) -> bool:
    if author and author.lower() in BOT_AUTHORS:
        return True
    text_lower = (text or "").lower()
    return any(pattern in text_lower for pattern in BOT_TEXT_PATTERNS)


def _collect_player_texts(player_name: str, as_of: "date | None" = None) -> list[str]:
    """
    Searches all SUBREDDITS for `player_name`, collecting post titles +
    selftext + comments, with comments filtered to only those that also
    mention the player by name (or surname).

    Args:
        as_of: Defaults to config.DECISION_DATE. Converted to a Unix
            timestamp (end of that day, UTC) and passed as `before` to
            both the post and comment searches.
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
                        continue
                    texts.append(body)

    return texts


def load_arctic_shift_bronze(seed_df: pd.DataFrame, run_date: str = "", as_of: "date | None" = None) -> str:
    """
    Collects raw text mentioning each of the 49 candidates from Reddit via
    Arctic Shift (no auth needed), for downstream sentiment scoring.

    Args:
        seed_df:  Must contain columns: player_id, player_name
        run_date: Timestamp string; auto-generated if empty
        as_of: Decision-date cutoff passed through to
            _collect_player_texts(). Defaults to config.DECISION_DATE.

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
            record["reddit_texts"] = texts
            record["reddit_text_count"] = len(texts)
        records.append(record)

    assert_min_coverage(records, min_pct=0.15, source="arctic_shift")

    object_name = f"reddit/players_reddit_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"