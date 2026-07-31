# analytics/set_piece_analyzer.py
# "Set Piece & Clutch" — the buildable slice of PROJECT.md's original spec.
#
# Added 2026-07-30.
#
# ─────────────────────────────────────────────────────────────────────────
# WHAT'S HERE, AND WHAT ISN'T
# ─────────────────────────────────────────────────────────────────────────
# PROJECT.md § Dashboard page 8 was three things bundled together:
#   1. "Pressure Index chart"                    -> exists already,
#      analytics/scoring/pressure_index.py (built 2026-07-14). This module
#      wraps it with a readable label, nothing more (see clutch_ranking()).
#   2. "90+ minutes performance"                  -> NOT built. Checked
#      ingestion/loaders/statsbomb_loader.py directly: it only estimates
#      TOTAL minutes per player (matches_played * 80, a rough approximation
#      — see MIN_MINUTES/"Estimate minutes" in that file), never extracts
#      minute-by-minute event timestamps. Genuine "90th-minute-onward
#      performance" needs raw match-event data with in-game clock, which
#      this project's Bronze layer does not retain anywhere.
#   3. "Spécialistes set pieces" (corners, free kicks, headers)
#                                                  -> NOT built. Checked
#      every Bronze loader and Silver column — grep for corner/free.kick/
#      set.piece/header across the whole codebase returns nothing. No
#      source here tags an event as a corner/free-kick/header, so there is
#      no way to identify a "set-piece specialist" honestly.
#
# See ninety_plus_performance() and set_piece_specialists() below — both
# exist as named stubs raising NotImplementedError with this same
# explanation, same pattern as competition_context.py's
# tactical_fit_vs_opponent() and opponent_analyzer.py's
# tactical_style_profile().
#
# ─────────────────────────────────────────────────────────────────────────
# A GAP THIS MODULE FIXES: PressureIndex HAD NO REAL/IMPUTED FLAG
# ─────────────────────────────────────────────────────────────────────────
# analytics/scoring/pressure_index.py computes pressure_delta = sb_xg_per90
# - xg_per90, but only 10/11 StatsBomb-covered outfield players actually
# have BOTH values — the other ~38-39/49 get a position-group-median
# IMPUTED value (see that module's _impute_pressure_delta()), and this
# imputation is never flagged anywhere in Gold's output columns (checked:
# gold/players_scored_*.csv has no pressure_delta confidence column,
# unlike ga_imputed_flag or intl_caps_data_missing elsewhere in this
# project's culture of flagging imputation). clutch_ranking() below
# reconstructs the same "both raw values present" condition
# pressure_index.py's own source uses internally, and surfaces it as
# `pressure_data_confidence` — so a player's imputed group-median
# pressure_delta isn't presented as if it were their own real signal.

from __future__ import annotations

import pandas as pd

CLUTCH_THRESHOLD = 0.03  # pressure_delta above/below this -> a labeled tendency, not just noise (illustrative, not calibrated — see pressure_index.py's own docstring for why: no outcome data exists to calibrate against)

REQUIRED_COLUMNS = ["player_name", "pressure_delta", "PressureIndex"]


def clutch_ranking(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns df sorted by PressureIndex descending, with two added columns:
      - ClutchLabel: "Thrives Under Pressure" (pressure_delta > +CLUTCH_THRESHOLD),
        "Drops Off" (< -CLUTCH_THRESHOLD), or "Consistent" (in between).
        CLUTCH_THRESHOLD is a round, illustrative cutoff — see module
        constant comment, not fitted against any outcome.
      - pressure_data_confidence: "Real" if this player had BOTH
        sb_xg_per90 and xg_per90 (i.e. their pressure_delta is their own
        signal, not a position-group-median fallback), else "Imputed".
        Requires sb_xg_per90 and xg_per90 columns to compute this — if
        either is missing from df, confidence is reported as "Unknown"
        for every row rather than guessed.

    Args:
        df: must have REQUIRED_COLUMNS (gold/players_scored_*.csv has all
            of these plus sb_xg_per90/xg_per90 already).
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"clutch_ranking() requires columns {REQUIRED_COLUMNS}, missing: {missing}")

    result = df.copy()

    result["ClutchLabel"] = pd.cut(
        result["pressure_delta"],
        bins=[-float("inf"), -CLUTCH_THRESHOLD, CLUTCH_THRESHOLD, float("inf")],
        labels=["Drops Off", "Consistent", "Thrives Under Pressure"],
    ).astype(str)

    if "sb_xg_per90" in result.columns and "xg_per90" in result.columns:
        has_real_data = result["sb_xg_per90"].notna() & result["xg_per90"].notna()
        result["pressure_data_confidence"] = has_real_data.map({True: "Real", False: "Imputed"})
    else:
        result["pressure_data_confidence"] = "Unknown"

    return result.sort_values("PressureIndex", ascending=False).reset_index(drop=True)


def ninety_plus_performance(*args, **kwargs):
    """
    NOT IMPLEMENTED — no minute-by-minute event data exists anywhere in
    this project's Bronze/Silver layers (statsbomb_loader.py only
    estimates TOTAL minutes, never in-game timestamps). See module
    docstring for the full explanation.
    """
    raise NotImplementedError(
        "ninety_plus_performance() is intentionally not built — this project has no "
        "minute-by-minute match-event data anywhere in Bronze/Silver, only estimated "
        "season totals. Building it honestly requires ingesting raw StatsBomb (or "
        "equivalent) event timestamps first. See this module's docstring."
    )


def set_piece_specialists(*args, **kwargs):
    """
    NOT IMPLEMENTED — no corner/free-kick/header event tagging exists
    anywhere in this project's Bronze/Silver layers. See module docstring
    for the full explanation.
    """
    raise NotImplementedError(
        "set_piece_specialists() is intentionally not built — this project has no "
        "set-piece event tagging (corners/free-kicks/headers) anywhere in Bronze/Silver. "
        "Building it honestly requires a new ingestion source for set-piece-level event "
        "data first. See this module's docstring."
    )
