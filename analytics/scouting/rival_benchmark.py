# analytics/rival_benchmark.py
# Football Atlas — Rival/Opponent Player Benchmarking (spec 23_rival_benchmark.md)
#
# Added 2026-08-01.
#
# WHAT THIS IS: lets the dashboard show a Morocco candidate's raw per-90
# stats next to 2-3 curated international reference players at the same
# position, for visual context ("is our best CB actually competitive at
# this level?") — see seeds/rival_reference_players.csv for the curated
# list and the reasoning behind each pick.
#
# WHAT THIS IS DELIBERATELY NOT (spec #23, Functional Requirement #3):
# a scoring input. Nothing in this module ever touches DSSScore or any
# of its components. The min-max scaling below is display-only, computed
# fresh per chart across exactly {this candidate, these references} — it
# is NOT the same normalization run over the 49-candidate Morocco pool
# that feeds PerformanceScore etc. (see analytics/scoring/normalize.py).
# Mixing those two would be the exact "our player vs a player we don't
# control" conflation the spec explicitly warns against.
#
# WHY RAW PER-90 STATS, NOT THE COMPOSITE SUB-SCORES: the existing radar
# chart on 2_player_analysis.py plots PerformanceScore/FormScore/etc. —
# those are Morocco-49-candidate-relative PERCENTILES (see
# percentile_score.py), meaningless for an external player who was never
# in that pool. Raw per-90 stats (goals/90, tackles/90, ...) are the
# thing that's actually comparable across any two players regardless of
# which pool either of them came from — that's the whole point of a
# per-90 rate stat. So this module reuses the underlying STAT
# categories, not the composite SCORE categories, and applies its own
# lightweight display-only scaling just so the radar chart has a sane
# [0, 1] axis.

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REFERENCE_PLAYERS_PATH = PROJECT_ROOT / "seeds" / "rival_reference_players.csv"

# Which raw per-90 categories are meaningful per position bucket, mirroring
# the position taxonomy already used everywhere else in this project
# (GK/DEF/MID/FWD — see seeds/players_49.csv). These are a SUBSET of the
# canonical merged per-90 columns already computed in Gold (Gls_per90,
# Ast_per90, xg_per90, xa_per90, Tkl_per90, Int_per90, Clr_per90,
# GA_per90, GK_saves_per90 — see run_gold.py) chosen for being the ones
# that actually differentiate quality at that position.
RADAR_CATEGORIES_BY_POSITION: dict[str, list[str]] = {
    "GK":  ["GA_per90", "GK_saves_per90"],
    "DEF": ["Tkl_per90", "Int_per90", "Clr_per90"],
    "MID": ["Tkl_per90", "Int_per90", "Ast_per90", "xa_per90"],
    "FWD": ["Gls_per90", "Ast_per90", "xg_per90", "xa_per90"],
}

# GA_per90 (goals conceded/90) is a "lower is better" stat — everything
# else here is "higher is better". Flagged so the display-only scaling
# below can invert it (a GK conceding FEWER goals should plot as BETTER,
# i.e. closer to the outer edge of the radar, same convention the rest
# of this project's radar chart already uses for its 0-1 scored axes).
INVERTED_CATEGORIES = {"GA_per90"}


def load_reference_players() -> pd.DataFrame:
    """
    Loads the curated reference-player list from
    seeds/rival_reference_players.csv. Returns an empty DataFrame (not
    None/an exception) if the file is missing — callers should treat
    "no reference data available" as a normal, displayable state, not a
    crash, matching this module's/this project's general convention.
    """
    if not REFERENCE_PLAYERS_PATH.exists():
        logger.warning("Reference player seed file not found at %s.", REFERENCE_PLAYERS_PATH)
        return pd.DataFrame()
    return pd.read_csv(REFERENCE_PLAYERS_PATH)


