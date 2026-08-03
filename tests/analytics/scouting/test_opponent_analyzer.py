# tests/test_opponent_analyzer.py
#
# Test suite for analytics/opponent_analyzer.py — real, static WC2026
# fixture history (not a scraper, not tactical analysis — see module
# docstring).

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from analytics.scouting.opponent_analyzer import (
    get_fixtures,
    tournament_run_summary,
    tactical_style_profile,
    MOROCCO_WC2026_FIXTURES,
    FINAL_ROUND_REACHED,
    ELIMINATED_BY,
)


class TestFixtureData:
    def test_six_matches_total(self):
        assert len(MOROCCO_WC2026_FIXTURES) == 6

    def test_chronological_order(self):
        df = get_fixtures()
        dates = df["date"].tolist()
        assert dates == sorted(dates)

    def test_all_expected_opponents_present(self):
        df = get_fixtures()
        assert set(df["opponent"]) == {"Brazil", "Scotland", "Haiti", "Netherlands", "Canada", "France"}

    def test_group_stage_has_three_matches(self):
        df = get_fixtures()
        assert (df["round"] == "Group Stage").sum() == 3

    def test_final_match_is_quarterfinal_loss_to_france(self):
        df = get_fixtures()
        last = df.iloc[-1]
        assert last["round"] == "Quarterfinal"
        assert last["opponent"] == "France"
        assert last["result"] == "Loss"
        assert last["advanced"] is False

    def test_netherlands_match_recorded_as_penalty_win(self):
        df = get_fixtures()
        row = df[df["opponent"] == "Netherlands"].iloc[0]
        assert row["morocco_goals"] == row["opponent_goals"] == 1  # 1-1 in regulation
        assert row["penalties"] == "3-2 Morocco"
        assert row["advanced"] is True


class TestTournamentRunSummary:
    def test_final_round_and_eliminated_by(self):
        summary = tournament_run_summary()
        assert summary.final_round_reached == FINAL_ROUND_REACHED == "Quarterfinal"
        assert summary.eliminated_by == ELIMINATED_BY == "France"

    def test_record_is_3_wins_2_draws_1_loss(self):
        # Wins: Scotland, Haiti, Canada = 3
        # Draws (incl. shootout win, still a draw in 90/120 min): Brazil, Netherlands = 2
        # Losses: France = 1
        summary = tournament_run_summary()
        assert summary.record == {"wins": 3, "draws": 2, "losses": 1}

    def test_goal_tallies(self):
        summary = tournament_run_summary()
        # 1+1+4+1+3+0 = 10 for; 1+0+2+1+0+2 = 6 against
        assert summary.goals_for == 10
        assert summary.goals_against == 6
        assert summary.goal_difference == 4


class TestTacticalStubHonesty:
    def test_tactical_style_profile_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="no opponent style/formation"):
            tactical_style_profile("France")
