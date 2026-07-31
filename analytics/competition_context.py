# analytics/competition_context.py
# Best XI Generator (formation-based) — the buildable half of PROJECT.md's
# original "competition_context.py" spec.
#
# Added 2026-07-30.
#
# ─────────────────────────────────────────────────────────────────────────
# WHAT'S NOT HERE, AND WHY (read this before reporting a "missing feature")
# ─────────────────────────────────────────────────────────────────────────
# PROJECT.md's original spec for this file was two things bundled together:
#   1. "Analyser le style de jeu des adversaires du groupe" +
#      "Calculer Tactical Fit Score vs chaque adversaire"  -> requires an
#      OPPONENT data source (formations, pressing stats, style profiles for
#      Morocco's actual World Cup opponents). Checked exhaustively across
#      this codebase (grep for opponent/adversaire/fixture across every
#      .py file, every Bronze loader, the seeds/players_49.csv header) —
#      there is NO such source anywhere. Nothing in ingestion/loaders/
#      collects opponent-team data; the entire Bronze layer is
#      candidate-player-centric (11+ sources, all about the 49 Morocco
#      candidates themselves, never about Croatia/Netherlands/whoever).
#      Building "Tactical Fit vs Opponent" honestly would mean designing
#      and shipping a brand-new Bronze ingestion pipeline first (opponent
#      squad lists, formations, pressing/possession stats per opponent) —
#      that is a separate, much larger piece of work, not something this
#      module can fake with data that doesn't exist. See
#      tactical_fit_vs_opponent() below — it exists as a named stub that
#      raises NotImplementedError with this same explanation, specifically
#      so this gap is visible and documented rather than silently absent.
#   2. "Générer Best XI vs chaque adversaire" -> the "vs chaque adversaire"
#      half needs (1), but a plain "Best XI by formation" from the
#      candidates you've already selected and scored does NOT need any
#      opponent data at all. That's what this module actually builds.
#
# ─────────────────────────────────────────────────────────────────────────
# A REAL SIMPLIFICATION WORTH FLAGGING: NO SUB-POSITIONS
# ─────────────────────────────────────────────────────────────────────────
# seeds/players_49.csv's `position` column only has 4 values: GK, DEF, MID,
# FWD — no CB/FB/DM/CM/AM/LW/RW/ST granularity. That means, for example,
# "4-2-3-1" and "4-3-3" both resolve to the exact same slot counts here
# (4 DEF, 5 MID, 1 FWD is NOT distinguishable from "2 DM + 3 AM" without
# finer position data — this module treats all central/wide midfielders as
# one interchangeable MID pool). Formation names below are a genuine
# simplification for slot-counting purposes, not a tactically accurate
# board — do not present this as "the AM slot is empty" or similar,
# because this module has no concept of AM at all.

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

# Formation -> required outfield slot counts (GK is always exactly 1,
# added separately below, not counted in these dicts).
# Counts intentionally only use {DEF, MID, FWD} — see module docstring on
# why finer roles (wing-back, DM, AM, winger...) aren't modeled.
FORMATIONS: dict[str, dict[str, int]] = {
    "4-3-3":     {"DEF": 4, "MID": 3, "FWD": 3},
    "4-2-3-1":   {"DEF": 4, "MID": 5, "FWD": 1},  # identical slot counts to 4-3-3-ish MID-heavy shapes — see note above
    "4-4-2":     {"DEF": 4, "MID": 4, "FWD": 2},
    "3-5-2":     {"DEF": 3, "MID": 5, "FWD": 2},
    "5-3-2":     {"DEF": 5, "MID": 3, "FWD": 2},  # wing-backs counted as DEF here — simplification
}

REQUIRED_COLUMNS = ["player_name", "position", "DSSScore"]


def _resolve_id_col(df: pd.DataFrame, id_col: str | None) -> str:
    """
    Picks the column to use as a unique player identifier.

    gold/players_scored_*.csv has `player_id` (see MASTER_SPEC.md § 9), but
    gold/top26_*.csv deliberately does NOT (it's a "key columns only"
    export — see MASTER_SPEC.md's Gold Outputs table) — found this the
    hard way by running this module against a real top26_*.csv file, not
    by reading the spec. Falls back to `player_name` with a warning if
    `player_id` isn't present; raises if neither is.
    """
    if id_col is not None:
        if id_col not in df.columns:
            raise ValueError(f"id_col={id_col!r} not found in candidate_pool columns.")
        return id_col
    if "player_id" in df.columns:
        return "player_id"
    if "player_name" in df.columns:
        logger.warning(
            "candidate_pool has no 'player_id' column (this is expected for "
            "gold/top26_*.csv, which doesn't include it) — falling back to "
            "'player_name' as the unique identifier. This is fine unless two "
            "candidates share an exact name."
        )
        return "player_name"
    raise ValueError("candidate_pool has neither 'player_id' nor 'player_name' to identify players by.")


@dataclass
class BestXIResult:
    formation: str
    starting_xi: pd.DataFrame          # 11 rows: GK + formation's outfield slots
    bench: pd.DataFrame                # remaining candidates, sorted by DSSScore within position
    shortfalls: dict[str, int] = field(default_factory=dict)  # position -> how many short, if any
    pool_size: int = 0
    used_fallback_pool: bool = False   # True if candidate_pool didn't have enough depth and a wider pool was used


def _pick_top_n(pool: pd.DataFrame, position: str, n: int) -> pd.DataFrame:
    """Top-n players at `position` from `pool`, sorted by DSSScore descending."""
    subset = pool[pool["position"] == position].sort_values("DSSScore", ascending=False)
    return subset.head(n)


