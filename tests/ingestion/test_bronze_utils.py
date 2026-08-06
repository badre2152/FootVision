import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from ingestion.loaders._bronze_utils import mentions_player


class TestMentionsPlayerLatin:

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

    def test_darija_arabic_script_text_is_kept(self):
        text = "حكيمي لعب مزيان بزاف اليوم، كان الأحسن فالدفاع"
        assert mentions_player(text, "Achraf Hakimi") is True

    def test_darija_negative_comment_is_kept(self):
        text = "بلعماري ماشي مستوى المنتخب، خاصو يقعد فالبنش"
        assert mentions_player(text, "Youssef Belammari") is True

    def test_modern_standard_arabic_news_is_kept(self):
        text = "أعلن الاتحاد المغربي لكرة القدم عن قائمة اللاعبين المرشحين"
        assert mentions_player(text, "Achraf Hakimi") is True

    def test_majority_arabic_with_stray_latin_word_still_kept(self):
        text = "حكيمي كان top اليوم، دافع بزاف مزيان"
        assert mentions_player(text, "Achraf Hakimi") is True


class TestMentionsPlayerArabicRefinement:

    def test_english_article_with_one_arabic_word_not_auto_kept(self):
        text = "The match preview covers several players including a note on مرحبا as a greeting used by fans."
        assert mentions_player(text, "Achraf Hakimi") is False

    def test_english_article_with_one_arabic_word_but_real_match_kept(self):
        text = "Hakimi is expected to start, fans chanted مرحبا as he walked out."
        assert mentions_player(text, "Achraf Hakimi") is True