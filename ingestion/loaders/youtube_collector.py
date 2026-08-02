# ingestion/loaders/youtube_collector.py
# Source: YouTube Data API v3 — raw text for SentimentScore
#
# Added 2026-07-22 (Sentiment session). Free tier: 10,000 quota units/day.
# See MASTER_SPEC_ADDENDUM_2026-07-14.md §4 for why this was chosen over
# Twitter/X (no free tier since Feb 2026).
#
# ⚠️ UNTESTED AGAINST LIVE YOUTUBE ⚠️
# googleapis.com is not reachable from the sandbox this was written in.
# The request-building logic (correct parameter names, correct URI shape)
# WAS verified offline using google-api-python-client's bundled static
# discovery document (no network needed for that part — confirmed by
# building a real client object and inspecting request.uri without
# executing it). What's NOT verified: an actual authenticated call
# succeeding, real comment data coming back, or realistic result volume
# for these specific 49 players.
#
# ⚠️ QUOTA BUDGET — READ BEFORE RUNNING ⚠️
# search().list() costs 100 units per call. commentThreads().list() costs
# only 1 unit per call. With 49 players and 1 search call each, that's
# 4,900 units just for search — nearly HALF the daily 10,000-unit budget
# — before a single comment is fetched. This loader is written to:
#   - do exactly ONE search per player (not one per video-of-interest)
#   - fetch comments for only the TOP N videos from that search
#   - log a running quota estimate so you can see how close to the daily
#     cap a full 49-player run gets, and stop early if needed
# If you need to re-run this multiple times in a day, you WILL hit the
# quota — Google's console shows usage in real time, check it before
# assuming a failure is a bug.
#
# BEFORE TRUSTING THIS IN A REAL RUN:
#   1. Get a free API key: console.cloud.google.com -> enable
#      "YouTube Data API v3" -> Credentials -> API key.
#   2. Add to .env: YOUTUBE_API_KEY
#   3. Run for 2-3 players first (see --limit pattern used elsewhere in
#      this codebase, e.g. run_bronze.py --limit), check the quota
#      estimate in the logs, and confirm comments actually come back
#      non-empty before running all 49.
#   4. Some videos have comments disabted (common for official club/news
#      channels) — this loader handles that (HttpError 403) by skipping
#      the video, not crashing.

import logging
import os
from datetime import datetime

import pandas as pd

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import (
    validate_seed_columns, assert_min_coverage, mentions_player as _mentions_player,
)

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

MAX_VIDEOS_PER_PLAYER = 3       # search results to pull comments from
MAX_COMMENTS_PER_VIDEO = 20
SEARCH_QUOTA_COST = 100         # units, per YouTube Data API v3 pricing
COMMENT_QUOTA_COST = 1          # units, per commentThreads.list call

# Appended to the search query to bias results toward relevant football
# content instead of unrelated same-name matches (e.g. common names).
SEARCH_QUERY_SUFFIX = "Morocco football"


