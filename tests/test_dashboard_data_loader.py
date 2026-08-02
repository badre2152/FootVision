# tests/test_dashboard_data_loader.py
#
# Tests dashboard/data_loader.py in isolation — no real gold/ folder
# dependency (uses pytest's tmp_path + monkeypatch to point the module at
# a synthetic, throwaway directory instead). This means these tests keep
# passing regardless of what's currently in the real gold/ folder, and
# don't get slower or flakier as more real runs pile up there over time.
#
# CACHING NOTE (spec #22, added 2026-08-01): every load_latest_*()/
# load_all_*() function in data_loader.py is now wrapped in
# @st.cache_data(ttl=300) — see that module's docstring. Those functions
# take NO arguments, so Streamlit's cache key collapses to ONE global slot
# per function, shared across every test in this file/process. Without
# clearing it, test B calling load_latest_top26() right after test A
# would silently get test A's cached result even though `fake_dirs` points
# GOLD_DIR at a brand-new tmp_path for test B — a real, non-obvious way
# for these tests to start lying. `fake_dirs` below calls `.clear()` on
# every cached function before each test to prevent exactly that.

import json
import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    """Points GOLD_DIR/BRONZE_DIR at a fresh temp directory for each test,
    and clears every @st.cache_data-wrapped loader's cache so a previous
    test's cached result can never leak into this one (see module docstring)."""
    for fn in _CACHED_LOADERS:
        fn.clear()
    gold_dir = tmp_path / "gold"
    bronze_dir = tmp_path / "bronze"
    gold_dir.mkdir()
    bronze_dir.mkdir()
    monkeypatch.setattr(dl, "GOLD_DIR", gold_dir)
    monkeypatch.setattr(dl, "BRONZE_DIR", bronze_dir)
    return gold_dir, bronze_dir


# ─────────────────────────────────────────────────────────────────────────────
# _latest_file — the shared "find the most recent run" helper
# ─────────────────────────────────────────────────────────────────────────────

class TestLatestFile:

    def test_returns_none_for_empty_directory(self, fake_dirs):
        gold_dir, _ = fake_dirs
        assert dl._latest_file(gold_dir, "top26_", ".csv") is None

    def test_returns_none_for_nonexistent_directory(self, tmp_path):
        assert dl._latest_file(tmp_path / "does_not_exist", "top26_", ".csv") is None

    def test_picks_the_chronologically_latest_run(self, fake_dirs):
        gold_dir, _ = fake_dirs
        # Three runs, written in a DIFFERENT order than chronological, to
        # prove selection is based on the filename's run_date, not
        # creation order or file mtime.
        (gold_dir / "top26_20260713_100000.csv").write_text("a")
        (gold_dir / "top26_20260721_090000.csv").write_text("c")
        (gold_dir / "top26_20260717_120000.csv").write_text("b")

        latest = dl._latest_file(gold_dir, "top26_", ".csv")
        assert latest.name == "top26_20260721_090000.csv"

    def test_prefix_does_not_match_unrelated_files(self, fake_dirs):
        gold_dir, _ = fake_dirs
        (gold_dir / "players_scored_20260721_090000.csv").write_text("x")
        assert dl._latest_file(gold_dir, "top26_", ".csv") is None


# ─────────────────────────────────────────────────────────────────────────────
# load_latest_top26 / load_latest_players_scored
# ─────────────────────────────────────────────────────────────────────────────

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
        """Ensures the two loaders don't accidentally share/clobber state."""
        gold_dir, _ = fake_dirs
        pd.DataFrame({"player_name": ["A"]}).to_csv(gold_dir / "top26_20260721_090000.csv", index=False)
        pd.DataFrame({"player_name": ["A", "B", "C"]}).to_csv(gold_dir / "players_scored_20260721_090000.csv", index=False)

        top26 = dl.load_latest_top26()
        players = dl.load_latest_players_scored()
        assert len(top26) == 1
        assert len(players) == 3


