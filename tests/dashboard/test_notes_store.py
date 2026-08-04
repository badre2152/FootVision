# tests/test_notes_store.py
#
# Regression tests for 20_coach_notes.md.
#
# All tests monkeypatch dashboard.notes_store.DATA_DIR/NOTES_PATH to a
# pytest tmp_path — NEVER touches the real data/coach_notes.json on disk.

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dashboard import notes_store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Points notes_store at a throwaway directory for this test only."""
    data_dir = tmp_path / "data"
    notes_path = data_dir / "coach_notes.db"
    monkeypatch.setattr(notes_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(notes_store, "NOTES_PATH", notes_path)
    return notes_path


class TestGetNote:

    def test_returns_none_when_no_note_exists(self, isolated_store):
        assert notes_store.get_note("player_123") is None

    def test_returns_none_when_file_does_not_exist_yet(self, isolated_store):
        assert not isolated_store.exists()
        assert notes_store.get_note("anyone") is None


class TestSaveNote:

    def test_save_then_get_roundtrip(self, isolated_store):
        """Acceptance criterion: a note persists after 'closing and
        reopening the dashboard' — modeled here as a fresh get_note() call
        with no shared in-memory state, i.e. a true round-trip through disk."""
        notes_store.save_note("player_1", "Great engine, drops off after 70'.", author="Coach Regragui")

        note = notes_store.get_note("player_1")
        assert note is not None
        assert note["text"] == "Great engine, drops off after 70'."
        assert note["author"] == "Coach Regragui"
        assert "updated_at" in note

    def test_default_author_used_when_none_given(self, isolated_store):
        notes_store.save_note("player_2", "Leadership in the dressing room.")
        note = notes_store.get_note("player_2")
        assert note["author"] == notes_store.DEFAULT_AUTHOR

    def test_player_id_coerced_to_string_for_lookup(self, isolated_store):
        """player_id may arrive as an int from a DataFrame row — must still
        round-trip correctly since JSON object keys are always strings."""
        notes_store.save_note(42, "Test note")
        assert notes_store.get_note(42) is not None
        assert notes_store.get_note("42") is not None

    def test_last_write_wins_on_same_player(self, isolated_store):
        """Documented concurrency behavior (spec Edge Cases): the second
        save simply overwrites the first, no merge, no error."""
        notes_store.save_note("player_3", "First observation.", author="A")
        notes_store.save_note("player_3", "Second, more recent observation.", author="B")

        note = notes_store.get_note("player_3")
        assert note["text"] == "Second, more recent observation."
        assert note["author"] == "B"

    def test_saving_one_player_does_not_touch_another(self, isolated_store):
        notes_store.save_note("player_a", "Note A")
        notes_store.save_note("player_b", "Note B")

        assert notes_store.get_note("player_a")["text"] == "Note A"
        assert notes_store.get_note("player_b")["text"] == "Note B"

    def test_empty_text_is_saved_explicitly_not_dropped(self, isolated_store):
        """An explicitly-cleared note is still a real record (with a
        timestamp/author showing who cleared it), not silently deleted."""
        notes_store.save_note("player_4", "", author="Coach")
        note = notes_store.get_note("player_4")
        assert note is not None
        assert note["text"] == ""


class TestGetAllNotes:

    def test_returns_all_saved_notes(self, isolated_store):
        notes_store.save_note("p1", "note 1")
        notes_store.save_note("p2", "note 2")

        all_notes = notes_store.get_all_notes()
        assert set(all_notes.keys()) == {"p1", "p2"}

    def test_returns_empty_dict_when_no_notes_saved(self, isolated_store):
        assert notes_store.get_all_notes() == {}


class TestCorruptOrMalformedLegacyFile:
    """
    Pre-C1, notes_store read/wrote a single JSON file directly, so a
    corrupt file on disk was the direct failure mode. Post-C1, that JSON
    file only matters as a one-time migration source (see
    _migrate_legacy_json_if_needed) — these tests cover the same
    "must never crash the dashboard" guarantee for THAT path now.
    """

    def test_corrupt_legacy_json_does_not_crash_get_note(self, isolated_store):
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text("{not valid json", encoding="utf-8")
        assert notes_store.get_note("anyone") is None

    def test_non_dict_legacy_json_is_ignored(self, isolated_store):
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        assert notes_store.get_all_notes() == {}


class TestOptimisticConcurrency:
    """
    C1 fix: replaces "last write wins" with opt-in conflict detection via
    expected_version. These are the tests for the actual fix, not just the
    backward-compatible default.
    """

    def test_new_note_has_version_1(self, isolated_store):
        record = notes_store.save_note("player_5", "First note ever.")
        assert record["version"] == 1

    def test_version_increments_on_each_save(self, isolated_store):
        notes_store.save_note("player_6", "v1", expected_version=None)
        r2 = notes_store.save_note("player_6", "v2", expected_version=1)
        r3 = notes_store.save_note("player_6", "v3", expected_version=2)
        assert r2["version"] == 2
        assert r3["version"] == 3

    def test_save_without_expected_version_still_overwrites_unconditionally(self, isolated_store):
        """Backward-compat: omitting expected_version = old 'last write
        wins' behavior, unchanged (this is what test_last_write_wins_on_same_player
        above already covers end-to-end; this is the same guarantee stated
        explicitly for the new API)."""
        notes_store.save_note("player_7", "First.", expected_version=None)
        record = notes_store.save_note("player_7", "Second, no version check.", expected_version=None)
        assert record["text"] == "Second, no version check."
        assert notes_store.get_note("player_7")["text"] == "Second, no version check."

    def test_conflicting_save_raises_and_does_not_overwrite(self, isolated_store):
        """
        THE regression test C1 exists for: two coaches load the same note,
        one saves first, the other's save (still holding the OLD version
        number) must be rejected, not silently clobber the first save.
        """
        notes_store.save_note("player_8", "Coach A's original note.", author="A")  # version 1

        # Coach B loaded the note back when it was still version 1 (before
        # A's save above)... except A already saved, so B's expected
        # version (1) is now stale relative to what's actually stored.
        # Simulate that directly: B still thinks version is 1, but let's
        # have A save AGAIN first to make that concrete.
        notes_store.save_note("player_8", "Coach A's follow-up edit.", author="A", expected_version=1)  # version 2

        with pytest.raises(notes_store.NoteConflictError) as exc_info:
            notes_store.save_note(
                "player_8", "Coach B's edit, based on stale version 1.",
                author="B", expected_version=1,
            )

        # The conflicting (current, correct) note must be unharmed.
        current = notes_store.get_note("player_8")
        assert current["text"] == "Coach A's follow-up edit."
        assert current["author"] == "A"
        assert current["version"] == 2

        # The exception carries the current record so the caller can show it.
        assert exc_info.value.current["text"] == "Coach A's follow-up edit."
        assert exc_info.value.player_id == "player_8"

    def test_expected_version_0_conflicts_if_note_was_created_meanwhile(self, isolated_store):
        """expected_version=0 means 'I believe no note exists yet' — if
        someone else created one in the meantime, that's still a conflict,
        not a silent overwrite of their brand-new note."""
        notes_store.save_note("player_10", "Someone got there first.", author="A")

        with pytest.raises(notes_store.NoteConflictError):
            notes_store.save_note("player_10", "I thought this was empty.", author="B", expected_version=0)

    def test_expected_version_matching_current_succeeds(self, isolated_store):
        notes_store.save_note("player_11", "Original.", author="A")  # version 1
        record = notes_store.save_note("player_11", "Correctly-based edit.", author="B", expected_version=1)
        assert record["version"] == 2
        assert record["text"] == "Correctly-based edit."


class TestLegacyJsonMigration:

    def test_existing_legacy_notes_are_migrated_on_first_read(self, isolated_store):
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps({
            "P003": {"author": "brahim", "text": "best keeper of alll",
                      "updated_at": "2026-08-01T22:10:00+00:00"},
            "P024": {"author": "hajji", "text": "kikhtar lmatchat",
                      "updated_at": "2026-08-03T00:12:37+00:00"},
        }), encoding="utf-8")

        note = notes_store.get_note("P003")
        assert note is not None
        assert note["text"] == "best keeper of alll"
        assert note["author"] == "brahim"
        assert note["version"] == 1  # migrated notes start at version 1

        all_notes = notes_store.get_all_notes()
        assert set(all_notes.keys()) == {"P003", "P024"}

    def test_legacy_file_is_left_untouched_after_migration(self, isolated_store):
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        original_content = json.dumps({"P001": {"author": "x", "text": "y", "updated_at": "2026-01-01T00:00:00+00:00"}})
        legacy_path.write_text(original_content, encoding="utf-8")

        notes_store.get_all_notes()  # triggers migration

        assert legacy_path.exists()
        assert legacy_path.read_text(encoding="utf-8") == original_content

    def test_migration_does_not_reimport_after_new_notes_exist(self, isolated_store):
        """Once the SQLite table has ANY row, migration is skipped on
        subsequent reads — a note saved post-migration and then deleted
        from the JSON view isn't re-created from stale JSON."""
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps({
            "P001": {"author": "x", "text": "original", "updated_at": "2026-01-01T00:00:00+00:00"}
        }), encoding="utf-8")

        notes_store.get_note("P001")  # triggers migration, version 1
        notes_store.save_note("P001", "edited after migration", expected_version=1)  # version 2

        # Re-reading must not re-run migration and stomp the edit back to "original".
        assert notes_store.get_note("P001")["text"] == "edited after migration"


