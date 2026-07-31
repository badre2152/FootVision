# analytics/rotation_strategy.py
# Rotation Strategy — transparent, rule-based squad rotation recommender
#
# Added 2026-07-30, same design philosophy as analytics/ml/injury_predictor.py:
# a fully transparent composite index with documented, round-number weights,
# NOT a trained model — there is no historical "which rotation choice led to
# the best outcome" label in this project to fit against. Lives at
# analytics/rotation_strategy.py (top-level, matching PROJECT.md's original
# file layout — this is a squad-management tool, not a scoring/ML component,
# so it deliberately sits outside analytics/scoring/ and analytics/ml/).
#
# ─────────────────────────────────────────────────────────────────────────
# GROUNDED IN THE REAL 2026 GROUP STAGE — AND A REAL SURPRISE
# ─────────────────────────────────────────────────────────────────────────
# REAL_GROUP_STAGE_2026 below is Morocco's actual Group C schedule (Brazil,
# Scotland, Haiti — sourced 2026-07-30, see each fixture's citation).
#
# The real finding this module should be read against: Morocco reportedly
# played the SAME starting XI in all three group matches — see
# REAL_XI_ALL_THREE_MATCHES and its source citation. Even away to
# already-eliminated Haiti in a dead-rubber-ish finale (a textbook rotation
# opportunity by the logic this module implements), no personnel changes
# were made. validate_vs_real_xi() reports this plainly: whatever this
# module recommends for match 3, it will very likely NOT match reality,
# and that disagreement is not evidence the module is "wrong" — it is
# evidence real coaches optimize for things this module cannot see
# (rhythm, cohesion, trust, avoiding disruption mid-tournament) at least
# as much as for fatigue/opponent-difficulty math. Same epistemic posture
# as analytics/ml/selection_classifier.py's audit_vs_dss_ranking(): report
# the disagreement, do not paper over it, do not claim the model should
# have "won".
#
# ─────────────────────────────────────────────────────────────────────────
# WHAT THE SCORE IS BUILT FROM (weights sum to 1.0, all round numbers)
# ─────────────────────────────────────────────────────────────────────────
#   quality_component     (0.45) — DSSScore, position-normalized (z-score
#                          within position group, then rescaled to [0,1] —
#                          raw DSSScore isn't comparable across positions,
#                          see config.py's own per-position weighting logic
#                          for why cross-position raw comparison is unsound)
#   availability_component (0.20) — AvailabilityScore as-is (already [0,1])
#   fatigue_penalty        (0.20) — accumulated minutes-equivalent load
#                          from THIS module's own simulated prior matches
#                          in the sequence, scaled down by rest days before
#                          the next match. Starts at 0 for match 1 (no
#                          history yet) — see _fatigue_component().
#   injury_risk_penalty    (0.15) — 1 - InjuryRiskScore from
#                          analytics/ml/injury_predictor.py, if provided
#                          (optional — neutral 0.5 contribution if the
#                          caller didn't merge injury_predictor output in).
#
# A rotation_bonus is layered on top for weaker opponents (see
# OPPONENT_DIFFICULTY_ROTATION_BONUS) — this is what makes the difference
# between "always pick the strongest XI" and an actual rotation policy: for
# an "Easy" opponent, non-starters with good quality_component get a boost
# specifically to surface rotation candidates the coach could blood without
# a large expected-result cost, not to declare them "better" than the
# incumbent.
#
# NEVER combine RotationPriority into DSSScore, same non-double-counting
# principle as injury_predictor.py — this is a squad-management view over
# the SAME underlying scores, not a new scoring input.

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# Real 2026 World Cup Group C schedule (Morocco)
# Sources: aol.com/Independent (fixture dates/venues), Yahoo Sports World
# Cup results tracker, whensport.com (kickoff times) — cross-checked
# 2026-07-30.
# ─────────────────────────────────────────────────────────────────────────
REAL_GROUP_STAGE_2026: list[dict] = [
    {
        "match_number": 1, "opponent": "Brazil", "date": "2026-06-13",
        "venue": "MetLife Stadium, New Jersey", "result": "1-1 draw",
        "difficulty": "Very Hard", "rest_days_before": None,  # tournament opener
    },
    {
        "match_number": 2, "opponent": "Scotland", "date": "2026-06-19",
        "venue": "Gillette Stadium, Foxborough", "result": "1-0 win",
        "difficulty": "Medium", "rest_days_before": 6,
    },
    {
        "match_number": 3, "opponent": "Haiti", "date": "2026-06-24",
        "venue": "Mercedes-Benz Stadium, Atlanta", "result": "4-2 win",
        "difficulty": "Easy", "rest_days_before": 5,
    },
]

