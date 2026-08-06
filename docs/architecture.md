# Architecture Notes — FootVision

This file holds architecture-level decisions that don't fit naturally in
`README.md`'s user-facing Quick Start / pipeline walkthrough, starting with
the project's storage/concurrency model (spec `21_concurrency_docs.md`).

---

## Concurrency & Storage Guarantees

**This project uses flat-file storage (CSV/JSON on MinIO/local disk) for
Bronze/Silver/Gold, with no locking or transactional guarantees. Coach
Notes (below) no longer follows this pattern — see "RESOLVED" note.**

Concretely:

- **Bronze / Silver / Gold** (`bronze/`, `silver/`, `gold/`): every
  `run_bronze.py` / `run_silver.py` / `run_gold.py` invocation reads
  whatever's currently on MinIO/local disk and writes a fresh,
  run-date-suffixed file (e.g. `players_scored_20260801_160628.csv`). There
  is no row-level locking, no "table" concept, no transaction — two
  pipeline runs writing at the same time can interleave partial writes on
  the same object, and nothing detects or prevents that. **Still true —
  not addressed by the Coach Notes fix below.**
- **Coach/Staff Notes** (`data/coach_notes.db`, spec `20_coach_notes.md`,
  see `dashboard/notes_store.py`): **RESOLVED 2026-08-04.** Used to be a
  single JSON file, read-modify-written as a whole on every `save_note()`
  call, with "last write wins" on concurrent edits to the same player. Now
  backed by SQLite in WAL mode, with optimistic concurrency (`version`
  column + `expected_version` parameter on `save_note()`) — a save whose
  expected version is stale raises `NoteConflictError` instead of silently
  overwriting someone else's edit. `dashboard/pages/01_player_analysis.py`
  passes the loaded version and shows the conflicting note to the user
  instead of clobbering it. The old JSON file is migrated automatically on
  first read and left in place, untouched, as a backup. See
  `notes_store.py`'s module docstring for the full design.

### Why the remaining (Bronze/Silver/Gold) gap is fine today

At the project's current scale — a single developer/analyst running
scripts sequentially, 49 tracked players — this is a **non-issue in
practice**. Nobody is triggering two pipeline runs at once. This is a
deliberate, low-risk trade-off for a graduation-project scale system, not
an oversight: adding real locking or a database now would be engineering
effort spent on a problem that doesn't exist yet. Unlike Coach Notes, this
data is fully machine-regenerated (never user-authored), so a lost/
interleaved write here means re-running the pipeline, not losing
irreplaceable input — meaningfully lower risk than the Coach Notes case
that has now been fixed.

### Trigger condition — when the remaining gap stops being fine

**Revisit this the moment more than one person can be logged in and
triggering pipeline runs simultaneously.** Concretely, that means:

- Spec `06` (authentication) shipping and enabling multiple simultaneous
  staff accounts who can each trigger `run_bronze.py` / `run_gold.py` —
  note that spec `06` **has** shipped (see `dashboard/auth.py`), which is
  exactly what triggered the Coach Notes fix above; the pipeline-writes
  gap below remains open because multi-user *pipeline-triggering* (as
  opposed to multi-user *dashboard viewing/note-taking*) is not yet a
  real usage pattern for this project.
- Multiple people independently triggering `run_bronze.py` /
  `run_gold.py` re-runs against the same MinIO bucket at the same time.

### What the actual fix looks like, when triggered

- **Pipeline writes (Bronze/Silver/Gold):** add basic file-locking around
  write operations (e.g. a lock file per bucket/prefix, or MinIO's
  conditional-write / versioning features if available), so two
  concurrent runs fail loudly or queue instead of silently interleaving.
- ~~**Coach Notes:** migrate `data/coach_notes.json` to **SQLite with WAL
  mode**~~ — **done, see "RESOLVED" above.**

---

*Cross-references: `README.md`'s "Known limitations / roadmap" section
links here. `dashboard/notes_store.py`'s module docstring links here for
the concurrency behavior it implements today.*
