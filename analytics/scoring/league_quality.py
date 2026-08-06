import logging
from datetime import date

logger = logging.getLogger(__name__)

_CLUBELO_BUCKET = "football-bronze"
_CLUBELO_PREFIX_TEMPLATE = "clubelo/clubelo_{date}"

_ELO_SNAPSHOT_CACHE: dict[str, dict | None] = {}

LEAGUE_DIFFICULTY: dict[str, float] = {
    "Premier League":          1.00,
    "La Liga":                 1.00,
    "Bundesliga":              1.00,
    "Serie A":                 1.00,
    "Ligue 1":                 1.00,

    "Saudi Pro League":        0.90,
    "Eredivisie":              0.90,
    "Jupiler Pro League":      0.88,
    "Championship":            0.88,
    "Primeira Liga":           0.87,
    "Süper Lig":               0.85,

    "Super League Greece":     0.82,
    "Egyptian Premier League": 0.80,
    "UAE Pro League":          0.78,
    "Al Ittihad Club":         0.78,

    "Botola Pro":              0.72,

    "_default":                0.85,
}


def _resolve_as_of_date(as_of_date: "date | str | None") -> date:
    if as_of_date is None:
        from config import DECISION_DATE
        return DECISION_DATE
    if isinstance(as_of_date, str):
        return date.fromisoformat(as_of_date)
    return as_of_date


def _load_elo_snapshot(as_of_date: date) -> dict | None:
    date_str = as_of_date.strftime("%Y%m%d")
    if date_str in _ELO_SNAPSHOT_CACHE:
        return _ELO_SNAPSHOT_CACHE[date_str]

    from clients.minio_client import read_latest_json

    prefix = _CLUBELO_PREFIX_TEMPLATE.format(date=date_str)
    snapshot = read_latest_json(bucket=_CLUBELO_BUCKET, prefix=prefix)
    if not snapshot:
        logger.debug(
            "ClubElo: no Bronze snapshot found for %s (prefix='%s') — "
            "Elo-based coefficients unavailable for this run, falling back "
            "to the manual LEAGUE_DIFFICULTY table.",
            date_str, prefix,
        )
    _ELO_SNAPSHOT_CACHE[date_str] = snapshot
    return snapshot


def compute_elo_based_coefficient(
    league_name: str | None,
    as_of_date: "date | str | None" = None,
) -> float | None:
    if not league_name or not isinstance(league_name, str):
        return None

    resolved_date = _resolve_as_of_date(as_of_date)
    snapshot = _load_elo_snapshot(resolved_date)
    if not snapshot:
        return None

    difficulty = snapshot.get("difficulty_by_league", {})
    if not difficulty:
        return None

    if league_name in difficulty:
        return float(difficulty[league_name])

    league_lower = league_name.lower()
    for key, val in difficulty.items():
        if key.lower() == league_lower:
            return float(val)

    return None


def get_league_factor(league: str | None, as_of_date: "date | str | None" = None) -> float:
    if not league or not isinstance(league, str):
        return LEAGUE_DIFFICULTY["_default"]

    elo_factor = compute_elo_based_coefficient(league, as_of_date)
    if elo_factor is not None:
        logger.debug(
            "League factor (Elo-based): '%s' -> %.3f (ClubElo snapshot)",
            league, elo_factor,
        )
        return elo_factor

    if league in LEAGUE_DIFFICULTY:
        return LEAGUE_DIFFICULTY[league]

    league_lower = league.lower()
    for key, val in LEAGUE_DIFFICULTY.items():
        if key.startswith("_"):
            continue
        if key.lower() in league_lower or league_lower in key.lower():
            logger.debug("League partial match: '%s' -> '%s' (factor=%.2f)", league, key, val)
            return val

    logger.warning(
        "Unknown league '%s' — using default factor %.2f. "
        "Not covered by ClubElo and not in LEAGUE_DIFFICULTY in league_quality.py.",
        league,
        LEAGUE_DIFFICULTY["_default"],
    )
    return LEAGUE_DIFFICULTY["_default"]


def adjust_stat_for_league(value: float, league: str | None, inverse: bool = False) -> float:
    factor = get_league_factor(league)
    if factor <= 0:
        return value
    return value / factor if inverse else value * factor


def adjust_gk_ga_per90(ga_per90: float, league: str | None) -> float:
    adjusted = adjust_stat_for_league(ga_per90, league, inverse=True)
    if abs(adjusted - ga_per90) > 0.05:
        logger.debug(
            "GA/90 adjusted: raw=%.3f factor=%.2f adjusted=%.3f (league='%s')",
            ga_per90, get_league_factor(league), adjusted, league,
        )
    return adjusted