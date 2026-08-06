import csv
import io
import logging
import time
import urllib.request
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

CLUBELO_URL = "http://api.clubelo.com/{date}"
BUCKET      = "football-bronze"
OBJECT_PATH = "clubelo/clubelo_{date}.json"

# Clubs for our 49 players — used for lookup after fetching the full CSV
OUR_CLUBS = [
    "Raja Casablanca", "Al-Hilal", "Paris Saint-Germain", "PSV Eindhoven",
    "Al-Ittihad", "Al Ain", "FAR Rabat", "Olympiacos", "KRC Genk",
    "Watford", "VfB Stuttgart", "Real Betis", "Feyenoord", "Girona",
    "Manchester United", "AS Roma", "Wydad", "Real Sociedad",
    "RSC Anderlecht", "Al Ahly", "Pyramids", "Eintracht Frankfurt",
    "AS Monaco", "Olympique de Marseille", "Al-Fateh", "RCD Espanyol",
    "Bournemouth", "Villarreal", "LOSC Lille", "Stade Rennais",
    "Angers", "RC Strasbourg", "ESTAC Troyes", "Ajax",
    "Jupiler Pro League", "Botola", "Saudi",
]

# Big-5 average Elo — baseline for computing the difficulty factor.
# Computed from the average of the top 20 Big-5 clubs (historically ~1800-1900).
BIG5_BASELINE_ELO = 1850.0


