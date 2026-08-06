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
	@echo "  make bronze              Run in-script Bronze ingestion (11 sources; see --skip-* flags in pipelines/bronze/run_bronze.py)"
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
	@echo "  covered by pipelines/bronze/run_bronze.py (see RUN_INSTRUCTIONS.md). Run them in order:"
	@echo "  bronze-sd-extractor -> bronze-fotmob -> bronze-sofifa."

install:
	pip install -r requirements.txt --break-system-packages -q
	@echo "✓ Dependencies installed"

start:
	@docker --version > /dev/null 2>&1 && docker-compose up -d \
		&& echo "✓ MinIO started — console at http://localhost:9101 (S3 API on 9100)" \
		|| echo "⚠ Docker not available — using MINIO_MOCK=1 (filesystem mock)"

stop:
	@docker --version > /dev/null 2>&1 && docker-compose down || true

bronze:
	MINIO_MOCK=0 python -m pipelines.bronze.run_bronze

bronze-test:
	@echo "Running Bronze in TEST MODE (3 players, filesystem mock)..."
	MINIO_MOCK=1 python -m pipelines.bronze.run_bronze --limit 3 --fail-fast \
		--skip-statsbomb --skip-wikidata

bronze-fast:
	MINIO_MOCK=1 python -m pipelines.bronze.run_bronze \
		--skip-understat --skip-sofascore --skip-statsbomb --skip-wikidata

bronze-sd-extractor:
	python -m pipelines.bronze.run_sd_extractor

bronze-fotmob:
	python -m pipelines.bronze.run_fotmob

bronze-sofifa:
	python -m pipelines.bronze.run_sofifa

silver:
	MINIO_MOCK=0 python -m pipelines.silver.run_silver

gold:
	MINIO_MOCK=1 python -m pipelines.gold.run_gold --top-n 26

pipeline: bronze silver gold
	@echo "✓ Pipeline complete: bronze -> silver -> gold"

ml-dna:
	python -m pipelines.ml.run_player_dna

ml-injury:
	python -m pipelines.ml.run_injury_predictor --reference-date $(DECISION_DATE)

ml-selection-audit:
	python -m pipelines.ml.run_selection_audit

ml-rotation:
	python -m pipelines.ml.run_rotation_strategy

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