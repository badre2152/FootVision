# analytics/scoring/percentile_score.py
#
# FIX: DSS range مضغوط (0.28 بين #1 و#26) — min-max normalization تُسوّي الفوارق
#
# الحل: Exponential Stretch بدل Power Transform
#   1. rank كل لاعب بين الـ 49 → percentile smoothed
#   2. exponential stretch: (e^(γ·p) - 1) / (e^γ - 1)
#      γ=2.0 → يُوسّع range ×1.47 ويُضاعف فرق elite vs fringe ×2.4
#   3. rescale إلى [0, max_original_dss]
#
# النتيجة:
#   Bounou(#1)  vs Maamar(#20): فرق أصلي 0.123 → جديد 0.296 (×2.4)
#   الترتيب لا يتغيّر — فقط الفجوات تتوسّع
#
# استخدام:
#   - DSSScore_pct للعرض في Dashboard وتقارير المدرب
#   - DSSScore الأصلي للاختيار الداخلي (select_squad_with_position_cap)
#   - DSSScore_pct لا يُغيّر الـ top-26 — فقط يُوضّح الفوارق

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

GAMMA: float = 2.0  # exponential stretch factor — اختبر 1.5/2.0/2.5


def _exponential_stretch(percentiles: pd.Series, gamma: float) -> pd.Series:
    """
    (e^(γ·p) - 1) / (e^γ - 1)
    p=0 → 0, p=1 → 1, لكن الزيادة في الطرف العلوي أسرع.
    """
    if gamma == 0:
        return percentiles
    denom = np.exp(gamma) - 1
    return (np.exp(gamma * percentiles) - 1) / denom


def apply_percentile_scoring(
    df: pd.DataFrame,
    score_col: str = "DSSScore",
    out_col: str = "DSSScore_pct",
    gamma: float = GAMMA,
) -> pd.DataFrame:
    """
    يُطبّق exponential stretch على DSSScore.
    يُضيف عمود DSSScore_pct — لا يُغيّر DSSScore الأصلي ولا Rank.

    Args:
        df:        DataFrame يحتوي score_col.
        score_col: العمود الأصلي.
        out_col:   العمود الجديد.
        gamma:     شدة التمديد (2.0 = معتدل-قوي).
    """
    n = len(df)
    if n == 0 or score_col not in df.columns:
        logger.warning("percentile_scoring: score_col '%s' not found.", score_col)
        return df

    # ── Smoothed percentile (يتجنب 0 و 1 الحدية) ────────────────────────
    ranks_asc  = df[score_col].rank(method="average", ascending=True)
    percentiles = ranks_asc / (n + 1)

    # ── Exponential stretch ───────────────────────────────────────────────
    stretched = _exponential_stretch(percentiles, gamma)

    # ── Rescale إلى [0, max_original] ────────────────────────────────────
    orig_max   = df[score_col].max()
    df[out_col] = (stretched * orig_max).round(4)

    # ── تحقق: الترتيب يجب أن يبقى مطابقاً ───────────────────────────────
    orig_rank  = df[score_col].rank(ascending=False, method="min").astype(int)
    new_rank   = df[out_col].rank(ascending=False, method="min").astype(int)
    mismatches = (orig_rank != new_rank).sum()

    orig_range = df[score_col].max() - df[score_col].min()
    new_range  = df[out_col].max()   - df[out_col].min()
    expansion  = new_range / orig_range if orig_range > 0 else 1.0

    if mismatches > 0:
        logger.error(
            "percentile_scoring RANK MISMATCH: %d لاعبين تغيّر ترتيبهم — "
            "تحقق من gamma=%.1f",
            mismatches, gamma,
        )
    else:
        logger.info(
            "percentile_scoring ✓ | range: %.4f → %.4f (×%.2f) | gamma=%.1f",
            orig_range, new_range, expansion, gamma,
        )

    return df


def score_summary(df: pd.DataFrame, top_n: int = 26) -> dict:
    """
    إحصائيات مقارنة بين DSSScore و DSSScore_pct.
    تُعاد في squad_summary.json.
    """
    top = df.sort_values("Rank").head(top_n)

    def _range(col):
        return round(float(top[col].max() - top[col].min()), 4) if col in top.columns else None

    # فرق بين #1 و #n/2 (نصف القائمة)
    def _mid_gap(col):
        if col not in top.columns:
            return None
        half = top.iloc[top_n // 2][col]
        top1 = top.iloc[0][col]
        return round(float(top1 - half), 4)

    return {
        "dss_range_original":   _range("DSSScore"),
        "dss_range_percentile": _range("DSSScore_pct"),
        "expansion_factor":     round(
            _range("DSSScore_pct") / _range("DSSScore"), 2
        ) if _range("DSSScore") else None,
        "elite_fringe_gap_original":   _mid_gap("DSSScore"),
        "elite_fringe_gap_percentile": _mid_gap("DSSScore_pct"),
    }
