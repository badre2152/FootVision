# analytics/ml/injury_predictor.py
# "Injury Predictor" — Transparent Rule-Based Injury Risk Index
#
# Added 2026-07-29 (same session as player_clustering.py). Lives under
# analytics/ml/ to match PROJECT.md's Phase 4 file layout, but — same as
# that module's own header explains for itself — this is NOT the
# "XGBoost -> probabilité blessure 0-100%" component PROJECT.md originally
# specced. That component is explicitly NOT built here, on purpose.
#
# ─────────────────────────────────────────────────────────────────────────
# WHY THIS ISN'T A TRAINED MODEL (same reasoning as player_clustering.py's
# header, repeated here because this file needs to stand on its own)
# ─────────────────────────────────────────────────────────────────────────
# A real injury predictor needs: (a) a genuine target label — did THIS
# player suffer a NEW injury in some future window — and (b) a time-series
# per-player injury/workload history to learn from. This project has
# neither:
#   - inj_count_2y / inj_days_missed_2y (silver_pipeline.py, from
#     transfermarkt_injury_loader.py) are static 2-YEAR AGGREGATES, not a
#     dated event sequence. There is no "did they get injured in the 3
#     months after this snapshot" label to train or validate against.
#   - n=49 candidates total. Even with a real label, this is far too few
#     rows for a supervised model (XGBoost or otherwise) to learn anything
#     beyond memorizing noise — and it invites the exact failure mode this
#     project has been careful to avoid elsewhere (see
#     player_clustering.py's Selection Classifier discussion): a model
#     that OUTPUTS a probability-shaped number people will trust as if it
#     were validated, when it isn't.
#
# What follows instead is a fully transparent, auditable composite index
# built from established sports-medicine RISK FACTORS (age, injury
# recurrence count/burden, soft-tissue vs. traumatic injury type) — not
# fitted or calibrated against any outcome in this dataset (there is no
# outcome to fit against). Every weight below is a round, defensible,
# clearly-labeled judgment call, not a "learned" coefficient — and this
# module says so at every point that matters, rather than dressing a
# heuristic up as a model.
#
# ─────────────────────────────────────────────────────────────────────────
# CRITICAL: DO NOT DOUBLE-COUNT THIS AGAINST AvailabilityScore
# ─────────────────────────────────────────────────────────────────────────
# analytics/scoring/performance_score.py's compute_availability_score()
# ALREADY consumes inj_count_2y and inj_days_missed_2y as an inverse
# penalty inside DSSScore (composite weight 0.0795 as of the 2026-07-22
# rebalance — see config.py COMPOSITE_WEIGHTS). This module reuses the
# SAME two raw columns for a different purpose (a human-readable risk flag
# for medical/coaching staff review) — it must NEVER be wired into
# DSSScore or run_gold.py's squad_summary/selection logic as an additional
# weighted score. Doing so would penalize the same underlying injury
# history twice under two different names, silently double-weighting it
# in the final ranking without anyone deciding that on purpose. This is
# exactly the kind of silent-duplication bug AUDIT_AND_FIXES.md's Fix 2
# and MASTER_SPEC.md's CONFLICT review process exist to catch — flagging
# it here up front instead of waiting to find it later.
#
# Consumers (e.g. a future dashboard "Fitness & Rotation" page) should
# display InjuryRiskLevel as a standalone advisory panel, separate from
# and never combined into DSSScore.
#
# ─────────────────────────────────────────────────────────────────────────
# COMPONENTS (weights sum to 1.0, all round numbers, all documented)
# ─────────────────────────────────────────────────────────────────────────
#   age_component            (0.30) — age-related injury risk is a broadly
#                             cited pattern in sports-medicine literature
#                             (soft-tissue injury incidence rising from the
#                             late-20s onward); NOT derived from this
#                             dataset's outcomes (there are none to derive
#                             it from). Linear from age 23 (0.0) to age 35+
#                             (1.0), capped both ends.
#   history_days_component   (0.30) — inj_days_missed_2y, same [0, 365]
#                             cap already used for this column elsewhere
#                             in the codebase (config.py
#                             NORMALIZATION_BOUNDS) — kept consistent
#                             rather than inventing a new bound.
#   history_count_component  (0.20) — inj_count_2y, same [0, 10] cap
#                             already used in compute_availability_score()
#                             — same reasoning: consistency over novelty.
#   recurrence_type_component (0.20) — 1.0 if the player's most recent
#                             recorded injury is a soft-tissue/muscular
#                             type (muscle, hamstring, calf, groin —
#                             clinically associated with higher re-injury
#                             rates than one-off traumatic injuries), 0.0
#                             if it's a clearly non-soft-tissue type
#                             (ankle, knee ligament, rib, etc.), and 0.5
#                             (neutral) if inj_last_type is missing or too
#                             vague to classify ("unknown injury",
#                             "Fitness", "Rest", "Knock") — see
#                             injury_risk_confidence below for why this
#                             matters and isn't hidden.
#
# ─────────────────────────────────────────────────────────────────────────
# CUTOFFS ARE ILLUSTRATIVE, NOT CALIBRATED
# ─────────────────────────────────────────────────────────────────────────
# Low / Medium / High level thresholds (0.35 / 0.60) are round numbers
# chosen for readability, exactly like the composite's weights — there is
# no outcome data in this project to calibrate them against. Treat
# InjuryRiskLevel as a conversation-starter for medical staff, not a
# clinical threshold.

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)

AGE_RISK_MIN = 23   # age at/below which age_component = 0.0
AGE_RISK_MAX = 35   # age at/above which age_component = 1.0 (capped)

DAYS_CAP = 365.0    # matches config.py NORMALIZATION_BOUNDS for inj_days_missed_2y
COUNT_CAP = 10.0    # matches compute_availability_score()'s count_pct cap

WEIGHTS = {
    "age_component": 0.30,
    "history_days_component": 0.30,
    "history_count_component": 0.20,
    "recurrence_type_component": 0.20,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

LEVEL_CUTOFFS = {"Low": 0.35, "Medium": 0.60}  # High = anything above Medium's cutoff

# Keyword classification for inj_last_type — deliberately conservative:
# anything not clearly matching either list falls into the neutral 0.5
# bucket rather than being guessed at. Matched case-insensitively as a
# substring, since inj_last_type is free-text from Transfermarkt
# (see MASTER_SPEC.md § 5, transfermarkt_injury_loader.py), not a
# controlled vocabulary.
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
    df: pd.DataFrame                     # input df + InjuryRisk* columns added
    n_players: int
    n_low_confidence: int                # players missing dob or inj_last_type
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
        # e.g. "Hamstring muscle injury" matches both lists' generic terms
        # in edge cases — soft-tissue keywords win (they were the more
        # specific/frequent match in this dataset's actual values, see
        # module verification), but flagged low-confidence since it's an
        # ambiguous match, not a clean one.
        return 1.0, True

    # Free text present but matched nothing we recognize — don't guess.
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

    # spec #4 fix: default to the fixed DECISION_DATE, not datetime.now().
    # datetime.now() here would make the age component (and therefore
    # InjuryRiskScore) silently change every day the script is re-run,
    # independent of any real change in a player's data — and could leak
    # post-decision-date information into a supposedly point-in-time score.
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