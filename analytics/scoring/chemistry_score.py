# analytics/scoring/chemistry_score.py
# Gold Layer — ChemistryScore Computation
#
# Rebuilt 2026-07-14 (was in a prior session, missing from this codebase
# snapshot — see AUDIT session notes).
#
# Measures how well a player combines with teammates, using the average
# combined-passes/90 rate from the pairwise StatsBomb pass network
# (statsbomb_loader._aggregate_pairwise_chemistry -> silver_pipeline
# join_chemistry_pairs -> Silver column "chem_avg_per90").
#
# Coverage: 11/49 candidates have REAL pairwise data (only StatsBomb-covered
# WC2018/2022 + AFCON2023 matches give pass-recipient event data). The other
# 38/49 are ALREADY imputed with position-group median inside Silver
# (impute_by_position() — chem_avg_per90 is in IMPUTE_OUTFIELD_COLS /
# IMPUTE_GK_COLS), so by the time this module runs the column is 49/49
# non-null. This module only normalizes it — no further imputation needed
# here, unlike MentalScore which does its own two-source COALESCE.
#
# This is a genuine data-availability ceiling, not a bug: pairwise chemistry
# can only be measured between two players who started together in a
# covered StatsBomb match. 38/49 imputed players get their position group's
# median chemistry — a neutral assumption, not a penalty or a bonus.

import logging
import numpy as np
import pandas as pd

from analytics.scoring.normalize import normalize_column

logger = logging.getLogger(__name__)


def compute_chemistry_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes ChemistryScore = normalized chem_avg_per90.

    Args:
        df: Silver DataFrame with a "chem_avg_per90" column
            (49/49 non-null after Silver's position-group imputation).

    Returns:
        DataFrame with an added "ChemistryScore" column in [0, 1].
    """
    if "chem_avg_per90" not in df.columns:
        logger.error(
            "chem_avg_per90 column missing entirely — ChemistryScore will be all-NaN. "
            "Check that join_chemistry_pairs() ran in silver_pipeline.py."
        )
        df["ChemistryScore"] = np.nan
        return df

    missing_before = df["chem_avg_per90"].isna().sum()
    if missing_before > 0:
        logger.warning(
            "chem_avg_per90 has %d/%d NaN at Gold time — Silver imputation may not "
            "have run. These players will get NaN ChemistryScore.",
            missing_before, len(df),
        )

    df["ChemistryScore"] = normalize_column(df["chem_avg_per90"], "chem_avg_per90").round(4)

    real_data_count = (df.get("chem_num_partners", pd.Series(dtype=int)).fillna(0) > 0).sum()
    logger.info(
        "ChemistryScore computed for %d/%d players (%d with real StatsBomb pairwise data, "
        "%d position-median imputed).",
        df["ChemistryScore"].notna().sum(), len(df),
        real_data_count, len(df) - real_data_count,
    )
    return df
