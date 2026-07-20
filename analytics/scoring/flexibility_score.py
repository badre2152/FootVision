# analytics/scoring/flexibility_score.py
# Gold Layer — FlexibilityScore Computation
#
# Added 2026-07-14. Was entirely absent from this codebase before this
# session (see AUDIT session notes) — no loader anywhere captured
# secondary/multi-position data.
#
# ⚠️ IMPORTANT — TESTING STATUS ⚠️
# The raw input (tm_num_positions) comes from a NEW extraction added to
# ingestion/loaders/transfermarkt_loader.py's _extract_secondary_positions()
# in this same session. That extraction is WRITTEN but UNTESTED against a
# live Transfermarkt page — the sandbox this was written in cannot reach
# transfermarkt.com (only github/pypi/npm domains are whitelisted). This
# module itself (the scoring math) IS tested (see test call below) — what's
# NOT verified is whether the upstream selector in transfermarkt_loader.py
# actually matches TM's current markup.
#
# BEFORE TRUSTING THIS IN A REAL RUN:
#   1. Run run_bronze.py --skip-everything-except-transfermarkt on a
#      machine with real network access.
#   2. Check the Silver output for a few KNOWN-versatile players (e.g. a
#      full-back who also plays winger, or a converted midfielder) —
#      confirm tm_num_positions > 1 for at least some of them.
#   3. If ALL 49 players show tm_num_positions == 1, the selector in
#      _extract_secondary_positions() is almost certainly stale (TM
#      changes markup periodically) — this module will silently produce a
#      flat, uninformative FlexibilityScore (everyone gets the same
#      normalized floor value) rather than crashing. That is the specific
#      failure mode to check for.
#
# Definition: FlexibilityScore = normalized(tm_num_positions), where
# tm_num_positions = 1 + count of secondary positions listed on the
# player's Transfermarkt profile. Neutral default of 1 (no bonus, no
# penalty) for any player where the fetch failed or no secondary position
# was found (see silver_pipeline.py Step 3c) — NOT imputed with a
# position-group median, since "how many positions you can play" isn't
# meaningfully predicted by which position group you're already in.

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
