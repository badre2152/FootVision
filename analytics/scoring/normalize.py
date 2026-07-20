# analytics/scoring/normalize.py
# Silver Layer — Column Normalization to [0, 1]
#
# All Bronze columns are normalized here before scoring.
# Inverse columns (lower = better) are flipped after normalization.
# Uses bounds from config.py — never hardcoded here.

import logging
import numpy as np
import pandas as pd

from config import NORMALIZATION_BOUNDS, INVERSE_COLUMNS

logger = logging.getLogger(__name__)


def normalize_column(
    series: pd.Series,
    col_name: str,
    bounds: tuple[float, float] | None = None,
    inverse: bool = False,
) -> pd.Series:
    """
    Normalizes a single column to [0, 1] using min-max scaling.

    Args:
        series:    Raw column values (may contain NaN).
        col_name:  Column name (used to look up bounds from config).
        bounds:    Override (min, max) tuple; uses config if None.
        inverse:   If True, flip the result: 1 - normalized_value.

    Returns:
        pd.Series of float64 in [0, 1], NaN preserved for missing values.
    """
    if bounds is None:
        bounds = NORMALIZATION_BOUNDS.get(col_name)

    if bounds is None:
        logger.debug(
            "No normalization bounds for '%s' — using dynamic min/max.", col_name
        )
        col_min = series.min()
        col_max = series.max()
    else:
        col_min, col_max = bounds

    if col_max == col_min:
        logger.warning(
            "Column '%s' has zero range (min=max=%s) — returning 0.5.", col_name, col_min
        )
        return series.where(series.isna(), 0.5)

    # Clip to bounds then scale
    clipped    = series.clip(lower=col_min, upper=col_max)
    normalized = (clipped - col_min) / (col_max - col_min)

    if inverse or col_name in INVERSE_COLUMNS:
        normalized = 1.0 - normalized

    return normalized.round(6)


def normalize_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalizes all numeric columns that have entries in NORMALIZATION_BOUNDS.
    Adds normalized columns with suffix '_norm' alongside originals.

    Args:
        df: Merged Silver DataFrame (output of join_all_bronze_sources)

    Returns:
        Same DataFrame with added *_norm columns.
    """
    cols_normalized = 0
    for col_name in NORMALIZATION_BOUNDS:
        if col_name not in df.columns:
            continue
        if not pd.api.types.is_numeric_dtype(df[col_name]):
            continue

        norm_col = f"{col_name}_norm"
        df[norm_col] = normalize_column(df[col_name], col_name)
        cols_normalized += 1

    logger.info("normalize_all: %d columns normalized.", cols_normalized)
    return df
