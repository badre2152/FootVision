import json
import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import dashboard.data_loader as dl

_CACHED_LOADERS = [
    dl.load_latest_top26,
    dl.load_latest_players_scored,
    dl.load_latest_squad_summary,
    dl.load_latest_selection_audit,
    dl.load_latest_injury_predictor,
    dl.load_latest_chemistry_pairs,
    dl.load_all_players_scored_history,
]


@pytest.fixture
def fake_dirs(tmp_path, monkeypatch):
    for fn in _CACHED_LOADERS:
        fn.clear()
    gold_dir = tmp_path / "gold"
    bronze_dir = tmp_path / "bronze"
    gold_dir.mkdir()
    bronze_dir.mkdir()
    monkeypatch.setattr(dl, "GOLD_DIR", gold_dir)
    monkeypatch.setattr(dl, "BRONZE_DIR", bronze_dir)
    return gold_dir, bronze_dir


class TestLatestFile:

    def test_returns_none_for_empty_directory(self, fake_dirs):
        gold_dir, _ = fake_dirs
        assert dl._latest_file(gold_dir, "top26_", ".csv") is None

    def test_returns_none_for_nonexistent_directory(self, tmp_path):
        assert dl._latest_file(tmp_path / "does_not_exist", "top26_", ".csv") is None

    def test_picks_the_chronologically_latest_run(self, fake_dirs):
        gold_dir, _ = fake_dirs
        (gold_dir / "top26_20260713_100000.csv").write_text("a")
        (gold_dir / "top26_20260721_090000.csv").write_text("c")
        (gold_dir / "top26_20260717_120000.csv").write_text("b")

        latest = dl._latest_file(gold_dir, "top26_", ".csv")
        assert latest.name == "top26_20260721_090000.csv"

    def test_prefix_does_not_match_unrelated_files(self, fake_dirs):
        gold_dir, _ = fake_dirs
        (gold_dir / "players_scored_20260721_090000.csv").write_text("x")
        assert dl._latest_file(gold_dir, "top26_", ".csv") is None


class TestLoadLatestCsvFiles:

    def test_top26_returns_none_when_missing(self, fake_dirs):
        assert dl.load_latest_top26() is None

    def test_top26_loads_and_tags_source_file(self, fake_dirs):
        gold_dir, _ = fake_dirs
        csv_path = gold_dir / "top26_20260721_090000.csv"
        pd.DataFrame({
            "player_name": ["Bounou", "Hakimi"],
            "position": ["GK", "DEF"],
            "DSSScore": [0.52, 0.44],
        }).to_csv(csv_path, index=False)

        df = dl.load_latest_top26()
        assert df is not None
        assert len(df) == 2
        assert df.attrs["source_file"] == "top26_20260721_090000.csv"

    def test_players_scored_independent_of_top26(self, fake_dirs):
        gold_dir, _ = fake_dirs
        pd.DataFrame({"player_name": ["A"]}).to_csv(gold_dir / "top26_20260721_090000.csv", index=False)
        pd.DataFrame({"player_name": ["A", "B", "C"]}).to_csv(gold_dir / "players_scored_20260721_090000.csv", index=False)

        top26 = dl.load_latest_top26()
        players = dl.load_latest_players_scored()
        assert len(top26) == 1
        assert len(players) == 3


class TestLoadLatestSquadSummary:

    def test_returns_none_when_missing(self, fake_dirs):
        assert dl.load_latest_squad_summary() is None

    def test_loads_and_tags_source_file(self, fake_dirs):
        gold_dir, _ = fake_dirs
        summary = {"total_candidates": 49, "squad_size": 26, "position_counts": {"GK": 3}}
        (gold_dir / "squad_summary_20260721_090000.json").write_text(json.dumps(summary))

        loaded = dl.load_latest_squad_summary()
        assert loaded["total_candidates"] == 49
        assert loaded["_source_file"] == "squad_summary_20260721_090000.json"

    def test_older_schema_without_position_counts_still_loads(self, fake_dirs):
        gold_dir, _ = fake_dirs
        old_summary = {
            "generated_at": "2026-06-29T00:00:00",
            "total_candidates": 49,
            "squad_size": 26,
            "position_breakdown": {"GK": ["Bounou", "Al Harrar", "Tagnaouti"]},
            "flags": {"high_injury_risk": [], "limited_data": []},
            "top_player": {"name": "Bounou", "score": 0.58, "club": "Al-Hilal SFC"},
        }
        (gold_dir / "squad_summary_20260629_000000.json").write_text(json.dumps(old_summary))
        loaded = dl.load_latest_squad_summary()
        assert "position_counts" not in loaded
        assert loaded["position_breakdown"]["GK"] == ["Bounou", "Al Harrar", "Tagnaouti"]


