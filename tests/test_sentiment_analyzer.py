# tests/test_sentiment_analyzer.py
#
# Tests analytics/nlp/sentiment_analyzer.py.
#
# Language detection and label normalization are tested for REAL (no
# mocking needed — langdetect runs offline, normalize_label is pure
# string/math logic). The actual transformers model loading/inference is
# MOCKED throughout — see the module's own docstring for why (no
# huggingface.co access, no local GPU/torch runtime in this sandbox).

import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.nlp.sentiment_analyzer import (
    detect_language,
    is_likely_darija,
    normalize_label,
    score_text,
    aggregate_player_sentiment,
    MODEL_IDS,
)


# ─────────────────────────────────────────────────────────────────────────────
# detect_language — REAL, runs offline (langdetect, no models)
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectLanguage:

    def test_detects_french(self):
        assert detect_language("Hakimi est un excellent joueur de football") == "fr"

    def test_detects_english(self):
        assert detect_language("Hakimi is a great football player") == "en"

    def test_detects_arabic(self):
        assert detect_language("حكيمي لاعب كرة قدم ممتاز") == "ar"

    def test_short_text_returns_unknown(self):
        assert detect_language("ok") == "unknown"

    def test_empty_text_returns_unknown(self):
        assert detect_language("") == "unknown"
        assert detect_language(None) == "unknown"

    def test_never_raises_on_garbage_input(self):
        """Must degrade to 'unknown', never propagate an exception."""
        garbage_inputs = ["!!!???", "12345", "🙂🙂🙂", "a" * 5000]
        for text in garbage_inputs:
            result = detect_language(text)
            assert result in ("ar", "fr", "en", "unknown")


# ─────────────────────────────────────────────────────────────────────────────
# is_likely_darija — REAL, pure string logic
# ─────────────────────────────────────────────────────────────────────────────

class TestIsLikelyDarija:

    def test_detects_darija_keyword(self):
        assert is_likely_darija("هاد اللاعب بزاف زوين") is True

    def test_standard_arabic_without_keywords_returns_false(self):
        assert is_likely_darija("هذا اللاعب ممتاز جدا") is False

    def test_empty_string_returns_false(self):
        assert is_likely_darija("") is False


# ─────────────────────────────────────────────────────────────────────────────
# normalize_label — REAL, exhaustive across every model's label scheme
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeLabel:

    def test_star_ratings_map_linearly(self):
        assert normalize_label("1 star", 0.9) == pytest.approx(0.0)
        assert normalize_label("2 stars", 0.9) == pytest.approx(0.25)
        assert normalize_label("3 stars", 0.9) == pytest.approx(0.5)
        assert normalize_label("4 stars", 0.9) == pytest.approx(0.75)
        assert normalize_label("5 stars", 0.9) == pytest.approx(1.0)

    def test_star_rating_case_insensitive(self):
        assert normalize_label("5 STARS", 0.9) == pytest.approx(1.0)

    def test_positive_scales_with_confidence(self):
        low_conf  = normalize_label("positive", 0.6)
        high_conf = normalize_label("positive", 0.99)
        assert 0.5 < low_conf < high_conf <= 1.0

    def test_negative_scales_with_confidence(self):
        low_conf  = normalize_label("negative", 0.6)
        high_conf = normalize_label("negative", 0.99)
        assert 0.0 <= high_conf < low_conf < 0.5

    def test_neutral_is_always_midpoint(self):
        assert normalize_label("neutral", 0.5) == 0.5
        assert normalize_label("neutral", 0.99) == 0.5  # confidence doesn't matter for neutral

    def test_label_0_label_1_binary_scheme_with_warning(self, caplog):
        with caplog.at_level("WARNING"):
            neg = normalize_label("LABEL_0", 0.8)
            pos = normalize_label("LABEL_1", 0.8)
        assert neg < 0.5 < pos
        assert any("UNVERIFIED assumption" in r.message for r in caplog.records)

    def test_unrecognized_label_returns_none_and_warns(self, caplog):
        with caplog.at_level("WARNING"):
            result = normalize_label("some_totally_unknown_label", 0.8)
        assert result is None
        assert any("unrecognized label" in r.message for r in caplog.records)

    def test_never_returns_out_of_range_value(self):
        """Sanity sweep — every recognized label must stay within [0, 1]."""
        for label in ["1 star", "2 stars", "3 stars", "4 stars", "5 stars",
                      "positive", "negative", "neutral", "LABEL_0", "LABEL_1"]:
            for conf in [0.0, 0.5, 0.99, 1.0]:
                result = normalize_label(label, conf)
                if result is not None:
                    assert 0.0 <= result <= 1.0, f"{label}@{conf} -> {result} out of range"


