import argparse
import logging
import sys
from datetime import datetime

import pandas as pd

from ingestion.loaders.wikidata_loader             import load_wikidata_bronze
from ingestion.loaders.statsbomb_loader            import load_statsbomb_bronze
from ingestion.loaders.fbref_loader                import load_fbref_bronze
from ingestion.loaders.transfermarkt_loader        import load_transfermarkt_bronze

from ingestion.loaders.understat_loader            import load_understat_bronze
from ingestion.loaders.transfermarkt_injury_loader import load_injury_bronze
from ingestion.loaders.fbref_intl_loader           import load_intl_bronze

from ingestion.loaders.club_stats_csv_loader        import load_club_stats_csv_bronze

try:
    from ingestion.loaders.sofascore.client import load_sofascore_bronze
except ImportError:
    load_sofascore_bronze = None

try:
    from ingestion.loaders.sofascore.detail_loader import load_sofascore_detail_bronze
except ImportError:
    load_sofascore_detail_bronze = None

try:
    from ingestion.loaders.flashscore_loader import load_flashscore_bronze
except ImportError:
    from ingestion.loaders.sofascore.client  import load_flashscore_bronze

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/bronze_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("run_bronze")

SEED_PATH = "seeds/players_49.csv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Football DSS - Bronze Layer Ingestion v18",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_bronze.py                              Full ingestion (all 9 sources, 49 players)
  python run_bronze.py --limit 3                   Test mode - 3 players only
  python run_bronze.py --only-flashscore           FlashScore only (rating scraper test)
  python run_bronze.py --only-flashscore --limit 3 FlashScore, 3 players (quickest test)
  python run_bronze.py --fail-fast                 Abort immediately on any MinIO write failure
  python run_bronze.py --skip-sofascore            Skip SofaScore if rate-limited
  python run_bronze.py --skip-sofascore-detail     Skip SofaScore career+season detail (slow DOM scrape)
        """,
    )
    p.add_argument("--skip-wikidata",       action="store_true", help="Skip Wikidata loader")
    p.add_argument("--skip-statsbomb",      action="store_true", help="Skip StatsBomb loader")
    p.add_argument("--skip-fbref",          action="store_true", help="Skip FBref club stats loader")
    p.add_argument("--skip-transfermarkt",  action="store_true", help="Skip Transfermarkt market value loader")
    p.add_argument("--skip-understat",      action="store_true", help="Skip Understat npxG/xA loader")
    p.add_argument("--skip-sofascore",      action="store_true", help="Skip SofaScore ratings loader")
    p.add_argument("--skip-sofascore-detail", action="store_true", help="Skip SofaScore career+season detail loader (DOM scrape)")
    p.add_argument("--skip-injury",         action="store_true", help="Skip Transfermarkt injury loader")
    p.add_argument("--skip-intl",           action="store_true", help="Skip FBref international stats loader")
    p.add_argument("--skip-flashscore",     action="store_true", help="Skip FlashScore ratings loader")
    p.add_argument("--skip-club-stats-csv", action="store_true", help="Skip local CSV club stats loader (Big-5 leagues, Cloudflare-bypass workaround)")
    p.add_argument("--enable-reddit",  action="store_true", help="Enable Reddit collector (SentimentScore) via Arctic Shift — NO credentials needed (switched from PRAW 2026-07-23, see arctic_shift_collector.py)")
    p.add_argument("--enable-youtube", action="store_true", help="Enable YouTube collector (SentimentScore) — needs YOUTUBE_API_KEY in .env, costs ~4900/10000 daily quota for 49 players")
    p.add_argument("--enable-newsapi", action="store_true", help="Enable NewsAPI collector (SentimentScore) — needs NEWSAPI_KEY in .env, free tier is 100 requests/day")
    p.add_argument(
        "--only-flashscore",
        action="store_true",
        help="Run FlashScore loader only - skips all other sources. Useful for isolated testing.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit ingestion to first N players (testing mode)",
    )
    p.add_argument(
        "--fail-fast",
        action="store_true",
        help=(
            "FIX C-04: Abort immediately if any Bronze source upload returns FAILED. "
            "Use in CI/CD to catch MinIO connectivity issues early."
        ),
    )
    p.add_argument(
        "--as-of",
        default=None,
        help=(
            "YYYY-MM-DD decision-date cutoff applied to every date-relative "
            "Bronze loader (currently: transfermarkt_injury — 2-year lookback "
            "window + ongoing-injury day estimate). Defaults to "
            "config.DECISION_DATE. NOTE: this is NOT the same as run_date "
            "(the filename timestamp of THIS scrape) — --as-of controls what "
            "the DATA reflects, run_date only controls where it's filed in "
            "MinIO."
        ),
    )
    return p.parse_args()


def main() -> None:
    args     = parse_args()
    run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    from datetime import date as _date_cls
    from config import DECISION_DATE
    as_of_date = _date_cls.fromisoformat(args.as_of) if args.as_of else DECISION_DATE
    as_of_dt   = datetime.combine(as_of_date, datetime.min.time())
    logger.info("as-of (decision-date cutoff): %s", as_of_date.isoformat())

    if args.only_flashscore:
        args.skip_wikidata      = True
        args.skip_statsbomb     = True
        args.skip_fbref         = True
        args.skip_transfermarkt = True
        args.skip_understat     = True
        args.skip_sofascore     = True
        args.skip_sofascore_detail = True
        args.skip_injury        = True
        args.skip_intl          = True
        args.skip_flashscore    = False
        args.skip_club_stats_csv = True

    logger.info("=" * 60)
    logger.info("Football DSS - Bronze ingestion v18 started")
    logger.info("run_date   : %s", run_date)
    logger.info("fail-fast  : %s", args.fail_fast)
    logger.info("limit      : %s", args.limit or "all 49 players")
    if args.only_flashscore:
        logger.info("mode       : FlashScore only (--only-flashscore)")
    logger.info("=" * 60)

    try:
        seed_df = pd.read_csv(SEED_PATH)
    except FileNotFoundError:
        logger.error("Seed file not found: %s", SEED_PATH)
        sys.exit(1)

    if len(seed_df) != 49:
        logger.error("Expected 49 players in seed, got %d. Check players_49.csv.", len(seed_df))
        sys.exit(1)

    if args.limit:
        seed_df = seed_df.head(args.limit)
        logger.info("TEST MODE: limited to first %d players", args.limit)

    results: dict[str, str] = {}

    def _run(name: str, fn, *fn_args) -> None:
        logger.info(">> Starting %s ...", name)
        try:
            status = fn(*fn_args)
        except Exception as e:
            status = "FAILED"
            logger.error("[%s] Loader raised exception: %s", name, e, exc_info=True)

        results[name] = status
        symbol = "[OK]" if status.startswith("OK") else "[XX]"
        logger.info("%s %s -> %s", symbol, name, status)

        if args.fail_fast and status == "FAILED":
            raise RuntimeError(
                f"Bronze source '{name}' upload FAILED and --fail-fast is set. "
                "Check MinIO connectivity and loader logs above."
            )

    if not args.skip_wikidata:
        _run("wikidata",      load_wikidata_bronze,      seed_df, run_date)
    if not args.skip_statsbomb:
        _run("statsbomb",     load_statsbomb_bronze,     seed_df, run_date)
    if not args.skip_fbref:
        _run("fbref",         load_fbref_bronze,         seed_df, run_date)
    if not args.skip_transfermarkt:
        _run("transfermarkt", load_transfermarkt_bronze, seed_df, run_date)
    if not args.skip_understat:
        _run("understat",     load_understat_bronze,     seed_df, run_date)
    if not args.skip_sofascore:
        if load_sofascore_bronze:
            _run("sofascore", load_sofascore_bronze, seed_df, run_date)
        else:
            logger.warning("SofaScore loader not available - skipping (ingestion/loaders/sofascore/client.py has no load_sofascore_bronze)")
    if not args.skip_sofascore_detail:
        if load_sofascore_detail_bronze:
            _run("sofascore_detail", load_sofascore_detail_bronze, seed_df, run_date)
        else:
            logger.warning("SofaScore detail loader not available - skipping (ingestion/loaders/sofascore/detail_loader.py import failed)")
    if not args.skip_injury:
        _run("injury",        load_injury_bronze,        seed_df, run_date, as_of_dt)
    if not args.skip_intl:
        _run("intl",          load_intl_bronze,          seed_df, run_date)
    if not args.skip_flashscore:
        _run("flashscore",    load_flashscore_bronze,    seed_df, run_date)
    if not args.skip_club_stats_csv:
        _run("club_stats_csv", load_club_stats_csv_bronze, seed_df, run_date)

    if args.enable_reddit:
        from ingestion.loaders.arctic_shift_collector import load_arctic_shift_bronze
        _run("reddit (via Arctic Shift)", load_arctic_shift_bronze, seed_df, run_date)
    if args.enable_youtube:
        from ingestion.loaders.youtube_collector import load_youtube_bronze
        _run("youtube", load_youtube_bronze, seed_df, run_date)
    if args.enable_newsapi:
        from ingestion.loaders.newsapi_collector import load_newsapi_bronze
        _run("newsapi", load_newsapi_bronze, seed_df, run_date)

    logger.info("=" * 60)
    logger.info("Bronze ingestion complete - %d sources ran", len(results))
    logger.info("-" * 60)
    for source, status in results.items():
        symbol = "[OK]" if status.startswith("OK") else "[XX]"
        logger.info("  %s  %-25s %s", symbol, source, status)

    failed = [s for s, st in results.items() if st == "FAILED"]
    if failed:
        logger.warning("FAILED sources: %s", failed)
        logger.warning("Silver will merge with missing columns for these sources.")
        sys.exit(1)
    else:
        logger.info("All sources completed successfully.")
        logger.info("=" * 60)


if __name__ == "__main__":
    main()