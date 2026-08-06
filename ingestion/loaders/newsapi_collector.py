import logging
import os
from datetime import datetime

import pandas as pd
import requests

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage, mentions_player as _mentions_player

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"
NEWSAPI_URL = "https://newsapi.org/v2/everything"

MAX_ARTICLES_PER_PLAYER = 20
REQUEST_TIMEOUT_SECONDS = 15

SEARCH_QUERY_SUFFIX = "football"


def _collect_player_articles(api_key: str, player_name: str, as_of: "date | None" = None) -> list[str]:
    """
    Queries NewsAPI's /v2/everything for `player_name` + football context,
    returning a flat list of text (title + description per article).

    Args:
        as_of: TEMPORAL CUTOFF FIX (01_temporal_cutoff.md). Upper-bound
            date passed as NewsAPI's `to` param — defaults to
            config.DECISION_DATE. Without this, this collector had ZERO
            date bound at all (no `from`/`to` params), so a live run
            could pull in football coverage from any time, including
            well after the actual decision date, straight into
            SentimentScore.

            ⚠️ KNOWN LIMITATION (free tier): NewsAPI's free "Developer"
            plan only serves articles from the last ~30 real-world days
            (see module header) — it does NOT let `to` reach arbitrarily
            far into the past. If this collector is run more than ~30
            days after `as_of`, the free tier's own rolling window and
            `to=as_of` will not overlap at all, and NewsAPI will
            correctly return zero articles rather than silently serving
            post-cutoff ones. That's the SAFE failure mode (no leakage),
            but it does mean NewsAPI-sourced SentimentScore coverage is
            structurally unavailable once real time has moved far enough
            past DECISION_DATE — a paid tier with full historical access
            would be needed to backfill it correctly. This is a genuine
            data-source constraint, not something more code can fix.

    Returns an empty list on any request failure (rate limit, network
    error, bad key) — logged at debug level, never raised, so one
    player's failure doesn't stop the whole 49-player run.
    """
    if as_of is None:
        from config import DECISION_DATE
        as_of = DECISION_DATE

    texts: list[str] = []
    params = {
        "q": f'"{player_name}" {SEARCH_QUERY_SUFFIX}',
        "sortBy": "relevancy",
        "pageSize": MAX_ARTICLES_PER_PLAYER,
        "to": as_of.isoformat(),
        "apiKey": api_key,
    }

    try:
        resp = requests.get(NEWSAPI_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.debug("NewsAPI request failed for %s: %s", player_name, e)
        return texts
    except ValueError as e:
        logger.debug("NewsAPI returned non-JSON for %s: %s", player_name, e)
        return texts

    if data.get("status") != "ok":
        logger.debug(
            "NewsAPI error for %s: %s", player_name, data.get("message", "unknown error")
        )
        return texts

    for article in data.get("articles", []):
        title = article.get("title")
        description = article.get("description")
        if title and _mentions_player(title, player_name):
            texts.append(title)
        if description and _mentions_player(description, player_name):
            texts.append(description)

    return texts


def load_newsapi_bronze(seed_df: pd.DataFrame, run_date: str = "", as_of: "date | None" = None) -> str:
    """
    Collects raw text (headlines + descriptions) mentioning each of the
    49 candidates from NewsAPI.org, for downstream sentiment scoring.

    Args:
        seed_df:  Must contain columns: player_id, player_name
        run_date: Timestamp string; auto-generated if empty
        as_of: TEMPORAL CUTOFF FIX (01_temporal_cutoff.md) — decision-date
            cutoff passed through to _collect_player_articles(). Defaults
            to config.DECISION_DATE. See that function's docstring for the
            free-tier limitation this runs into.

    Returns:
        Status string, e.g. "OK (22/49 players with data)"
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    if as_of is None:
        from config import DECISION_DATE
        as_of = DECISION_DATE

    validate_seed_columns(seed_df, ["player_id", "player_name"], source="newsapi")

    api_key = os.getenv("NEWSAPI_KEY")
    if not api_key:
        logger.warning(
            "NEWSAPI_KEY missing from .env — skipping NewsAPI source. "
            "Get a free key at https://newsapi.org/register."
        )
        records = [{"player_id": row["player_id"]} for _, row in seed_df.iterrows()]
        upload_json(BUCKET, f"newsapi/players_newsapi_{run_date}.json", records)
        return "OK (0/49 — API key missing)"

    if len(seed_df) > 100:
        logger.warning(
            "seed_df has %d players but the free NewsAPI tier allows only "
            "100 requests/day — this run WILL hit the rate limit before "
            "finishing. Consider --limit on the calling script.",
            len(seed_df),
        )

    records = []
    for _, row in seed_df.iterrows():
        texts = _collect_player_articles(api_key, row["player_name"], as_of=as_of)
        record = {"player_id": row["player_id"]}
        if texts:
            record["newsapi_texts"] = texts
            record["newsapi_text_count"] = len(texts)
        records.append(record)

    assert_min_coverage(records, min_pct=0.20, source="newsapi")

    object_name = f"newsapi/players_newsapi_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"