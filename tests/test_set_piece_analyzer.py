# tests/test_set_piece_analyzer.py

import sys
import os

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.set_piece_analyzer import (
    clutch_ranking,
    ninety_plus_performance,
    set_piece_specialists,
    CLUTCH_THRESHOLD,
)


def _df(rows):
    return pd.DataFrame(rows)


class TestClutchRanking:
    def test_missing_required_columns_raises(self):
        df = _df([{"player_name": "P"}])
        with pytest.raises(ValueError, match="requires columns"):
            clutch_ranking(df)

    def test_sorted_by_pressure_index_descending(self):
        df = _df([
            {"player_name": "A", "pressure_delta": 0.0, "PressureIndex": 0.5},
            {"player_name": "B", "pressure_delta": 0.0, "PressureIndex": 0.9},
            {"player_name": "C", "pressure_delta": 0.0, "PressureIndex": 0.2},
        ])
        result = clutch_ranking(df)
        assert result["player_name"].tolist() == ["B", "A", "C"]

    def test_thrives_under_pressure_label(self):
        df = _df([{"player_name": "A", "pressure_delta": CLUTCH_THRESHOLD + 0.01, "PressureIndex": 0.6}])
        result = clutch_ranking(df)
        assert result.iloc[0]["ClutchLabel"] == "Thrives Under Pressure"

    def test_drops_off_label(self):
        df = _df([{"player_name": "A", "pressure_delta": -CLUTCH_THRESHOLD - 0.01, "PressureIndex": 0.4}])
        result = clutch_ranking(df)
        assert result.iloc[0]["ClutchLabel"] == "Drops Off"

    def test_consistent_label_within_threshold(self):
        df = _df([{"player_name": "A", "pressure_delta": 0.0, "PressureIndex": 0.5}])
        result = clutch_ranking(df)
        assert result.iloc[0]["ClutchLabel"] == "Consistent"

    def test_confidence_real_when_both_raw_columns_present(self):
        df = _df([{
            "player_name": "A", "pressure_delta": 0.05, "PressureIndex": 0.6,
            "sb_xg_per90": 0.4, "xg_per90": 0.35,
        }])
        result = clutch_ranking(df)
        assert result.iloc[0]["pressure_data_confidence"] == "Real"

    def test_confidence_imputed_when_raw_columns_missing_values(self):
        df = _df([{
            "player_name": "A", "pressure_delta": -0.08, "PressureIndex": 0.4,
            "sb_xg_per90": np.nan, "xg_per90": 0.35,
        }])
        result = clutch_ranking(df)
        assert result.iloc[0]["pressure_data_confidence"] == "Imputed"

    def test_confidence_unknown_when_raw_columns_absent_entirely(self):
        df = _df([{"player_name": "A", "pressure_delta": 0.0, "PressureIndex": 0.5}])
        result = clutch_ranking(df)
        assert result.iloc[0]["pressure_data_confidence"] == "Unknown"

    def test_row_count_preserved(self):
        df = _df([
            {"player_name": f"P{i}", "pressure_delta": 0.01 * i, "PressureIndex": 0.1 * i}
            for i in range(5)
        ])
        result = clutch_ranking(df)
        assert len(result) == 5


class TestStubHonesty:
    def test_ninety_plus_performance_raises(self):
        with pytest.raises(NotImplementedError, match="no minute-by-minute"):
            ninety_plus_performance()

    def test_set_piece_specialists_raises(self):
        with pytest.raises(NotImplementedError, match="no set-piece event tagging"):
            set_piece_specialists()
