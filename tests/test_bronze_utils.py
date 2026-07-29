# tests/test_bronze_utils.py
# Regression tests for ingestion/loaders/_bronze_utils.py, with special
# focus on the critical Arabic-script mentions_player() bug
# (found 2026-07-27, reapplied 2026-07-29 after it did not survive into
# the delivered archive — see project handoff notes).

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ingestion.loaders._bronze_utils import mentions_player


class TestMentionsPlayerLatin:
    """Baseline behavior must not regress while fixing the Arabic bug."""

    def test_full_name_match(self):
        assert mentions_player("Achraf Hakimi was incredible today", "Achraf Hakimi") is True

    def test_surname_only_match(self):
        assert mentions_player("Hakimi was incredible today, best defender", "Achraf Hakimi") is True

    def test_case_insensitive(self):
        assert mentions_player("HAKIMI SCORED", "Achraf Hakimi") is True

    def test_no_match_wrong_player(self):
        messi_comment = "Messi is still the GOAT, nobody comes close this generation"
        assert mentions_player(messi_comment, "Achraf Hakimi") is False

    def test_no_match_unrelated_text(self):
        assert mentions_player("Didn't age well", "Youssef En-Nesyri") is False

    def test_empty_text(self):
        assert mentions_player("", "Achraf Hakimi") is False

    def test_none_text(self):
        assert mentions_player(None, "Achraf Hakimi") is False


class TestMentionsPlayerArabicCriticalBug:
    """
    THE critical regression test. Before the fix, every one of these
    returned False — meaning 100% of Arabic-script content was silently
    discarded for every player, systematically stripping out the more
    critical/negative Darija sentiment and inflating SentimentScore.
    """

    def test_darija_arabic_script_text_is_kept(self):
        # Real-style Darija comment about Hakimi, written in Arabic script.
        # Contains ZERO Latin characters — the old Latin-only check
        # returned False for this 100% of the time.
        text = "حكيمي لعب مزيان بزاف اليوم، كان الأحسن فالدفاع"
        assert mentions_player(text, "Achraf Hakimi") is True

    def test_darija_negative_comment_is_kept(self):
        # This is exactly the kind of critical/negative Darija content
        # the bug was silently erasing from every player's sentiment pool.
        text = "بلعماري ماشي مستوى المنتخب، خاصو يقعد فالبنش"
        assert mentions_player(text, "Youssef Belammari") is True

    def test_modern_standard_arabic_news_is_kept(self):
        text = "أعلن الاتحاد المغربي لكرة القدم عن قائمة اللاعبين المرشحين"
        assert mentions_player(text, "Achraf Hakimi") is True

    def test_majority_arabic_with_stray_latin_word_still_kept(self):
        # Arabic-majority text that happens to contain one Latin word
        # (common in real Darija social media — mixed script comments).
        text = "حكيمي كان top اليوم، دافع بزاف مزيان"
        assert mentions_player(text, "Achraf Hakimi") is True


class TestMentionsPlayerArabicRefinement:
    """
    The refinement added on top of the original fix: an English article
    that merely quotes one Arabic word/name should NOT get a free pass —
    it still has to pass the normal Latin-name relevance check.
    """

    def test_english_article_with_one_arabic_word_not_auto_kept(self):
        # Mostly Latin script, one incidental Arabic word (e.g. a quoted
        # nickname) — should NOT bypass the relevance check, and since it
        # doesn't mention the target player in Latin script either, it
        # must be rejected.
        text = "The match preview covers several players including a note on مرحبا as a greeting used by fans."
        assert mentions_player(text, "Achraf Hakimi") is False

    def test_english_article_with_one_arabic_word_but_real_match_kept(self):
        text = "Hakimi is expected to start, fans chanted مرحبا as he walked out."
        assert mentions_player(text, "Achraf Hakimi") is True
