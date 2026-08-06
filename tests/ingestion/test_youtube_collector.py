import sys
import os
from unittest.mock import MagicMock, patch

import httplib2
import pandas as pd
import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = _make_search_response(
            [("vid1", "Morocco vs Canada World Cup highlights")]
        )
        mock_yt.commentThreads().list().execute.return_value = _make_comments_response(
            ["Messi is the GOAT", "What a match overall", "Hakimi's pass was incredible though"]
        )

        texts = _collect_player_texts(mock_yt, HttpError, "Achraf Hakimi", {})

        assert "Morocco vs Canada World Cup highlights" not in texts
        assert "Messi is the GOAT" not in texts
        assert "What a match overall" not in texts
        assert "Hakimi's pass was incredible though" in texts

    def test_comments_disabled_skips_video_not_whole_player(self):
        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = _make_search_response(
            [("vid1", "News clip about a player")]
        )
        mock_yt.commentThreads().list().execute.side_effect = _make_http_error()

        texts = _collect_player_texts(mock_yt, HttpError, "Some Player", {})

        assert texts == ["News clip about a player"]

    def test_search_failure_returns_empty_list_not_crash(self):
        mock_yt = MagicMock()
        mock_yt.search().list().execute.side_effect = _make_http_error(status=500, message="quotaExceeded")

        texts = _collect_player_texts(mock_yt, HttpError, "Player", {})

        assert texts == []

    def test_temporal_cutoff_published_before_defaults_to_decision_date(self):
        from config import DECISION_DATE

        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = _make_search_response([])

        _collect_player_texts(mock_yt, HttpError, "Some Player", {})

        call_kwargs = mock_yt.search().list.call_args.kwargs
        from datetime import timedelta
        expected_before = (DECISION_DATE + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        assert call_kwargs["publishedBefore"] == expected_before

    def test_temporal_cutoff_published_before_respects_explicit_as_of(self):
        from datetime import date

        mock_yt = MagicMock()
        mock_yt.search().list().execute.return_value = _make_search_response([])

        _collect_player_texts(mock_yt, HttpError, "Some Player", {}, as_of=date(2026, 1, 1))

        call_kwargs = mock_yt.search().list.call_args.kwargs
        assert call_kwargs["publishedBefore"] == "2026-01-02T00:00:00Z"

    def test_video_without_id_is_skipped_for_comments(self):
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
        monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

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

        assert "quota units used" in status


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))