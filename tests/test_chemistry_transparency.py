# tests/test_chemistry_transparency.py
#
# Regression tests for 19_chemistry_transparency.md.
#
# Asserts the SPECIFIC behavior requested there, not just a general smoke
# test (per the project's 03_regression_protection.md pattern):
#   1. join_chemistry_pairs() sets a "chem_is_real" flag BEFORE imputation,
#      True only for players who actually appear in the pairwise Bronze
#      source.
#   2. impute_by_position() must NOT overwrite chem_is_real (it's not in
#      IMPUTE_OUTFIELD_COLS/IMPUTE_GK_COLS) — it must survive Silver→Gold
#      untouched, still reflecting the PRE-imputation reality even after
#      chem_avg_per90 itself has been filled with a position-group median.
#   3. sum(chem_is_real) matches the real StatsBomb pairwise coverage
#      (i.e. the players actually present in the mocked pairs records).

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MINIO_MOCK", "1")

from ingestion.silver_pipeline import join_chemistry_pairs, impute_by_position


def _base_df(n=6):
    """A minimal Silver-shaped frame: n players, mixed positions, all other
    IMPUTE_OUTFIELD_COLS/IMPUTE_GK_COLS columns present so impute_by_position()
    doesn't choke on missing columns."""
    positions = ["GK", "DEF", "DEF", "MID", "MID", "FWD"][:n]
    return pd.DataFrame({
        "player_id": [f"p{i}" for i in range(n)],
        "player_name": [f"Player {i}" for i in range(n)],
        "position": positions,
    })


class TestChemIsRealFlag:

    def test_flag_true_only_for_players_in_pairs_source(self, monkeypatch):
        """chem_is_real must be True exactly for players present in the
        pairwise Bronze source, False for everyone else — set BEFORE any
        imputation runs."""
        df = _base_df(6)

        # Player 0 <-> Player 1 share a pair; Player 2 <-> Player 3 share
        # another. Players 4 and 5 have no pairwise data at all (mirrors
        # the 11/49 real-coverage ceiling documented in chemistry_score.py).
        mock_records = [
            {"player_a_name": "Player 0", "player_b_name": "Player 1",
             "chem_per90": 5.0, "shared_matches": 1, "passes_combined": 10},
            {"player_a_name": "Player 2", "player_b_name": "Player 3",
             "chem_per90": 7.0, "shared_matches": 1, "passes_combined": 12},
        ]
        monkeypatch.setattr(
            "ingestion.silver_pipeline.read_latest_json",
            lambda bucket, prefix: mock_records,
        )

        out = join_chemistry_pairs(df)

        assert "chem_is_real" in out.columns
        expected_real = {"Player 0", "Player 1", "Player 2", "Player 3"}
        real_names = set(out.loc[out["chem_is_real"], "player_name"])
        assert real_names == expected_real
        assert int(out["chem_is_real"].sum()) == 4
        # Untouched players must be NaN pre-imputation, not falsely "real".
        assert out.loc[out["player_name"] == "Player 4", "chem_avg_per90"].isna().all()
        assert not out.loc[out["player_name"] == "Player 4", "chem_is_real"].any()

    def test_flag_survives_position_median_imputation(self, monkeypatch):
        """After impute_by_position() fills chem_avg_per90 for everyone
        (49/49 non-null, per chemistry_score.py's docstring), chem_is_real
        must still correctly say who was real vs who was filled in."""
        df = _base_df(6)
        mock_records = [
            {"player_a_name": "Player 0", "player_b_name": "Player 1",
             "chem_per90": 5.0, "shared_matches": 1, "passes_combined": 10},
        ]
        monkeypatch.setattr(
            "ingestion.silver_pipeline.read_latest_json",
            lambda bucket, prefix: mock_records,
        )

        df = join_chemistry_pairs(df)
        real_before = set(df.loc[df["chem_is_real"], "player_name"])

        out = impute_by_position(df)

        # chem_avg_per90 must now be 100% non-null (imputed for the rest).
        assert out["chem_avg_per90"].notna().all()
        # chem_is_real must be UNCHANGED by imputation — still a boolean
        # flag, not overwritten to True for everyone just because
        # chem_avg_per90 is now non-null for everyone.
        real_after = set(out.loc[out["chem_is_real"], "player_name"])
        assert real_after == real_before
        assert out["chem_is_real"].dtype == bool or out["chem_is_real"].dtype == np.dtype("bool")

    def test_flag_false_for_everyone_when_bronze_source_missing(self, monkeypatch):
        """If the chemistry_pairs Bronze source is empty/missing entirely,
        chem_is_real must be False for all players (never guessed True)."""
        df = _base_df(4)
        monkeypatch.setattr(
            "ingestion.silver_pipeline.read_latest_json",
            lambda bucket, prefix: None,
        )

        out = join_chemistry_pairs(df)

        assert "chem_is_real" in out.columns
        assert not out["chem_is_real"].any()
        assert out["chem_avg_per90"].isna().all()

    def test_real_count_matches_documented_coverage_shape(self, monkeypatch):
        """Sanity check on the acceptance criterion: sum(chem_is_real)
        must equal the number of DISTINCT players appearing in the pairs
        source, not the number of pairs (a player can appear in >1 pair)."""
        df = _base_df(6)
        mock_records = [
            {"player_a_name": "Player 0", "player_b_name": "Player 1",
             "chem_per90": 5.0, "shared_matches": 1, "passes_combined": 10},
            {"player_a_name": "Player 0", "player_b_name": "Player 2",
             "chem_per90": 3.0, "shared_matches": 1, "passes_combined": 8},
        ]
        monkeypatch.setattr(
            "ingestion.silver_pipeline.read_latest_json",
            lambda bucket, prefix: mock_records,
        )

        out = join_chemistry_pairs(df)

        # 3 distinct players (0, 1, 2) despite 2 pair-rows.
        assert int(out["chem_is_real"].sum()) == 3
