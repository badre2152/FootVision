# Architecture Notes — Football Atlas

This file holds architecture-level decisions that don't fit naturally in
`README.md`'s user-facing Quick Start / pipeline walkthrough, starting with
the project's storage/concurrency model (spec `21_concurrency_docs.md`).

---

## Concurrency & Storage Guarantees

**This project uses flat-file storage (CSV/JSON on MinIO/local disk) with
no locking or transactional guarantees.**

Concretely:

- **Bronze / Silver / Gold** (`bronze/`, `silver/`, `gold/`): every
  `run_bronze.py` / `run_silver.py` / `run_gold.py` invocation reads
  whatever's currently on MinIO/local disk and writes a fresh,
  run-date-suffixed file (e.g. `players_scored_20260801_160628.csv`). There
  is no row-level locking, no "table" concept, no transaction — two
  pipeline runs writing at the same time can interleave partial writes on
  the same object, and nothing detects or prevents that.
- **Coach/Staff Notes** (`data/coach_notes.json`, spec `20_coach_notes.md`,
  see `dashboard/notes_store.py`): a single JSON file, read-modify-written
  as a whole on every `save_note()` call. Two people saving a note for the
  *same* player at nearly the same moment will not merge or conflict —
  whichever write reaches disk last simply overwrites the other
  ("**last write wins**", stated explicitly rather than left as an
  implicit/undefined behavior — see that module's docstring). The write
  itself is atomic (temp file + `os.replace()`), so a crash mid-save can't
  corrupt the *whole* file, but it does nothing to prevent one user's note
  from silently clobbering another's.

### Why this is fine today

At the project's current scale — a single developer/analyst running
scripts sequentially, 49 tracked players, no concurrent dashboard users —
this is a **non-issue in practice**. Nobody is triggering two pipeline
runs at once, and nobody is racing another person to save the same coach
note in the same second. This is a deliberate, low-risk trade-off for a
graduation-project scale system, not an oversight: adding real locking or
a database now would be engineering effort spent on a problem that
doesn't exist yet.

### Trigger condition — when this stops being fine

**Revisit this the moment more than one person can be logged in and
interacting with the system simultaneously.** Concretely, that means:

- Spec `06` (authentication) shipping and enabling multiple simultaneous
  staff accounts — the exact scenario that turns "nobody else is touching
  this file right now" from a safe assumption into a real race condition.
- Multiple people independently triggering `run_bronze.py` /
  `run_gold.py` re-runs against the same MinIO bucket at the same time.
- Multiple coaching staff members actively editing notes (spec `20`)
  during the same live scouting/selection session — the scenario spec
  `20_coach_notes.md`'s own "Edge Cases" section calls out directly.

### What the actual fix looks like, when triggered

- **Pipeline writes (Bronze/Silver/Gold):** add basic file-locking around
  write operations (e.g. a lock file per bucket/prefix, or MinIO's
  conditional-write / versioning features if available), so two
  concurrent runs fail loudly or queue instead of silently interleaving.
- **Coach Notes:** migrate `data/coach_notes.json` to **SQLite with WAL
  mode** — a lightweight upgrade path that keeps the "no separate DB
  server to run" property this project's storage choices have had from
  the start, while adding real concurrent-write safety (row-level
  transactions, no "last write wins" data loss). This does not require
  re-architecting anything else — `notes_store.py`'s public interface
  (`get_note` / `save_note` / `get_all_notes`) would stay the same; only
  its internals change.

---

*Cross-references: `README.md`'s "Known limitations / roadmap" section
links here. `dashboard/notes_store.py`'s module docstring links here for
the concurrency behavior it implements today.*