# ─────────────────────────────────────────────────────────────────────────────
# score_text — model inference MOCKED (see module docstring)
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreText:

    def test_routes_french_text_to_french_model(self):
        with patch("analytics.nlp.sentiment_analyzer._get_model") as mock_get_model:
            mock_model = MagicMock(return_value=[{"label": "5 stars", "score": 0.9}])
            mock_get_model.return_value = mock_model

            result = score_text("Hakimi est un joueur exceptionnel et talentueux")

            mock_get_model.assert_called_once_with("french")
            assert result["normalized_score"] == pytest.approx(1.0)
            assert result["language"] == "fr"

    def test_routes_arabic_text_to_arabic_model_by_default(self):
        with patch("analytics.nlp.sentiment_analyzer._get_model") as mock_get_model:
            mock_model = MagicMock(return_value=[{"label": "positive", "score": 0.8}])
            mock_get_model.return_value = mock_model

            result = score_text("هذا اللاعب ممتاز جدا في كرة القدم")

            mock_get_model.assert_called_once_with("arabic")
            assert result["model_used"] == "arabic"

    def test_routes_darija_text_to_darija_model(self):
        with patch("analytics.nlp.sentiment_analyzer._get_model") as mock_get_model:
            mock_model = MagicMock(return_value=[{"label": "positive", "score": 0.8}])
            mock_get_model.return_value = mock_model

            result = score_text("هاد اللاعب بزاف زوين ديال كرة القدم")

            mock_get_model.assert_called_once_with("darija")
            assert result["model_used"] == "darija"

    def test_routes_english_to_multilingual_fallback(self):
        with patch("analytics.nlp.sentiment_analyzer._get_model") as mock_get_model:
            mock_model = MagicMock(return_value=[{"label": "positive", "score": 0.8}])
            mock_get_model.return_value = mock_model

            result = score_text("This player is having an amazing season")

            mock_get_model.assert_called_once_with("multilingual")

    def test_empty_text_short_circuits_without_calling_any_model(self):
        with patch("analytics.nlp.sentiment_analyzer._get_model") as mock_get_model:
            result = score_text("")
            mock_get_model.assert_not_called()
            assert result["normalized_score"] is None

    def test_model_failure_returns_none_score_not_crash(self):
        with patch("analytics.nlp.sentiment_analyzer._get_model") as mock_get_model:
            mock_get_model.side_effect = RuntimeError("model failed to load")
            result = score_text("This player is amazing")
            assert result["normalized_score"] is None


# ─────────────────────────────────────────────────────────────────────────────
# aggregate_player_sentiment
# ─────────────────────────────────────────────────────────────────────────────

class TestAggregatePlayerSentiment:

    def test_no_texts_returns_none_sentiment(self):
        result = aggregate_player_sentiment([])
        assert result["sentiment_raw"] is None
        assert result["text_count"] == 0

    def test_averages_across_multiple_texts(self):
        with patch("analytics.nlp.sentiment_analyzer.score_text") as mock_score:
            mock_score.side_effect = [
                {"normalized_score": 1.0, "language": "en", "model_used": "multilingual"},
                {"normalized_score": 0.0, "language": "en", "model_used": "multilingual"},
            ]
            result = aggregate_player_sentiment(["great player", "terrible player"])

        assert result["sentiment_raw"] == pytest.approx(0.5)
        assert result["text_count"] == 2
        assert result["scored_count"] == 2

    def test_unscoreable_texts_excluded_from_average_but_counted(self):
        with patch("analytics.nlp.sentiment_analyzer.score_text") as mock_score:
            mock_score.side_effect = [
                {"normalized_score": 1.0, "language": "en", "model_used": "multilingual"},
                {"normalized_score": None, "language": "unknown", "model_used": None},
            ]
            result = aggregate_player_sentiment(["great player", "???"])

        assert result["sentiment_raw"] == pytest.approx(1.0)  # only the scoreable one counted
        assert result["text_count"] == 2
        assert result["scored_count"] == 1

    def test_language_breakdown_tracks_all_texts(self):
        with patch("analytics.nlp.sentiment_analyzer.score_text") as mock_score:
            mock_score.side_effect = [
                {"normalized_score": 0.8, "language": "fr", "model_used": "french"},
                {"normalized_score": 0.6, "language": "ar", "model_used": "arabic"},
                {"normalized_score": 0.7, "language": "fr", "model_used": "french"},
            ]
            result = aggregate_player_sentiment(["a", "b", "c"])

        assert result["language_breakdown"] == {"fr": 2, "ar": 1}


class TestModelRegistry:

    def test_all_expected_models_registered(self):
        assert set(MODEL_IDS.keys()) == {"arabic", "darija", "french", "multilingual"}

    def test_all_model_ids_are_nonempty_strings(self):
        for key, model_id in MODEL_IDS.items():
            assert isinstance(model_id, str) and "/" in model_id, f"{key} -> {model_id!r} looks wrong"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
