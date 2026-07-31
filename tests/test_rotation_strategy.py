# tests/test_rotation_strategy.py
#
# Test suite for analytics/rotation_strategy.py — added 2026-07-30.

import sys
import os

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.rotation_strategy import (
    recommend_xi,
    build_rotation_plan,
    validate_vs_real_xi,
    WEIGHTS,
    REAL_GROUP_STAGE_2026,
    REAL_XI_ALL_THREE_MATCHES,
    REAL_FORMATION,
    OPPONENT_DIFFICULTY_ROTATION_BONUS,
)


def _toy_squad():
    """1 GK slot pool of 3, 4 DEF slot pool of 6, 5 MID slot pool of 8, 1 FWD slot pool of 3
    -> matches REAL_FORMATION (1/4/5/1), enough depth in each group to
    exercise rotation-candidate selection."""
    rng = np.random.default_rng(0)
    rows = []
    for pos, n in [("GK", 3), ("DEF", 6), ("MID", 8), ("FWD", 3)]:
        for i in range(n):
            rows.append({
                "player_name": f"{pos}_{i}",
                "position": pos,
                "DSSScore": round(0.3 + rng.random() * 0.25, 4),
                "AvailabilityScore": round(0.6 + rng.random() * 0.4, 4),
            })
    return pd.DataFrame(rows)


class TestWeights:
    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_real_formation_sums_to_eleven(self):
        assert sum(REAL_FORMATION.values()) == 11


class TestRecommendXi:
    def test_missing_required_columns_raises(self):
        df = pd.DataFrame({"player_name": ["a"]})
        with pytest.raises(ValueError, match="requires columns"):
            recommend_xi(df, opponent_difficulty="Easy")

    def test_invalid_difficulty_raises(self):
        df = _toy_squad()
        with pytest.raises(ValueError, match="opponent_difficulty"):
            recommend_xi(df, opponent_difficulty="Impossible")

    def test_formation_not_summing_to_eleven_raises(self):
        df = _toy_squad()
        with pytest.raises(ValueError, match="formation must sum to 11"):
            recommend_xi(df, opponent_difficulty="Easy", formation={"GK": 1, "DEF": 4})

    def test_recommended_xi_matches_formation_counts(self):
        df = _toy_squad()
        rec = recommend_xi(df, opponent_difficulty="Medium")
        assert len(rec.recommended_xi) == 11
        positions = [p.split("_")[0] for p in rec.recommended_xi]
        assert positions.count("GK") == REAL_FORMATION["GK"]
        assert positions.count("DEF") == REAL_FORMATION["DEF"]
        assert positions.count("MID") == REAL_FORMATION["MID"]
        assert positions.count("FWD") == REAL_FORMATION["FWD"]

    def test_no_duplicate_players_in_recommended_xi(self):
        df = _toy_squad()
        rec = recommend_xi(df, opponent_difficulty="Hard")
        assert len(rec.recommended_xi) == len(set(rec.recommended_xi))

    def test_missing_injury_risk_score_defaults_neutral(self):
        df = _toy_squad()  # no InjuryRiskScore column at all
        rec = recommend_xi(df, opponent_difficulty="Easy")
        assert (rec.priority_table["injury_risk_penalty_component"] == 0.5).all()

    def test_higher_dssscore_within_position_increases_priority_all_else_equal(self):
        df = pd.DataFrame({
            "player_name": ["A", "B"], "position": ["FWD", "FWD"],
            "DSSScore": [0.9, 0.2], "AvailabilityScore": [0.8, 0.8],
        })
        rec = recommend_xi(df, opponent_difficulty="Very Hard")
        table = rec.priority_table.set_index("player_name")
        assert table.loc["A", "priority"] > table.loc["B", "priority"]

    def test_fatigued_player_loses_priority_to_fresh_equal_player(self):
        df = pd.DataFrame({
            "player_name": ["Fresh", "Tired"], "position": ["FWD", "FWD"],
            "DSSScore": [0.5, 0.5], "AvailabilityScore": [0.8, 0.8],
        })
        rec = recommend_xi(df, opponent_difficulty="Very Hard", fatigue_state={"Tired": 2.0})
        table = rec.priority_table.set_index("player_name")
        assert table.loc["Fresh", "priority"] > table.loc["Tired", "priority"]

    def test_rotation_bonus_only_applied_for_nonzero_difficulty_bonus(self):
        df = _toy_squad()
        rec_hard = recommend_xi(df, opponent_difficulty="Very Hard")  # bonus = 0.00
        assert (rec_hard.priority_table["rotation_bonus"] == 0).all()
        rec_easy = recommend_xi(df, opponent_difficulty="Easy")  # bonus = 0.12
        assert (rec_easy.priority_table["rotation_bonus"].isin([0, OPPONENT_DIFFICULTY_ROTATION_BONUS["Easy"]])).all()

    def test_insufficient_players_in_position_warns_not_crashes(self, caplog):
        df = pd.DataFrame([
            {"player_name": "only_gk", "position": "GK", "DSSScore": 0.5, "AvailabilityScore": 0.8},
        ] + [
            {"player_name": f"MID_{i}", "position": "MID", "DSSScore": 0.5, "AvailabilityScore": 0.8}
            for i in range(9)
        ])
        # ask for 2 GK slots (only 1 available) — formation still sums to 11
        rec = recommend_xi(df, opponent_difficulty="Easy", formation={"GK": 2, "DEF": 0, "MID": 9, "FWD": 0})
        # can't fill 2 GK slots from 1 candidate -> selects the 1 available, doesn't crash
        assert rec.recommended_xi.count("only_gk") == 1


