# analytics/scoring/pressure_index.py
# Gold Layer — PressureIndex Computation
#
# Rebuilt 2026-07-14 (was in a prior session, missing from this codebase
# snapshot — see AUDIT session notes).
#
# ORIGINAL DEFINITION (PROJECT.md): "performance in big matches vs normal
# matches". The data reality: StatsBomb open data for Morocco covers ONLY
# major tournaments (WC2018/2022, AFCON2023) — i.e. only "big matches" —
# so there is no "normal match" baseline from the SAME source to compare
# against.
#
# PROXY USED HERE (documented, not hidden): compare each player's
# attacking-chance quality in these high-stakes international matches
# (sb_xg_per90) against their club-football baseline (xg_per90, already
# COALESCE'd in Silver from Understat -> SofaScore -> Fotmob):
#
#     pressure_delta = sb_xg_per90 - xg_per90
#
# Positive  -> generates MORE quality chances in big internationals than
#              for their club (thrives under pressure)
# Negative  -> drops off in big internationals relative to their club form
# ~0        -> performs at their normal level
#
# This is a REASONABLE proxy given available data, not the literal
# PROJECT.md definition. If richer match-level "big vs normal" labels ever
# become available (e.g. a proper match-importance feed), this should be
# replaced rather than extended.
#
# Coverage: only 10/11 StatsBomb-covered outfield players have BOTH
# sb_xg_per90 and a club xg_per90 baseline (goalkeepers have no club xG by
# definition, so GKs always fall through to imputation here). The remaining
# ~39/49 are imputed with position-group median, then global median as a
# last resort (mirrors silver_pipeline.impute_by_position's fallback logic,
# done locally here since pressure_delta is a DERIVED column, not a raw
# Silver field, and therefore isn't in Silver's IMPUTE_*_COLS lists).

import logging
import numpy as np
import pandas as pd

from analytics.scoring.normalize import normalize_column

logger = logging.getLogger(__name__)


def _impute_pressure_delta(df: pd.DataFrame) -> pd.Series:
    """
    Position-group median imputation for pressure_delta, with a global
    median fallback if an entire position group has no real data (this WILL
    happen for GK, since no goalkeeper has both sb_xg_per90 and a club
    xg_per90 baseline — GKs don't generate registered xG for their club).
    """
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
    """
    Computes PressureIndex = normalized(sb_xg_per90 - xg_per90), imputed by
    position-group median (falling back to global median for GK).

    Args:
        df: Silver DataFrame with "sb_xg_per90" and "xg_per90" columns.

    Returns:
        DataFrame with added "pressure_delta" (raw, pre-normalization,
        kept for transparency/debugging) and "PressureIndex" (normalized,
        the actual scoring input) columns.
    """
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
