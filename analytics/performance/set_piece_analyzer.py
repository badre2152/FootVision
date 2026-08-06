from __future__ import annotations

import pandas as pd

CLUTCH_THRESHOLD = 0.03

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