def build_best_xi(
    candidate_pool: pd.DataFrame,
    formation: str = "4-3-3",
    fallback_pool: pd.DataFrame | None = None,
    id_col: str | None = None,
) -> BestXIResult:
    """
    Selects a starting XI from `candidate_pool` for the given `formation`,
    purely by DSSScore within each position bucket — no opponent context
    (see module docstring for why).

    Args:
        candidate_pool: gold/top26_*.csv OR gold/players_scored_*.csv —
            must have REQUIRED_COLUMNS. Every row is assumed eligible/fit;
            this function does no availability/injury filtering itself
            (compose with analytics.ml.injury_predictor's InjuryRiskLevel
            upstream if you want to exclude "High" risk players from
            contention — deliberately NOT done automatically here, since
            excluding a player from a *starting XI suggestion* is a
            coaching decision, not something to hardcode silently).
        formation: one of FORMATIONS' keys.
        fallback_pool: a wider pool (e.g. the full 49 players_scored_*.csv)
            to draw from ONLY if candidate_pool doesn't have enough depth
            at some position for the chosen formation. If a fallback was
            needed, `used_fallback_pool=True`. If None (default) and depth
            is insufficient, the shortfall is reported in `shortfalls` and
            those slots are left unfilled (fewer than 11 rows in
            starting_xi) rather than silently picking bad-fit players.
        id_col: column to use as unique player identifier. Defaults to
            'player_id' if present, else falls back to 'player_name' (see
            _resolve_id_col — top26_*.csv has no player_id column).

    Returns:
        BestXIResult.
    """
    if formation not in FORMATIONS:
        raise ValueError(
            f"Unknown formation {formation!r} — choose from {sorted(FORMATIONS)}"
        )
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in candidate_pool.columns]
    if missing_cols:
        raise ValueError(f"candidate_pool missing required columns: {missing_cols}")

    id_col = _resolve_id_col(candidate_pool, id_col)
    if fallback_pool is not None and id_col not in fallback_pool.columns:
        raise ValueError(f"fallback_pool must also have the {id_col!r} column.")

    slots = {"GK": 1, **FORMATIONS[formation]}
    picked_frames: list[pd.DataFrame] = []
    shortfalls: dict[str, int] = {}
    used_fallback = False

    for position, n_needed in slots.items():
        picked = _pick_top_n(candidate_pool, position, n_needed)
        got = len(picked)

        if got < n_needed and fallback_pool is not None:
            already_picked_ids = pd.concat(picked_frames + [picked])[id_col] if picked_frames or got else pd.Series(dtype=object)
            wider = fallback_pool[~fallback_pool[id_col].isin(already_picked_ids)]
            extra_needed = n_needed - got
            extra = _pick_top_n(wider, position, extra_needed)
            if len(extra):
                used_fallback = True
                picked = pd.concat([picked, extra], ignore_index=True)
                got = len(picked)
                logger.info(
                    "Formation %s: pulled %d extra %s player(s) from fallback_pool "
                    "(candidate_pool only had %d/%d).",
                    formation, len(extra), position, got - len(extra), n_needed,
                )

        if got < n_needed:
            shortfalls[position] = n_needed - got
            logger.warning(
                "Formation %s: short %d %s player(s) — only %d available. "
                "Leaving slot(s) unfilled rather than picking a mismatched position.",
                formation, n_needed - got, position, got,
            )

        picked_frames.append(picked)

    starting_xi = pd.concat(picked_frames, ignore_index=True)

    bench = candidate_pool[
        ~candidate_pool[id_col].isin(starting_xi[id_col])
    ].sort_values(["position", "DSSScore"], ascending=[True, False])

    return BestXIResult(
        formation=formation,
        starting_xi=starting_xi,
        bench=bench,
        shortfalls=shortfalls,
        pool_size=len(candidate_pool),
        used_fallback_pool=used_fallback,
    )


def compare_formations(
    candidate_pool: pd.DataFrame,
    formations: list[str] | None = None,
) -> pd.DataFrame:
    """
    Runs build_best_xi() for every formation in `formations` (default: all
    of FORMATIONS) and returns a summary table: formation, total starting
    XI DSSScore, mean DSSScore, any shortfalls. Useful for a dashboard
    "which formation gets our best players on the pitch" comparison — NOT
    a tactical recommendation (this module has no opponent or tactical
    context, see docstring), purely "which shape best uses the squad
    depth you actually have."
    """
    formations = formations or list(FORMATIONS)
    rows = []
    for formation in formations:
        result = build_best_xi(candidate_pool, formation=formation)
        rows.append({
            "formation": formation,
            "n_starters": len(result.starting_xi),
            "total_DSSScore": result.starting_xi["DSSScore"].sum(),
            "mean_DSSScore": result.starting_xi["DSSScore"].mean(),
            "shortfalls": result.shortfalls or None,
        })
    return pd.DataFrame(rows).sort_values("total_DSSScore", ascending=False).reset_index(drop=True)


def tactical_fit_vs_opponent(*args, **kwargs):
    """
    NOT IMPLEMENTED — see module docstring's "WHAT'S NOT HERE, AND WHY"
    section. This exists as a named, documented stub (not a silent
    omission) so anyone looking for opponent-specific tactical fit finds
    a clear explanation instead of a missing file or a vague TODO.

    Building this for real requires a new Bronze data source: opponent
    squads, formations, and style/pressing metrics for Morocco's actual
    World Cup opponents — none of which exists anywhere in this project's
    ingestion layer today.
    """
    raise NotImplementedError(
        "tactical_fit_vs_opponent() is intentionally not built — this project "
        "has no opponent-team data source anywhere in Bronze/Silver. Building "
        "it honestly requires a new ingestion pipeline for opponent squads/"
        "formations/style first. See this module's docstring for the full "
        "explanation. This is a scoping decision, not a bug."
    )
