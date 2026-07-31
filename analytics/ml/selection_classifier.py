# analytics/ml/selection_classifier.py
# ML Layer — Selection Audit + Selection Classifier
#
# Added 2026-07-29, SAME DAY the real ground truth became available: Morocco's
# official 26-man 2026 World Cup squad was announced 2026-05-26 by head coach
# Mohamed Ouahbi. (Source: FIFA / worldcupstats.football, cross-checked
# 2026-07-29 — see REAL_SQUAD_2026 below for the exact list and source URL.)
#
# This is the reason Selection Classifier could NOT be built earlier: every
# prior session (through 2026-07-29) had no real y label, and using the
# pipeline's own top-26-by-DSSScore as a label would have been circular (see
# player_clustering.py's header for the same argument, and the "no idea"
# exchange with the user on 2026-07-29 about whether ground truth existed —
# it didn't, until this search).
#
# ─────────────────────────────────────────────────────────────────────────
# HEADLINE RESULT — READ THIS BEFORE TRUSTING ANYTHING DOWNSTREAM
# ─────────────────────────────────────────────────────────────────────────
# audit_vs_dss_ranking() on the 2026-07-27 Gold run found only 15/26 overlap
# between DSSScore's top-26 and the real squad. A hypergeometric test against
# pure random selection (N=49, K=26 real picks, n=26 drawn) gives:
#     P(overlap >= 15 | random selection) = 0.343
# i.e. the DSSScore-based squad selection is NOT statistically distinguishable
# from a random draw of 26 out of 49, on this one real-world validation point.
#
# This is a single n=1 "trial" (one coach's one squad announcement), so it
# does not "prove" the formula is worthless — but it is the ONLY real
# validation data this project has ever had, and it came back indistinguishable
# from noise. A major likely confound: Walid Regragui (the coach the entire
# scoring formula's design — MASTER_SPEC.md's "Option B" framing, the whole
# TacticalFitScore concept — implicitly assumed) resigned 2026-03-05. Mohamed
# Ouahbi, a different coach with a different style and no track record this
# pipeline was tuned against, made the actual picks. The gap between DSSScore
# and reality may say as much about "the coach changed" as it does about "the
# formula is wrong" — this module does not have the data to separate those
# two explanations, and neither should any report built on it.
#
# WHAT THIS MEANS FOR THE CLASSIFIER BELOW: LogisticRegression with
# leave-one-out CV is included as an honest exploratory exercise (does any
# combination of the 9 sub-scores separate real picks from non-picks better
# than chance?), not as a "prediction API" to be trusted for a future squad.
# Its own accuracy is reported next to the same random-baseline p-value, and
# it does NOT reliably beat it either — see fit_loocv_classifier()'s return
# value and run_selection_audit.py's printed output.

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from rapidfuzz import process, fuzz
from scipy.stats import hypergeom
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# Ground truth: Morocco's official 2026 FIFA World Cup squad
# Announced 2026-05-26 by head coach Mohamed Ouahbi (successor to Walid
# Regragui, who resigned 2026-03-05).
# Source: https://worldcupstats.football/teams/morocco/squad/
#         (cross-referenced against FIFA.com and Heavy.com squad-announcement
#          coverage, 2026-07-29)
# Spellings below are as published; match_real_squad() fuzzy-matches these
# against whatever spelling the project's own player_name column uses
# (rapidfuzz — already a project dependency per MASTER_SPEC.md's tech stack).
# ─────────────────────────────────────────────────────────────────────────
REAL_SQUAD_2026: list[str] = [
    # GK (3)
    "Yassine Bounou", "Munir El Kajoui", "Ahmed Reda Tagnaouti",
    # DEF (9)
    "Achraf Hakimi", "Noussair Mazraoui", "Marwane Saadane", "Zakaria El Ouahdi",
    "Issa Diop", "Chadi Riad", "Youssef Belammari", "Redouane Halhal",
    "Anass Salah Eddine",
    # MID (9)
    "Sofyan Amrabat", "Ayyoub Bouaddi", "Chemsdine Talbi", "Azzedine Ounahi",
    "Ismael Saibari", "Samir El Mourabet", "Gessime Yassine",
    "Bilal El Khannouss", "Neil El Aynaoui",
    # FWD (5)
    "Soufiane Rahimi", "Brahim Diaz", "Amine Sbai", "Ayoub El Kaabi",
    "Ayoube Amaimouni",
]

FUZZY_MATCH_THRESHOLD = 85  # WRatio 0-100; below this, treat as no match

