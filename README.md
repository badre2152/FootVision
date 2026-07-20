# Football Decision Intelligence System — Bronze Layer v17

Pipeline end-to-end لاختيار 26 لاعباً للمنتخب المغربي من قائمة 49 مرشحاً.

## Project Structure

```
football_atlas/
├── clients/
│   └── minio_client.py          # upload_json, read_latest_json
├── ingestion/
│   ├── loaders/
│   │   ├── _bronze_utils.py     # validate_seed_columns, assert_min_coverage (shared)
│   │   ├── understat_loader.py  # Source 8 — npxG, xA (ADR-012)
│   │   ├── sofascore_loader.py  # Source 9 — match ratings (ADR-013)
│   │   ├── transfermarkt_injury_loader.py  # Source 10 — injury history (ADR-014)
│   │   └── fbref_intl_loader.py # Source 11 — national team stats (ADR-015)
│   └── silver_pipeline.py       # Reads from MinIO, merges all Bronze sources
├── seeds/
│   └── players_49.csv           # 49 candidate players with all IDs
├── scripts/
│   └── collect_ids.py           # Fuzzy-match understat_id & sofascore_id
├── tests/
│   └── smoke_test_bronze.py     # Post-ingestion validation (T-01 to T-06)
├── config.py                    # Weights, normalization bounds, thresholds
├── run_bronze.py                # Bronze orchestrator — run this
├── docker-compose.yml           # Local MinIO simulator
├── requirements.txt
└── .env.example
```

## Quick Start

### 1. Start local MinIO

```bash
docker-compose up -d
# Console: http://localhost:9001  (admin / minioadmin)
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env if needed (defaults work with docker-compose)
```

### 4. (First time) Collect missing player IDs

```bash
python scripts/collect_ids.py --source understat --output seeds/understat_ids_draft.csv
python scripts/collect_ids.py --source sofascore  --output seeds/sofascore_ids_draft.csv
# Review drafts, fill in missing IDs, merge into seeds/players_49.csv
```

### 5. Run Bronze ingestion

```bash
# Full ingestion — all 8 sources, 49 players
python run_bronze.py

# Test mode — 3 players only (quick)
python run_bronze.py --limit 3

# Skip slow scrapers (development)
python run_bronze.py --skip-sofascore --skip-understat --limit 5

# CI/CD — abort on first failure
python run_bronze.py --fail-fast
```

### 6. Validate

```bash
python tests/smoke_test_bronze.py
```

## Bronze Sources

| # | Source | Type | Columns | Coverage |
|---|--------|------|---------|----------|
| 6 | Kaggle-Transfermarkt | CSV | market_value, age, tm_id | ~49/49 |
| 7 | FBref Club Stats | Scraping | Gls, Ast, xG, Tkl… | ~45/49 |
| 8 | Understat | Scraping | npxg_per90, xa_per90, conversion | ~35/49 |
| 9 | SofaScore | API (unoff.) | rating_avg, form_5 | ~40/49 |
| 10 | TM Injury | Scraping | inj_count_2y, inj_days_2y | ~45/49 |
| 11 | FBref Intl | Scraping | intl_caps, intl_gls_per90 | ~49/49 |

## v17 Fixes Applied

| ID | Issue Fixed |
|----|------------|
| C-01 | `run_date=""` → objects named without timestamp |
| C-02 | Silent `None` from missing seed columns → now raises `ValueError` |
| C-03 | SofaScore form_5 made up to 245 extra API calls → capped at 5 with dedup |
| C-04 | `--fail-fast` flag added to `run_bronze.py` |
| C-05 | `unicode_escape` corrupting non-ASCII names (Óscar, Müller) → fixed |
| C-06 | Ongoing injuries counted as 0 days → now estimated from date_from |
| C-07 | FBref column resolution ambiguity → exact-match-first |
| C-08 | Empty Bronze uploads undetected by Silver → coverage check added |

## MinIO Bucket Layout

```
s3://football-bronze/
├── wikidata/        players_wikidata_YYYYMMDD_HHMMSS.json
├── statsbomb/       matches/, events/
├── fbref/           players_fbref_YYYYMMDD_HHMMSS.json
├── transfermarkt/   players_tm_YYYYMMDD_HHMMSS.json
├── understat/       players_understat_YYYYMMDD_HHMMSS.json
├── sofascore/       players_sofascore_YYYYMMDD_HHMMSS.json
├── transfermarkt_injury/  players_injury_YYYYMMDD_HHMMSS.json
└── fbref_intl/      players_intl_YYYYMMDD_HHMMSS.json
```
