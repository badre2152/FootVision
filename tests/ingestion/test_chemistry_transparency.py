import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("MINIO_MOCK", "1")

from ingestion.silver_pipeline import join_chemistry_pairs, impute_by_position


def _base_df(n=6):
    positions = ["GK", "DEF", "DEF", "MID", "MID", "FWD"][:n]
    return pd.DataFrame({
        "player_id": [f"p{i}" for i in range(n)],
        "player_name": [f"Player {i}" for i in range(n)],
        "position": positions,
    })


class TestChemIsRealFlag:

    def test_flag_true_only_for_players_in_pairs_source(self, monkeypatch):
        df = _base_df(6)

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
        assert out.loc[out["player_name"] == "Player 4", "chem_avg_per90"].isna().all()
        assert not out.loc[out["player_name"] == "Player 4", "chem_is_real"].any()

    def test_flag_survives_position_median_imputation(self, monkeypatch):
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

        assert out["chem_avg_per90"].notna().all()
        real_after = set(out.loc[out["chem_is_real"], "player_name"])
        assert real_after == real_before
        assert out["chem_is_real"].dtype == bool or out["chem_is_real"].dtype == np.dtype("bool")

    def test_flag_false_for_everyone_when_bronze_source_missing(self, monkeypatch):
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

        assert int(out["chem_is_real"].sum()) == 3