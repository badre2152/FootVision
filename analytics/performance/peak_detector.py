from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_MINUTES_FOR_VALID_SEASON = 450
MIN_SEASONS_FOR_TREND = 2
DEFAULT_N_SEASONS = 3

RISING_RELATIVE_SLOPE = 0.15
DECLINING_RELATIVE_SLOPE = -0.15
PEAK_NEAR_BEST_MARGIN = 0.90

LABEL_INSUFFICIENT = "Insufficient Data"
LABEL_NO_DATA = "No Data"
LABELS = ["Rising", "Peak", "Stable", "Declining", LABEL_INSUFFICIENT, LABEL_NO_DATA]


@dataclass
class PeakDetectionSummary:
    df: pd.DataFrame
    label_counts: dict[str, int]
    n_no_data: int
    n_insufficient: int


def _parse_season_start_year(label: str) -> int:
    """'24/25' -> 2024. Assumes 2000s (fine for any player born after ~1985)."""
    first = label.split("/")[0]
    year = int(first)
    return 2000 + year if year < 100 else year


def _extract_club_seasons(career_entries) -> list[dict]:
    """
    Filters raw sofascore_detail_career entries down to club-season summary
    rows only (see module docstring's two-filter rule), sorted oldest ->
    newest by parsed season start year.
    """
    if career_entries is None or (isinstance(career_entries, float) and pd.isna(career_entries)):
        return []
    seasons = []
    for entry in career_entries:
        label = entry.get("label", "")
        if not entry.get("is_summary"):
            continue
        if "/" not in label:
            continue
        try:
            start_year = _parse_season_start_year(label)
        except (ValueError, IndexError):
            logger.debug("Could not parse season label %r — skipping.", label)
            continue
        seasons.append({**entry, "_start_year": start_year})
    return sorted(seasons, key=lambda e: e["_start_year"])


def _quality_proxy(season: dict, position: str) -> float | None:
    """
    Per-90 quality proxy for one season entry. Returns None if MIN is too
    low to trust (see MIN_MINUTES_FOR_VALID_SEASON) or required stats are
    missing for this position.

    GK:      -(goals conceded per 90)   — higher (closer to 0) is better
    Outfield: (goals + assists) per 90  — higher is better

    See module docstring: these two proxies are NEVER compared to each
    other or across players directly — only used to build one player's own
    trend line.
    """
    minutes = season.get("MIN")
    if minutes is None or minutes < MIN_MINUTES_FOR_VALID_SEASON:
        return None

    if position == "GK":
        gc = season.get("GC")
        if gc is None:
            return None
        return -(gc / minutes * 90.0)

    gls = season.get("GLS") or 0.0
    ast = season.get("AST") or 0.0
    if season.get("GLS") is None and season.get("AST") is None:
        return None
    return (gls + ast) / minutes * 90.0


def _classify_trend(qualities: list[float]) -> tuple[str, float | None]:
    """
    Returns (label, relative_slope). qualities must be chronologically
    ordered (oldest first) and already filtered to valid seasons.
    """
    n = len(qualities)
    if n < MIN_SEASONS_FOR_TREND:
        return LABEL_INSUFFICIENT, None

    x = np.arange(n)
    y = np.array(qualities)
    slope, _intercept = np.polyfit(x, y, deg=1)

    mean_q = np.mean(np.abs(y))
    relative_slope = slope / mean_q if mean_q > 1e-9 else 0.0

    if relative_slope >= RISING_RELATIVE_SLOPE:
        return "Rising", relative_slope
    if relative_slope <= DECLINING_RELATIVE_SLOPE:
        return "Declining", relative_slope

    latest = y[-1]
    own_max = np.max(y)
    if own_max > 1e-9 and latest >= PEAK_NEAR_BEST_MARGIN * own_max:
        return "Peak", relative_slope
    return "Stable", relative_slope


def compute_peak_decline(
    df: pd.DataFrame,
    n_seasons: int = DEFAULT_N_SEASONS,
    career_col: str = "sofascore_detail_career",
) -> PeakDetectionSummary:
    """
    Adds a Peak/Rising/Stable/Declining trend label per player, based on
    their last `n_seasons` valid club seasons (see module docstring for
    full methodology and its limits).

    Args:
        df: must have `position` and `career_col`.
        n_seasons: how many of the most recent valid seasons to use for
            the trend (default 3, matching ROADMAP.md's own "tendance sur
            3 saisons" instruction). Players with fewer valid seasons than
            MIN_SEASONS_FOR_TREND (2) get LABEL_INSUFFICIENT — never a
            guessed trend from 0-1 data points.
        career_col: column holding the raw per-season entries. Defaults
            to Silver's actual column name — verified to exist before
            writing this module (47/49 real coverage).

    Returns:
        PeakDetectionSummary. df gains:
          - PeakDeclineLabel: one of LABELS
          - trend_slope_relative: float or NaN — the relative per-season
            slope used to classify (see docstring for why relative, not
            absolute)
          - seasons_used: how many valid seasons actually went into the
            trend (0, 1, or up to n_seasons)
    """
    required = ["position", career_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"compute_peak_decline() requires columns {required}, missing: {missing}")

    result_df = df.copy()
    labels = []
    slopes = []
    seasons_used_list = []

    for _, row in result_df.iterrows():
        club_seasons = _extract_club_seasons(row[career_col])
        if not club_seasons:
            labels.append(LABEL_NO_DATA)
            slopes.append(np.nan)
            seasons_used_list.append(0)
            continue

        recent = club_seasons[-n_seasons:]
        qualities = [
            q for q in (_quality_proxy(s, row["position"]) for s in recent) if q is not None
        ]

        label, slope = _classify_trend(qualities)
        labels.append(label)
        slopes.append(slope if slope is not None else np.nan)
        seasons_used_list.append(len(qualities))

    result_df["PeakDeclineLabel"] = labels
    result_df["trend_slope_relative"] = slopes
    result_df["seasons_used"] = seasons_used_list

    label_counts = result_df["PeakDeclineLabel"].value_counts().to_dict()
    n_no_data = int((result_df["PeakDeclineLabel"] == LABEL_NO_DATA).sum())
    n_insufficient = int((result_df["PeakDeclineLabel"] == LABEL_INSUFFICIENT).sum())

    logger.info(
        "Peak/Decline trend: %d players processed. Label counts: %s. "
        "%d with no career data at all, %d with too few valid seasons.",
        len(result_df), label_counts, n_no_data, n_insufficient,
    )

    return PeakDetectionSummary(
        df=result_df,
        label_counts=label_counts,
        n_no_data=n_no_data,
        n_insufficient=n_insufficient,
    )