class TestLoadLatestChemistryPairs:

    def test_returns_empty_list_not_none_when_missing(self, fake_dirs):
        result = dl.load_latest_chemistry_pairs()
        assert result == []
        assert result is not None

    def test_loads_real_pair_structure(self, fake_dirs):
        _, bronze_dir = fake_dirs
        chem_dir = bronze_dir / "statsbomb_chemistry"
        chem_dir.mkdir()
        pairs = [
            {"player_a_name": "Yassine Bounou", "player_b_name": "Nayef Aguerd",
             "shared_matches": 8, "passes_combined": 128, "chem_per90": 18.0},
        ]
        (chem_dir / "chemistry_pairs_20260721_090000.json").write_text(json.dumps(pairs))

        loaded = dl.load_latest_chemistry_pairs()
        assert len(loaded) == 1
        assert loaded[0]["player_a_name"] == "Yassine Bounou"


class TestAvailableComponentColumns:

    def test_old_run_only_core_columns(self):
        df = pd.DataFrame(columns=[
            "player_name", "PerformanceScore", "FormScore",
            "MentalScore", "AvailabilityScore", "InternationalScore",
        ])
        cols = dl.available_component_columns(df)
        assert cols == ["PerformanceScore", "FormScore", "MentalScore", "AvailabilityScore", "InternationalScore"]

    def test_new_run_includes_all_9(self):
        df = pd.DataFrame(columns=[
            "player_name", "PerformanceScore", "FormScore", "MentalScore",
            "AvailabilityScore", "InternationalScore", "ChemistryScore",
            "PressureIndex", "FlexibilityScore", "SentimentScore", "DSSScore_pct",
        ])
        cols = dl.available_component_columns(df)
        assert cols == [
            "PerformanceScore", "FormScore", "MentalScore", "AvailabilityScore",
            "InternationalScore", "ChemistryScore", "PressureIndex", "FlexibilityScore", "SentimentScore",
        ]
        assert "DSSScore_pct" not in cols, "DSSScore_pct is an output score, not a component — must not appear here"

    def test_partial_new_columns_only_returns_whats_present(self):
        df = pd.DataFrame(columns=[
            "player_name", "PerformanceScore", "ChemistryScore", "PressureIndex",
        ])
        cols = dl.available_component_columns(df)
        assert cols == ["PerformanceScore", "ChemistryScore", "PressureIndex"]

    def test_empty_dataframe_returns_empty_list(self):
        df = pd.DataFrame(columns=["player_name"])
        assert dl.available_component_columns(df) == []


def _write_players_scored(gold_dir, run_date_str, rows):
    path = gold_dir / f"players_scored_{run_date_str}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _write_squad_summary(gold_dir, run_date_str, decision_date_enforced):
    path = gold_dir / f"squad_summary_{run_date_str}.json"
    path.write_text(json.dumps({"decision_date_enforced": decision_date_enforced}))
    return path


class TestIsRunCutoffEnforced:

    def test_missing_squad_summary_fails_closed_to_false(self, fake_dirs):
        assert dl._is_run_cutoff_enforced("20260701_090000") is False

    def test_true_when_squad_summary_says_enforced(self, fake_dirs):
        gold_dir, _ = fake_dirs
        _write_squad_summary(gold_dir, "20260801_090000", decision_date_enforced=True)
        assert dl._is_run_cutoff_enforced("20260801_090000") is True

    def test_false_when_squad_summary_says_not_enforced(self, fake_dirs):
        gold_dir, _ = fake_dirs
        _write_squad_summary(gold_dir, "20260701_090000", decision_date_enforced=False)
        assert dl._is_run_cutoff_enforced("20260701_090000") is False

    def test_false_when_field_missing_from_summary(self, fake_dirs):
        gold_dir, _ = fake_dirs
        (gold_dir / "squad_summary_20260701_090000.json").write_text(json.dumps({"generated_at": "x"}))
        assert dl._is_run_cutoff_enforced("20260701_090000") is False

    def test_false_on_corrupt_json(self, fake_dirs):
        gold_dir, _ = fake_dirs
        (gold_dir / "squad_summary_20260701_090000.json").write_text("{not valid json")
        assert dl._is_run_cutoff_enforced("20260701_090000") is False


