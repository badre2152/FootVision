from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR     = PROJECT_ROOT / "data"
NOTES_PATH   = DATA_DIR / "coach_notes.db"

DEFAULT_AUTHOR = "Coaching staff"


class NoteConflictError(Exception):
    """
    Raised by save_note() when expected_version is given but doesn't match
    what's currently stored — i.e. someone else saved a note for this same
    player_id after this caller last read it. Carries the CURRENT
    (conflicting) record so the caller can show it to the user instead of
    just failing silently or crashing.
    """
    def __init__(self, player_id: str, current: dict):
        self.player_id = player_id
        self.current = current
        super().__init__(
            f"Note for player_id={player_id!r} was changed by someone else "
            f"(now at version {current['version']}, last edited by "
            f"{current['author']!r} at {current['updated_at']}) since it was "
            f"last loaded. Not overwritten."
        )


@contextmanager
def _connect():
    """
    Opens a connection with WAL mode enabled (readers don't block writers
    and vice versa) and a busy_timeout so a writer waits briefly for a
    concurrent writer instead of failing immediately under normal contention.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(NOTES_PATH, timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        _migrate_legacy_json_if_needed(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            player_id  TEXT PRIMARY KEY,
            text       TEXT NOT NULL,
            author     TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version    INTEGER NOT NULL DEFAULT 1
        )
    """)


def _migrate_legacy_json_if_needed(conn: sqlite3.Connection) -> None:
    """
    One-time migration: if the old coach_notes.json exists and the new
    table is still empty, import every note from it as version 1. Runs on
    every connection but is a no-op after the first successful migration.
    Never deletes the old JSON file — it's left in place as a backup.
    """
    legacy_json_path = DATA_DIR / "coach_notes.json"
    if not legacy_json_path.exists():
        return

    (count,) = conn.execute("SELECT COUNT(*) FROM notes").fetchone()
    if count > 0:
        return

    try:
        with open(legacy_json_path, encoding="utf-8") as f:
            legacy = json.load(f)
        if not isinstance(legacy, dict):
            logger.warning(
                "Legacy %s did not contain a JSON object — skipping migration.",
                legacy_json_path,
            )
            return
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            "Could not read legacy %s (%s) — skipping migration, starting "
            "with an empty notes store.", legacy_json_path, e,
        )
        return

    migrated = 0
    for player_id, record in legacy.items():
        if not isinstance(record, dict):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO notes (player_id, text, author, updated_at, version) "
            "VALUES (?, ?, ?, ?, 1)",
            (
                str(player_id),
                record.get("text", ""),
                record.get("author", DEFAULT_AUTHOR),
                record.get("updated_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
            ),
        )
        migrated += 1

    if migrated:
        logger.info(
            "Migrated %d note(s) from legacy %s into %s (version=1 each). "
            "The old JSON file was left in place, untouched.",
            migrated, legacy_json_path, NOTES_PATH,
        )


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "text":       row["text"],
        "author":     row["author"],
        "updated_at": row["updated_at"],
        "version":    row["version"],
    }


def get_note(player_id: str) -> dict | None:
    """
    Returns {"text": str, "author": str, "updated_at": ISO-8601 str,
    "version": int} for this player_id, or None if no note has ever been
    saved for them.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT text, author, updated_at, version FROM notes WHERE player_id = ?",
            (str(player_id),),
        ).fetchone()
        return _row_to_dict(row) if row is not None else None


def get_all_notes() -> dict:
    """
    Returns the full {player_id: note_record} store — used by pages that
    need to show notes for many players at once without one query per player.
    """
    with _connect() as conn:
        rows = conn.execute("SELECT player_id, text, author, updated_at, version FROM notes").fetchall()
        return {row["player_id"]: _row_to_dict(row) for row in rows}


def save_note(
    player_id: str,
    text: str,
    author: str | None = None,
    expected_version: int | None = None,
) -> dict:
    """
    Saves (creates or overwrites) the note for player_id. An empty/
    whitespace-only text still gets saved as a real record (an explicitly-
    cleared note), not silently dropped.

    expected_version (optional): pass the `version` from a previously-
    loaded note (get_note()'s return value) to enable conflict detection.
    If someone else has saved a note for this same player_id since then,
    raises NoteConflictError instead of overwriting their change. Pass 0
    if no note existed yet when the caller last checked.

    Leave expected_version=None (default) to skip the check entirely and
    always overwrite.

    Returns the saved note record.
    """
    with _connect() as conn:
        existing = conn.execute(
            "SELECT version FROM notes WHERE player_id = ?", (str(player_id),)
        ).fetchone()
        current_version = existing["version"] if existing is not None else 0

        if expected_version is not None and expected_version != current_version:
            current_row = conn.execute(
                "SELECT text, author, updated_at, version FROM notes WHERE player_id = ?",
                (str(player_id),),
            ).fetchone()
            current_record = _row_to_dict(current_row) if current_row is not None else {
                "text": "", "author": DEFAULT_AUTHOR,
                "updated_at": None, "version": 0,
            }
            raise NoteConflictError(str(player_id), current_record)

        new_version = current_version + 1
        record = {
            "text":       text or "",
            "author":     author or DEFAULT_AUTHOR,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version":    new_version,
        }
        conn.execute(
            "INSERT INTO notes (player_id, text, author, updated_at, version) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(player_id) DO UPDATE SET text=excluded.text, author=excluded.author, "
            "updated_at=excluded.updated_at, version=excluded.version",
            (str(player_id), record["text"], record["author"], record["updated_at"], new_version),
        )
        logger.info(
            "Saved coach note for player_id=%s (author=%s, %d chars, version=%d).",
            player_id, record["author"], len(record["text"]), new_version,
        )
        return record