def _try_build_youtube_client():
    """
    Builds a YouTube Data API v3 client. Returns None (not a raised
    exception) if the API key is missing or the client library isn't
    installed — matches this codebase's existing graceful-degradation
    pattern for optional sources.
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        logger.warning("google-api-python-client not installed — run: pip install google-api-python-client")
        return None, None

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        logger.warning(
            "YOUTUBE_API_KEY missing from .env — skipping YouTube source. "
            "Get a free key at console.cloud.google.com (enable 'YouTube Data API v3')."
        )
        return None, None

    # static_discovery=True avoids a live network call just to BUILD the
    # client (the discovery doc for youtube v3 ships bundled with the
    # library) — only actual .execute() calls need network. Verified
    # offline that this works without reaching googleapis.com.
    client = build("youtube", "v3", developerKey=api_key, cache_discovery=False, static_discovery=True)
    return client, HttpError


def _collect_player_texts(youtube, HttpError, player_name: str, quota_tracker: dict, as_of: "date | None" = None) -> list[str]:
    """
    Searches YouTube for `player_name` + context, then pulls top comments
    from the top MAX_VIDEOS_PER_PLAYER results.

    Args:
        quota_tracker: a shared dict with key "units_used", mutated in
            place so the caller can log a running total across all 49
            players (see the quota-budget warning in the module docstring).
        as_of: TEMPORAL CUTOFF FIX (01_temporal_cutoff.md). Upper-bound
            date passed as YouTube's `publishedBefore` param — defaults to
            config.DECISION_DATE. Without this, this collector had ZERO
            date bound at all, so a live run could pull in videos/comments
            from any time, including well after the actual decision date,
            straight into SentimentScore. Set to the START of the day
            AFTER `as_of` (RFC 3339, UTC) so content published ON as_of's
            date is still included — only strictly-after content is cut.

    Returns:
        Flat list of strings (video titles + comment text).
    """
    if as_of is None:
        from config import DECISION_DATE
        as_of = DECISION_DATE
    from datetime import timedelta
    published_before = (as_of + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

    texts: list[str] = []

    try:
        search_response = youtube.search().list(
            part="snippet",
            q=f"{player_name} {SEARCH_QUERY_SUFFIX}",
            type="video",
            maxResults=MAX_VIDEOS_PER_PLAYER,
            order="relevance",
            publishedBefore=published_before,  # TEMPORAL CUTOFF FIX
        ).execute()
        quota_tracker["units_used"] = quota_tracker.get("units_used", 0) + SEARCH_QUOTA_COST
    except HttpError as e:
        logger.debug("YouTube search failed for %s: %s", player_name, e)
        return texts

    for item in search_response.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        title = item.get("snippet", {}).get("title")
        # FIX (found via real-data analysis, 2026-07-24 — same class of bug
        # as arctic_shift_collector.py and newsapi_collector.py, found
        # independently in each of the 3 sources): a video matching the
        # search query is NOT the same as its comments actually being
        # about the player. YouTube comment sections are especially prone
        # to drifting off-topic (praise for a different player, general
        # match banter) — this was likely a major contributor to the
        # all-positive SentimentScore skew found in the first real
        # end-to-end run.
        if title and _mentions_player(title, player_name):
            texts.append(title)
        if not video_id:
            continue

        try:
            comments_response = youtube.commentThreads().list(
                part="snippet",
                videoId=video_id,
                maxResults=MAX_COMMENTS_PER_VIDEO,
                order="relevance",
                textFormat="plainText",
            ).execute()
            quota_tracker["units_used"] = quota_tracker.get("units_used", 0) + COMMENT_QUOTA_COST
        except HttpError as e:
            # Common and expected: comments disabled on this video (403).
            # Skip it, don't treat as a fatal error for the whole player.
            logger.debug("Comments unavailable for video %s (%s): %s", video_id, player_name, e)
            continue

        for item in comments_response.get("items", []):
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            comment_text = snippet.get("textDisplay")
            if comment_text and _mentions_player(comment_text, player_name):
                texts.append(comment_text)

    return texts


def load_youtube_bronze(seed_df: pd.DataFrame, run_date: str = "", as_of: "date | None" = None) -> str:
    """
    Collects raw text (video titles + comments) mentioning each of the 49
    candidates from YouTube, for downstream sentiment scoring.

    Args:
        seed_df:  Must contain columns: player_id, player_name
        run_date: Timestamp string; auto-generated if empty
        as_of: TEMPORAL CUTOFF FIX (01_temporal_cutoff.md) — decision-date
            cutoff passed through to _collect_player_texts(). Defaults to
            config.DECISION_DATE.

    Returns:
        Status string, e.g. "OK (18/49 players with data, ~5200/10000 quota units used)"
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    if as_of is None:
        from config import DECISION_DATE
        as_of = DECISION_DATE

    validate_seed_columns(seed_df, ["player_id", "player_name"], source="youtube")

    youtube, HttpError = _try_build_youtube_client()
    if youtube is None:
        records = [{"player_id": row["player_id"]} for _, row in seed_df.iterrows()]
        upload_json(BUCKET, f"youtube/players_youtube_{run_date}.json", records)
        return "OK (0/49 — client not available, see logs)"

    quota_tracker: dict = {"units_used": 0}
    records = []

    for _, row in seed_df.iterrows():
        # Stop early if we're about to blow the daily budget — better to
        # skip the remaining players cleanly than get every remaining
        # request rejected with a quota-exceeded error.
        if quota_tracker["units_used"] + SEARCH_QUOTA_COST > 9500:
            logger.warning(
                "YouTube: stopping early at %d/49 players — quota estimate "
                "(%d units) is approaching the 10,000/day free limit.",
                len(records), quota_tracker["units_used"],
            )
            records.append({"player_id": row["player_id"]})
            continue

        texts = _collect_player_texts(youtube, HttpError, row["player_name"], quota_tracker, as_of=as_of)
        record = {"player_id": row["player_id"]}
        if texts:
            record["youtube_texts"] = texts
            record["youtube_text_count"] = len(texts)
        records.append(record)

    assert_min_coverage(records, min_pct=0.20, source="youtube")

    object_name = f"youtube/players_youtube_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    logger.info(
        "YouTube quota estimate: ~%d/10000 units used this run.",
        quota_tracker["units_used"],
    )
    return (
        f"OK ({ok_count}/{len(records)} players with data, "
        f"~{quota_tracker['units_used']}/10000 quota units used)"
        if success else "FAILED"
    )