# ─────────────────────────────────────────────────────────────────────────────
# load_latest_squad_summary
# ─────────────────────────────────────────────────────────────────────────────

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
        """
        Pre-2026-07-14 squad_summary.json files don't have "position_counts"
        or "percentile_summary" — the loader itself must not require them
        (app.py has its own fallback logic for deriving position_counts
        from position_breakdown when missing — this test just confirms the
        LOADER doesn't choke on the older, smaller schema).
        """
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


# ─────────────────────────────────────────────────────────────────────────────
# load_latest_chemistry_pairs
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadLatestChemistryPairs:

    def test_returns_empty_list_not_none_when_missing(self, fake_dirs):
        """
        Deliberately different contract from the other loaders (which
        return None) — chemistry pairs are optional/supplementary, and
        the Chemistry dashboard page is written to treat [] as "show an
        explanatory message", not "crash because None was unexpected".
        """
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


# ─────────────────────────────────────────────────────────────────────────────
# available_component_columns
# ─────────────────────────────────────────────────────────────────────────────

class TestAvailableComponentColumns:

    def test_old_run_only_core_columns(self):
        """Pre-2026-07-14 players_scored.csv — only the 5 original components."""
        df = pd.DataFrame(columns=[
            "player_name", "PerformanceScore", "FormScore",
            "MentalScore", "AvailabilityScore", "InternationalScore",
        ])
        cols = dl.available_component_columns(df)
        assert cols == ["PerformanceScore", "FormScore", "MentalScore", "AvailabilityScore", "InternationalScore"]

    def test_new_run_includes_all_9(self):
        """
        UPDATED 2026-07-24: was "all_8" / included DSSScore_pct — fixed
        after live dashboard screenshots showed SentimentScore missing
        from the radar chart entirely (forgotten when added 2026-07-22)
        and DSSScore_pct incorrectly appearing as if it were a component
        (it's the final display-scaled OUTPUT, not a sub-score input).
        """
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
        """E.g. Chemistry+Pressure present but Flexibility not yet (mid-session state)."""
        df = pd.DataFrame(columns=[
            "player_name", "PerformanceScore", "ChemistryScore", "PressureIndex",
        ])
        cols = dl.available_component_columns(df)
        assert cols == ["PerformanceScore", "ChemistryScore", "PressureIndex"]

    def test_empty_dataframe_returns_empty_list(self):
        df = pd.DataFrame(columns=["player_name"])
        assert dl.available_component_columns(df) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ─────────────────────────────────────────────────────────────────────────────
# load_all_players_scored_history / compute_biggest_movers — spec #18
# ─────────────────────────────────────────────────────────────────────────────
# Re-added 2026-08-02: this coverage existed in an earlier session but was
# lost somewhere in a later rewrite of this file (only the function's
# @st.cache_data .clear() registration in _CACHED_LOADERS survived). Also
# updated for spec #1 now being genuinely implemented: is_pre_cutoff_fix is
# a REAL per-run flag read from squad_summary_<timestamp>.json's
# decision_date_enforced field (see data_loader.py's
# _is_run_cutoff_enforced()) — no longer a hardcoded True for everything.

def _write_players_scored(gold_dir, run_date_str, rows):
    """Helper: writes a minimal players_scored_<run_date_str>.csv fixture."""
    path = gold_dir / f"players_scored_{run_date_str}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _write_squad_summary(gold_dir, run_date_str, decision_date_enforced):
    """Helper: writes a minimal squad_summary_<run_date_str>.json fixture."""
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
        """
        Core spec #1-integration behavior: a run whose squad_summary_*.json
        says decision_date_enforced=True must show is_pre_cutoff_fix=False,
        and vice versa — no more blanket "always pre-fix" assumption now
        that the code genuinely stamps this per run.
        """
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
        """No squad_summary_*.json at all for a snapshot → fail closed (pre-fix)."""
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