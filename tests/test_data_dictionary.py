import os
import sys
import re
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.generate_data_dictionary as gdd


class TestClassifySource:
    def test_exact_match_takes_priority(self):
        source, confident = gdd.classify_source("player_id")
        assert confident is True
        assert "merge key" in source.lower()

    def test_longest_prefix_wins_gk_before_generic_fbref(self):
        source, confident = gdd.classify_source("fbref_gk_saves_per90")
        assert confident is True
        assert "goalkeeper" in source.lower()

    def test_generic_fbref_prefix_when_not_gk(self):
        source, confident = gdd.classify_source("fbref_goals_per90")
        assert confident is True
        assert "fbref" in source.lower()
        assert "goalkeeper" not in source.lower()

    def test_norm_suffix_classified_as_gold_pipeline_when_no_other_match(self):
        source, confident = gdd.classify_source("some_unknown_col_norm")
        assert confident is True
        assert "gold pipeline" in source.lower()

    def test_unknown_column_flagged_not_confident(self):
        source, confident = gdd.classify_source("totally_unrecognized_xyz")
        assert confident is False
        assert "unclear" in source.lower()

    def test_all_prefix_map_entries_are_distinguishable(self):
        prefixes = [p for p, _ in gdd.PREFIX_SOURCE_MAP]
        for i, p in enumerate(prefixes):
            for other in prefixes[i + 1:]:
                if other.startswith(p):
                    pytest.fail(f"'{p}' (listed before '{other}') would shadow it")


class TestClassifyRawOrDerived:
    def test_norm_suffix_is_derived(self):
        assert "derived" in gdd.classify_raw_or_derived("Gls_per90_norm")

    def test_gold_derived_column_is_derived(self):
        assert "derived" in gdd.classify_raw_or_derived("DSSScore")

    def test_composite_weight_key_is_derived(self):
        for col in gdd.config.COMPOSITE_WEIGHTS:
            assert "derived" in gdd.classify_raw_or_derived(col), col

    def test_plain_raw_column_is_raw(self):
        assert gdd.classify_raw_or_derived("fbref_goals_per90") == "raw"


class TestHumanizeColumnName:
    def test_underscores_become_spaces(self):
        assert gdd.humanize_column_name("fbref_goals_per90") == "Fbref goals per90"

    def test_camel_case_gets_split(self):
        result = gdd.humanize_column_name("PlayerDNA")
        assert " " in result


class TestFindUsedIn:
    def test_finds_exact_column_reference(self):
        index = {"foo.py": 'df["Gls_per90"] = 1'}
        result = gdd.find_used_in("Gls_per90", index)
        assert "foo.py" in result

    def test_does_not_false_positive_on_longer_column_containing_it(self):
        index = {"foo.py": 'df["Gls_per90_norm"] = 1'}
        result = gdd.find_used_in("Gls_per90", index)
        assert "foo.py" not in result
        assert "not referenced" in result

    def test_no_match_returns_not_referenced_message(self):
        index = {"foo.py": "print('hello')"}
        result = gdd.find_used_in("some_column", index)
        assert "not referenced" in result

    def test_truncates_and_counts_extra_files(self):
        index = {f"file{i}.py": "col_x = 1" for i in range(10)}
        result = gdd.find_used_in("col_x", index, max_files=3)
        assert "+7 more" in result
        assert result.count("`file") == 3


class TestEndToEnd:
    @pytest.fixture
    def fake_project(self, tmp_path, monkeypatch):
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        docs_dir = tmp_path / "docs"

        df = pd.DataFrame({
            "player_id": [1, 2, 3],
            "fbref_goals_per90": [0.5, None, 0.3],
            "Gls_per90_norm": [0.1, 0.2, None],
            "DSSScore": [0.9, 0.8, 0.7],
            "totally_unrecognized_xyz": [1, 2, 3],
        })
        df.to_csv(gold_dir / "players_scored_20260101_000000.csv", index=False)

        monkeypatch.setattr(gdd, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(gdd, "OUTPUT_PATH", docs_dir / "data_dictionary.md")
        return tmp_path, docs_dir / "data_dictionary.md"

    def test_main_writes_output_file(self, fake_project):
        tmp_path, output_path = fake_project
        gdd.main()
        assert output_path.exists()

    def test_output_contains_all_columns(self, fake_project):
        tmp_path, output_path = fake_project
        gdd.main()
        text = output_path.read_text(encoding="utf-8")
        for col in ["player_id", "fbref_goals_per90", "Gls_per90_norm", "DSSScore", "totally_unrecognized_xyz"]:
            assert f"`{col}`" in text

    def test_output_reports_correct_coverage(self, fake_project):
        tmp_path, output_path = fake_project
        gdd.main()
        text = output_path.read_text(encoding="utf-8")
        assert "2/3 (66.7%)" in text

    def test_unrecognized_column_flagged_in_output(self, fake_project):
        tmp_path, output_path = fake_project
        gdd.main()
        text = output_path.read_text(encoding="utf-8")
        row_match = re.search(r"\| `totally_unrecognized_xyz`.*\|", text)
        assert row_match is not None
        assert "unclear" in row_match.group(0) or "manual review" in row_match.group(0)

    def test_raises_clear_error_when_no_gold_files(self, tmp_path, monkeypatch):
        empty_gold = tmp_path / "gold"
        empty_gold.mkdir()
        monkeypatch.setattr(gdd, "PROJECT_ROOT", tmp_path)
        with pytest.raises(FileNotFoundError, match="run_gold.py"):
            gdd.load_latest_players_scored()