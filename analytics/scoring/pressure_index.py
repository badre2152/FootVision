import logging
import numpy as np
import pandas as pd

from analytics.scoring.normalize import normalize_column

logger = logging.getLogger(__name__)


def _impute_pressure_delta(df: pd.DataFrame) -> pd.Series:
    delta = df["pressure_delta"].copy()
    if "position" not in df.columns:
        logger.error("No 'position' column — cannot group-impute pressure_delta.")
        return delta

    global_median = delta.median()

    for pos in df["position"].unique():
        pos_mask = df["position"] == pos
        null_before = delta[pos_mask].isna().sum()
        if null_before == 0:
            continue

        group_median = delta[pos_mask].median()
        if pd.isna(group_median):
            if pd.isna(global_median):
                logger.warning(
                    "pressure_delta: no real data ANYWHERE (0 players) — "
                    "leaving NaN, ChemistryScore-style neutral fallback unavailable."
                )
                continue
            logger.warning(
                "pressure_delta: position '%s' has zero real data (expected for "
                "GK — no club xG exists for goalkeepers) — using GLOBAL median "
                "(%.4f) instead of position median.",
                pos, global_median,
            )
            group_median = global_median

        fill_mask = pos_mask & delta.isna()
        delta.loc[fill_mask] = group_median
        logger.info(
            "Imputed pressure_delta [%s]: %d players -> %.4f (group/global median)",
            pos, fill_mask.sum(), group_median,
        )

    return delta


def compute_pressure_index(df: pd.DataFrame) -> pd.DataFrame:
    if "sb_xg_per90" not in df.columns or "xg_per90" not in df.columns:
        logger.error(
            "sb_xg_per90 or xg_per90 missing entirely — PressureIndex will be all-NaN."
        )
        df["pressure_delta"] = np.nan
        df["PressureIndex"]  = np.nan
        return df

    both_present = df["sb_xg_per90"].notna() & df["xg_per90"].notna()
    real_count = both_present.sum()

    df["pressure_delta"] = np.where(
        both_present,
        df["sb_xg_per90"] - df["xg_per90"],
        np.nan,
    )
    df["pressure_delta"] = _impute_pressure_delta(df)

    df["PressureIndex"] = normalize_column(df["pressure_delta"], "pressure_delta").round(4)

    logger.info(
        "PressureIndex computed for %d/%d players (%d with real StatsBomb+club data, "
        "%d imputed).",
        df["PressureIndex"].notna().sum(), len(df), real_count, len(df) - real_count,
    )
    return df