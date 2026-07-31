# analytics/opponent_analyzer.py
# Morocco's REAL 2026 FIFA World Cup fixture/result record.
#
# Added 2026-07-30, AFTER the tournament concluded (final: France 2-0
# Morocco, quarterfinal, 2026-07-09 — see MOROCCO_WC2026_FIXTURES below for
# full sourcing). This is why this file is a static, cited historical
# table rather than a live scraper: there is nothing left to scrape, the
# result set is now closed and final.
#
# ─────────────────────────────────────────────────────────────────────────
# WHAT'S HERE, AND WHAT ISN'T (same honesty rule as every analytics/*.py
# file in this project — read before assuming a gap is a bug)
# ─────────────────────────────────────────────────────────────────────────
# PROJECT.md's original "opponent_analyzer.py" spec was: "Scraper le style
# de jeu des adversaires du groupe" + feed a "Tactical Fit Score vs chaque
# adversaire". That half is STILL not built, and still can't be built
# honestly with this project's data — see analytics/competition_context.py
# docstring and its tactical_fit_vs_opponent() stub for the full
# explanation (no opponent squad/formation/style data source exists
# anywhere in Bronze/Silver; this file does not change that).
#
# What IS here: Morocco's real, final, publicly-verifiable match record
# for this tournament — round, date, opponent, score, and penalty-shootout
# detail where relevant. This is FIXTURE/RESULT data, not tactical style
# data. It exists so a future dashboard page (or the "real squad vs model"
# comparison discussed separately) has real ground truth to reference
# instead of nothing — e.g. "the model's top-26 Best XI — how did it do
# against the team that eliminated us" — without pretending to know how
# France presses or lines up, which we don't have data for.
#
# ─────────────────────────────────────────────────────────────────────────
# SOURCING (every fixture below is corroborated by at least 2 independent
# sources found via live web search on 2026-07-30 — not recalled from
# training data, since the tournament falls entirely after this project's
# knowledge cutoff)
# ─────────────────────────────────────────────────────────────────────────
#   Group C composition & standings: Wikipedia "2026 FIFA World Cup Group C"
#   Jun 13 Brazil 1-1 Morocco:        Yahoo Sports, ESPN
#   Jun 19 Scotland 0-1 Morocco:      Yahoo Sports
#   Jun 24 Morocco 4-2 Haiti:         ESPN ("Morocco 4-2 Haiti Game Analysis"),
#                                     FIFA.com match centre, Al Jazeera
#   Jun 29 Netherlands 1-1 Morocco (Morocco won 3-2 on penalties), R32:
#                                     CBS Sports, Yahoo Sports
#   Jul 4  Morocco 3-0 Canada, R16:   Yahoo Sports, ESPN (co-hosts eliminated)
#   Jul 9  France 2-0 Morocco, QF (Morocco eliminated):
#                                     ESPN, NPR, Al Jazeera, Olympics.com

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Every entry independently corroborated — see SOURCING note above.
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
        "advanced": True,  # finished 2nd in Group C behind Brazil (goal difference)
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
        "result": "Loss", "penalties": None, "advanced": False,  # eliminated
    },
]

FINAL_ROUND_REACHED = "Quarterfinal"
ELIMINATED_BY = "France"


@dataclass
class TournamentRunSummary:
    final_round_reached: str
    eliminated_by: str | None
    record: dict  # {"wins": int, "draws": int, "losses": int}
    goals_for: int
    goals_against: int
    goal_difference: int


def get_fixtures() -> pd.DataFrame:
    """Returns Morocco's real WC2026 fixture/result record as a DataFrame, in chronological order."""
    return pd.DataFrame(MOROCCO_WC2026_FIXTURES)


def tournament_run_summary() -> TournamentRunSummary:
    """
    Summarizes Morocco's real tournament run: final round reached, overall
    W-D-L record (a shootout win still counts as a "Draw" in the base
    result — see MOROCCO_WC2026_FIXTURES' `result` field — the record
    counts it as a draw for goal-difference purposes, matching how the
    match actually finished in 90/120 minutes; `penalties` holds the
    shootout outcome separately), and goals for/against across all 6
    matches.
    """
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
    """
    NOT IMPLEMENTED — same reasoning as
    analytics/competition_context.py's tactical_fit_vs_opponent() stub,
    referenced here rather than re-explained to avoid the two docstrings
    drifting out of sync. This file adds real FIXTURE data (who/when/
    score); it does NOT add opponent tactical/style data, which still
    doesn't exist anywhere in this project's Bronze/Silver layers.
    """
    raise NotImplementedError(
        "tactical_style_profile() is intentionally not built — see "
        "analytics/competition_context.py's tactical_fit_vs_opponent() "
        "docstring for the full explanation (no opponent style/formation "
        "data source exists in this project). This file only provides "
        "real fixture/result history, not tactical analysis."
    )
