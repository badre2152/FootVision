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

    clipped    = series.clip(lower=col_min, upper=col_max)
    normalized = (clipped - col_min) / (col_max - col_min)

    if inverse or col_name in INVERSE_COLUMNS:
        normalized = 1.0 - normalized

    return normalized.round(6)


def normalize_all(df: pd.DataFrame) -> pd.DataFrame:
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