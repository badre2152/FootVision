#!/usr/bin/env python
# run_silver.py — Football DSS Silver Layer Pipeline
#
# Reads all Bronze sources from MinIO, applies:
#   JOIN → RENAME → COALESCE → IMPUTE → VALIDATE
# Writes output to:
#   silver/players_silver_{run_date}.parquet  (primary — Gold reads this)
#   silver/players_silver_{run_date}.csv      (human inspection)
#
# Run AFTER run_bronze.py, BEFORE run_gold.py:
#   python run_silver.py
#   python run_silver.py --seed seeds/players_49.csv --output-dir silver

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from ingestion.silver_pipeline import build_silver


def ensure_minio_buckets() -> None:
    """Auto-create football-silver and football-gold buckets if not present."""
    if os.getenv("MINIO_MOCK", "0") == "1":
        return
    try:
        from clients.minio_client import get_minio_client, _ensure_bucket
        client = get_minio_client()
        for bucket in ["football-bronze", "football-silver", "football-gold"]:
            _ensure_bucket(client, bucket)
        logging.getLogger("run_silver").info("MinIO buckets verified/created.")
    except Exception as e:
        logging.getLogger("run_silver").warning("Could not verify MinIO buckets: %s", e)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_silver")

SILVER_OUTPUT_DIR = Path("silver")
SEED_PATH         = "seeds/players_49.csv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Football DSS — Silver Layer Pipeline")
    p.add_argument(
        "--seed", type=str, default=SEED_PATH,
        help=f"Path to seed CSV (default: {SEED_PATH})",
    )
    p.add_argument(
        "--output-dir", type=str, default="silver",
        help="Output directory for Silver parquet/csv (default: silver/)",
    )
    p.add_argument(
        "--no-parquet", action="store_true",
        help="Skip parquet output (write CSV only)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Run full pipeline but do not write output files",
    )
    return p.parse_args()


def main() -> None:
    args     = parse_args()
    run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    logger.info("=" * 60)
    logger.info("Football DSS — Silver Pipeline")
    logger.info("run_date: %s", run_date)
    logger.info("seed:     %s", args.seed)
    logger.info("output:   %s/", output_dir)
    logger.info("=" * 60)

    # Auto-create buckets if needed
    ensure_minio_buckets()

    # ── Run full Silver pipeline ──────────────────────────────────────────────
    df = build_silver(run_date=run_date, seed_path=args.seed)

    # ── Coverage report ───────────────────────────────────────────────────────
    logger.info("-" * 60)
    logger.info("SILVER COVERAGE REPORT:")
    report_cols = [
        ("form_final",            "FormScore input"),
        ("rating_final",          "FormScore input"),
        ("Gls_per90",             "PerformanceScore"),
        ("Ast_per90",             "PerformanceScore"),
        ("understat_npxg_per90",  "PerformanceScore"),
        ("GA_per90",              "PerformanceScore (GK)"),
        ("sb_pressures_per90",    "MentalScore"),
        ("intl_caps",             "TacticalFitScore"),
        ("inj_days_missed_2y",    "AvailabilityScore"),
        ("market_value_eur",      "Context"),
    ]
    for col, score in report_cols:
        if col in df.columns:
            non_null = df[col].notna().sum()
            bar = "█" * non_null + "░" * (49 - non_null)
            logger.info("  %-25s %s %d/49  [%s]", col, bar[:10], non_null, score)
    logger.info("-" * 60)

    # ── Write outputs ─────────────────────────────────────────────────────────
    if args.dry_run:
        logger.info("DRY RUN — no files written.")
        return

    # Parquet (primary — Gold reads this)
    if not args.no_parquet:
        import shutil
        parquet_path = output_dir / f"players_silver_{run_date}.parquet"
        df.to_parquet(parquet_path, index=False)
        logger.info("Silver parquet: %s", parquet_path)

        # Copy as 'latest' for convenience (symlinks require admin on Windows)
        latest_parquet = output_dir / "players_silver_latest.parquet"
        shutil.copy2(parquet_path, latest_parquet)
        logger.info("Latest copy:   %s", latest_parquet)

        # Upload to MinIO football-silver bucket
        if os.getenv("MINIO_MOCK", "0") != "1":
            try:
                from clients.minio_client import get_minio_client, _ensure_bucket
                import io
                minio_client = get_minio_client()
                _ensure_bucket(minio_client, "football-silver")
                object_name = f"players_silver_{run_date}.parquet"
                raw = parquet_path.read_bytes()
                minio_client.put_object(
                    "football-silver",
                    object_name,
                    io.BytesIO(raw),
                    length=len(raw),
                    content_type="application/octet-stream",
                )
                logger.info(
                    "Uploaded s3://football-silver/%s (%d bytes)",
                    object_name, len(raw),
                )
            except Exception as e:
                logger.warning("MinIO upload failed (non-fatal): %s", e)

    # CSV (human inspection)
    csv_path = output_dir / f"players_silver_{run_date}.csv"
    df.to_csv(csv_path, index=False)
    logger.info("Silver CSV:     %s", csv_path)

    logger.info("=" * 60)
    logger.info("Silver pipeline DONE — %d rows × %d columns", *df.shape)
    logger.info("Next step: python run_gold.py")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()