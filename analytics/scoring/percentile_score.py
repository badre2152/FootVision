import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

GAMMA: float = 2.0


def _exponential_stretch(percentiles: pd.Series, gamma: float) -> pd.Series:
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
    n = len(df)
    if n == 0 or score_col not in df.columns:
        logger.warning("percentile_scoring: score_col '%s' not found.", score_col)
        return df

    ranks_asc  = df[score_col].rank(method="average", ascending=True)
    percentiles = ranks_asc / (n + 1)

    stretched = _exponential_stretch(percentiles, gamma)

    orig_max   = df[score_col].max()
    df[out_col] = (stretched * orig_max).round(4)

    orig_rank  = df[score_col].rank(ascending=False, method="min").astype(int)
    new_rank   = df[out_col].rank(ascending=False, method="min").astype(int)
    mismatches = (orig_rank != new_rank).sum()

    orig_range = df[score_col].max() - df[score_col].min()
    new_range  = df[out_col].max()   - df[out_col].min()
    expansion  = new_range / orig_range if orig_range > 0 else 1.0

    if mismatches > 0:
        logger.error(
            "percentile_scoring RANK MISMATCH: %d players changed rank — "
            "check gamma=%.1f",
            mismatches, gamma,
        )
    else:
        logger.info(
            "percentile_scoring | range: %.4f → %.4f (×%.2f) | gamma=%.1f",
            orig_range, new_range, expansion, gamma,
        )

    return df


def score_summary(df: pd.DataFrame, top_n: int = 26) -> dict:
    top = df.sort_values("Rank").head(top_n)
    if top.empty:
        return {
            "dss_range_original": None,
            "dss_range_percentile": None,
            "expansion_factor": None,
            "elite_fringe_gap_original": None,
            "elite_fringe_gap_percentile": None,
        }

    def _range(col):
        return round(float(top[col].max() - top[col].min()), 4) if col in top.columns else None

    def _mid_gap(col):
        if col not in top.columns:
            return None
        half = top.iloc[len(top) // 2][col]
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