import sys
import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ingestion.loaders.newsapi_collector import (
    _collect_player_articles,
    load_newsapi_bronze,
)


def _make_ok_response(articles):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "status": "ok",
        "totalResults": len(articles),
        "articles": [
            {"title": title, "description": desc} for title, desc in articles
        ],
    }
    return mock_resp


def _make_error_response(message="rateLimited"):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"status": "error", "code": "rateLimited", "message": message}
    return mock_resp


class TestCollectPlayerArticles:

    def test_extracts_titles_and_descriptions(self):
        mock_resp = _make_ok_response([
            ("Hakimi scores again", "Hakimi was the standout performer today"),
            ("Morocco squad announced, Hakimi included", None),
        ])
        with patch("ingestion.loaders.newsapi_collector.requests.get", return_value=mock_resp):
            texts = _collect_player_articles("fake-key", "Achraf Hakimi")

        assert "Hakimi scores again" in texts
        assert "Hakimi was the standout performer today" in texts
        assert "Morocco squad announced, Hakimi included" in texts
        assert len(texts) == 3

    def test_filters_articles_not_actually_mentioning_player(self):
        mock_resp = _make_ok_response([
            ("How to watch France vs Morocco: Free Streams, TV Channels", "Kick-off time for the quarterfinal"),
            ("Hakimi named Man of the Match", "Hakimi delivered a standout performance for PSG"),
        ])
        with patch("ingestion.loaders.newsapi_collector.requests.get", return_value=mock_resp):
            texts = _collect_player_articles("fake-key", "Achraf Hakimi")

        assert not any("How to watch" in t for t in texts)
        assert not any("Kick-off time" in t for t in texts)
        assert "Hakimi named Man of the Match" in texts
        assert "Hakimi delivered a standout performance for PSG" in texts

    def test_api_error_status_returns_empty_list(self):
        with patch("ingestion.loaders.newsapi_collector.requests.get", return_value=_make_error_response()):
            texts = _collect_player_articles("bad-key", "Some Player")
        assert texts == []

    def test_network_failure_returns_empty_list_not_crash(self):
        with patch("ingestion.loaders.newsapi_collector.requests.get", side_effect=requests.ConnectionError("no route")):
            texts = _collect_player_articles("fake-key", "Some Player")
        assert texts == []

    def test_http_error_status_returns_empty_list(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")
        with patch("ingestion.loaders.newsapi_collector.requests.get", return_value=mock_resp):
            texts = _collect_player_articles("fake-key", "Some Player")
        assert texts == []

    def test_no_articles_returns_empty_list(self):
        mock_resp = _make_ok_response([])
        with patch("ingestion.loaders.newsapi_collector.requests.get", return_value=mock_resp):
            texts = _collect_player_articles("fake-key", "Obscure Player")
        assert texts == []

    def test_query_includes_player_name_and_football_context(self):
        mock_resp = _make_ok_response([])
        with patch("ingestion.loaders.newsapi_collector.requests.get", return_value=mock_resp) as mock_get:
            _collect_player_articles("my-key", "Youssef En-Nesyri")

        call_kwargs = mock_get.call_args.kwargs
        assert "En-Nesyri" in call_kwargs["params"]["q"]
        assert "football" in call_kwargs["params"]["q"]
        assert call_kwargs["params"]["apiKey"] == "my-key"

    def test_temporal_cutoff_to_param_defaults_to_decision_date(self):
        from config import DECISION_DATE

        mock_resp = _make_ok_response([])
        with patch("ingestion.loaders.newsapi_collector.requests.get", return_value=mock_resp) as mock_get:
            _collect_player_articles("my-key", "Youssef En-Nesyri")

        assert mock_get.call_args.kwargs["params"]["to"] == DECISION_DATE.isoformat()

    def test_temporal_cutoff_to_param_respects_explicit_as_of(self):
        from datetime import date

        mock_resp = _make_ok_response([])
        with patch("ingestion.loaders.newsapi_collector.requests.get", return_value=mock_resp) as mock_get:
            _collect_player_articles("my-key", "Youssef En-Nesyri", as_of=date(2026, 1, 1))

        assert mock_get.call_args.kwargs["params"]["to"] == "2026-01-01"


class TestLoadNewsapiBronze:

    def test_missing_api_key_degrades_gracefully(self, monkeypatch):
        monkeypatch.delenv("NEWSAPI_KEY", raising=False)
        seed = pd.DataFrame({"player_id": ["P001"], "player_name": ["Player A"]})

        with patch("ingestion.loaders.newsapi_collector.upload_json", return_value=True) as mock_upload:
            status = load_newsapi_bronze(seed, run_date="TEST")

        assert "API key missing" in status
        mock_upload.assert_called_once()

    def test_missing_seed_columns_raises(self):
        bad_seed = pd.DataFrame({"player_id": ["P001"]})
        with pytest.raises(ValueError):
            load_newsapi_bronze(bad_seed, run_date="TEST")

    def test_full_flow_assembles_per_player_results(self, monkeypatch):
        monkeypatch.setenv("NEWSAPI_KEY", "fake-key")
        seed = pd.DataFrame({
            "player_id": ["P001", "P002"],
            "player_name": ["Popular Player", "Obscure Player"],
        })

        def fake_get(url, params, timeout):
            if "Popular" in params["q"]:
                return _make_ok_response([("Popular Player wins award", "Great season")])
            return _make_ok_response([])

        with patch("ingestion.loaders.newsapi_collector.upload_json", return_value=True), \
             patch("ingestion.loaders.newsapi_collector.requests.get", side_effect=fake_get):
            status = load_newsapi_bronze(seed, run_date="TEST")

        assert "1/2" in status

    def test_warns_when_over_100_players(self, monkeypatch, caplog):
        monkeypatch.setenv("NEWSAPI_KEY", "fake-key")
        seed = pd.DataFrame({
            "player_id": [f"P{i:03d}" for i in range(101)],
            "player_name": [f"Player {i}" for i in range(101)],
        })

        with patch("ingestion.loaders.newsapi_collector.upload_json", return_value=True), \
             patch("ingestion.loaders.newsapi_collector.requests.get", return_value=_make_ok_response([])), \
             caplog.at_level("WARNING"):
            load_newsapi_bronze(seed, run_date="TEST")

        assert any("100 requests/day" in r.message for r in caplog.records)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))