import sys
import os
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ingestion.loaders.arctic_shift_collector import (
    _search_posts,
    _search_comments,
    _collect_player_texts,
    _is_bot_or_removed,
    _mentions_player,
    load_arctic_shift_bronze,
)


def _make_response(json_data, status_ok=True):
    mock_resp = MagicMock()
    if status_ok:
        mock_resp.raise_for_status = MagicMock()
    else:
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500")
    mock_resp.json.return_value = json_data
    return mock_resp


class TestSearchPosts:

    def test_parses_real_verified_response_shape(self):
        real_shaped_response = {
            "data": [
                {"title": "Hakimi scores again", "selftext": "", "id": "abc123", "num_comments": 5},
                {"title": "Morocco squad news", "selftext": "Some discussion text", "id": "def456", "num_comments": 0},
            ]
        }
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   return_value=_make_response(real_shaped_response)):
            posts = _search_posts("soccer", "Hakimi")

        assert len(posts) == 2
        assert posts[0]["title"] == "Hakimi scores again"
        assert posts[1]["selftext"] == "Some discussion text"

    def test_empty_data_returns_empty_list(self):
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   return_value=_make_response({"data": []})):
            posts = _search_posts("soccer", "Completely Obscure Player")
        assert posts == []

    def test_network_failure_returns_empty_list_not_crash(self):
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   side_effect=requests.ConnectionError("no route")):
            posts = _search_posts("soccer", "Player")
        assert posts == []

    def test_http_error_returns_empty_list(self):
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   return_value=_make_response({}, status_ok=False)):
            posts = _search_posts("soccer", "Player")
        assert posts == []

    def test_no_auth_headers_or_params_sent(self):
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   return_value=_make_response({"data": []})) as mock_get:
            _search_posts("soccer", "Player")

        call_kwargs = mock_get.call_args.kwargs
        assert "headers" not in call_kwargs or not call_kwargs.get("headers")
        assert "apiKey" not in call_kwargs.get("params", {})
        assert "client_id" not in call_kwargs.get("params", {})

    def test_before_param_passed_through_when_given(self):
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   return_value=_make_response({"data": []})) as mock_get:
            _search_posts("soccer", "Player", before_ts=1780000000)

        assert mock_get.call_args.kwargs["params"]["before"] == 1780000000

    def test_no_before_param_when_not_given(self):
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   return_value=_make_response({"data": []})) as mock_get:
            _search_posts("soccer", "Player")

        assert "before" not in mock_get.call_args.kwargs["params"]


class TestCollectPlayerTextsTemporalCutoff:

    def test_before_ts_reaches_search_posts_and_defaults_to_decision_date(self):
        from config import DECISION_DATE
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td

        expected_before_ts = int(
            (_dt.combine(DECISION_DATE, _dt.min.time(), tzinfo=_tz.utc) + _td(days=1)).timestamp()
        )

        with patch("ingestion.loaders.arctic_shift_collector._search_posts", return_value=[]) as mock_posts:
            _collect_player_texts("Some Player")

        for call in mock_posts.call_args_list:
            assert call.kwargs.get("before_ts") == expected_before_ts

    def test_before_ts_respects_explicit_as_of(self):
        from datetime import date, datetime as _dt, timezone as _tz, timedelta as _td

        as_of = date(2026, 1, 1)
        expected_before_ts = int(
            (_dt.combine(as_of, _dt.min.time(), tzinfo=_tz.utc) + _td(days=1)).timestamp()
        )

        with patch("ingestion.loaders.arctic_shift_collector._search_posts", return_value=[]) as mock_posts:
            _collect_player_texts("Some Player", as_of=as_of)

        for call in mock_posts.call_args_list:
            assert call.kwargs.get("before_ts") == expected_before_ts


