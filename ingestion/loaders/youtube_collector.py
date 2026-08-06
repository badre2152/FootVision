import logging
import os
from datetime import datetime, date

import pandas as pd

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import (
    validate_seed_columns, assert_min_coverage, mentions_player as _mentions_player,
)

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

MAX_VIDEOS_PER_PLAYER = 3
MAX_COMMENTS_PER_VIDEO = 20
SEARCH_QUOTA_COST = 100
COMMENT_QUOTA_COST = 1

SEARCH_QUERY_SUFFIX = "Morocco football"


def _try_build_youtube_client():
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

    client = build("youtube", "v3", developerKey=api_key, cache_discovery=False, static_discovery=True)
    return client, HttpError


def _collect_player_texts(youtube, HttpError, player_name: str, quota_tracker: dict, as_of: "date | None" = None) -> list[str]:
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
            publishedBefore=published_before,
        ).execute()
        quota_tracker["units_used"] = quota_tracker.get("units_used", 0) + SEARCH_QUOTA_COST
    except HttpError as e:
        logger.debug("YouTube search failed for %s: %s", player_name, e)
        return texts

    for item in search_response.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        title = item.get("snippet", {}).get("title")
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
            logger.debug("Comments unavailable for video %s (%s): %s", video_id, player_name, e)
            continue

        for item in comments_response.get("items", []):
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            comment_text = snippet.get("textDisplay")
            if comment_text and _mentions_player(comment_text, player_name):
                texts.append(comment_text)

    return texts


def load_youtube_bronze(seed_df: pd.DataFrame, run_date: str = "", as_of: "date | None" = None) -> str:
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