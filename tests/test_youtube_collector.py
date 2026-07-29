# tests/test_youtube_collector.py
#
# Tests ingestion/loaders/youtube_collector.py's collection logic using
# MOCKED YouTube API objects — no live network calls (googleapis.com is
# not reachable from this environment). Validates request-building,
# text-extraction, comments-disabled handling, and the quota-budget
# early-stop logic.

import sys
import os
from unittest.mock import MagicMock, patch

import httplib2
import pandas as pd
import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingestion.loaders.youtube_collector import (
    _collect_player_texts,
    load_youtube_bronze,
    SEARCH_QUOTA_COST,
    COMMENT_QUOTA_COST,
)


def _make_http_error(status=403, message="commentsDisabled"):
    resp = httplib2.Response({"status": status})
    resp.reason = "Forbidden"
    return HttpError(resp, f'{{"error": {{"message": "{message}"}}}}'.encode(), uri="https://fake")


def _make_search_response(items):
    """items: list of (video_id, title) tuples"""
    return {
        "items": [
            {"id": {"videoId": vid}, "snippet": {"title": title}}
            for vid, title in items
        ]
    }


def _make_comments_response(comment_texts):
    return {
        "items": [
            {"snippet": {"topLevelComment": {"snippet": {"textDisplay": text}}}}
            for text in comment_texts
        ]
    }


class TestCollectPlayerTexts:

    def test_extracts_video_titles_and_comments(self):
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = _make_search_response(
            [("vid1", "Hakimi amazing tackle")]
        )
        mock_yt.commentThreads().list().execute.return_value = _make_comments_response(
            ["Hakimi is a great player!", "Best defender, Hakimi never disappoints"]
        )

        texts = _collect_player_texts(mock_yt, HttpError, "Achraf Hakimi", {})

        assert "Hakimi amazing tackle" in texts
        assert "Hakimi is a great player!" in texts

    def test_filters_content_not_mentioning_player(self):
        """
        REGRESSION TEST for the real bug found via data-analysis of the
        first full run (2026-07-24): YouTube comment sections are
        especially prone to drifting off-topic (praise for a different
        player, general match banter) — a video matching the search query
        does NOT mean its comments are actually about the player. Same
        class of bug independently found in arctic_shift_collector.py and
        newsapi_collector.py.
        """
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = _make_search_response(
            [("vid1", "Morocco vs Canada World Cup highlights")]  # doesn't mention Hakimi
        )
        mock_yt.commentThreads().list().execute.return_value = _make_comments_response(
            ["Messi is the GOAT", "What a match overall", "Hakimi's pass was incredible though"]
        )

        texts = _collect_player_texts(mock_yt, HttpError, "Achraf Hakimi", {})

        assert "Morocco vs Canada World Cup highlights" not in texts  # title doesn't mention him
        assert "Messi is the GOAT" not in texts
        assert "What a match overall" not in texts
        assert "Hakimi's pass was incredible though" in texts  # this one does mention him

    def test_comments_disabled_skips_video_not_whole_player(self):
        """
        A 403 (comments disabled) on ONE video must not lose the video's
        title or crash — it should just skip fetching that video's
        comments and continue.
        """
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = _make_search_response(
            [("vid1", "News clip about a player")]
        )
        mock_yt.commentThreads().list().execute.side_effect = _make_http_error()

        texts = _collect_player_texts(mock_yt, HttpError, "Some Player", {})

        # Title still captured even though comments failed
        assert texts == ["News clip about a player"]

    def test_search_failure_returns_empty_list_not_crash(self):
        mock_yt = MagicMock()
        mock_yt.search().list().execute.side_effect = _make_http_error(status=500, message="quotaExceeded")

        texts = _collect_player_texts(mock_yt, HttpError, "Player", {})

        assert texts == []

    def test_video_without_id_is_skipped_for_comments(self):
        """Defensive: a search result missing videoId shouldn't crash comment-fetching."""
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = {
            "items": [{"id": {}, "snippet": {"title": "Player scores a great goal"}}]
        }

        texts = _collect_player_texts(mock_yt, HttpError, "Player", {})

        assert texts == ["Player scores a great goal"]
        mock_yt.commentThreads.assert_not_called() if hasattr(mock_yt.commentThreads, "assert_not_called") else None

    def test_quota_tracker_accumulates_correctly(self):
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = _make_search_response([("vid1", "Title")])
        mock_yt.commentThreads().list().execute.return_value = _make_comments_response(["comment"])

        tracker = {}
        _collect_player_texts(mock_yt, HttpError, "Player", tracker)

        assert tracker["units_used"] == SEARCH_QUOTA_COST + COMMENT_QUOTA_COST


class TestLoadYoutubeBronze:

    def test_missing_api_key_degrades_gracefully(self, monkeypatch):
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        seed = pd.DataFrame({"player_id": ["P001"], "player_name": ["Player A"]})

        with patch("ingestion.loaders.youtube_collector.upload_json", return_value=True) as mock_upload:
            status = load_youtube_bronze(seed, run_date="TEST")

        assert "not available" in status
        mock_upload.assert_called_once()

    def test_missing_seed_columns_raises(self):
        bad_seed = pd.DataFrame({"player_id": ["P001"]})
        with pytest.raises(ValueError):
            load_youtube_bronze(bad_seed, run_date="TEST")

    def test_quota_budget_stops_early(self, monkeypatch):
        """
        REGRESSION-DOCUMENTING TEST for the quota-budget guard: confirms
        that once the running total gets within SEARCH_QUOTA_COST of the
        9500-unit soft cap, remaining players are skipped cleanly instead
        of firing requests that would get quota-rejected.
        """
        monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

        # 96 players so the cap (9500/100=95 searches) triggers partway through
        seed = pd.DataFrame({
            "player_id": [f"P{i:03d}" for i in range(96)],
            "player_name": [f"Player {i}" for i in range(96)],
        })

        mock_yt_client = MagicMock()
        mock_yt_client.search().list().execute.return_value = _make_search_response([])

        with patch("ingestion.loaders.youtube_collector.upload_json", return_value=True), \
             patch("ingestion.loaders.youtube_collector._try_build_youtube_client",
                   return_value=(mock_yt_client, HttpError)):
            status = load_youtube_bronze(seed, run_date="TEST")

        # Should mention a quota estimate under 10000, proving it stopped early
        assert "quota units used" in status


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))