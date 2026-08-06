# FootVision — Morocco WC26 Squad Selection Decision Support System

A medallion-architecture (Bronze → Silver → Gold) data pipeline, plus four
decision-support modules and a password-gated, 12-page Streamlit dashboard
(Home + 11 pages), built to help reason about Morocco's 26-man squad
selection for the 2026 World Cup from a pool of 49 candidate players. The
dashboard is trilingual (English / Français / العربية).

Pipeline end-to-end لاختيار 26 لاعباً للمنتخب المغربي من قائمة 49 مرشحاً.

> **Naming note:** the project was renamed **FootVision**
> (GitHub: [`badre2152/FootVision`](https://github.com/badre2152/FootVision)).
> The rename is now applied everywhere — every in-app string that used to
> say the old name (page titles across `dashboard/pages/`, the login
> header in `dashboard/auth.py`, the PDF export title in
> `dashboard/report_utils.py`, `CHANGELOG.md`, `docs/`, `RUN_INSTRUCTIONS.md`,
> `dashboard/i18n/__init__.py`'s header comment) has been updated to
> "FootVision," and `tests/dashboard/test_auth.py`'s assertion on the
> login title was updated to match so it doesn't start failing. Re-ran the
> full suite after the rename (`MINIO_MOCK=1 pytest tests/ -q`): same
> 512 passed / 5 failed split as before the rename (see "Testing" below for
> what those 5 are) — confirms the rename itself didn't break anything.

---

## ⚠️ The `DECISION_DATE` constraint — read this first

This is arguably the single most important fact about this project's design,
so it's stated here instead of only being discoverable by reading code.

**The idea:** a squad-selection decision-support tool is only honest if every
number it shows reflects data that was actually available *as of the moment
the decision would have been made* — not data scraped afterwards, with the
benefit of hindsight. Morocco's real 26-man 2026 World Cup squad was
announced **2026-05-26** by head coach Mohamed Ouahbi. For this project's
Selection Audit module to mean anything as a genuine "would our scoring have
picked the same squad" test, the Bronze data feeding it must be frozen as of
a single, explicit **decision date**, at or before 2026-05-26.

**Current state of the implementation (re-verified 2026-08-06 against the
archive this README ships with) — please read carefully, this replaces an
earlier draft of this section that is no longer accurate:**
- `config/scoring_config.py` defines a single project-wide constant,
  `DECISION_DATE: date = date(2026, 5, 25)` — the day **before** the real
  announcement, so nothing dated on/after announcement day can leak back
  into a tool meant to support that decision.
- It **is** wired through the live path end-to-end: `pipelines/bronze/run_bronze.py`,
  `pipelines/bronze/run_sd_extractor.py`, `pipelines/bronze/run_fotmob.py`, `pipelines/bronze/run_sofifa.py`, and `pipelines/gold/run_gold.py`
  all accept an `--as-of YYYY-MM-DD` flag that defaults to
  `config.DECISION_DATE` when omitted; `pipelines/ml/run_injury_predictor.py` exposes the
  equivalent as `--reference-date`. Every date-relative computation on the
  live path (injury lookback windows, ClubElo snapshot date, age
  calculations) resolves against this date, never against
  `datetime.now()`/`date.today()`.
- This constant has regressed silently at least twice across delivered
  archives (full history in `CHANGELOG.md`, entry `01_temporal_cutoff.md`)
  — once by 7 of 8 touched files being reverted wholesale, once by the date
  itself being quietly moved to `2026-06-01` (**after** the real
  announcement, which would have defeated the whole point). **If you are
  auditing a fresh copy of this repo, do not trust this section — grep
  `config/scoring_config.py` for `DECISION_DATE` and re-run
  `pytest tests/test_temporal_cutoff.py -v` yourself.**
- **Scope, on purpose:** `analytics/opponent_analyzer.py`,
  `analytics/rotation_strategy.py`, and `analytics/ml/selection_classifier.py`
  (dashboard pages Fitness & Rotation / Competition Context / Selection
  Audit) intentionally use **real, post-tournament** data — an audit tool
  has to see the actual outcome to audit against it. Only the live
  Bronze → Silver → Gold → DSSScore path is gated by `DECISION_DATE`.
- **Still open:** a live network re-run against transfermarkt.com /
  api.clubelo.com to confirm `inj_last_date.max() <= DECISION_DATE` on a
  fresh export has not been possible from a network-sandboxed environment —
  the unit-level guarantee is tested, the live end-to-end one is not yet
  independently re-verified. This is still true as of the 2026-08-06 audit;
  `tests/test_temporal_cutoff.py` passed on this archive (`DECISION_DATE`
  is `date(2026, 5, 25)`, unchanged), but that only re-confirms the
  offline/unit guarantee, not a fresh live pull.

---

## What this tool IS / IS NOT

**Is:**
- A decision-*support* tool: it scores, ranks, clusters, and flags — a human
  (coach/technical staff) still decides.
- An offline, batch-run analytics pipeline over historical/public data.
- A transparent scoring system — every weight lives in `config/scoring_config.py`, nothing
  is a black box.

**Is NOT:**
- A live, in-tournament tool (no real-time data feed, no fixture-by-fixture
  updates during a running tournament).
- A replacement for coaching judgement, medical staff input, or tactical
  knowledge that isn't captured in the underlying data sources.
- A calibrated predictive model in most of its components — several
  thresholds (injury risk levels, clutch/pressure cutoffs, etc.) are
  explicitly documented in-code as **illustrative, round numbers**, not
  values fitted against labeled outcomes.

---

## Architecture: Bronze → Silver → Gold → Decision-Support → Dashboard

```
Bronze (raw, per-source)  →  Silver (normalized, joined)  →  Gold (scored, ranked)
        │                                                          │
        │                                                          ├─ Player DNA (K-Means clustering)
        │                                                          ├─ Injury Risk Index (rule-based)
        │                                                          ├─ Selection Audit (LOOCV logistic regression)
        │                                                          └─ Rotation Strategy (rule-based)
        │                                                                 │
        └── Optional: ClubElo (Gold-time, --use-clubelo)                  ▼
                                                              Streamlit Dashboard (Home + 11 pages, 🔒 auth-gated)
```

### Bronze layer — 11 sources orchestrated by `pipelines/bronze/run_bronze.py`, + 3 standalone scripts

`pipelines/bronze/run_bronze.py` runs these 11 sources internally (each individually
skippable via `--skip-<name>`):

| Source | What it provides |
|---|---|
| Wikidata | Biographical data |
| StatsBomb | Open event/match data |
| FBref (club stats) | Gls, Ast, xG, minutes… |
| Transfermarkt | Market value, age, TM id |
| Understat | npxG/xA per 90 |
| Transfermarkt Injury | Injury history |
| FBref Intl | National-team stats/caps |
| Club Stats CSV | Local CSV fallback for Big-5 leagues (Cloudflare-bypass workaround) |
| SofaScore | Match ratings (`__NEXT_DATA__` extraction) |
| SofaScore Detail | Career + season DOM scrape (complements the above) |
| FlashScore | Rating fallback (used when SofaScore is unavailable) |

Both SofaScore rows are backed by `ingestion/loaders/sofascore/`, which is a
package rather than a single file: `client.py` (HTTP/session handling),
`detail_loader.py` + `detail_extractor.py` (career/season DOM scrape and its
Bronze-JSON extraction step — this is what `run_sd_extractor.py` below
consumes), and `parsers_career.py` / `parsers_season.py`. Worth knowing if
you go looking for "the SofaScore loader" and only find an `__init__.py` at
the old single-file path.

Optional sentiment collectors (off by default, `--enable-*`): Reddit (via
Arctic Shift — no credentials needed since Reddit's official API access
closed), YouTube, NewsAPI.

Three sources are **not** covered by `pipelines/bronze/run_bronze.py` and must be run as
separate scripts (see Quick Start below):

| Script | What it does |
|---|---|
| `pipelines/bronze/run_sd_extractor.py` | Not a new external source — extracts structured per-90 columns (`sd_*`) from the raw `sofascore_detail` Bronze JSON. Must run before `pipelines/silver/run_silver.py`. |
| `pipelines/bronze/run_fotmob.py` | Fotmob loader. |
| `pipelines/bronze/run_sofifa.py` | SoFIFA loader (Playwright-based; needs `playwright install chromium`). |

Optionally, `run_gold.py --use-clubelo` can replace the hardcoded league
difficulty table with live ClubElo ratings (`ingestion/loaders/clubelo_loader.py`).
This is **off by default** — the live fetch is untested from a
network-restricted environment; verify GK rankings after your first run
before trusting it.

That's **14+ distinct sources** feeding the pipeline in total.

### Silver layer (`pipelines/silver/run_silver.py`)

Reads every Bronze source from MinIO, resolves conflicting/overlapping
columns via `COALESCE` chains (e.g. `form_final = COALESCE(sofascore_form_5,
flashscore_form_5)`), validates coverage, and writes a single normalized
`silver/players_silver_*.parquet` (+ CSV) table — one row per candidate
player.

### Gold layer (`pipelines/gold/run_gold.py`)

Computes a 9-component composite **DSSScore** per player (weights live in
`config/scoring_config.py`'s `COMPOSITE_WEIGHTS`):

`PerformanceScore`, `TacticalFitScore`, `FormScore`, `MentalScore`,
`AvailabilityScore`, `ChemistryScore`, `PressureIndex`, `FlexibilityScore`,
`SentimentScore`.

Outputs: full scored table, top-26 recommended squad, and a JSON summary
(position breakdown + flagged risks). `pipelines/gold/run_gold.py` also runs Player DNA
clustering inline and rebuilds its own Silver internally from whatever is
currently in MinIO Bronze — it does not depend on a prior local
`pipelines/silver/run_silver.py` run (though results should match if Bronze hasn't changed
between the two).

**Recently refactored (commits `refactor(C2+C4)` and `refactor(C3)`,
2026-08-06 — not yet written up in `CHANGELOG.md`, see the note at the
bottom of this section):** `run_gold.py`'s `main()` used to be a single
"God Function"; it's now decomposed into named stages (`_compute_all_subscores`,
`_apply_final_scoring`, `_run_player_dna`, `_select_and_export_squad`,
`_build_and_export_summary`, `_check_guard_rails`, plus the module-level
`build_top_cols()`), and squad-selection domain logic
(`select_squad_with_position_cap`, `build_squad_summary`) moved out of the
pipeline file entirely into `analytics/squad/squad_selection.py`. Stage
ordering is now enforced at runtime by `pipelines/_stage_guard.py`'s
`StageGuard` — e.g. the DSSScore gate declares `depends_on=("final_score",)`
and `StageGuard.run()` asserts that dependency actually ran first, raising
immediately with a "pipeline ordering violation" message instead of
silently producing wrong output if a future edit reorders the calls. This
replaces what used to be comment-only ordering protection, and it directly
resolves the two "known issues" `CHANGELOG.md` flagged as still-broken on
2026-08-02 (`build_top_cols` missing from `run_gold.py`, `apply_dss_gate`
missing from `analytics/scoring/minutes_filter.py`) — both functions exist
and are wired into `main()` as of this archive; re-verified by reading the
current source, not just trusting the commit message.

⚠️ `SentimentScore`'s pipeline (3 collectors + 4 NLP models) is explicitly
documented as **untested end-to-end against live services** — expect it to
be the sparsest, least-proven component until real runs establish coverage.

📖 What every one of the 214 columns in `players_scored.csv` actually means,
where it comes from, and how well-covered it is: see
[`docs/data_dictionary.md`](docs/data_dictionary.md) (generated —
regenerate with `python scripts/generate_data_dictionary.py` after any
column change, don't hand-edit it).

### Decision-support modules (on top of Gold)

| Module | Entry point | Nature |
|---|---|---|
| Player DNA | `pipelines/ml/run_player_dna.py` | Real ML — K-Means clustering (auto-k via silhouette, k=3..6). Descriptive/exploratory only — explicitly **not** a selection input. |
| Injury Risk Index | `pipelines/ml/run_injury_predictor.py` | Rule-based, transparent composite index. Cutoffs are illustrative, not calibrated against labeled outcomes. |
| Selection Audit | `pipelines/ml/run_selection_audit.py` | Real ML — LOOCV logistic regression comparing DSSScore ranking against Morocco's actual announced 2026 squad. Based on a single n=1 trial (one coach's one squad announcement) — see the temporal-constraint note above. |
| Rotation Strategy | `pipelines/ml/run_rotation_strategy.py` | Rule-based, transparent composite index for group-stage rotation planning. Validated against Morocco's real Group C schedule (Brazil, Scotland, Haiti) when available. |

### Dashboard (Home + 11 pages, `streamlit run dashboard/app.py`)

🔒 **Password-gated since 2026-08-02** (`dashboard/auth.py`, spec
`06_authentication.md`) — every page, including this one, calls
`require_auth()` before rendering any data. Copy
`.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` and set a real
`APP_PASSWORD` before running; the app **fails closed** (blocks with a
config error) if the file or key is missing, it does not silently allow
access. **Change the placeholder password before any non-localhost
deployment.** This is a single shared password (no per-user accounts or
access log) — see `dashboard/auth.py`'s own docstring for the documented
scope limits.

🌐 **Trilingual (EN / FR / AR), `dashboard/i18n/`** — a sidebar language
switcher (English / Français / العربية) stores the choice in
`st.session_state["lang"]`, so it persists across page navigation within a
browser tab. Translation coverage is **not uniform yet**: `sidebar.py`,
`app.py`, `pages/03_final_squad.py`, and `pages/04_selection_audit.py` (incl.
Plotly axis labels) are fully translated; the remaining pages currently only
translate the nav title / page config, per the documented minimum scope in
the `15_localization.md` spec. If you're extending this, check
`dashboard/i18n/__init__.py`'s module docstring first — it lists exactly
what's wired where.

| Page | File | Purpose |
|---|---|---|
| Home | `app.py` | Executive overview |
| Player Analysis | `pages/01_player_analysis.py` | Per-player deep dive |
| Chemistry Matrix | `pages/02_chemistry.py` | Pairwise chemistry scores |
| Final Squad | `pages/03_final_squad.py` | The recommended top-26 |
| Selection Audit | `pages/04_selection_audit.py` | DSSScore vs. the real 2026 squad |
| Fitness & Rotation | `pages/05_fitness.py` | Injury risk + rotation view |
| Tactical Board | `pages/06_tactical_board.py` | Premium HTML/CSS lineup view (formation/tactical layout) |
| Public Sentiment | `pages/07_sentiment.py` | SentimentScore breakdown |
| Competition Context | `pages/08_competition.py` | Opponent/group analysis |
| Historical Trend View | `pages/09_trend_history.py` | DSSScore trend across successive Gold runs — **missing from earlier drafts of this table, added back here** |
| Set Piece & Clutch | `pages/10_setpiece_clutch.py` | Set-piece and pressure performance |
| Squad Builder | `pages/11_squad_builder.py` | Interactive manual squad construction |

✅ The `6_`/`6_` page-numbering collision an earlier draft of this README
flagged (`6_fitness.py` vs `6_tactical_board.py`) is **fixed** — the tree
now has clean, unambiguous two-digit prefixes (`05_fitness.py`,
`06_tactical_board.py`, … `11_squad_builder.py`), so sidebar nav order is
explicit rather than resolved by an alphabetical tie-break. Re-verified by
listing `dashboard/pages/` directly on this archive, not by trusting the
old note.

---

## Project Structure

Reorganized (folders/files only, commit `refactor: reorganize project
structure`) since the last time this section was written — `analytics/`
in particular is now split into six subpackages instead of being a flat
folder of files. Tree below reflects what's actually in this archive:

```
FootVision/                          # repo root (GitHub: badre2152/FootVision — see naming note above)
├── clients/
│   ├── minio_client.py             # upload_json, read_latest_json (real MinIO or filesystem mock via MINIO_MOCK)
│   └── minio_mock.py               # filesystem-backed mock, auto-selected when MINIO_MOCK=1
├── ingestion/
│   ├── loaders/                    # one file per Bronze source (see table above)...
│   │   └── sofascore/              # ...except SofaScore, which is its own sub-package (client/detail_loader/
│   │                                #   detail_extractor/parsers_career/parsers_season)
│   └── silver_pipeline.py          # reads all Bronze sources from MinIO, builds Silver
├── analytics/
│   ├── ml/                         # player_clustering.py, injury_predictor.py, selection_classifier.py
│   ├── nlp/                        # sentiment_analyzer.py
│   ├── performance/                # peak_detector.py, set_piece_analyzer.py
│   ├── scoring/                    # the 9 DSSScore subscore components + normalize.py, minutes_filter.py
│   │                                #   (apply_dss_gate lives here)
│   ├── scouting/                   # competition_context.py, opponent_analyzer.py, rival_benchmark.py
│   └── squad/                      # squad_selection.py (moved out of run_gold.py), depth_chart.py,
│                                    #   rotation_strategy.py, squad_balance.py
├── dashboard/
│   ├── app.py                      # Streamlit home page (calls require_auth() first)
│   ├── auth.py                     # Password gate (spec 06_authentication.md) — imported by every page
│   ├── pages/                      # 11 additional pages, cleanly numbered 01–11 (see Dashboard table above)
│   ├── data_loader.py
│   ├── pitch_renderer.py           # matplotlib/mplsoccer pitch (legacy pages)
│   ├── pitch_renderer_html.py      # premium HTML/CSS pitch (Tactical Board redesign)
│   ├── notes_store.py              # coach notes persistence — SQLite/WAL + optimistic concurrency (see below)
│   ├── report_utils.py
│   ├── i18n/                       # EN/FR/AR dashboard localization (__init__.py + ar.json/en.json/fr.json)
│   ├── components.py               # shared widgets incl. post_hoc_banner() / pre_cutoff_fix_banner()
│   └── sidebar.py
├── seeds/
│   ├── players_49.csv              # 49 candidate players with all cross-source IDs
│   └── rival_reference_players.csv # international benchmark players for rival_benchmark.py
├── scripts/
│   ├── collect_ids.py              # Resolve missing understat/sofascore/flashscore IDs
│   └── generate_data_dictionary.py # Regenerates docs/data_dictionary.md — don't hand-edit that file
├── tests/                          # pytest suite — 37 files. `MINIO_MOCK=1 pytest tests/ -q` on THIS archive
│                                    #   (after installing streamlit/mplsoccer/rapidfuzz/google-api-python-client,
│                                    #   which ARE now pinned in requirements.txt): 517 collected,
│                                    #   512 passed / 5 failed. See "Testing" below for what those 5 are —
│                                    #   re-verify with `make test` before quoting a number, this has moved
│                                    #   before and will move again.
├── config/
│   └── scoring_config.py           # All weights, normalization bounds, thresholds, DECISION_DATE — single source of truth
├── pipelines/
│   ├── _stage_guard.py             # StageGuard — runtime pipeline-ordering enforcement (see Gold layer § above)
│   ├── bronze/
│   │   ├── run_bronze.py           # Bronze orchestrator (11 in-script sources)
│   │   ├── run_sd_extractor.py     # Standalone — SofaScore detail column extraction
│   │   ├── run_fotmob.py           # Standalone — Fotmob loader
│   │   └── run_sofifa.py           # Standalone — SoFIFA loader
│   ├── silver/
│   │   └── run_silver.py           # Silver pipeline
│   ├── gold/
│   │   └── run_gold.py             # Gold orchestrator — decomposed into named _stage functions (see above),
│   │                                #   squad-selection domain logic now lives in analytics/squad/
│   └── ml/
│       ├── run_player_dna.py       # Player DNA clustering (standalone re-run)
│       ├── run_injury_predictor.py # Injury Risk Index
│       ├── run_selection_audit.py  # Selection Audit vs. real squad
│       └── run_rotation_strategy.py # Rotation Strategy
├── docs/
│   ├── architecture.md             # Storage/concurrency model — coach notes RESOLVED 2026-08-04, see below
│   └── data_dictionary.md          # Auto-generated column reference for players_scored.csv (214 columns)
├── data/
│   ├── coach_notes.db              # SQLite (WAL mode) — coach notes; coach_notes.json kept as migration backup
│   └── raw/                        # Club Stats CSV fallback source files (Big-5 leagues)
├── Makefile                        # See `make help` — kept in sync with the scripts above (invokes them as `python -m pipelines.<layer>.<script>`)
├── docker-compose.yml               # Local MinIO simulator
├── requirements.txt
└── .env                             # MinIO + API_FOOTBALL_KEY — already present; rotate keys before making this repo public
```

---

## Quick Start

These mirror the `Makefile` targets (`make help` for the full list) —
the two are kept in sync.

### 1. Install dependencies

```bash
make install
# or: pip install -r requirements.txt --break-system-packages
```

### 2. Start local MinIO (optional — falls back to filesystem mock)

```bash
make start
# Console: http://localhost:9101  (admin credentials in docker-compose.yml)
```

`.env` should already exist in the project root. If it's missing, restore it
from wherever it was last shared — and rotate `API_FOOTBALL_KEY` if this
repo is ever made public.

### 3. (First time only) Resolve missing player IDs

```bash
python scripts/collect_ids.py --source understat  --output seeds/understat_ids_draft.csv
python scripts/collect_ids.py --source sofascore   --output seeds/sofascore_ids_draft.csv
python scripts/collect_ids.py --source flashscore  --output seeds/flashscore_ids_draft.csv
# Review drafts, fill in missing IDs, merge into seeds/players_49.csv
```

### 4. Run the pipeline

```bash
make bronze              # 11 in-script sources
make bronze-sd-extractor # required before silver — extracts sd_* columns
make bronze-fotmob       # standalone source
make bronze-sofifa       # standalone source (needs: playwright install chromium)
make silver
make gold
# or, for the core chain only:
make pipeline            # bronze -> silver -> gold
```

### 5. Decision-support modules

```bash
make ml-dna
make ml-injury
make ml-selection-audit
make ml-rotation
```

### 6. Dashboard

```bash
# First time only — the dashboard is password-gated (see § above):
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# then edit .streamlit/secrets.toml and set APP_PASSWORD to something real

make dashboard
```

### 7. Test

```bash
make test        # pytest tests/ -q — full suite
```

---

## Testing — actual result on this archive (2026-08-06)

`make test` (i.e. `MINIO_MOCK=1 pytest tests/ -q`) requires `streamlit`,
`google-api-python-client`, `mplsoccer`, and `rapidfuzz` — all four **are**
pinned in `requirements.txt` on this archive, so a plain `make install`
should pull them in (an earlier draft of this README claimed these were
"optional extras not pinned tight enough to auto-install"; re-checking
`requirements.txt` directly shows that's no longer accurate, if it ever
was).

With every dependency installed, the full suite on this exact archive
collects **517 tests: 512 passed, 5 failed.** Don't trust that split
without re-running it yourself — it's moved before (`CHANGELOG.md` records
400/9 as of 2026-08-02) and `CHANGELOG.md` has not been updated for any of
the four most recent commits (see the note below), so nothing else is
tracking it either. The 5 current failures, so you don't have to
re-diagnose them from scratch:

- **2× `tests/dashboard/test_dashboard_post_hoc_banner.py`**
  (`08_competition.py`, `04_selection_audit.py`): both pages *do* call
  `post_hoc_banner()` in the source (confirmed by grep), but the AppTest
  run in this sandbox has no `gold/top26_*.csv` to load (`gold/` ships
  with only `.gitkeep`), and the page appears to return before reaching the
  banner when there's no Gold data to show. Not confirmed as a real bug —
  most likely these two tests need a Gold snapshot to exist first (i.e.
  run `make pipeline` before `make test`), which the checklist in
  `CHANGELOG.md` doesn't mention as a test-suite precondition. Worth
  confirming with a Gold run before assuming either page is broken.
- **2× `tests/dashboard/test_dashboard_trend_history_page.py`**: same
  likely cause — `09_trend_history.py` reads Gold snapshot history, and
  this sandbox has none. The "Biggest movers" `st.subheader` these tests
  look for is present in the page's source at line ~102, so this reads as
  a missing-fixture-data issue, not a missing-feature one.
- **1× `tests/test_concurrency_docs.py::test_notes_store_cross_references_architecture_doc`**:
  this one's real, not a fixture artifact. The test asserts the literal
  string `"docs/architecture.md"` appears somewhere in `dashboard/notes_store.py`'s
  source; it doesn't (checked directly). `docs/architecture.md` itself
  *does* link back to `notes_store.py`, so the cross-reference is
  one-directional. Small, easy fix (add a line to `notes_store.py`'s module
  docstring) — flagged here rather than fixed here since it's a one-line
  doc change, not a design decision.

**`CHANGELOG.md` is now stale relative to `git log`.** Its most recent
entry is dated 2026-08-02, but `git log --oneline` shows four commits after
that — the Coach Notes SQLite migration, the `StageGuard` addition, the
`run_gold.py` decomposition, and the `analytics/` reorganization — none of
which have a corresponding dated entry. This is exactly the process gap
`CHANGELOG.md`'s own "Pre-delivery checklist" (item 4: "every new confirmed
fix this session gets its own dated entry") exists to catch, happening
again to this project's most recent, and arguably most structurally
significant, round of changes.

---

## Known limitations / roadmap

- **`DECISION_DATE` is enforced on the live path** (see the section at the
  top) but has a documented history of silently regressing between
  delivered archives — treat `tests/test_temporal_cutoff.py` passing as the
  source of truth, not this document. Live-network re-verification
  (transfermarkt.com / api.clubelo.com) of the end-to-end cutoff is still
  outstanding.
- **Dashboard access control is a single shared password**, no per-user
  accounts and no access log (`dashboard/auth.py`) — fine for one
  coaching-staff group, not sufficient if individual accountability is ever
  required.
- `SentimentScore`'s collector + NLP pipeline is untested end-to-end against
  live services.
- `--use-clubelo` in `pipelines/gold/run_gold.py` has untested live-fetch behavior from
  network-restricted environments; its parsing/matching/scoring logic is
  unit-tested, the live call is not.
- Several scoring cutoffs (injury risk levels, clutch/pressure thresholds)
  are explicitly documented in-code as illustrative round numbers, not
  values calibrated against labeled outcomes.
- Selection Audit's classifier is trained on a single real-world label
  (n=1 — one coach's one squad announcement) — treat its output
  qualitatively, not as a statistically robust classifier.
- **Bronze/Silver/Gold storage still has no concurrency guarantees** —
  flat CSV/JSON files on MinIO/local disk, no locking, no transactions.
  Two simultaneous `run_bronze.py`/`run_gold.py` runs can interleave
  partial writes and nothing detects it. Documented in
  [`docs/architecture.md`](docs/architecture.md) as a deliberate,
  low-risk trade-off at today's single-operator scale (this data is fully
  machine-regenerated, so a bad write just means re-running the pipeline,
  not losing anything irreplaceable) — revisit if/when multiple staff
  accounts can trigger pipeline runs at the same time.
- **Coach Notes — RESOLVED 2026-08-04, no longer flat-file/last-write-wins.**
  An earlier draft of this README listed Coach Notes under the same
  no-concurrency limitation as Bronze/Silver/Gold; that's no longer true.
  `dashboard/notes_store.py` now backs `data/coach_notes.db` with SQLite in
  WAL mode plus optimistic concurrency: every note carries a `version`, and
  `save_note()` raises `NoteConflictError` (surfaced to the user on
  `pages/01_player_analysis.py`, not swallowed) if someone else saved a
  newer version first, instead of silently overwriting their edit. The old
  `data/coach_notes.json` is migrated automatically on first read and kept
  in place as a backup. Full design rationale in
  [`docs/architecture.md`](docs/architecture.md)'s "Concurrency & Storage
  Guarantees" section — re-verified against the current
  `dashboard/notes_store.py` source for this update, not just taken from
  the doc's own claim.