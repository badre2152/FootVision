# Football Atlas — Morocco WC26 Squad Selection Decision Support System

A medallion-architecture (Bronze → Silver → Gold) data pipeline, plus four
decision-support modules and a password-gated, 12-page Streamlit dashboard
(Home + 11 pages), built to help reason about Morocco's 26-man squad
selection for the 2026 World Cup from a pool of 49 candidate players.

Pipeline end-to-end لاختيار 26 لاعباً للمنتخب المغربي من قائمة 49 مرشحاً.

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

**Current state of the implementation (as of 2026-08-02) — please read
carefully, this replaces an earlier draft of this section that is no longer
accurate:**
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
  independently re-verified.

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

⚠️ Note the two files both prefixed `6_` (`6_fitness.py` and
`6_tactical_board.py`) — this is a real naming collision in the current
tree, not a typo in this README. It doesn't break either page individually,
but it does mean their relative order in Streamlit's sidebar nav is decided
by alphabetical tie-break (`fitness` before `tactical_board`) rather than by
an explicit, unambiguous number. Worth a rename (`6_` / `7_` → shift
everything after) in a future cleanup pass — flagged here, not fixed here,
since it's outside this spec's scope.

---

## Project Structure

```
football_atlas/
├── clients/
│   └── minio_client.py             # upload_json, read_latest_json (real MinIO or filesystem mock via MINIO_MOCK)
├── ingestion/
│   ├── loaders/                    # one file per Bronze source (see table above)
│   └── silver_pipeline.py          # reads all Bronze sources from MinIO, builds Silver
├── analytics/
│   ├── ml/                         # player_clustering.py, injury_predictor.py, selection_classifier.py
│   ├── rotation_strategy.py
│   ├── competition_context.py
│   ├── opponent_analyzer.py
│   ├── peak_detector.py
│   └── set_piece_analyzer.py
├── dashboard/
│   ├── app.py                      # Streamlit home page (calls require_auth() first)
│   ├── auth.py                     # Password gate (spec 06_authentication.md) — imported by every page
│   ├── pages/                      # 11 additional pages (see Dashboard table above)
│   ├── data_loader.py
│   ├── pitch_renderer.py           # matplotlib/mplsoccer pitch (legacy pages)
│   ├── pitch_renderer_html.py      # premium HTML/CSS pitch (Tactical Board redesign)
│   ├── notes_store.py              # coach notes persistence
│   ├── report_utils.py
│   ├── i18n.py
│   ├── components.py
│   └── sidebar.py
├── seeds/
│   └── players_49.csv              # 49 candidate players with all cross-source IDs
├── scripts/
│   └── collect_ids.py              # Resolve missing understat/sofascore/flashscore IDs
├── tests/                          # pytest suite — 452 tests collected in this audit
│                                    #   (3 files need optional extras not pinned tight enough
│                                    #   to auto-install: mplsoccer, rapidfuzz, googleapiclient —
│                                    #   see `make test` / `docs/architecture.md`. CHANGELOG.md's
│                                    #   last full run: 400 passed / 9 known-failing as of 2026-08-02;
│                                    #   re-verify with `pytest tests/ -q` before quoting a number)
├── config/
│   └── scoring_config.py           # All weights, normalization bounds, thresholds — single source of truth
├── pipelines/                      # All orchestration entrypoints, grouped by layer
│   ├── bronze/
│   │   ├── run_bronze.py           # Bronze orchestrator (11 in-script sources)
│   │   ├── run_sd_extractor.py     # Standalone — SofaScore detail column extraction
│   │   ├── run_fotmob.py           # Standalone — Fotmob loader
│   │   └── run_sofifa.py           # Standalone — SoFIFA loader
│   ├── silver/
│   │   └── run_silver.py           # Silver pipeline
│   ├── gold/
│   │   └── run_gold.py             # Gold pipeline (scoring + ranking + Player DNA)
│   └── ml/
│       ├── run_player_dna.py       # Player DNA clustering (standalone re-run)
│       ├── run_injury_predictor.py # Injury Risk Index
│       ├── run_selection_audit.py  # Selection Audit vs. real squad
│       └── run_rotation_strategy.py # Rotation Strategy
├── Makefile                        # See `make help` — kept in sync with the scripts above (invokes them as `python -m pipelines.<layer>.<script>`)
├── docker-compose.yml              # Local MinIO simulator
├── requirements.txt
└── .env                            # MinIO + API_FOOTBALL_KEY — already present; rotate keys before making this repo public
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
- **Storage has no concurrency guarantees** — flat CSV/JSON files on
  MinIO/local disk, no locking, no transactions. Safe for today's
  single-operator sequential use; **not** safe for concurrent writes from
  multiple users/processes (e.g. two people editing the same coach note,
  or two simultaneous pipeline runs) without further work. Full
  discussion, trigger condition, and upgrade path (SQLite/WAL for coach
  notes) in [`docs/architecture.md`](docs/architecture.md).