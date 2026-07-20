# Football DSS — Makefile
.PHONY: help install start stop bronze bronze-test bronze-fast gold test lint clean

help:
	@echo "Football DSS — Available commands:"
	@echo ""
	@echo "  make install       Install Python dependencies"
	@echo "  make start         Start local MinIO (Docker) — skipped if unavailable"
	@echo "  make bronze        Run full Bronze ingestion (all 8 sources)"
	@echo "  make bronze-test   Quick test: 3 players, mock storage"
	@echo "  make bronze-fast   Skip slow scrapers (Understat + SofaScore)"
	@echo "  make gold          Run Gold pipeline: normalize + score + rank"
	@echo "  make test          Run smoke tests"
	@echo "  make lint          Run flake8"
	@echo "  make clean         Remove logs and gold outputs"

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

gold:
	MINIO_MOCK=1 python run_gold.py --top-n 26

test:
	MINIO_MOCK=1 PYTHONPATH=. python tests/smoke_test_bronze.py

lint:
	flake8 . --max-line-length=100 \
		--exclude=venv,env,.venv,__pycache__ \
		--ignore=E501,W503

clean:
	rm -f logs/*.log
	rm -rf gold/*.csv gold/*.json
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
