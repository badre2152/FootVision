# tests/test_rival_benchmark.py
#
# Regression tests for 23_rival_benchmark.md.

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.rival_benchmark import (
    RADAR_CATEGORIES_BY_POSITION,
    compare_to_reference,
    get_references_for_position,
    load_reference_players,
)


def _fake_references():
    return pd.DataFrame([
        {
            "ref_player_id": "REF001", "ref_player_name": "Ref GK", "position": "GK",
            "role_detail": "GK", "nation": "Testland", "club": "Test FC",
            "benchmark_reason": "test fixture", "season": "2024-2025",
            "season_end_date": "2025-05-25", "stats_verified": True,
            "GA_per90": 0.8, "GK_saves_per90": 3.2,
            "Gls_per90": None, "Ast_per90": None, "xg_per90": None, "xa_per90": None,
            "Tkl_per90": None, "Int_per90": None, "Clr_per90": None,
        },
        {
            "ref_player_id": "REF002", "ref_player_name": "Ref CB", "position": "DEF",
            "role_detail": "CB", "nation": "Testland", "club": "Test FC",
            "benchmark_reason": "test fixture", "season": "2024-2025",
            "season_end_date": "2025-05-25", "stats_verified": False,
            "Tkl_per90": 2.5, "Int_per90": 1.8, "Clr_per90": 5.0,
            "Gls_per90": None, "Ast_per90": None, "xg_per90": None, "xa_per90": None,
            "GA_per90": None, "GK_saves_per90": None,
        },
    ])


class TestLoadReferencePlayers:

    def test_loads_the_real_seed_file(self):
        """The actual seeds/rival_reference_players.csv shipped with this
        change must load and contain the 3 curated Brazil players (GK/CB/ST
        — the spec's explicit minimum: GK, CB, ST)."""
        df = load_reference_players()
        assert not df.empty
        assert set(df["position"]) >= {"GK", "DEF", "FWD"}
        assert "Alisson Becker" in df["ref_player_name"].values

    def test_missing_file_returns_empty_dataframe_not_crash(self, monkeypatch, tmp_path):
        import analytics.rival_benchmark as rb
        monkeypatch.setattr(rb, "REFERENCE_PLAYERS_PATH", tmp_path / "does_not_exist.csv")
        df = load_reference_players()
        assert df.empty


class TestGetReferencesForPosition:

    def test_filters_by_position(self):
        refs = _fake_references()
        gk_refs = get_references_for_position("GK", refs)
        assert len(gk_refs) == 1
        assert gk_refs.iloc[0]["ref_player_name"] == "Ref GK"

    def test_no_match_returns_empty(self):
        refs = _fake_references()
        mid_refs = get_references_for_position("MID", refs)
        assert mid_refs.empty


class TestCompareToReference:

    def test_never_touches_any_score_column(self):
        """Functional Requirement #3: this must never read/write DSSScore
        or any *Score column — only raw per-90 stat columns."""
        player = pd.Series({
            "player_name": "Test Candidate", "position": "GK",
            "DSSScore": 0.87, "PerformanceScore": 0.9,
            "GA_per90": 1.0, "GK_saves_per90": 2.5,
        })
        result = compare_to_reference(player, "GK", _fake_references())
        assert "DSSScore" not in str(result["categories"])
        assert all("Score" not in c for c in result["categories"])

    def test_player_worse_at_conceding_scales_lower_than_better_keeper(self):
        """GA_per90 is inverted (fewer goals conceded = better) — confirms
        the inversion actually happens, not just that a number comes out."""
        good_keeper = pd.Series({"player_name": "Good GK", "position": "GK", "GA_per90": 0.5, "GK_saves_per90": 3.0})
        bad_keeper = pd.Series({"player_name": "Bad GK", "position": "GK", "GA_per90": 2.0, "GK_saves_per90": 3.0})
        refs = _fake_references()

        good_result = compare_to_reference(good_keeper, "GK", refs)
        bad_result = compare_to_reference(bad_keeper, "GK", refs)

        assert good_result["player_values_scaled"]["GA_per90"] > bad_result["player_values_scaled"]["GA_per90"]

    def test_scaled_values_stay_within_zero_one(self):
        player = pd.Series({"player_name": "Test CB", "position": "DEF", "Tkl_per90": 4.0, "Int_per90": 3.0, "Clr_per90": 10.0})
        result = compare_to_reference(player, "DEF", _fake_references())
        for v in result["player_values_scaled"].values():
            if v is not None:
                assert 0.0 <= v <= 1.0
        for ref in result["references"]:
            for v in ref["values_scaled"].values():
                if v is not None:
                    assert 0.0 <= v <= 1.0

    def test_missing_stat_value_scales_to_none_not_zero(self):
        """A player missing a stat must show as 'no data' (None), never
        silently plotted as 0 (which would look like 'worst possible',
        a misleading claim this project explicitly avoids elsewhere too
        — see the chem_is_real / pressure_data_confidence precedent)."""
        player = pd.Series({"player_name": "Partial Data CB", "position": "DEF", "Tkl_per90": 4.0})
        result = compare_to_reference(player, "DEF", _fake_references())
        assert result["player_values_scaled"]["Int_per90"] is None
        assert result["player_values_scaled"]["Clr_per90"] is None

    def test_no_references_for_position_sets_warning_not_crash(self):
        player = pd.Series({"player_name": "Test MID", "position": "MID", "Tkl_per90": 2.0})
        result = compare_to_reference(player, "MID", _fake_references())
        assert result["warning"] is not None
        assert result["references"] == []

    def test_unknown_position_sets_warning_not_crash(self):
        player = pd.Series({"player_name": "??", "position": "WING-BACK"})
        result = compare_to_reference(player, "WING-BACK", _fake_references())
        assert result["warning"] is not None
        assert result["categories"] == []

    def test_stats_verified_flag_surfaces_per_reference(self):
        """The dashboard needs to know which references have real,
        verified stat values vs which are curated-but-not-yet-populated
        (see seeds/rival_reference_players.csv — all 3 shipped references
        are currently stats_verified=False, real values not yet fetched)."""
        player = pd.Series({"player_name": "Test GK", "position": "GK", "GA_per90": 1.0, "GK_saves_per90": 3.0})
        result = compare_to_reference(player, "GK", _fake_references())
        assert result["references"][0]["stats_verified"] is True

    def test_all_position_buckets_have_defined_categories(self):
        """Sanity check: GK/DEF/MID/FWD (this project's full position
        taxonomy — see seeds/players_49.csv) must all have SOME benchmark
        categories defined, not just the 3 the acceptance criteria calls
        out as the minimum (GK, CB->DEF, ST->FWD)."""
        for pos in ["GK", "DEF", "MID", "FWD"]:
            assert RADAR_CATEGORIES_BY_POSITION.get(pos), f"No categories defined for {pos}"


class TestRealSeedFileRespectsDecisionDate:

    def test_all_reference_seasons_end_before_decision_date(self):
        """Functional Requirement #2: reference player stats must be
        pre-DECISION_DATE. Checks the actual shipped seed file's
        season_end_date against config.DECISION_DATE."""
        import config
        df = load_reference_players()
        assert not df.empty
        season_end = pd.to_datetime(df["season_end_date"]).dt.date
        assert (season_end < config.DECISION_DATE).all(), (
            "A reference player's season_end_date is not before DECISION_DATE — "
            "would violate spec #23's Functional Requirement #2."
        )
