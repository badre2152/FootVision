# analytics/scoring/performance_score.py
# Gold Layer — PerformanceScore Computation
#
# Computes weighted composite PerformanceScore from normalized Bronze columns.
# Uses weights from config.py. Missing columns are redistributed (not zeroed).
# Component-level reweighting is supported per ADR-017 (v14 fix C-08).

import logging
import numpy as np
import pandas as pd

from config import (
    OFFENSIVE_WEIGHTS,
    DEFENSIVE_WEIGHTS,
    FORM_WEIGHTS,
    COMPOSITE_WEIGHTS,
    PERFORMANCE_SUB_WEIGHTS,   # CONFLICT-02 FIX
    MENTAL_WEIGHTS,            # CONFLICT-03 FIX
)

logger = logging.getLogger(__name__)


def compute_weighted_subscore(
    row: pd.Series,
    weights: dict[str, float],
    suffix: str = "_norm",
) -> float:
    """
    Computes a weighted average from normalized columns for one player row.

    Missing columns (NaN or not present) have their weights redistributed
    proportionally among available columns — never zeroed out.

    Args:
        row:     One player row from the Silver DataFrame.
        weights: {column_name: weight} dict (weights should sum to ~1.0).
        suffix:  Suffix appended to each column name to find normalized version.

    Returns:
        Weighted subscore in [0, 1], or np.nan if no columns available.
    """
    available_weight = 0.0
    weighted_sum     = 0.0

    for col, w in weights.items():
        norm_col = f"{col}{suffix}"
        if norm_col in row.index and pd.notna(row[norm_col]):
            weighted_sum     += row[norm_col] * w
            available_weight += w

    if available_weight < 0.01:
        return np.nan

    # Rescale to compensate for missing columns
    return weighted_sum / available_weight