def get_references_for_position(position: str, references_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Returns the curated reference players matching `position` (GK/DEF/MID/FWD)."""
    if references_df is None:
        references_df = load_reference_players()
    if references_df.empty or "position" not in references_df.columns:
        return references_df
    return references_df[references_df["position"] == position].reset_index(drop=True)


def _minmax_scale(value: float, lo: float, hi: float, invert: bool = False) -> float:
    """
    Display-only min-max scaling to [0, 1] for THIS radar chart only —
    see module docstring for why this must never be confused with (or
    feed into) the Morocco-49-candidate-pool normalization that produces
    DSSScore's components. Returns 0.5 if lo == hi (can't distinguish —
    neutral placement rather than a divide-by-zero).
    """
    if pd.isna(value):
        return None
    if hi == lo:
        return 0.5
    scaled = (value - lo) / (hi - lo)
    if invert:
        scaled = 1.0 - scaled
    return float(min(max(scaled, 0.0), 1.0))


def compare_to_reference(
    player_row: pd.Series,
    position: str,
    references_df: pd.DataFrame | None = None,
) -> dict:
    """
    Builds a chart-ready comparison structure for `player_row` (a row
    from players_scored_*.csv — must have the raw per-90 columns for
    `position`, plus player_name) against the curated reference players
    for that position.

    Returns:
        {
            "categories": [...],                 # raw per-90 column names used
            "player_name": str,
            "player_values_raw": {cat: float|None},
            "player_values_scaled": {cat: float|None},   # [0,1], display-only
            "references": [
                {
                    "name": str, "nation": str, "club": str, "season": str,
                    "benchmark_reason": str, "stats_verified": bool,
                    "values_raw": {cat: float|None},
                    "values_scaled": {cat: float|None},
                },
                ...
            ],
            "warning": str | None,   # e.g. "no curated references for this position yet"
        }

    NEVER writes to or reads from DSSScore/any *Score column — see module
    docstring, Functional Requirement #3 of the spec this implements.
    """
    categories = RADAR_CATEGORIES_BY_POSITION.get(position, [])
    result = {
        "categories": categories,
        "player_name": player_row.get("player_name", "Unknown"),
        "player_values_raw": {},
        "player_values_scaled": {},
        "references": [],
        "warning": None,
    }

    if not categories:
        result["warning"] = f"No benchmark stat categories defined for position '{position}'."
        return result

    refs = get_references_for_position(position, references_df)
    if refs.empty:
        result["warning"] = (
            f"No curated international reference players for position '{position}' yet — "
            "see seeds/rival_reference_players.csv to add one."
        )
        # Still return the player's own raw values (scaled against
        # themselves alone, i.e. 1.0/0.0/0.5 — not very useful, but keeps
        # the return shape consistent for callers that don't special-case this).

    # Raw values first.
    player_raw = {c: (float(player_row[c]) if c in player_row.index and pd.notna(player_row[c]) else None) for c in categories}
    ref_raws = []
    for _, ref in refs.iterrows():
        ref_raws.append({
            "name": ref.get("ref_player_name", "Unknown"),
            "nation": ref.get("nation", ""),
            "club": ref.get("club", ""),
            "season": ref.get("season", ""),
            "benchmark_reason": ref.get("benchmark_reason", ""),
            "stats_verified": bool(ref.get("stats_verified", False)),
            "values_raw": {c: (float(ref[c]) if c in ref.index and pd.notna(ref[c]) else None) for c in categories},
        })

    # Scale EACH category independently across {player, all references for
    # that category} — a per-category min-max, not a single global one,
    # since e.g. Gls_per90 and xa_per90 live on very different raw scales.
    for cat in categories:
        values_for_cat = [player_raw[cat]] + [r["values_raw"][cat] for r in ref_raws]
        values_for_cat = [v for v in values_for_cat if v is not None]
        if not values_for_cat:
            lo, hi = 0.0, 1.0
        else:
            lo, hi = min(values_for_cat), max(values_for_cat)
        invert = cat in INVERTED_CATEGORIES

        result["player_values_scaled"][cat] = _minmax_scale(player_raw[cat], lo, hi, invert) if player_raw[cat] is not None else None
        for r in ref_raws:
            r.setdefault("values_scaled", {})[cat] = (
                _minmax_scale(r["values_raw"][cat], lo, hi, invert) if r["values_raw"][cat] is not None else None
            )

    result["player_values_raw"] = player_raw
    result["references"] = ref_raws
    return result