def _fetch_clubelo_csv(target_date: str) -> list[dict]:
    """Fetches the full CSV from the ClubElo API for a given date ('YYYY-MM-DD')."""
    url = CLUBELO_URL.format(date=target_date)
    req = urllib.request.Request(url, headers={
        "User-Agent": "FootballAtlas/1.0 (research project)",
        "Accept": "text/csv,*/*",
    })

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                content = r.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
            logger.info("ClubElo: fetched %d clubs for %s", len(rows), target_date)
            return rows
        except Exception as e:
            logger.warning("ClubElo attempt %d failed: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(3 * (attempt + 1))

    logger.error("ClubElo: all attempts failed for %s", target_date)
    return []


def _match_club(club_name: str, elo_rows: list[dict]) -> dict | None:
    """Matches a club name against ClubElo rows using fuzzy matching."""
    name_lower = club_name.lower()

    # Attempt 1: exact match
    for row in elo_rows:
        if row.get("Club", "").lower() == name_lower:
            return row

    # Attempt 2: partial match
    for row in elo_rows:
        club = row.get("Club", "").lower()
        if name_lower in club or club in name_lower:
            return row

    # Attempt 3: keyword match. Risk: clubs sharing a >3-char keyword can
    # collide here — e.g. "Real Betis" and "Real Sociedad" both contain
    # "real". Only fires when attempts 1+2 fail; if it does fire for an
    # ambiguous name it silently returns the FIRST matching row, which may
    # be the wrong club. Logged as a WARNING so it's auditable — check
    # logs for "keyword fallback" matches before trusting the resulting
    # league difficulty factors.
    keywords = [w for w in name_lower.split() if len(w) > 3]
    for kw in keywords:
        for row in elo_rows:
            if kw in row.get("Club", "").lower():
                logger.warning(
                    "ClubElo: '%s' matched via risky keyword fallback ('%s' -> '%s') "
                    "— verify this is the correct club, ambiguous names can collide here.",
                    club_name, kw, row.get("Club"),
                )
                return row

    return None


def compute_difficulty_factor(elo: float, baseline: float = BIG5_BASELINE_ELO) -> float:
    """
    Converts an Elo rating into a difficulty factor between 0.5 and 1.0.
    factor = elo / baseline (clipped). Big-5 avg (1850) → 1.00,
    Botola (≈1200) → ~0.65, Saudi (≈1600) → ~0.86.
    """
    factor = elo / baseline
    return round(max(0.50, min(1.10, factor)), 3)


def build_league_quality_from_elo(
    seed_df: pd.DataFrame,
    elo_rows: list[dict],
) -> dict[str, float]:
    """Builds a new league_quality dict from real Elo data: {league_name: difficulty_factor}."""
    league_elos: dict[str, list[float]] = {}

    for _, row in seed_df.iterrows():
        club  = str(row.get("club", ""))
        league = str(row.get("league", ""))
        if not club or not league:
            continue

        match = _match_club(club, elo_rows)
        if match:
            try:
                elo = float(match.get("Elo", 0))
                if elo > 0:
                    league_elos.setdefault(league, []).append(elo)
                    logger.debug("Matched %s → Elo=%.0f (league=%s)", club, elo, league)
            except (ValueError, TypeError):
                pass
        else:
            logger.warning("ClubElo: no match for club '%s'", club)

    result = {}
    for league, elos in league_elos.items():
        avg_elo = sum(elos) / len(elos)
        factor  = compute_difficulty_factor(avg_elo)
        result[league] = factor
        logger.info(
            "League: %-35s avg_elo=%.0f → factor=%.3f",
            league, avg_elo, factor,
        )

    return result


def load_clubelo_bronze(
    seed_df: pd.DataFrame | None = None,
    run_date: str | None = None,
) -> dict:
    """
    Fetches the ClubElo CSV, computes league difficulty factors, and
    uploads the result to MinIO.

    Returns a dict with: elo_by_club, difficulty_by_league, run_date.
    """
    from clients.minio_client import upload_json

    if run_date is None:
        # The ClubElo snapshot date is the data-filtering axis here (it
        # selects which day's Elo ratings to fetch), so it must default to
        # config.DECISION_DATE — not date.today() — otherwise every club
        # would be scored using ratings from months after the decision.
        from config import DECISION_DATE
        run_date = DECISION_DATE.isoformat()

    logger.info("ClubElo loader — date=%s", run_date)

    elo_rows = _fetch_clubelo_csv(run_date)
    if not elo_rows:
        # Fallback must be one day before whatever run_date actually was
        # (DECISION_DATE by default), not one day before today — otherwise
        # a failed fetch silently falls back to real-time data instead of
        # decision-time data. timedelta also handles month/year boundaries
        # correctly, unlike naive day-1 arithmetic.
        fallback_date = (date.fromisoformat(run_date) - timedelta(days=1)).isoformat()
        logger.warning("Retrying with day before run_date: %s", fallback_date)
        elo_rows = _fetch_clubelo_csv(fallback_date)

    if not elo_rows:
        logger.error("ClubElo: no data retrieved — returning empty")
        return {}

    elo_by_club: dict[str, float] = {}
    for row in elo_rows:
        try:
            elo_by_club[row["Club"]] = float(row.get("Elo", 0))
        except (ValueError, TypeError, KeyError):
            pass

    difficulty_by_league: dict[str, float] = {}
    if seed_df is not None:
        difficulty_by_league = build_league_quality_from_elo(seed_df, elo_rows)

    result = {
        "run_date":            run_date,
        "total_clubs":         len(elo_by_club),
        "elo_by_club":         elo_by_club,
        "difficulty_by_league": difficulty_by_league,
    }

    object_name = OBJECT_PATH.format(date=run_date.replace("-", ""))
    upload_json(BUCKET, object_name, result)
    logger.info(
        "ClubElo uploaded → %s | %d clubs | %d leagues mapped",
        object_name, len(elo_by_club), len(difficulty_by_league),
    )

    return result


def patch_league_quality(clubelo_result: dict, seed_df: "pd.DataFrame | None" = None) -> None:
    """
    Updates LEAGUE_DIFFICULTY in league_quality.py with the new numbers.
    Call this right after load_clubelo_bronze():

        result = load_clubelo_bronze(seed_df)
        patch_league_quality(result, seed_df)

    Note: ClubElo's daily feed has zero coverage of Botola Pro, Saudi Pro
    League, UAE Pro League, Egyptian Premier League, and Super League
    Greece — these leagues simply aren't among ClubElo's tracked
    competitions, so they keep their old hardcoded difficulty estimate.

    `seed_df` (optional) lets this function report exactly which leagues
    in your candidate pool were left on the manual estimate.
    """
    from analytics.scoring.league_quality import LEAGUE_DIFFICULTY

    difficulty = clubelo_result.get("difficulty_by_league", {})
    updated = 0
    for league, factor in difficulty.items():
        old = LEAGUE_DIFFICULTY.get(league)
        LEAGUE_DIFFICULTY[league] = factor
        updated += 1
        logger.info(
            "league_quality updated: %-35s %.3f → %.3f (Elo-based)",
            league, old or 0.0, factor,
        )

    logger.info(
        "patch_league_quality: %d leagues updated from ClubElo",
        updated,
    )

    if seed_df is not None and "league" in seed_df.columns:
        candidate_leagues = set(seed_df["league"].dropna().unique())
        not_updated = candidate_leagues - set(difficulty.keys())
        if not_updated:
            logger.warning(
                "ClubElo did NOT cover %d league(s) present in your 49-candidate "
                "pool — these are STILL using the manual hardcoded estimate in "
                "league_quality.py, not real Elo data: %s. This is expected for "
                "non-European leagues (ClubElo's daily feed doesn't track them) "
                "— see patch_league_quality()'s docstring.",
                len(not_updated), sorted(not_updated),
            )