# dashboard/data_loader.py
# Football Atlas Dashboard — Data Loading Utilities
#
# Added 2026-07-14. Originally had ZERO Streamlit imports so it could be
# unit-tested without a running Streamlit server (see
# tests/test_dashboard_data_loader.py). That's still true in spirit —
# `st.cache_data` works correctly outside a live app too (Streamlit calls
# this "bare mode": you get a harmless "No runtime found"/"missing
# ScriptRunContext" warning on stderr, but caching and return values are
# 100% correct — verified manually before relying on it here). What
# CHANGED on 2026-08-01 (spec `22_dashboard_caching.md`) is that this file
# now imports `streamlit` itself, purely to reach `st.cache_data` — no
# other Streamlit API (no widgets, no layout calls) is used here, so
# nothing in this module actually requires a live app; it just requires
# the `streamlit` package to be installed (already true — it's in
# requirements.txt and every dashboard page already depends on it).
#
# CACHING (spec #22): every load_latest_*()/load_all_*() function below
# that reads from disk is wrapped in `@st.cache_data(ttl=300)`. These
# functions take NO arguments, so `st.cache_data`'s default per-argument
# cache key collapses to a single global slot per function — repeated
# calls within the 5-minute TTL window return the cached result without
# touching disk again, but a NEW players_scored_*.csv/top26_*.csv written
# by a fresh run_gold.py won't be picked up until that TTL expires (or the
# process restarts). This is a deliberate v1 tradeoff, not an oversight:
# at this project's actual usage pattern (a pipeline re-run is a rare,
# manual, several-minutes-long event — never happening twice inside the
# same 5-minute window in normal operation), "stale for up to 5 minutes
# after a re-run" is an acceptable price for "don't re-read 7 files from
# disk on every single sidebar filter click". If that usage pattern ever
# changes (e.g. an automated scheduler re-running run_gold.py every
# minute), this TTL needs revisiting or replacing with an mtime/run_date-
# aware cache key.
#
# TESTING IMPLICATION: because the cache is a single global slot per
# function (not per-tmp_path), tests/test_dashboard_data_loader.py's
# `fake_dirs` fixture now also calls `.clear()` on every cached function
# before each test — otherwise test N+1 could see test N's cached result
# even though it points GOLD_DIR/BRONZE_DIR at a completely different
# tmp_path. See that fixture for the actual reset logic.
#
# Reads directly from local disk (GOLD_DIR / BRONZE_DIR below), matching
# the run_date-suffixed filename convention every run_*.py script already
# uses (e.g. top26_20260713_145738.csv). If you're running against real
# MinIO instead of local files, point GOLD_DIR/BRONZE_DIR at a synced
# local copy, or extend load_latest_csv/load_latest_json to call
# clients/minio_client.py's read_latest_json instead — kept as local-disk
# for this first version since that's what every past Gold run in this
# codebase has actually produced (see gold/*.csv already in the repo).

import glob
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR     = PROJECT_ROOT / "gold"
BRONZE_DIR   = PROJECT_ROOT / "bronze"


def _latest_file(directory: Path, prefix: str, suffix: str) -> Path | None:
    """
    Finds the most recent file matching `{prefix}*{suffix}` in `directory`,
    using the run_date suffix in the filename (e.g. top26_20260713_145738.csv)
    for ordering — NOT file mtime, since these files may have been copied
    around (mtime isn't reliable, the run_date embedded in the name is).
    """
    if not directory.exists():
        return None
    pattern = str(directory / f"{prefix}*{suffix}")
    matches = sorted(glob.glob(pattern))  # lexical sort works: YYYYMMDD_HHMMSS sorts correctly
    return Path(matches[-1]) if matches else None


@st.cache_data(ttl=300)  # 5 min — see module docstring for the staleness/redundant-reads tradeoff
def load_latest_top26() -> pd.DataFrame | None:
    """Loads the most recent top26_*.csv. Returns None if none exists yet.

    Cached for up to 5 minutes (ttl=300) — a fresh run_gold.py output can
    take up to 5 minutes to appear in the dashboard. See module docstring.
    """
    path = _latest_file(GOLD_DIR, "top26_", ".csv")
    if path is None:
        logger.warning("No top26_*.csv found in %s — run run_gold.py first.", GOLD_DIR)
        return None
    df = pd.read_csv(path)
    df.attrs["source_file"] = path.name
    return df