class TestIsBotOrRemoved:

    def test_detects_automoderator_author(self):
        assert _is_bot_or_removed("some text", "AutoModerator") is True
        assert _is_bot_or_removed("some text", "automoderator") is True

    def test_detects_removal_message_text(self):
        real_bot_text = "Your post has been removed under the Submission Guidelines: Shitposting"
        assert _is_bot_or_removed(real_bot_text, None) is True

    def test_real_comment_not_flagged(self):
        assert _is_bot_or_removed("Hakimi was amazing today", "some_real_user") is False


class TestMentionsPlayer:

    def test_real_irrelevant_comment_is_filtered_for_hakimi(self):
        messi_comment = "Messi today against England: 2 assists 1 shot 94 touches 9 successful dribbles"
        assert _mentions_player(messi_comment, "Achraf Hakimi") is False

    def test_real_irrelevant_comment_is_filtered_for_en_nesyri(self):
        assert _mentions_player("Didn't age well", "Youssef En-Nesyri") is False
        assert _mentions_player(
            "So you have 0 argument, you proved it already, just handwaving",
            "Youssef En-Nesyri",
        ) is False

    def test_full_name_mention_is_kept(self):
        assert _mentions_player("Achraf Hakimi was incredible today", "Achraf Hakimi") is True

    def test_surname_only_mention_is_kept(self):
        assert _mentions_player("Hakimi was incredible today, best defender", "Achraf Hakimi") is True

    def test_case_insensitive(self):
        assert _mentions_player("HAKIMI SCORED", "Achraf Hakimi") is True


class TestSearchComments:

    def test_parses_comment_bodies(self):
        response = {"data": [{"body": "Great player!", "author": "fan1"}, {"body": "Amazing goal", "author": "fan2"}]}
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   return_value=_make_response(response)):
            comments = _search_comments("abc123")
        assert len(comments) == 2

    def test_failure_returns_empty_list(self):
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   side_effect=requests.ConnectionError("timeout")):
            comments = _search_comments("abc123")
        assert comments == []

    def test_requests_author_field_for_bot_filtering(self):
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   return_value=_make_response({"data": []})) as mock_get:
            _search_comments("abc123")

        call_kwargs = mock_get.call_args.kwargs
        assert "author" in call_kwargs["params"]["fields"]


