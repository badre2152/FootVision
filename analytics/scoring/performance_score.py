import logging
import numpy as np
import pandas as pd

from config import (
    OFFENSIVE_WEIGHTS,
    DEFENSIVE_WEIGHTS,
    FORM_WEIGHTS,
    COMPOSITE_WEIGHTS,
    PERFORMANCE_SUB_WEIGHTS,
    MENTAL_WEIGHTS,
)

logger = logging.getLogger(__name__)


def compute_weighted_subscore(
    row: pd.Series,
    weights: dict[str, float],
    suffix: str = "_norm",
) -> float:
    available_weight = 0.0
    weighted_sum     = 0.0

    for col, w in weights.items():
        norm_col = f"{col}{suffix}"
        if norm_col in row.index and pd.notna(row[norm_col]):
            weighted_sum     += row[norm_col] * w
            available_weight += w

    if available_weight < 0.01:
        return np.nan

    return weighted_sum / available_weight


def compute_performance_score(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Computing PerformanceScore for %d players (position-aware)...", len(df))

    gk_mask = df["position"] == "GK"

    from config import NORMALIZATION_BOUNDS
    from analytics.scoring.league_quality import adjust_gk_ga_per90, adjust_stat_for_league

    _OFFENSIVE_RAW_MAP = [
        ("Gls_per90", "Gls_per90", "Gls_per90"),
        ("Ast_per90", "Ast_per90", "Ast_per90"),
        ("xg_per90",  "xg_per90",  "xg_per90"),
        ("xa_per90",  "xa_per90",  "xa_per90"),
    ]

    def _league_adjusted_norm(row: pd.Series, raw_col: str, bounds_key: str,
                               inverse: bool = False) -> float:
        raw = row.get(raw_col)
        if raw is None or pd.isna(raw):
            return np.nan
        adjusted = adjust_stat_for_league(float(raw), row.get("league"), inverse=inverse)
        mn, mx = NORMALIZATION_BOUNDS.get(bounds_key, (0.0, 1.0))
        clipped = max(mn, min(mx, adjusted))
        return round((clipped - mn) / (mx - mn), 4) if mx > mn else 0.5

    def _outfield_offensive(row: pd.Series) -> float:
        adj = {
            f"{wk}_lgadj": _league_adjusted_norm(row, raw_col, bounds_key)
            for wk, raw_col, bounds_key in _OFFENSIVE_RAW_MAP
        }
        return compute_weighted_subscore(pd.Series(adj), OFFENSIVE_WEIGHTS, suffix="_lgadj")

    df["offensive_score"] = df.apply(
        lambda r: 0.0 if r.get("position") == "GK"
        else _outfield_offensive(r),
        axis=1,
    )

    _GK_DEF_MEDIAN = 0.55

    def _gk_defensive(row: pd.Series) -> float:
        raw_ga = row.get("GA_per90", np.nan)
        if row.get("ga_imputed_flag", False) or pd.isna(raw_ga):
            return _GK_DEF_MEDIAN
        adjusted = adjust_gk_ga_per90(float(raw_ga), row.get("league"))
        ga_min, ga_max = NORMALIZATION_BOUNDS.get("GA_per90", (0.0, 3.0))
        clipped = max(ga_min, min(ga_max, adjusted))
        norm = (clipped - ga_min) / (ga_max - ga_min) if ga_max > ga_min else 0.0
        return round(1.0 - norm, 4)

    def _outfield_defensive(row: pd.Series) -> float:
        FLOOR = 0.30

        def _first_nonzero(*cols):
            vals = [row.get(c) for c in cols]
            real = [float(v) for v in vals if v is not None and not pd.isna(v)]
            nonzero = [v for v in real if v > 0]
            if nonzero:
                return nonzero[0]
            return (0.0 if real else None)

        tkl = _first_nonzero("sd_tkl_per90", "fm_tkl_per90")
        int_ = _first_nonzero("sd_int_per90", "fm_int_per90")
        clr = _first_nonzero("sd_clr_per90", "fm_clr_per90")

        if tkl is None and int_ is None and clr is None:
            return FLOOR

        league = row.get("league")
        tkl = adjust_stat_for_league(tkl, league) if tkl is not None else None
        int_ = adjust_stat_for_league(int_, league) if int_ is not None else None
        clr = adjust_stat_for_league(clr, league) if clr is not None else None

        def _n(v, col):
            if v is None:
                return None
            mn, mx = NORMALIZATION_BOUNDS.get(col, (0.0, 10.0))
            return max(0.0, min(1.0, (v - mn) / (mx - mn))) if mx > mn else 0.5

        tw, tv = 0.0, 0.0
        for v, w, col in [
            (tkl,  0.35, "Tkl_per90"),
            (int_, 0.30, "Int_per90"),
            (clr,  0.10, "Clr_per90"),
        ]:
            n = _n(v, col)
            if n is not None:
                tv += n * w
                tw += w

        return round(max(FLOOR, tv / tw), 4) if tw > 0 else FLOOR

    df["defensive_score"] = df.apply(
        lambda r: _gk_defensive(r)
        if r.get("position") == "GK"
        else _outfield_defensive(r),
        axis=1,
    )

    df["form_score"] = df.apply(
        lambda r: compute_weighted_subscore(r, FORM_WEIGHTS), axis=1
    )

    gk_perf_weights = {
        "offensive_score": 0.00,
        "defensive_score": 0.50,
        "form_score":      0.30,
        "intl_score":      0.20,
    }

    def _perf(row: pd.Series) -> float:
        if row.get("position") == "GK":
            intl_norm = row.get("intl_caps_norm", 0.0) or 0.0
            gk_scores = {
                "offensive_score": row.get("offensive_score", 0.0),
                "defensive_score": row.get("defensive_score", 0.0),
                "form_score":      row.get("form_score", 0.0),
                "intl_score":      intl_norm,
            }
            total_w = sum(gk_perf_weights.values())
            score = sum(
                gk_scores.get(k, 0.0) * w
                for k, w in gk_perf_weights.items()
                if gk_scores.get(k) is not None
            )
            return score / total_w if total_w > 0 else 0.0
        return compute_weighted_subscore(row, PERFORMANCE_SUB_WEIGHTS, suffix="")

    df["PerformanceScore"] = df.apply(_perf, axis=1)

    for col in ["offensive_score", "defensive_score", "form_score", "PerformanceScore"]:
        df[col] = df[col].round(4)

    logger.info(
        "PerformanceScore — mean: %.3f | GK mean: %.3f | Outfield mean: %.3f",
        df["PerformanceScore"].mean(),
        df.loc[gk_mask, "PerformanceScore"].mean(),
        df.loc[~gk_mask, "PerformanceScore"].mean(),
    )

    return df


def compute_form_score(df: pd.DataFrame) -> pd.DataFrame:
    df["FormScore"] = df.apply(
        lambda r: compute_weighted_subscore(r, FORM_WEIGHTS), axis=1
    ).round(4)

    logger.info(
        "FormScore computed — coverage: %d/49, mean: %.3f",
        df["FormScore"].notna().sum(),
        df["FormScore"].mean(),
    )

    return df


def compute_mental_score(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Computing MentalScore for %d players (SoFIFA COALESCE)...", len(df))

    if "sb_mental_data_real" not in df.columns:
        logger.error(
            "sb_mental_data_real column missing — Silver parquet was built with "
            "an OLDER version of silver_pipeline.py (before the 2026-07-29 "
            "provenance fix). mental_source will be UNRELIABLE for this run "
            "(will not correctly distinguish real StatsBomb data from "
            "position-median imputed values). Re-run run_silver.py with the "
            "current code before trusting mental_source in this output."
        )

    gk_mental_weights = {
        "sb_passes_per90":  0.60,
        "sb_carries_per90": 0.40,
    }

    def _sofifa_mental(row: pd.Series) -> float | None:
        SOFIFA_DISCOUNT = 0.70
        POS_MEDIAN = {"GK": 0.3192, "DEF": 0.3755, "MID": 0.4007, "FWD": 0.4007}

        if row.get("position") == "GK":
            vals = [
                (row.get("sofifa_composure_norm"),  0.50),
                (row.get("sofifa_reactions_norm"),   0.35),
                (row.get("sofifa_gk_reflexes_norm"), 0.15),
            ]
        else:
            vals = [
                (row.get("sofifa_composure_norm"),   0.50),
                (row.get("sofifa_reactions_norm"),   0.35),
                (row.get("sofifa_aggression_norm"),  0.15),
            ]

        available = [
            (v, w) for v, w in vals
            if v is not None and not (isinstance(v, float) and v != v)
        ]
        if not available:
            return None

        tw = sum(w for _, w in available)
        tv = sum(v * w for v, w in available)
        raw_score = tv / tw if tw > 0 else None
        if raw_score is None:
            return None

        pos = row.get("position", "MID")
        median = POS_MEDIAN.get(pos, 0.4007)
        discounted = round(raw_score * SOFIFA_DISCOUNT + median * (1 - SOFIFA_DISCOUNT), 4)
        return discounted

    def _mental(row: pd.Series) -> tuple[float, str]:
        has_real_sb_data = bool(row.get("sb_mental_data_real", False))

        if has_real_sb_data:
            if row.get("position") == "GK":
                sb_score = compute_weighted_subscore(row, gk_mental_weights)
            else:
                sb_score = compute_weighted_subscore(row, MENTAL_WEIGHTS)

            if sb_score is not None and not (isinstance(sb_score, float) and sb_score != sb_score):
                return sb_score, "statsbomb"

        sof_score = _sofifa_mental(row)
        if sof_score is not None:
            return sof_score, "sofifa"

        return float("nan"), "imputed"

    results = df.apply(_mental, axis=1)
    df["MentalScore"]  = results.apply(lambda x: x[0]).round(4)
    df["mental_source"] = results.apply(lambda x: x[1])

    src_counts = df["mental_source"].value_counts().to_dict()
    real_count = (df["mental_source"] != "imputed").sum()
    logger.info(
        "MentalScore — %d/49 from real data | sources: %s | mean=%.3f",
        real_count,
        {k: v for k, v in src_counts.items()},
        df["MentalScore"].mean(),
    )

    if src_counts.get("sofifa", 0) > 0:
        logger.info(
            "SoFIFA mental coverage: %d players — composure/reactions/aggression",
            src_counts["sofifa"],
        )

    return df


def compute_availability_score(df: pd.DataFrame) -> pd.DataFrame:
    def _avail(row: pd.Series) -> float:
        days  = row.get("inj_days_missed_2y", np.nan)
        count = row.get("inj_count_2y", np.nan)

        if pd.isna(days):
            return 0.5

        days_pct  = min(float(days),  365.0) / 365.0
        count_pct = (min(float(count), 10.0) / 10.0) if pd.notna(count) else 0.0

        base    = 1.0 - days_pct
        penalty = count_pct * 0.30
        return float(max(0.0, min(1.0, base - penalty)))

    df["AvailabilityScore"] = df.apply(_avail, axis=1).round(4)

    logger.info(
        "AvailabilityScore — mean: %.3f | fully available (=1.0): %d | zero-risk: %d",
        df["AvailabilityScore"].mean(),
        (df["AvailabilityScore"] == 1.0).sum(),
        (df["AvailabilityScore"] == 0.0).sum(),
    )

    return df


def compute_international_score(df: pd.DataFrame) -> pd.DataFrame:
    intl_weights = {
        "intl_caps":        0.40,
        "intl_goals_per90": 0.30,
        "intl_ast_per90":   0.30,
    }

    df["InternationalScore"] = df.apply(
        lambda r: compute_weighted_subscore(r, intl_weights), axis=1
    ).round(4)

    return df


def compute_final_score(df: pd.DataFrame) -> pd.DataFrame:
    composite_weights = {
        "PerformanceScore":   COMPOSITE_WEIGHTS["PerformanceScore"],
        "InternationalScore": COMPOSITE_WEIGHTS["TacticalFitScore"],
        "FormScore":          COMPOSITE_WEIGHTS["FormScore"],
        "MentalScore":        COMPOSITE_WEIGHTS["MentalScore"],
        "AvailabilityScore":  COMPOSITE_WEIGHTS["AvailabilityScore"],
        "ChemistryScore":     COMPOSITE_WEIGHTS["ChemistryScore"],
        "PressureIndex":      COMPOSITE_WEIGHTS["PressureIndex"],
        "FlexibilityScore":   COMPOSITE_WEIGHTS["FlexibilityScore"],
        "SentimentScore":     COMPOSITE_WEIGHTS["SentimentScore"],
    }

    df["DSSScore"] = df.apply(
        lambda r: compute_weighted_subscore(r, composite_weights, suffix=""),
        axis=1,
    ).round(4)

    df["Rank"] = df["DSSScore"].rank(ascending=False, method="min").astype(int)

    logger.info(
        "DSSScore computed — top player: %s (%.4f)",
        df.loc[df["Rank"] == 1, "player_name"].values[0]
        if "player_name" in df.columns else "unknown",
        df["DSSScore"].max(),
    )

    return df