class TestLoadAllPlayersScoredHistory:

    def test_returns_empty_dataframe_when_no_snapshots(self, fake_dirs):
        history = dl.load_all_players_scored_history()
        assert isinstance(history, pd.DataFrame)
        assert history.empty

    def test_concatenates_every_snapshot_long_format(self, fake_dirs):
        gold_dir, _ = fake_dirs
        _write_players_scored(gold_dir, "20260701_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.5, "Rank": 1},
            {"player_id": "P2", "player_name": "B", "position": "DEF", "DSSScore": 0.4, "Rank": 2},
        ])
        _write_players_scored(gold_dir, "20260708_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.55, "Rank": 1},
            {"player_id": "P2", "player_name": "B", "position": "DEF", "DSSScore": 0.42, "Rank": 2},
        ])

        history = dl.load_all_players_scored_history()
        assert len(history) == 4
        assert history["run_date"].nunique() == 2
        assert list(history.columns[:3]) == ["run_date", "source_file", "is_pre_cutoff_fix"]
        assert history["run_date"].is_monotonic_increasing

    def test_is_pre_cutoff_fix_reads_real_flag_from_squad_summary(self, fake_dirs):
        gold_dir, _ = fake_dirs
        _write_players_scored(gold_dir, "20260701_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.5, "Rank": 1},
        ])
        _write_squad_summary(gold_dir, "20260701_090000", decision_date_enforced=False)

        _write_players_scored(gold_dir, "20260801_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.6, "Rank": 1},
        ])
        _write_squad_summary(gold_dir, "20260801_090000", decision_date_enforced=True)

        history = dl.load_all_players_scored_history()
        by_file = history.set_index("source_file")["is_pre_cutoff_fix"]
        assert bool(by_file["players_scored_20260701_090000.csv"]) is True
        assert bool(by_file["players_scored_20260801_090000.csv"]) is False

    def test_missing_squad_summary_defaults_to_pre_cutoff_fix_true(self, fake_dirs):
        gold_dir, _ = fake_dirs
        _write_players_scored(gold_dir, "20260701_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.5, "Rank": 1},
        ])
        history = dl.load_all_players_scored_history()
        assert history["is_pre_cutoff_fix"].all()

    def test_include_pre_cutoff_fix_false_excludes_only_pre_fix_runs(self, fake_dirs):
        gold_dir, _ = fake_dirs
        _write_players_scored(gold_dir, "20260701_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.5, "Rank": 1},
        ])
        _write_squad_summary(gold_dir, "20260701_090000", decision_date_enforced=False)

        _write_players_scored(gold_dir, "20260801_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.6, "Rank": 1},
        ])
        _write_squad_summary(gold_dir, "20260801_090000", decision_date_enforced=True)

        history = dl.load_all_players_scored_history(include_pre_cutoff_fix=False)
        assert history["source_file"].unique().tolist() == ["players_scored_20260801_090000.csv"]

    def test_skips_unparseable_filenames(self, fake_dirs):
        gold_dir, _ = fake_dirs
        (gold_dir / "players_scored_latest.csv").write_text("player_id,player_name\nP1,A\n")
        _write_players_scored(gold_dir, "20260701_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.5, "Rank": 1},
        ])
        history = dl.load_all_players_scored_history()
        assert len(history) == 1


class TestComputeBiggestMovers:

    def test_empty_history_returns_empty(self):
        assert dl.compute_biggest_movers(pd.DataFrame()).empty
        assert dl.compute_biggest_movers(None).empty

    def test_single_snapshot_returns_empty(self, fake_dirs):
        gold_dir, _ = fake_dirs
        _write_players_scored(gold_dir, "20260701_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.5, "Rank": 1},
        ])
        history = dl.load_all_players_scored_history()
        assert dl.compute_biggest_movers(history).empty

    def test_computes_rank_change_between_last_two_snapshots(self, fake_dirs):
        gold_dir, _ = fake_dirs
        _write_players_scored(gold_dir, "20260701_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.9, "Rank": 1},
            {"player_id": "P2", "player_name": "B", "position": "DEF", "DSSScore": 0.1, "Rank": 2},
        ])
        _write_players_scored(gold_dir, "20260708_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.6, "Rank": 2},
            {"player_id": "P2", "player_name": "B", "position": "DEF", "DSSScore": 0.7, "Rank": 1},
        ])
        _write_players_scored(gold_dir, "20260715_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.8, "Rank": 1},
            {"player_id": "P2", "player_name": "B", "position": "DEF", "DSSScore": 0.5, "Rank": 2},
        ])

        history = dl.load_all_players_scored_history()
        movers = dl.compute_biggest_movers(history)

        assert len(movers) == 2
        p1 = movers[movers["player_id"] == "P1"].iloc[0]
        p2 = movers[movers["player_id"] == "P2"].iloc[0]
        assert p1["previous_rank"] == 2 and p1["current_rank"] == 1 and p1["rank_change"] == 1
        assert p2["previous_rank"] == 1 and p2["current_rank"] == 2 and p2["rank_change"] == -1
        assert {"previous_run_date", "current_run_date"}.issubset(movers.columns)

    def test_only_common_players_across_both_snapshots_are_compared(self, fake_dirs):
        gold_dir, _ = fake_dirs
        _write_players_scored(gold_dir, "20260701_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.9, "Rank": 1},
        ])
        _write_players_scored(gold_dir, "20260708_090000", [
            {"player_id": "P1", "player_name": "A", "position": "MID", "DSSScore": 0.9, "Rank": 1},
            {"player_id": "P2", "player_name": "B", "position": "DEF", "DSSScore": 0.5, "Rank": 2},
        ])
        history = dl.load_all_players_scored_history()
        movers = dl.compute_biggest_movers(history)
        assert list(movers["player_id"]) == ["P1"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))