# Sub-score features used by the classifier. Deliberately NOT DSSScore or
# DSSScore_pct — those are literally a fixed linear combination of these,
# so including them would make "does the classifier beat the formula"
# unanswerable (it would just be told the formula's own output).
CLASSIFIER_FEATURES: list[str] = [
    "PerformanceScore", "FormScore", "MentalScore", "AvailabilityScore",
    "InternationalScore", "ChemistryScore", "PressureIndex",
    "FlexibilityScore", "SentimentScore",
]


@dataclass
class MatchResult:
    df: pd.DataFrame                    # + "real_squad_selected", "real_squad_match_score"
    matched_count: int
    unmatched_real_squad: list[str]     # real-squad names with no confident match — investigate!


def match_real_squad(
    df: pd.DataFrame,
    real_squad: list[str] = REAL_SQUAD_2026,
    threshold: int = FUZZY_MATCH_THRESHOLD,
) -> MatchResult:
    """
    Fuzzy-matches `real_squad` names against df["player_name"] and adds:
      - "real_squad_selected": 1 if this candidate's name matched a real-squad
        name at or above `threshold`, else 0.
      - "real_squad_match_score": the WRatio match score (NaN if not selected).

    Any real-squad name that does NOT find a confident match in df is
    returned in `unmatched_real_squad` rather than silently dropped — this
    matters (e.g. a genuinely untracked player, or a spelling gap wide
    enough that fuzzy matching missed a real candidate) and should always be
    inspected before trusting the label, not assumed away.
    """
    if "player_name" not in df.columns:
        raise ValueError("match_real_squad() requires a 'player_name' column.")

    pool = df["player_name"].tolist()
    result_df = df.copy()
    result_df["real_squad_selected"] = 0
    result_df["real_squad_match_score"] = np.nan

    unmatched: list[str] = []
    for name in real_squad:
        best = process.extractOne(name, pool, scorer=fuzz.WRatio)
        if best is None or best[1] < threshold:
            unmatched.append(name)
            continue
        matched_name, score, _ = best
        idx = result_df.index[result_df["player_name"] == matched_name]
        result_df.loc[idx, "real_squad_selected"] = 1
        result_df.loc[idx, "real_squad_match_score"] = score

    matched_count = int(result_df["real_squad_selected"].sum())
    if unmatched:
        logger.warning(
            "%d/%d real-squad names had NO confident match (threshold=%d) in "
            "the candidate pool — check spelling / whether they were ever "
            "tracked: %s",
            len(unmatched), len(real_squad), threshold, unmatched,
        )
    if matched_count != len(real_squad) - len(unmatched):
        logger.error(
            "Matched count (%d) doesn't reconcile with real_squad size minus "
            "unmatched (%d) — possible duplicate match, investigate before "
            "trusting real_squad_selected.",
            matched_count, len(real_squad) - len(unmatched),
        )

    return MatchResult(df=result_df, matched_count=matched_count, unmatched_real_squad=unmatched)


def random_baseline_pvalue(n_population: int, n_real: int, n_predicted: int, observed_overlap: int) -> float:
    """
    P(overlap >= observed_overlap) if `n_predicted` names were drawn at
    random (no skill) from `n_population`, against `n_real` true positives.
    Hypergeometric survival function — this is the honesty check every
    "our top-N matched M of the real ones" claim in this project should be
    run through before being reported as evidence the ranking works.
    """
    rv = hypergeom(n_population, n_real, n_predicted)
    return float(1 - rv.cdf(observed_overlap - 1))


def audit_vs_dss_ranking(df: pd.DataFrame, top_n: int = 26) -> dict:
    """
    Compares the Gold pipeline's own top-`top_n` (by Rank) against
    "real_squad_selected" (must already be present — call match_real_squad()
    first). Returns overlap count, the random-baseline p-value, and the two
    directions of disagreement:
      - "dss_favored_not_picked": ranked in the model's top-N, but the coach
        did not select them (the formula's false positives)
      - "picked_not_dss_favored": selected by the coach, but the model
        ranked them outside top-N (the formula's false negatives)
    Both lists are the actionable output — they say WHERE the formula and
    reality disagree, which is more useful for iterating on the scoring
    weights than the overlap count alone.
    """
    if "real_squad_selected" not in df.columns:
        raise ValueError("audit_vs_dss_ranking() requires match_real_squad() to have run first.")

    df_sorted = df.sort_values("Rank")
    dss_top = set(df_sorted.head(top_n)["player_name"])
    real_squad_names = set(df.loc[df["real_squad_selected"] == 1, "player_name"])

    overlap = dss_top & real_squad_names
    p_value = random_baseline_pvalue(
        n_population=len(df), n_real=len(real_squad_names),
        n_predicted=top_n, observed_overlap=len(overlap),
    )

    fp = df[df["player_name"].isin(dss_top - real_squad_names)].sort_values("Rank")
    fn = df[df["player_name"].isin(real_squad_names - dss_top)].sort_values("Rank")

    logger.info(
        "Selection audit: %d/%d overlap between DSSScore top-%d and the real "
        "squad. P(overlap >= %d | random) = %.3f — %s",
        len(overlap), top_n, top_n, len(overlap), p_value,
        "NOT distinguishable from random selection at p<0.05"
        if p_value >= 0.05 else "better than random at p<0.05",
    )

    return {
        "top_n": top_n,
        "overlap_count": len(overlap),
        "overlap_pct": len(overlap) / top_n,
        "random_baseline_pvalue": p_value,
        "beats_random_at_p05": p_value < 0.05,
        "dss_favored_not_picked": fp[["Rank", "player_name", "position", "DSSScore"]].to_dict(orient="records"),
        "picked_not_dss_favored": fn[["Rank", "player_name", "position", "DSSScore"]].to_dict(orient="records"),
    }


