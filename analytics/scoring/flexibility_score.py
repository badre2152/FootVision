# analytics/scoring/flexibility_score.py
# Gold Layer — FlexibilityScore Computation
#
# Added 2026-07-14. Was entirely absent from this codebase before this
# session (see AUDIT session notes) — no loader anywhere captured
# secondary/multi-position data.
#
# ⚠️ TESTING STATUS (updated 2026-07-20) ⚠️
# The raw input (tm_num_positions) comes from
# ingestion/loaders/transfermarkt_loader.py's _extract_secondary_positions().
# A tag-name bug (div vs the real <dd>) was found and fixed on 2026-07-20
# via live diagnostic against 5 real Transfermarkt profiles — this is why
# every run before that date showed FlexibilityScore flat at 0 for all 49
# players. The selector SYNTAX is now confirmed correct.
#
# STILL UNVERIFIED: whether the multi-position case (more than one <dd
# class="detail-position__position">) actually renders for genuinely
# versatile players — all 5 verification profiles were goalkeepers, who
# realistically only have one position. This proves the fix didn't break
# anything and matches the correct single-position case, but doesn't yet
# prove tm_num_positions > 1 works for a real multi-position player.
#
# BEFORE FULLY TRUSTING THIS IN A REAL SELECTION DECISION:
#   1. Run the Transfermarkt loader for a KNOWN-versatile outfield player
#      (e.g. a full-back who also plays winger, or a converted
#      midfielder).
#   2. Confirm tm_num_positions > 1 for at least one such player.
#   3. If it's still 1 for a player you know plays multiple positions,
#      TM may not always populate the "Other position" field, or the
#      widget may only appear for players with a sufficiently long
#      appearance history at the secondary position — investigate that
#      specific profile's HTML directly rather than assuming the
#      selector is broken again.
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


# ─────────────────────────────────────────────────────────────────────────────
# ADDED for spec #13 (flexibility_in_selection_logic.md) — 2026-08-01
#
# tm_secondary_positions holds raw Transfermarkt labels (e.g. "Right Midfield,
# Left-Back" — see transfermarkt_loader.py::_extract_secondary_positions()),
# NOT this project's coarse GK/DEF/MID/FWD groups used everywhere else
# (position_caps, the `position` column, etc). Squad-selection logic needs to
# know which coarse GROUP a secondary position falls into (e.g. "Left-Back"
# -> "DEF") to decide whether a player's flexibility actually covers a thin
# position group. This mapping is the missing link between the two.
#
# Mapping follows Transfermarkt's standard position taxonomy. Any label not
# in this table (e.g. a future TM wording change) is skipped rather than
# guessed at — see secondary_position_groups() below.
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