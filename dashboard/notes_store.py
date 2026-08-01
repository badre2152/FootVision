# dashboard/notes_store.py
# Football Atlas — Coach/Staff Notes persistence (spec #20)
#
# Added 2026-08-01.
#
# ─────────────────────────────────────────────────────────────────────────
# WHY THIS LIVES OUTSIDE Bronze/Silver/Gold
# ─────────────────────────────────────────────────────────────────────────
# Every other piece of data in this project (bronze/, silver/, gold/) is
# SCRAPED and gets fully regenerated on every pipeline run (run_bronze.py
# -> run_silver.py -> run_gold.py write timestamped files; nothing reads
# an old file back in and merges it). A coach's free-text note is the
# opposite: USER-GENERATED, must survive forever until deliberately
# changed, and must NEVER be touched by a pipeline re-run.
#
# So notes get their own top-level `data/` directory (sibling to bronze/
# silver/gold, not nested under any of them) and their own tiny JSON
# store — no MinIO, no Bronze/Silver/Gold pipeline involvement at all, per
# 20_coach_notes.md's explicit "Technical Requirements". This also means
# a note survives even if bronze/silver/gold are deleted and rebuilt from
# scratch (see tests/test_notes_store.py for the regression test proving
# this).
#
# Deliberately has ZERO Streamlit imports (same pattern as
# dashboard/data_loader.py) so it's unit-testable without a running
# Streamlit server.
#
# ─────────────────────────────────────────────────────────────────────────
# CONCURRENCY: "last write wins" (documented per spec's Edge Cases section)
# ─────────────────────────────────────────────────────────────────────────
# If two people save a note for the same player_id at nearly the same
# time, whichever save_note() call's write reaches disk last simply
# overwrites the other — there is no merge, no conflict detection, no
# locking. At this project's current single-coach-editing-at-a-time
# scale that's an acceptable, explicitly-chosen trade-off, NOT an
# oversight. Once spec #6's authentication exists, the `author` field at
# least tells you WHO clobbered WHAT, even though it can't prevent it.
#
# See docs/architecture.md ("Concurrency & Storage Guarantees", spec
# #21) for the full project-wide discussion of this trade-off — the
# trigger condition for when this needs a real fix (SQLite/WAL is the
# suggested upgrade path), and how it relates to Bronze/Silver/Gold's
# own lack of write-locking.

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR     = PROJECT_ROOT / "data"
NOTES_PATH   = DATA_DIR / "coach_notes.json"

DEFAULT_AUTHOR = "Coaching staff"


def _load_all() -> dict:
    """
    Reads the full notes store from disk. Returns {} if the file doesn't
    exist yet (first-ever note) or is corrupt (logs a warning rather than
    crashing the dashboard — a broken notes file must never take down
    every other page).
    """
    if not NOTES_PATH.exists():
        return {}
    try:
        with open(NOTES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("coach_notes.json did not contain a JSON object — ignoring its contents.")
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s (%s) — treating as empty (no notes lost on disk, just unreadable this run).", NOTES_PATH, e)
        return {}


def _save_all(notes: dict) -> None:
    """
    Writes the full notes store to disk atomically: write to a temp file
    in the same directory, then os.replace() it over the real path. This
    avoids ever leaving coach_notes.json half-written (e.g. Streamlit
    process killed mid-save) — a partial write would otherwise silently
    lose every OTHER player's notes too, not just the one being edited.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=".coach_notes_", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2, sort_keys=True)
        Path(tmp_path).replace(NOTES_PATH)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def get_note(player_id: str) -> dict | None:
    """
    Returns {"text": str, "author": str, "updated_at": ISO-8601 str} for
    this player_id, or None if no note has ever been saved for them.
    """
    notes = _load_all()
    return notes.get(str(player_id))


def get_all_notes() -> dict:
    """
    Returns the full {player_id: note_record} store — used by pages that
    need to show notes for many players at once (e.g. Final Squad's
    compact view) without re-reading the file once per player.
    """
    return _load_all()


def save_note(player_id: str, text: str, author: str | None = None) -> dict:
    """
    Saves (overwrites) the note for player_id — "last write wins" (see
    module docstring). An empty/whitespace-only text still gets saved as
    a real record (an explicitly-cleared note), not silently dropped, so
    "who cleared this and when" stays visible.

    Returns the saved note record.
    """
    notes = _load_all()
    record = {
        "text": text or "",
        "author": author or DEFAULT_AUTHOR,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    notes[str(player_id)] = record
    _save_all(notes)
    logger.info("Saved coach note for player_id=%s (author=%s, %d chars).", player_id, record["author"], len(record["text"]))
    return record