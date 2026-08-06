from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MOROCCO_WC2026_FIXTURES: list[dict] = [
    {
        "round": "Group Stage", "matchday": 1, "date": "2026-06-13",
        "opponent": "Brazil", "morocco_goals": 1, "opponent_goals": 1,
        "result": "Draw", "penalties": None, "advanced": None,
    },
    {
        "round": "Group Stage", "matchday": 2, "date": "2026-06-19",
        "opponent": "Scotland", "morocco_goals": 1, "opponent_goals": 0,
        "result": "Win", "penalties": None, "advanced": None,
    },
    {
        "round": "Group Stage", "matchday": 3, "date": "2026-06-24",
        "opponent": "Haiti", "morocco_goals": 4, "opponent_goals": 2,
        "result": "Win", "penalties": None,
        "advanced": True,
    },
    {
        "round": "Round of 32", "matchday": None, "date": "2026-06-29",
        "opponent": "Netherlands", "morocco_goals": 1, "opponent_goals": 1,
        "result": "Draw (won on penalties)", "penalties": "3-2 Morocco",
        "advanced": True,
    },
    {
        "round": "Round of 16", "matchday": None, "date": "2026-07-04",
        "opponent": "Canada", "morocco_goals": 3, "opponent_goals": 0,
        "result": "Win", "penalties": None, "advanced": True,
    },
    {
        "round": "Quarterfinal", "matchday": None, "date": "2026-07-09",
        "opponent": "France", "morocco_goals": 0, "opponent_goals": 2,
        "result": "Loss", "penalties": None, "advanced": False,
    },
]

FINAL_ROUND_REACHED = "Quarterfinal"
ELIMINATED_BY = "France"


@dataclass
class TournamentRunSummary:
    final_round_reached: str
    eliminated_by: str | None
    record: dict
    goals_for: int
    goals_against: int
    goal_difference: int


def get_fixtures() -> pd.DataFrame:
    return pd.DataFrame(MOROCCO_WC2026_FIXTURES)


def tournament_run_summary() -> TournamentRunSummary:
    df = get_fixtures()
    wins = int((df["result"] == "Win").sum())
    draws = int(df["result"].str.startswith("Draw").sum())
    losses = int((df["result"] == "Loss").sum())
    goals_for = int(df["morocco_goals"].sum())
    goals_against = int(df["opponent_goals"].sum())

    return TournamentRunSummary(
        final_round_reached=FINAL_ROUND_REACHED,
        eliminated_by=ELIMINATED_BY,
        record={"wins": wins, "draws": draws, "losses": losses},
        goals_for=goals_for,
        goals_against=goals_against,
        goal_difference=goals_for - goals_against,
    )


def tactical_style_profile(opponent: str):
    raise NotImplementedError(
        "tactical_style_profile() is intentionally not built — see "
        "analytics/competition_context.py's tactical_fit_vs_opponent() "
        "docstring for the full explanation (no opponent style/formation "
        "data source exists in this project). This file only provides "
        "real fixture/result history, not tactical analysis."
    )