def compute_performance_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes PerformanceScore for all 49 players — position-aware.

    Outfield (DEF/MID/FWD):
        offensive_score = Gls/Ast/npxG/xA (OFFENSIVE_WEIGHTS)
        defensive_score = 0.5 neutral  (no Tkl/Int/Clr data — TODO next Bronze run)
        form_score      = form_final + rating_final (FORM_WEIGHTS)
        PerformanceScore = weighted(offensive×0.50, defensive×0.35, form×0.15)

    GK:
        offensive_score = 0.0  (GKs don't score goals)
        defensive_score = GA_per90_norm  (fewer goals allowed = better)
        form_score      = form_final + rating_final (FORM_WEIGHTS)
        PerformanceScore = weighted(offensive×0.0, defensive×0.70, form×0.30)
        → GK performance is 70% shot-stopping + 30% form

    Args:
        df: Silver DataFrame with *_norm columns from normalize_all()

    Returns:
        DataFrame with added columns:
            offensive_score, defensive_score, form_score, PerformanceScore
    """
    logger.info("Computing PerformanceScore for %d players (position-aware)...", len(df))

    gk_mask = df["position"] == "GK"

    # ── Offensive score ─────────────────────────────────────────────────────
    # FIX (2026-07-29): كانت هذي الدالة كتقرا مباشرة من الأعمدة *_norm
    # (محسوبة عالمياً فـ normalize_all() قبل ما نعرف الدوري ديال كل لاعب) —
    # يعني مافيهاش أي تعديل لجودة الدوري إطلاقاً. نفس التحيز اللي كان موجود
    # فـ GA/90 قبل الإصلاح الأول (Botola/Saudi/UAE... مضخَّمين artificially)
    # كان موجود هنا بنفس القوة، غير مافيهاش حل. الحل: نبني الأعمدة المُعدَّلة
    # يدوياً (raw → league-adjust → normalize) بنفس نمط _gk_defensive أسفله،
    # بدل الاعتماد على *_norm الجاهزة.
    from config import NORMALIZATION_BOUNDS
    from analytics.scoring.league_quality import adjust_gk_ga_per90, adjust_stat_for_league

    _OFFENSIVE_RAW_MAP = [
        # (مفتاح الوزن فـ OFFENSIVE_WEIGHTS, عمود Silver الخام, مفتاح NORMALIZATION_BOUNDS)
        ("Gls_per90", "Gls_per90", "Gls_per90"),
        ("Ast_per90", "Ast_per90", "Ast_per90"),
        ("xg_per90",  "xg_per90",  "xg_per90"),
        ("xa_per90",  "xa_per90",  "xa_per90"),
    ]

    def _league_adjusted_norm(row: pd.Series, raw_col: str, bounds_key: str,
                               inverse: bool = False) -> float:
        """raw → league-adjust → clip/normalize، يُعيد NaN إذا القيمة الخام مفقودة."""
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

    _GK_DEF_MEDIAN = 0.55  # median تجريبي من run حقيقي

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

        # FIX (2026-07-29): تعديل جودة الدوري — نفس منطق GA/90 للحراس، لكن
        # هنا الاتجاه "الأعلى أحسن" (تدخل/اعتراض أكثر = أفضل)، فـ دوري أضعف
        # كيضخّم الرقم artificially (فرص دفاعية أسهل/ضغط أقل) → نُنقّصه
        # (نضرب × factor، factor<1) قبل التطبيع، بدل ما نطبّعو الرقم الخام
        # مباشرة كيما كان يصير قبل هذا الإصلاح.
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

    # Form: same formula for all positions
    df["form_score"] = df.apply(
        lambda r: compute_weighted_subscore(r, FORM_WEIGHTS), axis=1
    )

    # GK-specific weights: GA_per90 (inverse) + intl_caps as quality signal
    # Problem: Botola GKs have low GA/90 not because they're better but
    # because they face weaker opposition. intl_caps corrects this bias.
    gk_perf_weights = {
        "offensive_score": 0.00,   # GKs don't score
        "defensive_score": 0.50,   # GA_per90 — still primary metric
        "form_score":      0.30,   # recent form
        "intl_score":      0.20,   # intl_caps_norm — quality/experience signal
    }

    def _perf(row: pd.Series) -> float:
        if row.get("position") == "GK":
            # Add intl_caps as a 4th component for GKs
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
    """
    Computes FormScore from COALESCE rating columns (49/49 coverage).

    CONFLICT-01 FIX: uses form_final + rating_final instead of sofascore_* directly.
    Silver guarantees form_final and rating_final are non-null for all 49 players.

    Args:
        df: Silver DataFrame with form_final_norm + rating_final_norm columns.

    Returns:
        DataFrame with added FormScore column.
    """
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
    """
    Computes MentalScore من مصدرين بـ COALESCE:

    المصدر 1 — StatsBomb (أولوية عالية):
        pressures_per90 + passes_per90 + carries_per90
        Coverage: 11/49 — WC/AFCON matches only

    المصدر 2 — SoFIFA (fallback):
        composure_norm × 0.50 + reactions_norm × 0.35 + aggression_norm × 0.15
        Coverage: 49/49 — كل لاعب في FIFA database

    الـ COALESCE:
        إذا StatsBomb متاح → يُستخدم (أكثر موثوقية = بيانات حقيقية)
        إذا لا → SoFIFA (proxy جيد لكن تقديرات لعبة)
        إذا لا → position-group median (كما كان سابقاً)

    النتيجة: MentalScore 49/49 بدل 11/49 مع imputation للباقي.

    GK vs Outfield split:
        Outfield: pressures (work rate) + passes (composure) + carries (decisions)
        GKs:      passes (distribution) + carries (ball-playing) — pressures غير مفيد
        SoFIFA GK: gk_reflexes_norm بدل aggression

    Args:
        df: Silver DataFrame مع sb_*_norm و sofifa_*_norm columns

    Returns:
        DataFrame مع MentalScore + mental_source columns
    """
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
        """
        MentalScore من SoFIFA attributes مع discount factor.
        SOFIFA_DISCOUNT = 0.70: FIFA ratings تقديرات لعبة وليست إحصاء حقيقي.
        يُطبَّق كـ: sofifa_score × 0.70 + position_median × 0.30
        يُعطي وزناً أكبر للبيانات الحقيقية حين تتوفر (StatsBomb يتجاوزه كلياً).
        """
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

        # discount: blend مع position median لتخفيف التأثير المبالغ فيه
        pos = row.get("position", "MID")
        median = POS_MEDIAN.get(pos, 0.4007)
        discounted = round(raw_score * SOFIFA_DISCOUNT + median * (1 - SOFIFA_DISCOUNT), 4)
        return discounted

    def _mental(row: pd.Series) -> tuple[float, str]:
        """يُعيد (score, source)."""
        # ── Source 1: StatsBomb ──────────────────────────────────────────
        # FIX (2026-07-29): compute_weighted_subscore() تقرا من الأعمدة *_norm،
        # اللي راه Silver عوّضتها بالmedian للاعبين اللي ماعندهمش بيانات حقيقية
        # (impute_by_position). يعني sb_score كان دايماً non-NaN، وكان الكود
        # يصنّف الكل "statsbomb" — حتى الـ38/49 المُعوَّضين. نستعمل دابا
        # sb_mental_data_real (مُسجَّل فـ Silver *قبل* أي imputation) كبوابة
        # صادقة: لاعب بلا بيانات حقيقية ما يتصنّفش "statsbomb" حتى لو الرقم
        # موجود (لأنه فـ الحقيقة رقم الmedian، ماشي بياناته).
        has_real_sb_data = bool(row.get("sb_mental_data_real", False))

        if has_real_sb_data:
            if row.get("position") == "GK":
                sb_score = compute_weighted_subscore(row, gk_mental_weights)
            else:
                sb_score = compute_weighted_subscore(row, MENTAL_WEIGHTS)

            if sb_score is not None and not (isinstance(sb_score, float) and sb_score != sb_score):
                return sb_score, "statsbomb"

        # ── Source 2: SoFIFA ─────────────────────────────────────────────
        sof_score = _sofifa_mental(row)
        if sof_score is not None:
            return sof_score, "sofifa"

        # ── Source 3: median fallback (يُطبَّق في Silver imputation) ─────
        return float("nan"), "imputed"

    results = df.apply(_mental, axis=1)
    df["MentalScore"]  = results.apply(lambda x: x[0]).round(4)
    df["mental_source"] = results.apply(lambda x: x[1])

    # إحصائيات
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
    """
    Computes AvailabilityScore from injury history columns.

    BUG FIX: reads RAW inj_* columns (not _norm) to avoid double inversion.
    normalize_all() already inverts inj_days_missed_2y and inj_count_2y
    (both are in INVERSE_COLUMNS). If we used the _norm columns and did
    (1 - norm) again, the result would be inverted: high injury = high score.

    Formula (applied on raw values, then scaled to [0,1]):
        days_pct   = inj_days_missed_2y / 365  → 0=clean, 1=full year out
        count_pct  = inj_count_2y / 10         → 0=clean, 1=10 injuries
        base       = 1 - days_pct              → 1=fully available
        penalty    = count_pct * 0.30          → frequency penalty
        AvailabilityScore = max(0, base - penalty)

    Args:
        df: Silver DataFrame (raw columns must be present).

    Returns:
        DataFrame with added AvailabilityScore column.
    """
    def _avail(row: pd.Series) -> float:
        # Use RAW columns, not _norm (avoid double inversion)
        days  = row.get("inj_days_missed_2y", np.nan)
        count = row.get("inj_count_2y", np.nan)

        if pd.isna(days):
            return 0.5  # neutral when no injury data

        # Clip to bounds from config (0–365 days, 0–10 injuries)
        days_pct  = min(float(days),  365.0) / 365.0
        count_pct = (min(float(count), 10.0) / 10.0) if pd.notna(count) else 0.0

        base    = 1.0 - days_pct   # 0=full year injured, 1=injury-free
        penalty = count_pct * 0.30  # frequency of injuries
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
    """
    Computes InternationalScore from FBref international columns.
    Used as a proxy for TacticalFitScore at national team level.

    Args:
        df: Silver DataFrame with intl_*_norm columns.

    Returns:
        DataFrame with added InternationalScore column.
    """
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
    """
    Computes the final DSS composite score for each player.

    Composite = weighted average of (config.py COMPOSITE_WEIGHTS, 2026-07-14 rebalance):
        PerformanceScore    0.29
        InternationalScore  0.21  (proxy for TacticalFitScore)
        FormScore           0.17
        MentalScore         0.08
        AvailabilityScore   0.09
        ChemistryScore      0.08  (NEW — pairwise StatsBomb pass network)
        PressureIndex       0.08  (NEW — StatsBomb intl xG vs club xG delta)

    Args:
        df: Silver DataFrame with all sub-scores computed (including
            ChemistryScore and PressureIndex — run compute_chemistry_score()
            and compute_pressure_index() before calling this).

    Returns:
        DataFrame with added DSSScore and Rank columns.
    """
    # CONFLICT-01 FIX: form_score replaced by FormScore (uses form_final + rating_final)
    # CONFLICT-03 FIX: MentalScore now real — no longer absorbed proportionally
    # 2026-07-14: added ChemistryScore + PressureIndex (rebuilt — see AUDIT session notes)
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