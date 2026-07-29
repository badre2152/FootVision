# dashboard/data_loader.py
# Football Atlas Dashboard — Data Loading Utilities
#
# Added 2026-07-14. Deliberately has ZERO Streamlit imports so it can be
# unit-tested on its own (see tests/test_dashboard_data_loader.py) without
# needing a running Streamlit server.
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
from pathlib import Path

import pandas as pd

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


def load_latest_top26() -> pd.DataFrame | None:
    """Loads the most recent top26_*.csv. Returns None if none exists yet."""
    path = _latest_file(GOLD_DIR, "top26_", ".csv")
    if path is None:
        logger.warning("No top26_*.csv found in %s — run run_gold.py first.", GOLD_DIR)
        return None
    df = pd.read_csv(path)
    df.attrs["source_file"] = path.name
    return df


def load_latest_players_scored() -> pd.DataFrame | None:
    """Loads the most recent players_scored_*.csv (all 49 candidates, full columns)."""
    path = _latest_file(GOLD_DIR, "players_scored_", ".csv")
    if path is None:
        logger.warning("No players_scored_*.csv found in %s — run run_gold.py first.", GOLD_DIR)
        return None
    df = pd.read_csv(path)
    df.attrs["source_file"] = path.name
    return df


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


def load_latest_chemistry_pairs() -> list[dict]:
    """
    Loads the most recent statsbomb_chemistry/chemistry_pairs_*.json
    (pairwise pass-network data, ~50 real pairs among the 11 StatsBomb-
    covered candidates — see analytics/scoring/chemistry_score.py).

    Returns an empty list (not None) if not found — the Chemistry page
    handles this gracefully by showing an explanatory message instead of
    crashing, since this is genuinely optional/supplementary data.
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