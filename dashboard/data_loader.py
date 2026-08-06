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
    ordered by the run_date suffix in the filename (e.g. top26_20260713_145738.csv),
    not file mtime.
    """
    if not directory.exists():
        return None
    pattern = str(directory / f"{prefix}*{suffix}")
    matches = sorted(glob.glob(pattern))  # lexical sort works: YYYYMMDD_HHMMSS sorts correctly
    return Path(matches[-1]) if matches else None


@st.cache_data(ttl=300)  # 5 min: staleness after a fresh run_gold.py vs. avoiding redundant disk reads
def load_latest_top26() -> pd.DataFrame | None:
    """Loads the most recent top26_*.csv. Returns None if none exists yet."""
    path = _latest_file(GOLD_DIR, "top26_", ".csv")
    if path is None:
        logger.warning("No top26_*.csv found in %s — run run_gold.py first.", GOLD_DIR)
        return None
    df = pd.read_csv(path)
    df.attrs["source_file"] = path.name
    return df


@st.cache_data(ttl=300)
def load_latest_players_scored() -> pd.DataFrame | None:
    """Loads the most recent players_scored_*.csv (all 49 candidates, full columns)."""
    path = _latest_file(GOLD_DIR, "players_scored_", ".csv")
    if path is None:
        logger.warning("No players_scored_*.csv found in %s — run run_gold.py first.", GOLD_DIR)
        return None
    df = pd.read_csv(path)
    df.attrs["source_file"] = path.name
    return df


@st.cache_data(ttl=300)
def load_latest_squad_summary() -> dict | None:
    """Loads the most recent squad_summary_*.json."""
    path = _latest_file(GOLD_DIR, "squad_summary_", ".json")
    if path is None:
        logger.warning("No squad_summary_*.json found in %s — run run_gold.py first.", GOLD_DIR)
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["_source_file"] = path.name
    return data


@st.cache_data(ttl=300)
def load_latest_selection_audit() -> dict | None:
    """Loads the most recent selection_audit_*.json, produced by run_selection_audit.py."""
    path = _latest_file(GOLD_DIR, "selection_audit_", ".json")
    if path is None:
        logger.warning("No selection_audit_*.json found in %s — run run_selection_audit.py first.", GOLD_DIR)
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["_source_file"] = path.name
    return data


@st.cache_data(ttl=300)
def load_latest_injury_predictor() -> dict | None:
    """Loads the most recent injury_predictor_summary_*.json, produced by run_injury_predictor.py."""
    path = _latest_file(GOLD_DIR, "injury_predictor_summary_", ".json")
    if path is None:
        logger.warning("No injury_predictor_summary_*.json found in %s — run run_injury_predictor.py first.", GOLD_DIR)
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["_source_file"] = path.name
    return data


@st.cache_data(ttl=300)
def load_latest_chemistry_pairs() -> list[dict]:
    """
    Loads the most recent statsbomb_chemistry/chemistry_pairs_*.json.
    Returns an empty list (not None) if not found — optional/supplementary data.
    """
    chem_dir = BRONZE_DIR / "statsbomb_chemistry"
    path = _latest_file(chem_dir, "chemistry_pairs_", ".json")
    if path is None:
        logger.info("No chemistry_pairs_*.json found in %s.", chem_dir)
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Columns that may or may not exist depending on which pipeline version
# produced the file. Every page must check `col in df.columns` before
# using these, never assume they're present.
NEW_COMPONENT_COLUMNS = [
    "ChemistryScore", "PressureIndex", "FlexibilityScore", "SentimentScore",
]

# DSSScore_pct is deliberately excluded — it's the final display-scaled
# output score, not a sub-component/trait.

COMPONENT_COLUMNS_CORE = [
    "PerformanceScore", "FormScore", "MentalScore",
    "AvailabilityScore", "InternationalScore",
]


def available_component_columns(df: pd.DataFrame) -> list[str]:
    """Returns whichever composite sub-score columns actually exist in df, in a stable display order."""
    return [c for c in COMPONENT_COLUMNS_CORE + NEW_COMPONENT_COLUMNS if c in df.columns]


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
    field. Fails CLOSED (returns False) if the file is missing, unreadable,
    or the field is absent.
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


@st.cache_data(ttl=300)
def load_all_players_scored_history(include_pre_cutoff_fix: bool = True) -> pd.DataFrame:
    """
    Loads EVERY gold/players_scored_*.csv snapshot (not just the latest)
    into one long-format DataFrame: one row per (player, run_date).

    Args:
        include_pre_cutoff_fix: whether to include snapshots that predate
            DECISION_DATE-cutoff enforcement (see _is_run_cutoff_enforced()).
            Defaults to True. Set False to see only cutoff-enforced runs.

    Returns:
        DataFrame with columns `run_date` (datetime), `source_file`,
        `is_pre_cutoff_fix` (bool), plus every column from the underlying
        players_scored_*.csv files. Sorted by run_date ascending. Returns
        an EMPTY DataFrame if no snapshots are found or none survive the
        include_pre_cutoff_fix filter — callers should check `.empty`.
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
    Compares the two most recent run_dates in `history` and returns a
    DataFrame of players ranked by absolute Rank change, largest first.

    Returns:
        DataFrame with columns: player_id, player_name, position,
        previous_rank, current_rank, rank_change (positive = moved UP),
        previous_run_date, current_run_date. Empty if fewer than 2
        distinct run_dates.
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
    movers["rank_change"] = movers["previous_rank"] - movers["current_rank"]
    movers["previous_run_date"] = previous_date
    movers["current_run_date"] = current_date

    return movers.sort_values("rank_change", key=abs, ascending=False).reset_index(drop=True)