import logging
import numpy as np
import pandas as pd

from analytics.scoring.normalize import normalize_column

logger = logging.getLogger(__name__)


def compute_flexibility_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes FlexibilityScore = normalized tm_num_positions.

    Args:
        df: Silver DataFrame with a "tm_num_positions" column (should be
            49/49 non-null after silver_pipeline.py's default-fill step).

    Returns:
        DataFrame with an added "FlexibilityScore" column in [0, 1].
    """
    if "tm_num_positions" not in df.columns:
        logger.error(
            "tm_num_positions column missing entirely — FlexibilityScore will "
            "be all-NaN. Check that Silver's default-fill step ran."
        )
        df["FlexibilityScore"] = np.nan
        return df

    df["FlexibilityScore"] = normalize_column(df["tm_num_positions"], "tm_num_positions").round(4)

    versatile_count = (df["tm_num_positions"] > 1).sum()
    logger.info(
        "FlexibilityScore computed for %d/%d players (%d flagged as multi-position — "
        "if this is 0, the Transfermarkt secondary-position selector is likely stale, "
        "see module docstring).",
        df["FlexibilityScore"].notna().sum(), len(df), versatile_count,
    )
    if versatile_count == 0:
        logger.warning(
            "ALL players show exactly 1 position — FlexibilityScore is a flat, "
            "uninformative column. This is the expected failure mode if the "
            "untested Transfermarkt selector never matched. Verify before use."
        )
    return df


TM_POSITION_TO_GROUP = {
    "Goalkeeper": "GK",
    "Centre-Back": "DEF", "Right-Back": "DEF", "Left-Back": "DEF", "Sweeper": "DEF",
    "Defensive Midfield": "MID", "Central Midfield": "MID",
    "Right Midfield": "MID", "Left Midfield": "MID", "Attacking Midfield": "MID",
    "Right Winger": "FWD", "Left Winger": "FWD",
    "Second Striker": "FWD", "Centre-Forward": "FWD",
}


def secondary_position_groups(tm_secondary_positions) -> set:
    """
    Parses a player's raw tm_secondary_positions string (comma-joined TM
    labels, e.g. "Right Midfield, Left-Back") into this project's coarse
    position groups (e.g. {"MID", "DEF"}).

    Returns an empty set for NaN/None/empty input, or if none of the listed
    labels are recognized. Unrecognized labels are logged at debug level and
    otherwise silently skipped — a single unmapped label should never crash
    or silently distort squad selection.
    """
    if tm_secondary_positions is None:
        return set()
    if isinstance(tm_secondary_positions, float) and pd.isna(tm_secondary_positions):
        return set()
    if not isinstance(tm_secondary_positions, str) or not tm_secondary_positions.strip():
        return set()

    groups = set()
    for raw_label in tm_secondary_positions.split(","):
        label = raw_label.strip()
        if not label:
            continue
        group = TM_POSITION_TO_GROUP.get(label)
        if group:
            groups.add(group)
        else:
            logger.debug(
                "secondary_position_groups: unrecognized TM position label %r "
                "— skipped (no effect on selection).", label,
            )
    return groups