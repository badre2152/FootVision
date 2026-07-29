# RUN_INSTRUCTIONS.md — Football Atlas
**Last updated:** July 21, 2026 — after the full Chemistry/Pressure/Flexibility session

Single reference for running the whole pipeline. Every command below has
been run for real (by the project owner, on Windows/PowerShell) at least
once during this session — this isn't theoretical.

---

## 1. Setup

```powershell
pip install -r requirements.txt
```

`.env` should already exist in the project root (MinIO + API_FOOTBALL_KEY).
If missing, see `.env` delivered earlier this session — but consider
rotating `API_FOOTBALL_KEY` if this repo is ever made public, since it was
pasted in plain text during this conversation.

---

## 2. Bronze — full run

```powershell
python run_bronze.py
```

⚠️ **This does NOT cover everything.** Three sources need separate manual
scripts — easy to forget, not mentioned anywhere else in the codebase's
own docs:

```powershell
python run_sd_extractor.py      # SofaScore detail extraction
python run_fotmob.py            # Fotmob (run AFTER run_sd_extractor.py)
python run_sofifa.py            # SoFIFA (Playwright — needs: playwright install chromium)
```

### Running a single source only (faster iteration)
`run_bronze.py`'s actual flags (confirmed from its own `--help` output):
```
--skip-wikidata --skip-statsbomb --skip-fbref --skip-transfermarkt
--skip-understat --skip-sofascore --skip-sofascore-detail --skip-injury
--skip-intl --skip-flashscore --skip-club-stats-csv --only-flashscore
--limit LIMIT --fail-fast
```
Example — StatsBomb only:
```powershell
python run_bronze.py --skip-wikidata --skip-fbref --skip-transfermarkt --skip-understat --skip-sofascore --skip-sofascore-detail --skip-injury --skip-intl --skip-flashscore --skip-club-stats-csv
```
Example — Transfermarkt only:
```powershell
python run_bronze.py --skip-wikidata --skip-statsbomb --skip-fbref --skip-understat --skip-sofascore --skip-sofascore-detail --skip-flashscore --skip-club-stats-csv --skip-intl --skip-injury
```

---

## 3. Silver

```powershell
python run_silver.py
```

Watch the coverage report at the end and any `WARNING` lines — especially
`tm_num_positions: N/49 players defaulted to 1 (TM fetch failed)`, which
tells you how many players the Transfermarkt secondary-position selector
actually reached (some failures are normal — timeouts, missing profiles).

---

## 4. Gold — scoring (7-8 components)

```powershell
python run_gold.py --top-n 26
```

Note: `run_gold.py` rebuilds its OWN Silver internally from whatever's
currently in MinIO Bronze (Step 1/5 in its logs) — it does not reuse your
last local `run_silver.py` output directly, though the result should be
identical if nothing changed in Bronze between the two runs.

### Quick sanity checks after a run
```powershell
# Chemistry coverage (should be 49/49, ~11 real)
python -c "import pandas as pd; df = pd.read_parquet('silver/players_silver_latest.parquet'); print(df['chem_avg_per90'].notna().sum(), '/', len(df))"

# Flexibility distribution (should show real 1/2/3 spread, not one constant)
python -c "import pandas as pd; df = pd.read_parquet('silver/players_silver_latest.parquet'); print(df['tm_num_positions'].value_counts().sort_index())"

# Squad position cap (should always be GK=3, DEF=9, MID=9, FWD=5)
python -c "import pandas as pd; df = pd.read_csv(sorted(__import__('glob').glob('gold/top26_*.csv'))[-1]); print(df['position'].value_counts())"
```

---

## 5. Tests

```powershell
python -m pytest tests/test_gold2_components.py -v
```
16 tests — covers ChemistryScore, PressureIndex, FlexibilityScore,
percentile_score.py's rank-preservation contract, the minutes_filter
weight-binding regression, and the squad_summary position-cap regression.

---

## 6. Dashboard

```powershell
streamlit run dashboard/app.py
```
Opens at `http://localhost:8501`. 4 pages (sidebar nav): Executive
Overview, Player Analysis, Chemistry Matrix, Final Squad. Sidebar has
position filters + player search, shared across pages via
`st.session_state`.

---

## 7. Known remaining gaps (as of 2026-07-21)

| Gap | Status |
|---|---|
| SentimentScore | Not started — needs a full new Twitter/Reddit scraper + NLP pipeline. Explicitly deferred. |
| ClubElo-based league quality | `ingestion/loaders/clubelo_loader.py` exists fully written but is never called by anything — a more data-driven alternative to the hardcoded league-difficulty table in `league_quality.py`. Candidate for a future session. |
| `sofascore_parsers_season.py` | (Earlier flagged as orphaned — that was a mistake on my part, corrected: it IS used, via `sofascore_detail_loader.py`. No action needed.) |
| Full MASTER_SPEC.md rewrite | Only §7/§10 (Gold formula) and the Bronze source inventory have been re-verified this session (see `MASTER_SPEC_ADDENDUM_2026-07-14.md` and `MASTER_SPEC_v2_bronze_and_gold.md`). §6/§9 (Silver contract, detailed column mappings) still reflect the original June 22 spec, unverified. |
| MentalScore GK homogeneity | Not a bug — data-sparsity artifact (only 1/6 GK candidates has real StatsBomb coverage). Documented in the Addendum §6. Will improve naturally as StatsBomb coverage grows. |

---

*Football Atlas — RUN_INSTRUCTIONS.md · July 21, 2026*