@st.cache_data(ttl=300)  # 5 min — see module docstring for the staleness/redundant-reads tradeoff
def load_latest_players_scored() -> pd.DataFrame | None:
    """Loads the most recent players_scored_*.csv (all 49 candidates, full columns).

    Cached for up to 5 minutes (ttl=300) — a fresh run_gold.py output can
    take up to 5 minutes to appear in the dashboard. See module docstring.
    """
    path = _latest_file(GOLD_DIR, "players_scored_", ".csv")
    if path is None:
        logger.warning("No players_scored_*.csv found in %s — run run_gold.py first.", GOLD_DIR)
        return None
    df = pd.read_csv(path)
    df.attrs["source_file"] = path.name
    return df


@st.cache_data(ttl=300)  # 5 min — see module docstring for the staleness/redundant-reads tradeoff
def load_latest_squad_summary() -> dict | None:
    """Loads the most recent squad_summary_*.json.

    Cached for up to 5 minutes (ttl=300) — a fresh run_gold.py output can
    take up to 5 minutes to appear in the dashboard. See module docstring.
    """
    path = _latest_file(GOLD_DIR, "squad_summary_", ".json")
    if path is None:
        logger.warning("No squad_summary_*.json found in %s — run run_gold.py first.", GOLD_DIR)
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["_source_file"] = path.name
    return data


@st.cache_data(ttl=300)  # 5 min — see module docstring for the staleness/redundant-reads tradeoff
def load_latest_selection_audit() -> dict | None:
    """
    Loads the most recent selection_audit_*.json, produced by
    run_selection_audit.py (NOT run_gold.py — this is a standalone,
    deliberately-not-wired-in analysis, see that script's header for why).

    Cached for up to 5 minutes (ttl=300) — see module docstring.
    """
    path = _latest_file(GOLD_DIR, "selection_audit_", ".json")
    if path is None:
        logger.warning("No selection_audit_*.json found in %s — run run_selection_audit.py first.", GOLD_DIR)
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["_source_file"] = path.name
    return data


@st.cache_data(ttl=300)  # 5 min — see module docstring for the staleness/redundant-reads tradeoff
def load_latest_injury_predictor() -> dict | None:
    """
    Loads the most recent injury_predictor_summary_*.json, produced by
    run_injury_predictor.py (analytics/ml/injury_predictor.py — transparent
    rule-based index, NOT a trained model). Replaced the earlier
    load_latest_injury_risk()/run_injury_risk.py (LOOCV ML attempt) on
    2026-07-29 — see run_injury_predictor.py's header for why.

    Cached for up to 5 minutes (ttl=300) — see module docstring.
    """
    path = _latest_file(GOLD_DIR, "injury_predictor_summary_", ".json")
    if path is None:
        logger.warning("No injury_predictor_summary_*.json found in %s — run run_injury_predictor.py first.", GOLD_DIR)
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["_source_file"] = path.name
    return data


@st.cache_data(ttl=300)  # 5 min — see module docstring for the staleness/redundant-reads tradeoff
def load_latest_chemistry_pairs() -> list[dict]:
    """
    Loads the most recent statsbomb_chemistry/chemistry_pairs_*.json
    (pairwise pass-network data, ~50 real pairs among the 11 StatsBomb-
    covered candidates — see analytics/scoring/chemistry_score.py).

    Returns an empty list (not None) if not found — the Chemistry page
    handles this gracefully by showing an explanatory message instead of
    crashing, since this is genuinely optional/supplementary data.

    Cached for up to 5 minutes (ttl=300) — see module docstring.
    """
    chem_dir = BRONZE_DIR / "statsbomb_chemistry"
    path = _latest_file(chem_dir, "chemistry_pairs_", ".json")
    if path is None:
        logger.info("No chemistry_pairs_*.json found in %s.", chem_dir)
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Columns that may or may not exist depending on which pipeline version
# produced the file being loaded (older runs won't have Chemistry/
# Pressure/Flexibility/Sentiment — added across 2026-07-14 to 2026-07-22).
# Every page must check `col in df.columns` before using these, never
# assume they're present.
#
# FIX (2026-07-24, found via live dashboard screenshots): this list
# previously (a) never included "SentimentScore" — a real bug, it was
# simply forgotten when SentimentScore was added on 2026-07-22, so it
# never appeared on the Player Analysis radar chart or the Final Squad
# "strongest in..." justifications despite being a real, computed score —
# and (b) incorrectly included "DSSScore_pct", which is NOT a sub-
# component (it's the final display-scaled OUTPUT score, derived FROM
# the components below, not one of them) — mixing it into the same
# radar/list as PerformanceScore etc. was conceptually wrong, and could
# even get picked as a player's "strongest trait" on the Final Squad
# page, which is meaningless (DSSScore_pct isn't a "trait").
NEW_COMPONENT_COLUMNS = [
    "ChemistryScore", "PressureIndex", "FlexibilityScore", "SentimentScore",
]