class TestCollectPlayerTexts:

    def test_collects_across_all_subreddits(self):
        def fake_get(url, params, timeout):
            if "posts/search" in url:
                return _make_response({"data": [
                    {"title": f"Post about {params['query']} in {params['subreddit']}",
                     "selftext": "", "id": f"id_{params['subreddit']}", "num_comments": 0},
                ]})
            return _make_response({"data": []})

        with patch("ingestion.loaders.arctic_shift_collector.requests.get", side_effect=fake_get), \
             patch("ingestion.loaders.arctic_shift_collector.time.sleep"):
            texts = _collect_player_texts("Achraf Hakimi")

        from ingestion.loaders.arctic_shift_collector import SUBREDDITS
        assert len(texts) == len(SUBREDDITS)

    def test_skips_deleted_and_removed_content(self):
        def fake_get(url, params, timeout):
            if "posts/search" in url:
                return _make_response({"data": [
                    {"title": "Some Player scores winning goal", "selftext": "[deleted]", "id": "x", "num_comments": 1},
                ]})
            return _make_response({"data": [
                {"body": "[removed]", "author": "someone"},
                {"body": "Some Player was amazing today", "author": "real_fan"},
            ]})

        with patch("ingestion.loaders.arctic_shift_collector.requests.get", side_effect=fake_get), \
             patch("ingestion.loaders.arctic_shift_collector.time.sleep"):
            texts = _collect_player_texts("Some Player")

        assert "[deleted]" not in texts
        assert "[removed]" not in texts
        assert "Some Player scores winning goal" in texts
        assert "Some Player was amazing today" in texts

    def test_title_not_mentioning_player_is_filtered(self):
        def fake_get(url, params, timeout):
            if "posts/search" in url:
                return _make_response({"data": [
                    {"title": "Messi wins another Ballon d'Or", "selftext": "", "id": "x", "num_comments": 0},
                ]})
            return _make_response({"data": []})

        with patch("ingestion.loaders.arctic_shift_collector.requests.get", side_effect=fake_get), \
             patch("ingestion.loaders.arctic_shift_collector.time.sleep"):
            texts = _collect_player_texts("Achraf Hakimi")

        assert texts == []

    def test_integration_filters_real_world_noise_pattern(self):
        def fake_get(url, params, timeout):
            if "posts/search" in url:
                return _make_response({"data": [
                    {"title": "World Cup Megathread: France vs Morocco, Hakimi ready to start",
                     "selftext": "", "id": "megathread1", "num_comments": 2},
                ]})
            return _make_response({"data": [
                {"body": "Your post has been removed under the Submission Guidelines: Shitposting", "author": "AutoModerator"},
                {"body": "Messi today against England: 2 assists 1 shot 94 touches", "author": "some_fan"},
            ]})

        with patch("ingestion.loaders.arctic_shift_collector.requests.get", side_effect=fake_get), \
             patch("ingestion.loaders.arctic_shift_collector.time.sleep"):
            texts = _collect_player_texts("Achraf Hakimi")

        assert any("Hakimi ready to start" in t for t in texts)
        assert not any("Submission Guidelines" in t for t in texts)
        assert not any("Messi" in t for t in texts)

    def test_no_results_anywhere_returns_empty_list(self):
        with patch("ingestion.loaders.arctic_shift_collector.requests.get",
                   return_value=_make_response({"data": []})), \
             patch("ingestion.loaders.arctic_shift_collector.time.sleep"):
            texts = _collect_player_texts("Nobody Knows This Player")
        assert texts == []


class TestLoadArcticShiftBronze:

    def test_missing_seed_columns_raises(self):
        bad_seed = pd.DataFrame({"player_id": ["P001"]})
        with pytest.raises(ValueError):
            load_arctic_shift_bronze(bad_seed, run_date="TEST")

    def test_writes_to_same_folder_as_retired_reddit_collector(self):
        seed = pd.DataFrame({"player_id": ["P001"], "player_name": ["Player A"]})

        with patch("ingestion.loaders.arctic_shift_collector.upload_json", return_value=True) as mock_upload, \
             patch("ingestion.loaders.arctic_shift_collector._collect_player_texts", return_value=[]):
            load_arctic_shift_bronze(seed, run_date="TEST")

        args, kwargs = mock_upload.call_args
        object_name = args[1] if len(args) > 1 else kwargs.get("object_name")
        assert object_name == "reddit/players_reddit_TEST.json"

    def test_uses_same_field_name_as_retired_reddit_collector(self):
        seed = pd.DataFrame({"player_id": ["P001"], "player_name": ["Popular Player"]})

        with patch("ingestion.loaders.arctic_shift_collector.upload_json", return_value=True) as mock_upload, \
             patch("ingestion.loaders.arctic_shift_collector._collect_player_texts", return_value=["some text"]):
            load_arctic_shift_bronze(seed, run_date="TEST")

        records = mock_upload.call_args[0][2]
        assert records[0]["reddit_texts"] == ["some text"]

    def test_full_flow_assembles_per_player_results(self):
        seed = pd.DataFrame({
            "player_id": ["P001", "P002"],
            "player_name": ["Popular Player", "Obscure Player"],
        })

        def fake_collect(player_name, as_of=None):
            return ["some discussion"] if player_name == "Popular Player" else []

        with patch("ingestion.loaders.arctic_shift_collector.upload_json", return_value=True), \
             patch("ingestion.loaders.arctic_shift_collector._collect_player_texts", side_effect=fake_collect):
            status = load_arctic_shift_bronze(seed, run_date="TEST")

        assert "1/2" in status


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))