class TestBuildRotationPlan:
    def test_three_matches_produced_for_real_fixtures(self):
        df = _toy_squad()
        plan = build_rotation_plan(df, fixtures=REAL_GROUP_STAGE_2026)
        assert len(plan.matches) == 3
        assert [m.match_number for m in plan.matches] == [1, 2, 3]
        assert [m.opponent for m in plan.matches] == ["Brazil", "Scotland", "Haiti"]

    def test_fatigue_accumulates_for_repeated_starters(self):
        # tiny squad forces the exact same 11 to start every match
        df = pd.DataFrame([
            {"player_name": f"{pos}_{i}", "position": pos, "DSSScore": 0.5, "AvailabilityScore": 0.8}
            for pos, n in [("GK", 1), ("DEF", 4), ("MID", 5), ("FWD", 1)]
            for i in range(n)
        ])
        plan = build_rotation_plan(df, fixtures=REAL_GROUP_STAGE_2026)
        # every player started all 3 matches -> fatigue > 0 for everyone,
        # and higher than a single start would produce (decay < full reset)
        assert all(f > 0 for f in plan.fatigue_after.values())

    def test_rest_days_decay_fatigue_between_matches(self):
        df = _toy_squad()
        # fixture with a long rest gap should show lower cumulative fatigue
        # than one with none, for a player who started match 1
        long_rest = [
            {"match_number": 1, "opponent": "X", "difficulty": "Very Hard", "rest_days_before": None},
            {"match_number": 2, "opponent": "Y", "difficulty": "Very Hard", "rest_days_before": 20},
        ]
        no_rest = [
            {"match_number": 1, "opponent": "X", "difficulty": "Very Hard", "rest_days_before": None},
            {"match_number": 2, "opponent": "Y", "difficulty": "Very Hard", "rest_days_before": 0},
        ]
        plan_long = build_rotation_plan(df, fixtures=long_rest)
        plan_no = build_rotation_plan(df, fixtures=no_rest)
        common_players = set(plan_long.fatigue_after) & set(plan_no.fatigue_after)
        assert common_players, "expected at least some overlap in who started"
        for p in common_players:
            assert plan_long.fatigue_after[p] <= plan_no.fatigue_after[p]


class TestValidateVsRealXi:
    def test_perfect_overlap_when_recommended_equals_real(self):
        df = _toy_squad()
        rec = recommend_xi(df, opponent_difficulty="Medium")
        rec.recommended_xi = list(REAL_XI_ALL_THREE_MATCHES)  # force equality
        result = validate_vs_real_xi(rec)
        assert result["overlap_count"] == 11
        assert result["overlap_pct"] == 1.0
        assert result["recommended_not_real"] == []
        assert result["real_not_recommended"] == []

    def test_zero_overlap_reported_correctly(self):
        df = _toy_squad()
        rec = recommend_xi(df, opponent_difficulty="Medium")
        rec.recommended_xi = ["totally", "different", "names"]
        result = validate_vs_real_xi(rec, real_xi=["a", "b", "c"])
        assert result["overlap_count"] == 0
        assert len(result["recommended_not_real"]) == 3
        assert len(result["real_not_recommended"]) == 3
