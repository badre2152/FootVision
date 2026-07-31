# tests/test_peak_detector.py
#
# Test suite for analytics/peak_detector.py — Peak/Rising/Declining trend
# detector based on real multi-season sofascore_detail_career data.

import sys
import os

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.peak_detector import (
    compute_peak_decline,
    _extract_club_seasons,
    _quality_proxy,
    _classify_trend,
    _parse_season_start_year,
    LABEL_NO_DATA,
    LABEL_INSUFFICIENT,
)


def _season(label, mp=30, minutes=2500, gls=None, ast=None, gc=None, is_summary=True):
    return {
        "label": label, "is_summary": is_summary, "MP": mp, "MIN": minutes,
        "GLS": gls, "AST": ast, "GC": gc, "CLS": None,
    }


def _row(career, position="MID", player_name="Test"):
    return {"player_name": player_name, "position": position, "sofascore_detail_career": career}


# ─────────────────────────────────────────────────────────────────────────
# Season label parsing / filtering
# ─────────────────────────────────────────────────────────────────────────

class TestSeasonParsing:
    def test_parse_two_digit_season(self):
        assert _parse_season_start_year("24/25") == 2024
        assert _parse_season_start_year("17/18") == 2017

    def test_extract_drops_non_summary_rows(self):
        career = [
            _season("24/25", gls=5, ast=3),
            _season("Ligue 1", gls=4, ast=2, is_summary=False),
            _season("UEFA Champions League", gls=1, ast=1, is_summary=False),
        ]
        seasons = _extract_club_seasons(career)
        assert len(seasons) == 1
        assert seasons[0]["label"] == "24/25"

    def test_extract_drops_national_team_year_entries(self):
        career = [_season("24/25", gls=5), _season("2025", gls=1)]  # national-team style, no "/"
        seasons = _extract_club_seasons(career)
        assert len(seasons) == 1
        assert seasons[0]["label"] == "24/25"

    def test_extract_sorts_oldest_to_newest(self):
        career = [_season("24/25"), _season("22/23"), _season("23/24")]
        seasons = _extract_club_seasons(career)
        assert [s["label"] for s in seasons] == ["22/23", "23/24", "24/25"]

    def test_none_career_returns_empty(self):
        assert _extract_club_seasons(None) == []

    def test_nan_career_returns_empty(self):
        assert _extract_club_seasons(np.nan) == []


# ─────────────────────────────────────────────────────────────────────────
# Quality proxy
# ─────────────────────────────────────────────────────────────────────────

class TestQualityProxy:
    def test_outfield_uses_goals_plus_assists_per_90(self):
        s = _season("24/25", minutes=900, gls=5, ast=5)  # 10 contributions / 900 min * 90 = 1.0
        assert _quality_proxy(s, "MID") == pytest.approx(1.0)

    def test_gk_uses_negative_conceded_per_90(self):
        s = _season("24/25", minutes=900, gc=10)  # -10/900*90 = -1.0
        assert _quality_proxy(s, "GK") == pytest.approx(-1.0)

    def test_low_minutes_season_excluded(self):
        s = _season("24/25", minutes=100, gls=5, ast=5)
        assert _quality_proxy(s, "MID") is None

    def test_gk_missing_gc_excluded(self):
        s = _season("24/25", minutes=900, gc=None)
        assert _quality_proxy(s, "GK") is None

    def test_outfield_both_stats_missing_excluded_not_zero(self):
        s = _season("24/25", minutes=900, gls=None, ast=None)
        assert _quality_proxy(s, "MID") is None

    def test_outfield_zero_is_a_real_zero_not_excluded(self):
        # a pure defensive player really can have 0 goals AND 0 assists —
        # that's a real data point, not missing data, as long as at least
        # one of GLS/AST is explicitly present (not both None)
        s = _season("24/25", minutes=900, gls=0.0, ast=None)
        assert _quality_proxy(s, "MID") == 0.0


# ─────────────────────────────────────────────────────────────────────────
# Trend classification
# ─────────────────────────────────────────────────────────────────────────

