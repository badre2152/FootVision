# ingestion/loaders/newsapi_collector.py
# Source: NewsAPI.org — raw text (headlines + descriptions) for SentimentScore
#
# Added 2026-07-22 (Sentiment session). Free "Developer" tier: 100
# requests/day, articles from the last month only, non-commercial use.
# Uses plain `requests` against NewsAPI's REST endpoint — no official
# Python SDK needed, the API is a simple JSON GET request.
#
# ⚠️ UNTESTED AGAINST LIVE NEWSAPI ⚠️
# newsapi.org is not reachable from the sandbox this was written in. The
# request URL/params below match NewsAPI's public documentation (a
# stable, simple REST API — GET /v2/everything?q=...&apiKey=...), but
# have never been executed against the live service from this
# environment. Response-parsing logic IS unit-tested against a
# hand-built JSON shape matching NewsAPI's documented response format
# (see tests/test_newsapi_collector.py).
#
# ⚠️ QUOTA BUDGET ⚠️
# 100 requests/day on the free tier. With 49 players and ONE request
# each, that's 49/100 — comfortably within budget for a single daily
# run, but NOT enough for repeated same-day re-runs while debugging.
# Unlike YouTube's per-call cost model, NewsAPI is a flat "1 call = 1
# request" — simpler to reason about, but the daily cap is much lower.
#
# NOTE ON LANGUAGE: NewsAPI's `language` param supports "fr" and "ar"
# (among others) but does NOT have a Darija-specific option (Darija isn't
# a distinct ISO language code NewsAPI recognizes) — Moroccan sports
# coverage in Darija specifically will mostly surface under "ar" (if
# written in Arabic script) or not be filterable by language at all if
# written in Latin-script Darija. This loader queries WITHOUT a language
# filter by default (broader net) and lets the NLP layer's language
# detection sort it out — restricting to language="fr" or "ar" upfront
# would silently drop relevant English-language coverage of these
# players (many play in the Premier League, MLS-adjacent leagues, etc.)
#
# BEFORE TRUSTING THIS IN A REAL RUN:
#   1. Get a free key: https://newsapi.org/register (instant, no approval).
#   2. Add to .env: NEWSAPI_KEY
#   3. Run for 2-3 players, confirm articles actually come back — some
#      lesser-known Botola Pro players may have near-zero English/French/
#      Arabic news coverage (this is a real coverage ceiling, not a bug).

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

# Appended to the search query to bias results toward relevant football
# coverage instead of unrelated same-name matches.
SEARCH_QUERY_SUFFIX = "football"


def _collect_player_articles(api_key: str, player_name: str) -> list[str]:
    """
    Queries NewsAPI's /v2/everything for `player_name` + football context,
    returning a flat list of text (title + description per article).

    Returns an empty list on any request failure (rate limit, network
    error, bad key) — logged at debug level, never raised, so one
    player's failure doesn't stop the whole 49-player run.
    """
    texts: list[str] = []
    params = {
        "q": f'"{player_name}" {SEARCH_QUERY_SUFFIX}',
        "sortBy": "relevancy",
        "pageSize": MAX_ARTICLES_PER_PLAYER,
        "apiKey": api_key,
    }

    try:
        resp = requests.get(NEWSAPI_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.debug("NewsAPI request failed for %s: %s", player_name, e)
        return texts
    except ValueError as e:  # JSON decode error
        logger.debug("NewsAPI returned non-JSON for %s: %s", player_name, e)
        return texts

    if data.get("status") != "ok":
        # NewsAPI returns {"status": "error", "code": ..., "message": ...}
        # on rate-limit/auth failures — log it distinctly from a network
        # failure so it's clear the request reached the server.
        logger.debug(
            "NewsAPI error for %s: %s", player_name, data.get("message", "unknown error")
        )
        return texts

    for article in data.get("articles", []):
        title = article.get("title")
        description = article.get("description")
        # FIX (found via real data inspection, 2026-07-24): NewsAPI's
        # `q` search matches the player's name ANYWHERE in the full
        # article body, not necessarily in the title/description we
        # actually extract — a real run turned up generic match-preview
        # articles ("How to watch France vs Morocco", "Canada vs Morocco
        # projected lineups") among a specific player's texts, diluting
        # genuine sentiment with neutral/factual content that isn't
        # really "about" him. Same class of bug as the Arctic Shift
        # relevance fix from a day earlier — require the extracted text
        # to actually mention the player, not just trust that the
        # overall article matched the search.
        if title and _mentions_player(title, player_name):
            texts.append(title)
        if description and _mentions_player(description, player_name):
            texts.append(description)

    return texts


def load_newsapi_bronze(seed_df: pd.DataFrame, run_date: str = "") -> str:
    """
    Collects raw text (headlines + descriptions) mentioning each of the
    49 candidates from NewsAPI.org, for downstream sentiment scoring.

    Args:
        seed_df:  Must contain columns: player_id, player_name
        run_date: Timestamp string; auto-generated if empty

    Returns:
        Status string, e.g. "OK (22/49 players with data)"
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

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
        texts = _collect_player_articles(api_key, row["player_name"])
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