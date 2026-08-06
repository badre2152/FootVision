# RUN_INSTRUCTIONS.md — FootVision
**Last updated:** August 3, 2026 — synced with `README.md` and `Makefile`
(previous version, dated July 21, 2026, predated the `DECISION_DATE`
enforcement, the ML decision-support modules, dashboard authentication, and
7 of the current 11 dashboard pages — do not trust anything from a copy of
this file older than this date; diff against `Makefile`'s `help` target if
in doubt).

Single reference for running the whole pipeline, kept intentionally short —
`README.md` has the full architectural picture and the `DECISION_DATE`
explanation; this file is the command sequence only.

---

## 0. The one thing to know before running anything

Every live-path run (`pipelines/bronze/run_bronze.py`, `pipelines/bronze/run_sd_extractor.py`,
`pipelines/bronze/run_fotmob.py`, `pipelines/bronze/run_sofifa.py`, `pipelines/gold/run_gold.py`) is dated against
`config.DECISION_DATE` (`2026-05-25`) unless you pass `--as-of
YYYY-MM-DD` explicitly. `pipelines/ml/run_injury_predictor.py` uses `--reference-date`
instead. **Do not change `DECISION_DATE` to anything on/after
2026-05-26** (the real WC2026 squad announcement) — see `README.md`'s
top section for why. If you only remember one thing from this file, make
it this.

---

## 1. Setup

```bash
make install
# or: pip install -r requirements.txt --break-system-packages
```

`.env` should already exist in the project root (MinIO + `API_FOOTBALL_KEY`).
Rotate `API_FOOTBALL_KEY` before making this repo public.

---

## 2. Bronze

```bash
make bronze              # 11 in-script sources (pipelines/bronze/run_bronze.py)
make bronze-sd-extractor # REQUIRED before silver — extracts sd_* columns; run FIRST
make bronze-fotmob       # standalone — run AFTER bronze-sd-extractor
make bronze-sofifa       # standalone, Playwright — needs: playwright install chromium
```

Order matters for the three standalone scripts:
**bronze-sd-extractor → bronze-fotmob → bronze-sofifa.**
None of the three take `DECISION_DATE` from the Makefile's variable
automatically for `ml-injury`-style overriding on the command line — they
each default to `config.DECISION_DATE` internally; pass `--as-of` directly
to the underlying script if you need a different date for a one-off run:

```bash
python -m pipelines.bronze.run_sd_extractor --as-of 2026-05-25
python -m pipelines.bronze.run_fotmob --as-of 2026-05-25
python -m pipelines.bronze.run_sofifa --as-of 2026-05-25
```

### Running a single source only (faster iteration)

```bash
make bronze-fast   # skips Understat + SofaScore + StatsBomb + Wikidata
make bronze-test   # 3 players, filesystem mock, fail-fast
```

Or call `pipelines/bronze/run_bronze.py` directly with any combination of `--skip-*` flags
(`python -m pipelines.bronze.run_bronze --help` for the full, current list — the flags
themselves are the source of truth, not this file).

---

## 3. Silver

```bash
make silver
```

Watch the coverage report and any `WARNING` lines at the end.

---

## 4. Gold — scoring (9 components)

```bash
make gold   # python -m pipelines.gold.run_gold --top-n 26
```

`pipelines/gold/run_gold.py` rebuilds its own Silver internally from whatever's currently
in MinIO Bronze — it does not require a prior local `make silver` run,
though results should match if Bronze hasn't changed between the two.
Optional: `--use-clubelo` to replace the hardcoded league-difficulty table
with live ClubElo ratings (untested from network-restricted environments —
verify GK rankings after your first run).

### Quick sanity checks after a run

```bash
# Squad position cap (should always be GK=3, DEF=9, MID=9, FWD=5)
python -c "import pandas as pd, glob; df = pd.read_csv(sorted(glob.glob('gold/top26_*.csv'))[-1]); print(df['position'].value_counts())"

# DECISION_DATE compliance spot-check (injury data should never be dated after the cutoff)
python -c "import pandas as pd; df = pd.read_parquet('silver/players_silver_latest.parquet'); print(pd.to_datetime(df['inj_last_date'], errors='coerce').max())"
```

---

## 5. Decision-support modules (on top of Gold)

```bash
make ml-dna              # Player DNA — K-Means clustering
make ml-injury           # Injury Risk Index (--reference-date, defaults to DECISION_DATE)
make ml-selection-audit  # Selection Audit vs. the real 2026 squad (LOOCV logistic regression)
make ml-rotation         # Rotation Strategy (group-stage)
```

To override the reference date for `ml-injury` specifically:

```bash
make ml-injury DECISION_DATE=2026-11-01
```

---

## 6. Dashboard

```bash
# First time only — the dashboard is password-gated:
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit .streamlit/secrets.toml, set a real APP_PASSWORD

make dashboard
```

Opens at `http://localhost:8501`. **Home + 11 pages** (sidebar nav):
Player Analysis, Chemistry Matrix, Final Squad, Selection Audit, Fitness &
Rotation, Tactical Board, Public Sentiment, Competition Context,
Historical Trend View, Set Piece & Clutch, Squad Builder. Sidebar has
position filters + player search, shared across pages via
`st.session_state`. Every page fails closed with a config error if
`.streamlit/secrets.toml` is missing or has no `APP_PASSWORD` — that is
correct behavior, not a bug.

---

## 7. Tests

```bash
make test   # PYTHONPATH=. pytest tests/ -q — full suite
```

452 tests collected as of this writing (a handful of files need optional
extras — `mplsoccer`, `rapidfuzz`, `googleapiclient` — already listed in
`requirements.txt`; re-run `make install` if collection errors mention
`ModuleNotFoundError` for any of the three). For the temporal-cutoff
guarantee specifically:

```bash
pytest tests/test_temporal_cutoff.py -v
```

For a full accounting of what currently passes/fails and why, see the
**Known issues** entries in `CHANGELOG.md` — it is updated per-session and
is more current than any fixed number quoted here could stay.

---

## 8. MinIO console (optional)

```bash
make start   # http://localhost:9101 — falls back to MINIO_MOCK=1 if Docker is unavailable
make stop
```

---

*FootVision — RUN_INSTRUCTIONS.md · synced with README.md and Makefile · August 3, 2026*