# DSSScore_pct is deliberately NOT in NEW_COMPONENT_COLUMNS (see above) —
# pages that want to display it do so explicitly (see app.py's
# display_cols), never through available_component_columns().

COMPONENT_COLUMNS_CORE = [
    "PerformanceScore", "FormScore", "MentalScore",
    "AvailabilityScore", "InternationalScore",
]


def available_component_columns(df: pd.DataFrame) -> list[str]:
    """Returns whichever composite sub-score columns actually exist in df, in a stable display order."""
    return [c for c in COMPONENT_COLUMNS_CORE + NEW_COMPONENT_COLUMNS if c in df.columns]


# ══════════════════════════════════════════════════════════════════════════
# #18 — Historical Trend View Across Gold Snapshots
# ══════════════════════════════════════════════════════════════════════════
# Added 2026-08-01. Every past run_gold.py run leaves its
# gold/players_scored_YYYYMMDD_HHMMSS.csv behind — this reads ALL of them
# (not just the latest, see load_latest_players_scored() above) into one
# long-format DataFrame so a player's DSSScore/Rank can be tracked over
# time instead of only ever showing a single snapshot.
#
# DECISION_DATE / spec #1 CAVEAT — READ BEFORE CHANGING THIS DEFAULT:
# "18_historical_trend_view.md" asks for this loader to be "DECISION_DATE-
# aware (spec #1)" and to exclude, or clearly flag, "stale pre-fix
# snapshots ... non-cutoff-enforced ones". As of this change, spec #1
# (the Bronze/Silver-layer temporal-contamination fix that would make
# run_gold.py accept a validated `--as-of` and stamp its outputs
# accordingly — see config.py § 0) has NOT been implemented anywhere in
# this codebase: run_gold.py has no `--as-of` flag, and no Gold output
# (players_scored_*.csv or squad_summary_*.json) carries any field
# recording whether DECISION_DATE was enforced for that run.
#
# That means, today, there is NO reliable signal to tell a "cutoff-
# enforced" snapshot apart from a "stale pre-fix" one — every single
# gold/players_scored_*.csv currently on disk falls in the same
# "pre-fix, may contain leaked data" bucket described by the spec.
# EXCLUDING all of them would leave this entire feature empty (zero
# history to show), which defeats the point of #18. So the decision here
# is: include every snapshot, but tag each one via `is_pre_cutoff_fix`
# below and let the dashboard page render the "pre-fix, may contain
# leaked data" warning from 18_historical_trend_view.md instead of
# silently hiding the data.
#
# FORWARD COMPATIBILITY: once spec #1 lands and run_gold.py starts
# stamping a `decision_date_enforced: true/false` field into
# squad_summary_*.json (recommended location for that flag, once it
# exists), update `_parse_run_date`'s caller below to read it, and this
# loader will start excluding/flagging correctly without any other
# change needed on the dashboard page.
_SNAPSHOT_FILENAME_RE = re.compile(r"players_scored_(\d{8}_\d{6})\.csv$")


def _parse_run_date(path: Path):
    """Parses the YYYYMMDD_HHMMSS run_date out of a players_scored_*.csv filename."""
    match = _SNAPSHOT_FILENAME_RE.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _is_run_cutoff_enforced(run_timestamp: str) -> bool:
    """
    Looks up gold/squad_summary_<run_timestamp>.json's decision_date_enforced
    field (stamped by run_gold.py's build_squad_summary(), now that spec #1 is
    implemented — see config.py § 0). Fails CLOSED (returns False, i.e.
    "treat as pre-cutoff-fix") if the file is missing, unreadable, or the
    field itself is absent — matching this module's "never silently assume
    a snapshot is safe" posture from the big comment above
    load_all_players_scored_history().

    Deliberately NOT @st.cache_data-wrapped: it's a small per-call helper
    invoked once per snapshot INSIDE load_all_players_scored_history(),
    which is itself the cached entry point (see the decorator below) —
    caching both would just add cache-key bookkeeping for a function that
    already never runs outside its cached caller.
    """
    summary_path = GOLD_DIR / f"squad_summary_{run_timestamp}.json"
    if not summary_path.exists():
        return False
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    return bool(summary.get("decision_date_enforced", False))


@st.cache_data(ttl=300)  # 5 min — see module docstring; extends spec #22's pattern beyond
                          # the literal load_latest_* naming since this reads EVERY
                          # players_scored_*.csv snapshot on every call (heavier than any
                          # single load_latest_*() call), which is exactly the kind of
                          # redundant disk I/O spec #22's Objective section describes.
