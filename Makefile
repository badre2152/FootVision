# Football DSS — Makefile
#
# DECISION_DATE: single source of truth for any "as-of" date used across
# targets (currently only ml-injury's --reference-date accepts one — see
# the note above that target for why it is NOT wired into bronze/silver/gold).
# Override on the command line: `make ml-injury DECISION_DATE=2026-11-01`
DECISION_DATE ?= $(shell date +%Y-%m-%d)

.PHONY: help install start stop \
	bronze bronze-test bronze-fast bronze-sofifa bronze-fotmob bronze-sd-extractor \
	silver gold pipeline \
	ml-dna ml-injury ml-selection-audit ml-rotation \
	dashboard test lint clean

help:
	@echo "Football DSS — Available commands:"
	@echo ""
	@echo "  make install             Install Python dependencies"
	@echo "  make start               Start local MinIO (Docker) — skipped if unavailable"
	@echo "  make bronze              Run in-script Bronze ingestion (11 sources; see --skip-* flags in run_bronze.py)"
	@echo "  make bronze-test         Quick test: 3 players, mock storage"
	@echo "  make bronze-fast         Skip slow scrapers (Understat + SofaScore)"
	@echo "  make bronze-sd-extractor Run SofaScore detail extractor (separate script — run this first)"
	@echo "  make bronze-fotmob       Run Fotmob loader (separate script — run AFTER bronze-sd-extractor)"
	@echo "  make bronze-sofifa       Run SoFIFA loader (separate script, Playwright — needs: playwright install chromium)"
	@echo "  make silver              Run Silver pipeline (normalize + coalesce Bronze sources)"
	@echo "  make gold                Run Gold pipeline: normalize + score + rank (top 26)"
	@echo "  make pipeline            Full chain: bronze -> silver -> gold"
	@echo "  make ml-dna              Player DNA (K-Means) clustering"
	@echo "  make ml-injury           Injury Risk Index (rule-based)"
	@echo "  make ml-selection-audit  Selection audit vs. real 2026 WC squad"
	@echo "  make ml-rotation         Rotation strategy (group stage)"
	@echo "  make dashboard           Launch the Streamlit dashboard"
	@echo "  make test                Run the full test suite (pytest tests/)"
	@echo "  make lint                Run flake8"
	@echo "  make clean               Remove logs and gold outputs"
	@echo ""
	@echo "  Note: bronze-sd-extractor / bronze-fotmob / bronze-sofifa are NOT part of"
	@echo "  'make bronze' or 'make pipeline' — they are separate manual scripts not"
	@echo "  covered by run_bronze.py (see RUN_INSTRUCTIONS.md). Run them in order:"
	@echo "  bronze-sd-extractor -> bronze-fotmob -> bronze-sofifa."

install:
	pip install -r requirements.txt --break-system-packages -q
	@echo "✓ Dependencies installed"

start:
	@docker --version > /dev/null 2>&1 && docker-compose up -d \
		&& echo "✓ MinIO started at http://localhost:9001" \
		|| echo "⚠ Docker not available — using MINIO_MOCK=1 (filesystem mock)"

stop:
	@docker --version > /dev/null 2>&1 && docker-compose down || true

bronze:
	MINIO_MOCK=0 python run_bronze.py

bronze-test:
	@echo "Running Bronze in TEST MODE (3 players, filesystem mock)..."
	MINIO_MOCK=1 python run_bronze.py --limit 3 --fail-fast \
		--skip-statsbomb --skip-wikidata

bronze-fast:
	MINIO_MOCK=1 python run_bronze.py \
		--skip-understat --skip-sofascore --skip-statsbomb --skip-wikidata

# NOTE: none of run_sofifa.py / run_fotmob.py / run_sd_extractor.py expose an
# --as-of flag (confirmed via each script's own --help — see spec #8).
# DECISION_DATE is therefore defined above but deliberately NOT passed to
# these commands; doing so would just fail with "unrecognized arguments".
# Order matters — run_fotmob.py must run AFTER run_sd_extractor.py (see
# RUN_INSTRUCTIONS.md § 2).
bronze-sd-extractor:
	python run_sd_extractor.py

bronze-fotmob:
	python run_fotmob.py

bronze-sofifa:
	python run_sofifa.py

silver:
	MINIO_MOCK=0 python run_silver.py

gold:
	MINIO_MOCK=1 python run_gold.py --top-n 26

pipeline: bronze silver gold
	@echo "✓ Pipeline complete: bronze -> silver -> gold"

ml-dna:
	python run_player_dna.py

# --reference-date is the one real "as-of" hook in the project today.
ml-injury:
	python run_injury_predictor.py --reference-date $(DECISION_DATE)

ml-selection-audit:
	python run_selection_audit.py

ml-rotation:
	python run_rotation_strategy.py

dashboard:
	streamlit run dashboard/app.py

test:
	PYTHONPATH=. pytest tests/ -q

lint:
	flake8 . --max-line-length=100 \
		--exclude=venv,env,.venv,__pycache__ \
		--ignore=E501,W503

clean:
	rm -f logs/*.log
	rm -rf gold/*.csv gold/*.json
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true