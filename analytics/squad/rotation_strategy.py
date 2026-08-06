from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REAL_GROUP_STAGE_2026: list[dict] = [
    {
        "match_number": 1, "opponent": "Brazil", "date": "2026-06-13",
        "venue": "MetLife Stadium, New Jersey", "result": "1-1 draw",
        "difficulty": "Very Hard", "rest_days_before": None,
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

REAL_XI_ALL_THREE_MATCHES: list[str] = [
    "Yassine Bounou", "Achraf Hakimi", "Issa Diop", "Chadi Riad", "Noussair Mazraoui",
    "Neil El Aynaoui", "Ayyoub Bouaddi", "Brahim Diaz", "Azzedine Ounahi",
    "Bilal El Khannouss", "Ismael Saibari",
]

REAL_FORMATION: dict[str, int] = {"GK": 1, "DEF": 4, "MID": 5, "FWD": 1}

WEIGHTS = {
    "quality_component": 0.45,
    "availability_component": 0.20,
    "fatigue_penalty": 0.20,
    "injury_risk_penalty": 0.15,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

OPPONENT_DIFFICULTY_ROTATION_BONUS: dict[str, float] = {
    "Very Hard": 0.00,
    "Hard": 0.03,
    "Medium": 0.06,
    "Easy": 0.12,
}

FATIGUE_DECAY_PER_REST_DAY = 0.12
FATIGUE_PER_START = 1.0


@dataclass
class MatchRecommendation:
    match_number: int
    opponent: str
    difficulty: str
    recommended_xi: list[str]
    priority_table: pd.DataFrame
    rotation_candidates: list[str]


@dataclass
class RotationPlan:
    matches: list[MatchRecommendation]
    fatigue_after: dict[str, float]


def _zscore_within_position(df: pd.DataFrame, col: str) -> pd.Series:
    grouped = df.groupby("position")[col]
    mean = grouped.transform("mean")
    std = grouped.transform("std").replace(0, np.nan)
    z = (df[col] - mean) / std
    z = z.fillna(0.0)
    return 1 / (1 + np.exp(-z))


def _fatigue_component(fatigue_units: float) -> float:
    return 1 / (1 + np.exp(-(fatigue_units - 1.0)))


def recommend_xi(
    squad_df: pd.DataFrame,
    opponent_difficulty: str,
    fatigue_state: dict[str, float] | None = None,
    formation: dict[str, int] | None = None,
) -> MatchRecommendation:
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