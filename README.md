# Football Atlas — Morocco WC26 Squad Selection Decision Support System

A medallion-architecture (Bronze → Silver → Gold) data pipeline, plus four
decision-support modules and an 11-page Streamlit dashboard, built to help
reason about Morocco's 26-man squad selection for the 2026 World Cup from a
pool of 49 candidate players.

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
picked the same squad" test, the Bronze data feeding it should, in principle,
be frozen as of a single, explicit **decision date** — ideally at or before
2026-05-26.

**Current state of the implementation — please read carefully:**
- There is **no single, enforced `DECISION_DATE` variable** wired through
  Bronze → Silver → Gold today. This is a known gap, not a hidden feature.
- The one real "as-of" hook that exists is `run_injury_predictor.py`'s
  `--reference-date` flag (used to compute player age as-of a given date;
  defaults to today if not passed).
- In practice, the Bronze snapshots in this repo (see `logs/` and `gold/`
  filenames) were captured across **2026-06-23 → 2026-07-30** — i.e.
  **after** the real squad was already public knowledge. That means any
  Selection Audit output you see from these runs is a **retrospective
  validation exercise**, not a blind prediction, and should be read/presented
  that way.
- If you re-run this pipeline for a *future* decision (e.g. a hypothetical
  2030 squad announcement), you would need to manually ensure every Bronze
  source you scrape only reflects information available before that date —
  the pipeline does not currently guard against this for you.

Treat "add a real, enforced `DECISION_DATE` gate across the whole pipeline"
as a standing TODO, not a solved problem.

---

## What this tool IS / IS NOT

**Is:**
- A decision-*support* tool: it scores, ranks, clusters, and flags — a human
  (coach/technical staff) still decides.
- An offline, batch-run analytics pipeline over historical/public data.
- A transparent scoring system — every weight lives in `config.py`, nothing
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
                                                                    Streamlit Dashboard (11 pages)
```

### Bronze layer — 11 sources orchestrated by `run_bronze.py`, + 3 standalone scripts

`run_bronze.py` runs these 11 sources internally (each individually
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

Three sources are **not** covered by `run_bronze.py` and must be run as
separate scripts (see Quick Start below):

| Script | What it does |
|---|---|
| `run_sd_extractor.py` | Not a new external source — extracts structured per-90 columns (`sd_*`) from the raw `sofascore_detail` Bronze JSON. Must run before `run_silver.py`. |
| `run_fotmob.py` | Fotmob loader. |
| `run_sofifa.py` | SoFIFA loader (Playwright-based; needs `playwright install chromium`). |

Optionally, `run_gold.py --use-clubelo` can replace the hardcoded league
difficulty table with live ClubElo ratings (`ingestion/loaders/clubelo_loader.py`).
This is **off by default** — the live fetch is untested from a
network-restricted environment; verify GK rankings after your first run
before trusting it.

That's **14+ distinct sources** feeding the pipeline in total.

### Silver layer (`run_silver.py`)

Reads every Bronze source from MinIO, resolves conflicting/overlapping
columns via `COALESCE` chains (e.g. `form_final = COALESCE(sofascore_form_5,
flashscore_form_5)`), validates coverage, and writes a single normalized
`silver/players_silver_*.parquet` (+ CSV) table — one row per candidate
player.

### Gold layer (`run_gold.py`)

Computes a 9-component composite **DSSScore** per player (weights live in
`config.py`'s `COMPOSITE_WEIGHTS`):

`PerformanceScore`, `TacticalFitScore`, `FormScore`, `MentalScore`,
`AvailabilityScore`, `ChemistryScore`, `PressureIndex`, `FlexibilityScore`,
`SentimentScore`.

Outputs: full scored table, top-26 recommended squad, and a JSON summary
(position breakdown + flagged risks). `run_gold.py` also runs Player DNA
clustering inline and rebuilds its own Silver internally from whatever is
currently in MinIO Bronze — it does not depend on a prior local
`run_silver.py` run (though results should match if Bronze hasn't changed
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
| Player DNA | `run_player_dna.py` | Real ML — K-Means clustering (auto-k via silhouette, k=3..6). Descriptive/exploratory only — explicitly **not** a selection input. |
| Injury Risk Index | `run_injury_predictor.py` | Rule-based, transparent composite index. Cutoffs are illustrative, not calibrated against labeled outcomes. |
| Selection Audit | `run_selection_audit.py` | Real ML — LOOCV logistic regression comparing DSSScore ranking against Morocco's actual announced 2026 squad. Based on a single n=1 trial (one coach's one squad announcement) — see the temporal-constraint note above. |
| Rotation Strategy | `run_rotation_strategy.py` | Rule-based, transparent composite index for group-stage rotation planning. Validated against Morocco's real Group C schedule (Brazil, Scotland, Haiti) when available. |

### Dashboard (11 pages, `streamlit run dashboard/app.py`)

| Page | Purpose |
|---|---|
| Home (`app.py`) | Executive overview |
| Player Analysis | Per-player deep dive |
| Chemistry Matrix | Pairwise chemistry scores |
| Final Squad | The recommended top-26 |
| Selection Audit | DSSScore vs. the real 2026 squad |
| Fitness & Rotation | Injury risk + rotation view |
| Tactical Board | Formation/tactical layout |
| Public Sentiment | SentimentScore breakdown |
| Competition Context | Opponent/group analysis |
| Set Piece & Clutch | Set-piece and pressure performance |
| Squad Builder | Interactive manual squad construction |

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
│   ├── app.py                      # Streamlit home page
│   ├── pages/                      # 10 additional pages
│   ├── data_loader.py
│   ├── pitch_renderer.py
│   └── sidebar.py
├── seeds/
│   └── players_49.csv              # 49 candidate players with all cross-source IDs
├── scripts/
│   └── collect_ids.py              # Resolve missing understat/sofascore/flashscore IDs
├── tests/                          # pytest suite (285 tests as of this writing — see `make test`)
├── config.py                       # All weights, normalization bounds, thresholds — single source of truth
├── run_bronze.py                   # Bronze orchestrator (11 in-script sources)
├── run_sd_extractor.py             # Standalone — SofaScore detail column extraction
├── run_fotmob.py                   # Standalone — Fotmob loader
├── run_sofifa.py                   # Standalone — SoFIFA loader
├── run_silver.py                   # Silver pipeline
├── run_gold.py                     # Gold pipeline (scoring + ranking + Player DNA)
├── run_player_dna.py               # Player DNA clustering (standalone re-run)
├── run_injury_predictor.py         # Injury Risk Index
├── run_selection_audit.py          # Selection Audit vs. real squad
├── run_rotation_strategy.py        # Rotation Strategy
├── Makefile                        # See `make help` — kept in sync with the scripts above
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
make dashboard
```

### 7. Test

```bash
make test        # pytest tests/ -q — full suite
```

---

## Known limitations / roadmap

- **`DECISION_DATE` is not yet a real, enforced pipeline-wide gate** — see
  the section at the top. This is the highest-priority item to close before
  treating Selection Audit output as anything other than retrospective.
- `SentimentScore`'s collector + NLP pipeline is untested end-to-end against
  live services.
- `--use-clubelo` in `run_gold.py` has untested live-fetch behavior from
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