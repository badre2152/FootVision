from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)

AGE_RISK_MIN = 23
AGE_RISK_MAX = 35

DAYS_CAP = 365.0
COUNT_CAP = 10.0

WEIGHTS = {
    "age_component": 0.30,
    "history_days_component": 0.30,
    "history_count_component": 0.20,
    "recurrence_type_component": 0.20,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

LEVEL_CUTOFFS = {"Low": 0.35, "Medium": 0.60}

SOFT_TISSUE_KEYWORDS = [
    "muscle", "hamstring", "calf", "groin", "strain", "thigh", "adductor",
]
NON_SOFT_TISSUE_KEYWORDS = [
    "ankle", "knee", "ligament", "cruciate", "fracture", "rib", "shoulder",
    "surgery", "bone",
]
VAGUE_KEYWORDS = [
    "unknown", "fitness", "rest", "knock", "dead leg", "illness",
]


@dataclass
class InjuryRiskResult:
    df: pd.DataFrame
    n_players: int
    n_low_confidence: int
    level_counts: dict[str, int]


def _classify_injury_type(raw: str | float) -> tuple[float, bool]:
    """
    Returns (recurrence_component, is_low_confidence).

    is_low_confidence=True means: no usable inj_last_type text at all, or
    it matched neither the soft-tissue nor non-soft-tissue keyword lists
    (i.e. we genuinely don't know) — the neutral 0.5 in that case is a
    stated "don't know", not a claim that risk is actually average.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.5, True

    text = str(raw).strip().lower()
    if not text:
        return 0.5, True

    if any(kw in text for kw in VAGUE_KEYWORDS):
        return 0.5, True

    is_soft = any(kw in text for kw in SOFT_TISSUE_KEYWORDS)
    is_hard = any(kw in text for kw in NON_SOFT_TISSUE_KEYWORDS)

    if is_soft and not is_hard:
        return 1.0, False
    if is_hard and not is_soft:
        return 0.0, False
    if is_soft and is_hard:
        return 1.0, True

    return 0.5, True


def _age_component(dob: str | float, reference_date: date) -> tuple[float, bool]:
    """Returns (component, is_low_confidence). Missing/unparseable dob -> neutral 0.5, flagged."""
    if dob is None or (isinstance(dob, float) and pd.isna(dob)):
        return 0.5, True
    try:
        birth = pd.to_datetime(dob).date()
    except (ValueError, TypeError):
        logger.warning("Unparseable wiki_dob value %r — using neutral age_component.", dob)
        return 0.5, True

    age_years = (reference_date - birth).days / 365.25
    component = (age_years - AGE_RISK_MIN) / (AGE_RISK_MAX - AGE_RISK_MIN)
    return float(np.clip(component, 0.0, 1.0)), False


def compute_injury_risk(
    df: pd.DataFrame,
    reference_date: date | None = None,
) -> InjuryRiskResult:
    """
    Adds a transparent, rule-based injury risk index to df. See module
    docstring for full methodology and — importantly — its limits.

    Args:
        df: must have wiki_dob, inj_count_2y, inj_days_missed_2y columns.
            inj_last_type is optional (missing -> neutral component, see
            _classify_injury_type).
        reference_date: date to compute age as-of. Defaults to
            config.DECISION_DATE (NOT today's date — see "04_injury_predictor
            _temporal_fix.md" spec #4: using datetime.now() here would let
            the age component silently drift every day the script is re-run,
            and — for players near an age-bracket boundary — leak
            information from after the actual squad-decision date into the
            risk score). Pass an explicit date to override for what-if
            analysis.

    Returns:
        InjuryRiskResult. df gains:
          - InjuryRiskScore (float, [0, 1])
          - InjuryRiskLevel ("Low" / "Medium" / "High")
          - injury_risk_confidence ("High" / "Low") — "Low" if age or
            injury-type inputs were missing/unclassifiable for this player
          - injury_risk_age_component, _history_days_component,
            _history_count_component, _recurrence_type_component (for
            audit/debugging — never displayed as-is to end users, the
            composite is the useful number)
    """
    required = ["wiki_dob", "inj_count_2y", "inj_days_missed_2y"]
    missing_required = [c for c in required if c not in df.columns]
    if missing_required:
        raise ValueError(
            f"compute_injury_risk() requires columns {required}, "
            f"missing: {missing_required}"
        )

    ref_date = reference_date or config.DECISION_DATE
    result_df = df.copy()

    n = len(result_df)
    age_comp = np.zeros(n)
    age_low_conf = np.zeros(n, dtype=bool)
    for i, dob in enumerate(result_df["wiki_dob"].values):
        age_comp[i], age_low_conf[i] = _age_component(dob, ref_date)

    days_comp = (
        result_df["inj_days_missed_2y"].clip(lower=0, upper=DAYS_CAP) / DAYS_CAP
    ).fillna(0.5).values
    days_missing = result_df["inj_days_missed_2y"].isna().values

    count_comp = (
        result_df["inj_count_2y"].clip(lower=0, upper=COUNT_CAP) / COUNT_CAP
    ).fillna(0.5).values
    count_missing = result_df["inj_count_2y"].isna().values

    type_col = result_df["inj_last_type"] if "inj_last_type" in result_df.columns else pd.Series([None] * n)
    recur_comp = np.zeros(n)
    recur_low_conf = np.zeros(n, dtype=bool)
    for i, raw in enumerate(type_col.values):
        recur_comp[i], recur_low_conf[i] = _classify_injury_type(raw)

    result_df["injury_risk_age_component"] = age_comp
    result_df["injury_risk_history_days_component"] = days_comp
    result_df["injury_risk_history_count_component"] = count_comp
    result_df["injury_risk_recurrence_type_component"] = recur_comp

    score = (
        WEIGHTS["age_component"] * age_comp
        + WEIGHTS["history_days_component"] * days_comp
        + WEIGHTS["history_count_component"] * count_comp
        + WEIGHTS["recurrence_type_component"] * recur_comp
    )
    result_df["InjuryRiskScore"] = np.clip(score, 0.0, 1.0)

    result_df["InjuryRiskLevel"] = pd.cut(
        result_df["InjuryRiskScore"],
        bins=[-np.inf, LEVEL_CUTOFFS["Low"], LEVEL_CUTOFFS["Medium"], np.inf],
        labels=["Low", "Medium", "High"],
    ).astype(str)

    low_confidence = age_low_conf | recur_low_conf | days_missing | count_missing
    result_df["injury_risk_confidence"] = np.where(low_confidence, "Low", "High")

    level_counts = result_df["InjuryRiskLevel"].value_counts().to_dict()
    n_low_confidence = int(low_confidence.sum())

    logger.info(
        "Injury risk index: %d players scored, %d flagged low-confidence "
        "(missing/unclear dob or injury-type data). Level counts: %s",
        n, n_low_confidence, level_counts,
    )
    if n_low_confidence:
        logger.info(
            "Low-confidence players (verify manually with medical staff before "
            "acting on their risk level): %s",
            result_df.loc[low_confidence, "player_name"].tolist()
            if "player_name" in result_df.columns else "(no player_name column to list)",
        )

    return InjuryRiskResult(
        df=result_df,
        n_players=n,
        n_low_confidence=n_low_confidence,
        level_counts=level_counts,
    )