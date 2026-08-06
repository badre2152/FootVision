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