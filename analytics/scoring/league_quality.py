# analytics/scoring/league_quality.py
# League Quality Coefficients — GK GA/90 Bias Correction
#
# Problem: GA/90 is not league-independent. A Botola Pro GK facing 4 shots/game
# naturally concedes fewer goals than a Saudi Pro League GK facing 8+ shots/game.
# Without adjustment, Botola GKs appear artificially elite.
#
# Fix: multiply raw GA/90 by a league difficulty factor before normalization.
# adjusted_GA_per90 = raw_GA_per90 * league_factor
# Lower factor = weaker league = inflate GA/90 toward a league-neutral baseline.
#
# Coefficients are derived from:
#   - UEFA/CAF coefficient rankings (2024-25 season)
#   - Average shots on target per game per league (Opta/StatsBomb public data)
#   - Subject to manual validation by coaching staff
#
# Methodology: Big-5 avg (Premier League / La Liga / Bundesliga) = 1.00 baseline.
# All others indexed relative to that.
#
# IMPORTANT: This correction applies ONLY to GK GA_per90 normalization input.
# It does NOT change the raw Silver GA_per90 column (preserve source data).
# The adjusted value is used transiently inside compute_performance_score().

import logging

logger = logging.getLogger(__name__)

# ── League difficulty coefficients ────────────────────────────────────────────
# 1.00 = Big-5 level (Premier League, La Liga, Bundesliga, Serie A, Ligue 1)
# < 1.00 = weaker league → inflate GA/90 to compensate
# Values are conservative: erring toward smaller corrections to avoid over-engineering.
LEAGUE_DIFFICULTY: dict[str, float] = {
    # Tier 1 — Big 5 European (baseline = 1.00)
    "Premier League":          1.00,
    "La Liga":                 1.00,
    "Bundesliga":              1.00,
    "Serie A":                 1.00,
    "Ligue 1":                 1.00,

    # Tier 2 — Strong mid-table European / Top Gulf
    "Saudi Pro League":        0.90,   # High investment, good attacking players
    "Eredivisie":              0.90,
    "Jupiler Pro League":      0.88,
    "Championship":            0.88,   # Physically intense, less technical pressure
    "Primeira Liga":           0.87,
    "Süper Lig":               0.85,

    # Tier 3 — Mid-level European / Top African
    "Super League Greece":     0.82,
    "Egyptian Premier League": 0.80,
    "UAE Pro League":          0.78,
    "Al Ittihad Club":         0.78,   # Qatar / Saudi non-SPL

    # Tier 4 — Moroccan / regional
    "Botola Pro":              0.72,   # Key correction: Botola ≈ 72% of Big-5 difficulty

    # Fallback for unrecognised leagues
    "_default":                0.85,
}


def get_league_factor(league: str | None) -> float:
    """
    Returns the difficulty coefficient for a given league name.
    Falls back to _default if the league is not in the table.

    Args:
        league: League name string (as stored in Silver 'league' column).

    Returns:
        Float coefficient in (0, 1]. 1.0 = Big-5 baseline.
    """
    if not league or not isinstance(league, str):
        return LEAGUE_DIFFICULTY["_default"]

    # Exact match first
    if league in LEAGUE_DIFFICULTY:
        return LEAGUE_DIFFICULTY[league]

    # Partial match (case-insensitive) for slightly different names
    league_lower = league.lower()
    for key, val in LEAGUE_DIFFICULTY.items():
        if key.startswith("_"):
            continue
        if key.lower() in league_lower or league_lower in key.lower():
            logger.debug("League partial match: '%s' -> '%s' (factor=%.2f)", league, key, val)
            return val

    logger.warning(
        "Unknown league '%s' — using default factor %.2f. "
        "Add to LEAGUE_DIFFICULTY in league_quality.py.",
        league,
        LEAGUE_DIFFICULTY["_default"],
    )
    return LEAGUE_DIFFICULTY["_default"]


def adjust_gk_ga_per90(ga_per90: float, league: str | None) -> float:
    """
    Applies league difficulty adjustment to a GK's raw GA/90.

    adjusted = raw * (1 / factor)
    — A Botola GK with GA/90=0.38 and factor=0.72 gets adjusted to 0.53,
      making them more comparable to a Big-5 GK conceding 0.53/game.

    Args:
        ga_per90: Raw goals-against per 90 minutes.
        league:   League name from Silver 'league' column.

    Returns:
        Adjusted GA/90 — higher than raw for weaker leagues.
    """
    factor = get_league_factor(league)
    if factor <= 0:
        return ga_per90
    adjusted = ga_per90 / factor
    if abs(adjusted - ga_per90) > 0.05:
        logger.debug(
            "GA/90 adjusted: raw=%.3f factor=%.2f adjusted=%.3f (league='%s')",
            ga_per90, factor, adjusted, league,
        )
    return adjusted