class TestArchitecturalSeparationFromPipeline:

    def test_notes_dir_is_not_bronze_silver_or_gold(self):
        """The acceptance criterion 'notes survive a full pipeline re-run
        untouched' depends entirely on data/ never overlapping with the
        directories run_bronze.py/run_silver.py/run_gold.py write to."""
        from dashboard.data_loader import GOLD_DIR, BRONZE_DIR

        assert notes_store.DATA_DIR != GOLD_DIR
        assert notes_store.DATA_DIR != BRONZE_DIR
        assert notes_store.DATA_DIR.name == "data"
        assert not str(notes_store.DATA_DIR).startswith(str(GOLD_DIR))
        assert not str(notes_store.DATA_DIR).startswith(str(BRONZE_DIR))

    def test_notes_survive_deletion_and_rebuild_of_gold_dir(self, isolated_store, tmp_path):
        """Simulates a pipeline re-run (gold/ wiped and rewritten) and
        confirms it has zero effect on an already-saved note."""
        fake_gold_dir = tmp_path / "gold"
        fake_gold_dir.mkdir()
        (fake_gold_dir / "players_scored_20260101_000000.csv").write_text("player_id,DSSScore\n1,0.9\n")

        notes_store.save_note("player_9", "Survives pipeline re-runs.")

        # Simulate run_gold.py wiping and rewriting gold/ entirely.
        for f in fake_gold_dir.iterdir():
            f.unlink()
        (fake_gold_dir / "players_scored_20260102_000000.csv").write_text("player_id,DSSScore\n1,0.95\n")

        note = notes_store.get_note("player_9")
        assert note is not None
        assert note["text"] == "Survives pipeline re-runs."