# Reported starting XI for match 3 (vs Haiti) — a preview source stated
# Morocco "could name an unchanged starting lineup for a third consecutive
# match" with "no new injury concerns", i.e. the same XI as matches 1 and 2.
# Source: 101greatgoals.com Morocco v Haiti preview, 2026-06-23. Treated
# here as the real XI for ALL THREE group matches — a genuine real-world
# claim, not this module's assumption, and worth re-verifying against
# official match reports if exact certainty matters for your use case.
REAL_XI_ALL_THREE_MATCHES: list[str] = [
    "Yassine Bounou", "Achraf Hakimi", "Issa Diop", "Chadi Riad", "Noussair Mazraoui",
    "Neil El Aynaoui", "Ayyoub Bouaddi", "Brahim Diaz", "Azzedine Ounahi",
    "Bilal El Khannouss", "Ismael Saibari",
]
# Position counts implied by the real XI above (GK/DEF/MID/FWD only, this
# project's position taxonomy has no finer-grained roles) — used as the
# default `formation` for recommend_xi() so a like-for-like comparison in
# validate_vs_real_xi() is apples-to-apples.
REAL_FORMATION: dict[str, int] = {"GK": 1, "DEF": 4, "MID": 5, "FWD": 1}

WEIGHTS = {
    "quality_component": 0.45,
    "availability_component": 0.20,
    "fatigue_penalty": 0.20,
    "injury_risk_penalty": 0.15,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

OPPONENT_DIFFICULTY_ROTATION_BONUS: dict[str, float] = {
    # Added to non-incumbent candidates' priority score for this match only
    # — bigger bonus for weaker opponents, i.e. more room to rotate safely.
    "Very Hard": 0.00,
    "Hard": 0.03,
    "Medium": 0.06,
    "Easy": 0.12,
}

FATIGUE_DECAY_PER_REST_DAY = 0.12  # how much accumulated fatigue clears per rest day (capped at 1.0 total decay)
FATIGUE_PER_START = 1.0            # fatigue units added for starting a (simulated) match


@dataclass
class MatchRecommendation:
    match_number: int
    opponent: str
    difficulty: str
    recommended_xi: list[str]                  # player_name, len == sum(formation.values())
    priority_table: pd.DataFrame                # every eligible player, full breakdown, sorted
    rotation_candidates: list[str]               # non-incumbents recommended in specifically BECAUSE of the difficulty bonus


@dataclass
class RotationPlan:
    matches: list[MatchRecommendation]
    fatigue_after: dict[str, float]              # player_name -> accumulated fatigue units after the full plan


def _zscore_within_position(df: pd.DataFrame, col: str) -> pd.Series:
    """Z-scores `col` within each position group, then rescales to [0, 1]
    via the logistic function — keeps ties/ordering sane even for small
    position groups (e.g. n=6 GKs) and avoids the "DEF DSSScore isn't
    comparable to FWD DSSScore" problem raw values would have."""
    grouped = df.groupby("position")[col]
    mean = grouped.transform("mean")
    std = grouped.transform("std").replace(0, np.nan)
    z = (df[col] - mean) / std
    z = z.fillna(0.0)
    return 1 / (1 + np.exp(-z))  # logistic squash to (0, 1)


def _fatigue_component(fatigue_units: float) -> float:
    """Maps accumulated fatigue units (unbounded, decays with rest) to a
    [0, 1] penalty via the same logistic squash, centered so 1 full match
    started with no rest yet sits around the middle of the scale."""
    return 1 / (1 + np.exp(-(fatigue_units - 1.0)))


def recommend_xi(
    squad_df: pd.DataFrame,
    opponent_difficulty: str,
    fatigue_state: dict[str, float] | None = None,
    formation: dict[str, int] | None = None,
) -> MatchRecommendation:
    """
    Recommends a starting XI for one match.

    Args:
        squad_df: the 26-player squad (top26.csv-shaped). Requires
            player_name, position, DSSScore, AvailabilityScore. Optionally
            InjuryRiskScore (from analytics/ml/injury_predictor.py output,
            merged in by the caller) — treated as neutral 0.5 if absent.
        opponent_difficulty: one of OPPONENT_DIFFICULTY_ROTATION_BONUS's keys.
        fatigue_state: player_name -> accumulated fatigue units from prior
            matches in this plan. None/omitted for match 1.
        formation: position -> slot count, must sum to 11. Defaults to
            REAL_FORMATION for a like-for-like comparison against reality.

    Returns:
        MatchRecommendation with the full priority table (for audit) and
        the selected XI.
    """
    required = ["player_name", "position", "DSSScore", "AvailabilityScore"]
    missing = [c for c in required if c not in squad_df.columns]
    if missing:
        raise ValueError(f"recommend_xi() requires columns {required}, missing: {missing}")
    if opponent_difficulty not in OPPONENT_DIFFICULTY_ROTATION_BONUS:
        raise ValueError(
            f"opponent_difficulty must be one of {list(OPPONENT_DIFFICULTY_ROTATION_BONUS)}, "
            f"got {opponent_difficulty!r}"
        )
    formation = formation or REAL_FORMATION
    if sum(formation.values()) != 11:
        raise ValueError(f"formation must sum to 11 (a starting XI), got {formation} = {sum(formation.values())}")

    df = squad_df.copy()
    fatigue_state = fatigue_state or {}

    df["quality_component"] = _zscore_within_position(df, "DSSScore")
    df["availability_component"] = df["AvailabilityScore"].clip(0, 1)

    if "InjuryRiskScore" in df.columns:
        df["injury_risk_penalty_component"] = 1 - df["InjuryRiskScore"].clip(0, 1)
    else:
        df["injury_risk_penalty_component"] = 0.5
        logger.warning(
            "InjuryRiskScore not found in squad_df — injury_risk_penalty component "
            "defaulted to neutral (0.5) for everyone. Merge in "
            "analytics/ml/injury_predictor.py output for a real signal here."
        )

    df["fatigue_units"] = df["player_name"].map(fatigue_state).fillna(0.0)
    df["fatigue_penalty_component"] = 1 - df["fatigue_units"].apply(_fatigue_component)

    df["base_priority"] = (
        WEIGHTS["quality_component"] * df["quality_component"]
        + WEIGHTS["availability_component"] * df["availability_component"]
        + WEIGHTS["fatigue_penalty"] * df["fatigue_penalty_component"]
        + WEIGHTS["injury_risk_penalty"] * df["injury_risk_penalty_component"]
    )

    # rotation bonus: only meaningful relative to who WOULD have started —
    # applied uniformly here, then the selection itself naturally surfaces
    # which non-incumbents it pulled in over higher-fatigue starters.
    bonus = OPPONENT_DIFFICULTY_ROTATION_BONUS[opponent_difficulty]
    is_fresh = df["fatigue_units"] == 0.0
    df["rotation_bonus"] = np.where(is_fresh, bonus, 0.0)
    df["priority"] = (df["base_priority"] + df["rotation_bonus"]).clip(0, 1)

    selected: list[str] = []
    for position, slots in formation.items():
        pool = df[df["position"] == position].sort_values("priority", ascending=False)
        if len(pool) < slots:
            logger.warning(
                "Only %d %s available in squad_df, need %d for this formation — "
                "selecting all available.", len(pool), position, slots,
            )
        selected.extend(pool.head(slots)["player_name"].tolist())

    incumbents_by_fatigue = set(df.sort_values("fatigue_units", ascending=False)["player_name"].head(11))
    rotation_candidates = [
        p for p in selected
        if df.loc[df["player_name"] == p, "fatigue_units"].iloc[0] == 0.0
        and bonus > 0
    ]

    priority_table = df.sort_values("priority", ascending=False)[
        ["player_name", "position", "DSSScore", "AvailabilityScore",
         "quality_component", "availability_component", "fatigue_penalty_component",
         "injury_risk_penalty_component", "rotation_bonus", "priority"]
    ].reset_index(drop=True)

    return MatchRecommendation(
        match_number=-1, opponent="", difficulty=opponent_difficulty,
        recommended_xi=selected, priority_table=priority_table,
        rotation_candidates=rotation_candidates,
    )


def build_rotation_plan(
    squad_df: pd.DataFrame,
    fixtures: list[dict] = REAL_GROUP_STAGE_2026,
    formation: dict[str, int] | None = None,
) -> RotationPlan:
    """
    Runs recommend_xi() across a sequence of matches, carrying fatigue
    forward (with decay for rest days) between them — the actual "rotation
    strategy" output: one recommended XI per match, not just a single
    snapshot.
    """
    fatigue: dict[str, float] = {}
    matches: list[MatchRecommendation] = []

    for fixture in fixtures:
        rest_days = fixture.get("rest_days_before")
        if rest_days:
            decay = min(rest_days * FATIGUE_DECAY_PER_REST_DAY, 1.0)
            fatigue = {p: max(0.0, f * (1 - decay)) for p, f in fatigue.items()}

        rec = recommend_xi(
            squad_df, opponent_difficulty=fixture["difficulty"],
            fatigue_state=fatigue, formation=formation,
        )
        rec.match_number = fixture["match_number"]
        rec.opponent = fixture["opponent"]
        matches.append(rec)

        for player in rec.recommended_xi:
            fatigue[player] = fatigue.get(player, 0.0) + FATIGUE_PER_START

    return RotationPlan(matches=matches, fatigue_after=fatigue)


def validate_vs_real_xi(
    recommendation: MatchRecommendation,
    real_xi: list[str] = REAL_XI_ALL_THREE_MATCHES,
) -> dict:
    """
    Compares one match's recommended XI against the real reported XI. See
    module header — disagreement here is expected and informative, not a
    failure to "fix". Returns overlap count/pct and the two-way diff.
    """
    rec_set, real_set = set(recommendation.recommended_xi), set(real_xi)
    overlap = rec_set & real_set
    return {
        "match_number": recommendation.match_number,
        "opponent": recommendation.opponent,
        "overlap_count": len(overlap),
        "overlap_pct": len(overlap) / len(real_set) if real_set else float("nan"),
        "recommended_not_real": sorted(rec_set - real_set),
        "real_not_recommended": sorted(real_set - rec_set),
    }