class TestClassifyTrend:
    def test_insufficient_with_zero_seasons(self):
        label, slope = _classify_trend([])
        assert label == LABEL_INSUFFICIENT
        assert slope is None

    def test_insufficient_with_one_season(self):
        label, slope = _classify_trend([0.5])
        assert label == LABEL_INSUFFICIENT

    def test_clearly_rising(self):
        label, slope = _classify_trend([0.2, 0.4, 0.6])
        assert label == "Rising"
        assert slope > 0

    def test_clearly_declining(self):
        label, slope = _classify_trend([0.9, 0.6, 0.3])
        assert label == "Declining"
        assert slope < 0

    def test_flat_high_output_is_peak(self):
        label, slope = _classify_trend([0.95, 1.0, 0.98])
        assert label == "Peak"

    def test_flat_low_relative_to_own_history_is_stable(self):
        # zero net slope over 3 seasons (dip then recovery to the SAME
        # level, not the peak) and NOT near this player's own best season
        # -> Stable, not Declining (which requires a real net downward
        # slope) and not Peak (latest season isn't near the season-2 peak)
        label, slope = _classify_trend([0.5, 0.9, 0.5])
        assert label == "Stable"


# ─────────────────────────────────────────────────────────────────────────
# compute_peak_decline() — end to end
# ─────────────────────────────────────────────────────────────────────────

class TestComputePeakDecline:
    def test_missing_required_columns_raises(self):
        df = pd.DataFrame({"player_name": ["P"]})
        with pytest.raises(ValueError, match="requires columns"):
            compute_peak_decline(df)

    def test_no_data_label_when_career_missing(self):
        df = pd.DataFrame([_row(None)])
        result = compute_peak_decline(df)
        assert result.df.iloc[0]["PeakDeclineLabel"] == LABEL_NO_DATA
        assert result.n_no_data == 1

    def test_rising_player_end_to_end(self):
        career = [
            _season("22/23", minutes=2000, gls=2, ast=2),
            _season("23/24", minutes=2000, gls=5, ast=3),
            _season("24/25", minutes=2000, gls=8, ast=6),
        ]
        df = pd.DataFrame([_row(career, position="FWD")])
        result = compute_peak_decline(df)
        assert result.df.iloc[0]["PeakDeclineLabel"] == "Rising"
        assert result.df.iloc[0]["seasons_used"] == 3

    def test_gk_declining_end_to_end(self):
        career = [
            _season("22/23", minutes=2500, gc=15),
            _season("23/24", minutes=2500, gc=25),
            _season("24/25", minutes=2500, gc=40),
        ]
        df = pd.DataFrame([_row(career, position="GK")])
        result = compute_peak_decline(df)
        assert result.df.iloc[0]["PeakDeclineLabel"] == "Declining"

    def test_only_low_minutes_seasons_is_insufficient(self):
        career = [_season("23/24", minutes=100, gls=1), _season("24/25", minutes=200, gls=2)]
        df = pd.DataFrame([_row(career, position="MID")])
        result = compute_peak_decline(df)
        assert result.df.iloc[0]["PeakDeclineLabel"] == LABEL_INSUFFICIENT
        assert result.n_insufficient == 1

    def test_n_seasons_parameter_limits_window(self):
        career = [
            _season("18/19", minutes=2000, gls=0, ast=0),   # would look "Rising" if included
            _season("22/23", minutes=2000, gls=5, ast=5),
            _season("23/24", minutes=2000, gls=5, ast=5),
            _season("24/25", minutes=2000, gls=5, ast=5),
        ]
        df = pd.DataFrame([_row(career, position="MID")])
        result = compute_peak_decline(df, n_seasons=3)
        assert result.df.iloc[0]["seasons_used"] == 3  # 18/19 correctly excluded

    def test_row_count_and_columns_preserved(self):
        df = pd.DataFrame([_row(None), _row([_season("24/25", minutes=2000, gls=1, ast=1)])])
        result = compute_peak_decline(df)
        assert len(result.df) == 2
        for col in ["PeakDeclineLabel", "trend_slope_relative", "seasons_used"]:
            assert col in result.df.columns

    def test_label_counts_match_actual_column(self):
        df = pd.DataFrame([_row(None), _row(None)])
        result = compute_peak_decline(df)
        assert result.label_counts == result.df["PeakDeclineLabel"].value_counts().to_dict()