def load_all_players_scored_history(include_pre_cutoff_fix: bool = True) -> pd.DataFrame:
    """
    Loads EVERY gold/players_scored_*.csv snapshot (not just the latest)
    into one long-format DataFrame: one row per (player, run_date).

    Args:
        include_pre_cutoff_fix: whether to include snapshots that predate
            spec #1's DECISION_DATE-cutoff enforcement (see
            _is_run_cutoff_enforced() above — this now reads a REAL
            per-run flag stamped by run_gold.py, rather than assuming
            every snapshot is pre-fix). Defaults to True so the page has
            data to show even when every existing snapshot predates the
            fix; set False to see only genuinely cutoff-enforced runs.

    Returns:
        A DataFrame with columns `run_date` (datetime), `source_file`,
        `is_pre_cutoff_fix` (bool, now a REAL per-run flag — see
        _is_run_cutoff_enforced()), plus every column from the underlying
        players_scored_*.csv files (player_id, player_name, DSSScore,
        Rank, position, and whichever sub-score columns that particular
        run produced — see NEW_COMPONENT_COLUMNS above; older runs won't
        have all of them). Sorted by run_date ascending. Returns an EMPTY
        DataFrame (not None) if no snapshots are found or none survive
        the `include_pre_cutoff_fix` filter — callers should check
        `.empty`, matching the rest of this module's "never crash the
        page, show an explanatory message instead" convention.
    """
    pattern = str(GOLD_DIR / "players_scored_*.csv")
    paths = sorted(Path(p) for p in glob.glob(pattern))
    if not paths:
        logger.warning("No players_scored_*.csv snapshots found in %s.", GOLD_DIR)
        return pd.DataFrame()

    frames = []
    for path in paths:
        run_date = _parse_run_date(path)
        if run_date is None:
            logger.warning("Skipping %s — could not parse a run_date from its filename.", path.name)
            continue

        run_timestamp = _SNAPSHOT_FILENAME_RE.search(path.name).group(1)
        is_pre_cutoff_fix = not _is_run_cutoff_enforced(run_timestamp)
        if not include_pre_cutoff_fix and is_pre_cutoff_fix:
            continue

        df = pd.read_csv(path)
        meta = pd.DataFrame({
            "run_date": [run_date] * len(df),
            "source_file": [path.name] * len(df),
            "is_pre_cutoff_fix": [is_pre_cutoff_fix] * len(df),
        })
        df = pd.concat([meta, df], axis=1)
        frames.append(df)

    if not frames:
        logger.warning(
            "No players_scored_*.csv snapshots survived the include_pre_cutoff_fix=%s filter.",
            include_pre_cutoff_fix,
        )
        return pd.DataFrame()

    history = pd.concat(frames, ignore_index=True, sort=False)
    return history.sort_values("run_date").reset_index(drop=True)


def compute_biggest_movers(history: pd.DataFrame) -> pd.DataFrame:
    """
    Compares the two most recent run_dates in `history` (as returned by
    load_all_players_scored_history()) and returns a DataFrame of players
    ranked by absolute Rank change between those two snapshots, largest
    movement first.

    Args:
        history: long-format DataFrame from load_all_players_scored_history().

    Returns:
        DataFrame with columns: player_id, player_name, position,
        previous_rank, current_rank, rank_change (positive = moved UP,
        i.e. Rank number got smaller), previous_run_date, current_run_date.
        Empty DataFrame if `history` has fewer than 2 distinct run_dates.
    """
    if history is None or history.empty:
        return pd.DataFrame()

    run_dates = sorted(history["run_date"].unique())
    if len(run_dates) < 2:
        return pd.DataFrame()

    previous_date, current_date = run_dates[-2], run_dates[-1]
    previous = history[history["run_date"] == previous_date].set_index("player_id")
    current = history[history["run_date"] == current_date].set_index("player_id")

    common_ids = current.index.intersection(previous.index)
    if len(common_ids) == 0:
        return pd.DataFrame()

    movers = pd.DataFrame({
        "player_id": common_ids,
        "player_name": current.loc[common_ids, "player_name"].values,
        "position": current.loc[common_ids, "position"].values,
        "previous_rank": previous.loc[common_ids, "Rank"].values,
        "current_rank": current.loc[common_ids, "Rank"].values,
    })
    # Positive rank_change = moved UP the ranking (rank NUMBER decreased).
    movers["rank_change"] = movers["previous_rank"] - movers["current_rank"]
    movers["previous_run_date"] = previous_date
    movers["current_run_date"] = current_date

    return movers.sort_values("rank_change", key=abs, ascending=False).reset_index(drop=True)