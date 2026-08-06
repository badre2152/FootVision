import argparse
import json
import logging
import sys
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_sd_extractor")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SofaScore Detail Extractor")
    p.add_argument(
        "--limit", type=int, default=None,
        help="Process only first N players (default: all 49). Useful for testing.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Extract but do NOT upload to MinIO.",
    )
    p.add_argument(
        "--as-of", default=None,
        help=(
            "YYYY-MM-DD decision-date cutoff (config.DECISION_DATE by default). "
            "NOTE: this extractor re-parses an already-scraped sofascore_detail "
            "Bronze blob (career + season tables) and applies no date-window "
            "filtering of its own. This flag is accepted for CLI consistency "
            "(01_temporal_cutoff.md req. #3) and logged only — the real cutoff "
            "enforcement point for SofaScore data would be ingestion/loaders/sofascore/detail_loader.py "
            "if/when it gains lookback-window logic."
        ),
    )
    return p.parse_args()


def main():
    args     = parse_args()
    from config import DECISION_DATE
    as_of    = args.as_of or DECISION_DATE.isoformat()
    run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info("=" * 60)
    logger.info("SofaScore Detail Extractor — run_date: %s | as-of: %s", run_date, as_of)
    logger.info("limit    : %s", args.limit if args.limit else "all 49")
    logger.info("dry-run  : %s", args.dry_run)
    logger.info("=" * 60)

    from clients.minio_client import read_latest_json, upload_json
    from ingestion.loaders.sofascore.detail_extractor import extract_player

    raw = read_latest_json(bucket="football-bronze", prefix="sofascore_detail/")
    if not raw:
        logger.error("No sofascore_detail data found in MinIO — run run_bronze.py first.")
        sys.exit(1)

    logger.info("Loaded %d records from sofascore_detail/", len(raw))

    if args.limit:
        raw = raw[:args.limit]
        logger.info("Limiting to first %d players", args.limit)

    results = []
    ok = skipped = 0

    for i, record in enumerate(raw, 1):
        pid = record.get("player_id", "?")

        if len(record) <= 1:
            logger.info("[%d/%d] %s — no detail data, skipping", i, len(raw), pid)
            results.append({"player_id": pid})
            skipped += 1
            continue

        extracted = extract_player(record)
        results.append(extracted)

        has_stats = (
            extracted.get("sd_gls_per90") is not None
            or extracted.get("sd_tkl_per90") is not None
            or extracted.get("sd_ga_per90") is not None
        )
        if has_stats:
            ok += 1

        logger.info(
            "[%d/%d] %s — gls/90=%s tkl/90=%s int/90=%s ga/90=%s",
            i, len(raw), pid,
            extracted.get("sd_gls_per90", "—"),
            extracted.get("sd_tkl_per90", "—"),
            extracted.get("sd_int_per90", "—"),
            extracted.get("sd_ga_per90",  "—"),
        )

    logger.info("-" * 60)
    logger.info(
        "Extracted: %d/%d with stats | %d skipped (no detail data)",
        ok, len(results), skipped,
    )

    logger.info("COVERAGE:")
    cols = [
        ("sd_gls_per90",   "Gls/90"),
        ("sd_ast_per90",   "Ast/90"),
        ("sd_xg_per90",    "xG/90"),
        ("sd_xa_per90",    "xA/90"),
        ("sd_tkl_per90",   "Tkl/90 ← GAP 1"),
        ("sd_int_per90",   "Int/90 ← GAP 1"),
        ("sd_clr_per90",   "Clr/90 ← GAP 1"),
        ("sd_ga_per90",    "GA/90  (GK)"),
        ("sd_saves_per90", "Saves/90 (GK)"),
    ]
    for col, label in cols:
        n = sum(1 for r in results if r.get(col) is not None)
        bar = "█" * n + "░" * (len(results) - n)
        logger.info("  %-22s %s %d/%d  [%s]", col, bar[:15], n, len(results), label)

    if args.dry_run:
        logger.info("DRY RUN — skipping MinIO upload.")
        logger.info("Sample output (first player):")
        logger.info(json.dumps(results[0], indent=2, ensure_ascii=False))
        return

    object_name = f"sofascore_detail_extracted/players_sd_extracted_{run_date}.json"
    upload_json(
        bucket="football-bronze",
        object_name=object_name,
        data=results,
    )
    logger.info("Uploaded → s3://football-bronze/%s", object_name)
    logger.info("=" * 60)
    logger.info("Done — next: python run_fotmob.py  OR  python run_silver.py")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()