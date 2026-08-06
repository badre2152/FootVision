import sys
import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ingestion.loaders.reddit_collector import (
    _collect_player_texts,
    _build_reddit_client,
    load_reddit_bronze,
)


def _make_mock_submission(title, selftext, comment_bodies):
    submission = MagicMock()
    submission.title = title
    submission.selftext = selftext
    submission.comments = MagicMock()
    submission.comments.replace_more = MagicMock()
    submission.comments.list.return_value = [
        MagicMock(body=body) for body in comment_bodies
    ]
    return submission


class TestCollectPlayerTexts:

    def test_extracts_titles_selftext_and_comments(self):
        mock_reddit = MagicMock()
        mock_submission = _make_mock_submission(
            title="Hakimi is having a great season",
            selftext="Discuss his performances here",
            comment_bodies=["He's amazing", "Best full-back in the world"],
        )
        mock_reddit.subreddit.return_value.search.return_value = [mock_submission]

        texts = _collect_player_texts(mock_reddit, "Achraf Hakimi")

        assert "Hakimi is having a great season" in texts
        assert "Discuss his performances here" in texts
        assert "He's amazing" in texts
        assert "Best full-back in the world" in texts
        assert len(texts) == 4

    def test_skips_empty_title_and_selftext(self):
        mock_reddit = MagicMock()
        mock_submission = _make_mock_submission(title="", selftext="", comment_bodies=["only comment"])
        mock_reddit.subreddit.return_value.search.return_value = [mock_submission]

        texts = _collect_player_texts(mock_reddit, "Some Player")

        assert texts == ["only comment"]

    def test_no_results_returns_empty_list(self):
        mock_reddit = MagicMock()
        mock_reddit.subreddit.return_value.search.return_value = []

        texts = _collect_player_texts(mock_reddit, "Completely Obscure Player")

        assert texts == []

    def test_comment_limit_is_respected(self):
        from ingestion.loaders.reddit_collector import MAX_COMMENTS_PER_SUBMISSION
        mock_reddit = MagicMock()
        many_comments = [f"comment {i}" for i in range(MAX_COMMENTS_PER_SUBMISSION + 20)]
        mock_submission = _make_mock_submission(title="t", selftext="", comment_bodies=many_comments)
        mock_reddit.subreddit.return_value.search.return_value = [mock_submission]

        texts = _collect_player_texts(mock_reddit, "Player")

        assert len(texts) == 1 + MAX_COMMENTS_PER_SUBMISSION


class TestBuildRedditClient:

    def test_missing_credentials_returns_none(self, monkeypatch):
        monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
        monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)

        import praw
        result = _build_reddit_client(praw)
        assert result is None

    def test_partial_credentials_still_returns_none(self, monkeypatch):
        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)

        import praw
        result = _build_reddit_client(praw)
        assert result is None

    def test_full_credentials_builds_client(self, monkeypatch):
        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "def")
        monkeypatch.setenv("REDDIT_USER_AGENT", "football-atlas/1.0 by u/test")

        import praw
        result = _build_reddit_client(praw)
        assert result is not None
        assert result.read_only is True


class TestLoadRedditBronze:

    def test_missing_credentials_degrades_gracefully(self, monkeypatch):
        monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
        monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)

        seed = pd.DataFrame({
            "player_id": ["P001", "P002"],
            "player_name": ["Player A", "Player B"],
        })

        with patch("ingestion.loaders.reddit_collector.upload_json", return_value=True) as mock_upload:
            status = load_reddit_bronze(seed, run_date="TEST")

        assert "credentials missing" in status
        mock_upload.assert_called_once()

    def test_missing_seed_columns_raises(self):
        bad_seed = pd.DataFrame({"player_id": ["P001"]})
        with pytest.raises(ValueError):
            load_reddit_bronze(bad_seed, run_date="TEST")

    def test_full_flow_with_mocked_reddit_client(self, monkeypatch):
        monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "def")
        monkeypatch.setenv("REDDIT_USER_AGENT", "test-agent")

        seed = pd.DataFrame({
            "player_id": ["P001", "P002"],
            "player_name": ["Popular Player", "Obscure Player"],
        })

        mock_submission = _make_mock_submission(
            title="Popular Player scored again", selftext="", comment_bodies=["great goal"],
        )

        def fake_search(query, limit, time_filter):
            if query == "Popular Player":
                return [mock_submission]
            return []

        with patch("ingestion.loaders.reddit_collector.upload_json", return_value=True), \
             patch("ingestion.loaders.reddit_collector._try_import_praw") as mock_import:
            fake_praw = MagicMock()
            fake_reddit_instance = MagicMock()
            fake_reddit_instance.subreddit.return_value.search.side_effect = fake_search
            fake_praw.Reddit.return_value = fake_reddit_instance
            mock_import.return_value = fake_praw

            status = load_reddit_bronze(seed, run_date="TEST")

        assert "1/2" in status


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))