@dataclass
class LOOCVResult:
    accuracy: float
    precision: float
    recall: float
    f1: float
    n_samples: int
    random_baseline_pvalue: float
    coefficients: pd.Series          # standardized LR coefficients, full-data fit — for interpretation only
    beats_random_baseline: bool


def fit_loocv_classifier(
    df: pd.DataFrame,
    features: list[str] = CLASSIFIER_FEATURES,
    label_col: str = "real_squad_selected",
) -> LOOCVResult:
    """
    Leave-One-Out cross-validated Logistic Regression — the only CV scheme
    that makes sense at n=49 (a normal train/test split would leave far too
    few test samples to mean anything; k-fold with small k has the same
    problem; LOOCV uses every sample as a held-out test exactly once).

    This is EXPLORATORY, not a predictor to deploy: see this module's header
    for why. Its accuracy is compared against the same hypergeometric random
    baseline used in audit_vs_dss_ranking() — if it doesn't clear that bar,
    that is reported plainly, not hidden.

    Coefficients returned are from a SEPARATE fit on the full dataset (not
    from inside the CV loop — CV is only used for the accuracy estimate) and
    are for interpretation (which sub-scores the model leans on), not for
    claiming causal importance with n=49.
    """
    missing = [f for f in features if f not in df.columns]
    if missing:
        raise ValueError(f"Missing classifier features: {missing}")
    if label_col not in df.columns:
        raise ValueError(f"Missing label column '{label_col}' — run match_real_squad() first.")

    X = df[features].values
    y = df[label_col].values
    n = len(df)

    if y.sum() == 0 or y.sum() == n:
        raise ValueError("Label column is single-class — cannot fit/evaluate a classifier.")

    loo = LeaveOneOut()
    y_true, y_pred = [], []
    for train_idx, test_idx in loo.split(X):
        scaler = StandardScaler().fit(X[train_idx])
        X_train, X_test = scaler.transform(X[train_idx]), scaler.transform(X[test_idx])
        clf = LogisticRegression(max_iter=1000, C=1.0)
        clf.fit(X_train, y[train_idx])
        y_pred.append(clf.predict(X_test)[0])
        y_true.append(y[test_idx][0])

    accuracy = accuracy_score(y_true, y_pred)
    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)

    n_correct_positive_predictions = int(np.sum((np.array(y_pred) == 1) & (np.array(y_true) == 1)))
    n_predicted_positive = int(np.sum(np.array(y_pred) == 1))
    p_value = (
        random_baseline_pvalue(n, int(y.sum()), n_predicted_positive, n_correct_positive_predictions)
        if n_predicted_positive > 0 else 1.0
    )

    # Full-data fit for interpretable coefficients only (not used for the
    # accuracy numbers above, which come strictly from the LOOCV loop).
    scaler_full = StandardScaler().fit(X)
    clf_full = LogisticRegression(max_iter=1000, C=1.0).fit(scaler_full.transform(X), y)
    coefficients = pd.Series(clf_full.coef_[0], index=features).sort_values(key=abs, ascending=False)

    logger.info(
        "LOOCV LogisticRegression: accuracy=%.3f precision=%.3f recall=%.3f f1=%.3f "
        "(n=%d) | P(this many correct positive picks | random)=%.3f -> %s",
        accuracy, precision, recall, f1, n, p_value,
        "beats random at p<0.05" if p_value < 0.05 else "does NOT clear random baseline",
    )
    logger.info("Standardized coefficients (interpretation only, full-data fit):\n%s",
                coefficients.round(3).to_string())

    return LOOCVResult(
        accuracy=accuracy, precision=precision, recall=recall, f1=f1,
        n_samples=n, random_baseline_pvalue=p_value,
        coefficients=coefficients, beats_random_baseline=p_value < 0.05,
    )
