import base64
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Optional

import cloudscraper

logger = logging.getLogger(__name__)

BASE_URL      = "https://www.fotmob.com"
PLAYER_URL    = f"{BASE_URL}/api/players"
TARGET_SEASON = "2025/2026"
SLEEP_BETWEEN = 1.5

_XMAS_SECRET = b"cYmAYABmAzGGGGGA"

FOTMOB_IDS: dict[str, int] = {
    "P007": 770881,
    "P008": 775539,
    "P009": 617310,
    "P010": 1187506,
    "P012": 696443,
    "P013": 1050838,
    "P014": 909908,
    "P016": 956921,
    "P017": 1581722,
    "P018": 1208708,
    "P020": 903394,
    "P021": 614912,
    "P022": 1693008,
    "P025": 1340895,
    "P026": 1180288,
    "P028": 1561552,
    "P029": 1598170,
    "P030": 1260147,
    "P033": 1283407,
    "P035": 750027,
    "P036": 988749,
    "P037": 884382,
    "P039": 1368654,
    "P041": 1680173,
    "P045": 1640985,
    "P049": 1699296,
}


def _generate_xmas_token() -> str:
    ts  = int(time.time() * 1000)
    sig = hmac.new(_XMAS_SECRET, str(ts).encode(), hashlib.sha256).hexdigest()
    return base64.b64encode(json.dumps({"token": sig, "expiration": ts}).encode()).decode()


def _make_scraper() -> cloudscraper.CloudScraper:
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    scraper.headers.update({
        "x-mas":           _generate_xmas_token(),
        "Referer":         "https://www.fotmob.com/",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return scraper


def _fetch_next_data(fotmob_id: int, scraper) -> Optional[dict]:
    """Fetch __NEXT_DATA__ JSON from /api/players/{id}."""
    try:
        resp = scraper.get(f"{PLAYER_URL}/{fotmob_id}", timeout=20)
        if resp.status_code == 401:
            scraper.headers.update({"x-mas": _generate_xmas_token()})
            resp = scraper.get(f"{PLAYER_URL}/{fotmob_id}", timeout=20)
        resp.raise_for_status()

        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            resp.text, re.DOTALL
        )
        if not match:
            logger.warning("No __NEXT_DATA__ found for id=%s", fotmob_id)
            return None

        full = json.loads(match.group(1))
        return full.get("props", {}).get("pageProps", {}).get("data", {})

    except Exception as e:
        logger.warning("Fetch failed for id=%s: %s", fotmob_id, e)
        return None


def _extract_stats(data: dict, pid: str) -> dict:
    """
    Extract stats from pageProps.data structure.

    Sources:
    - careerHistory.careerItems.senior.seasonEntries → MP, Gls, Ast (2025/26)
    - careerHistory.careerItems."national team"       → intl_caps, intl_goals, intl_ast
    - firstSeasonStats.statsSection.items             → per90 rates (Tkl, Int, Clr, xG, xA)
    - mainLeague.stats                                → Goals, Assists (fallback)
    """
    out = {}

    name = data.get("name", "")
    out["fm_name"] = name

    career_items = data.get("careerHistory", {}).get("careerItems", {})
    senior = career_items.get("senior", {})
    season_entries = senior.get("seasonEntries", [])

    season_2526 = None
    for s in season_entries:
        if TARGET_SEASON in s.get("seasonName", ""):
            season_2526 = s
            break
    if not season_2526 and season_entries:
        season_2526 = season_entries[0]

    if season_2526:
        apps = season_2526.get("appearances")
        gls  = season_2526.get("goals")
        ast  = season_2526.get("assists")
        out["fm_mp"]  = int(apps) if apps else None
        out["fm_min"] = None

        recent = data.get("recentMatches", [])
        mins_2526 = sum(
            m.get("minutesPlayed", 0) for m in recent
            if m.get("minutesPlayed") and not _is_national(m)
        )
        if mins_2526 < 90 and out["fm_mp"]:
            mins_2526 = int(out["fm_mp"]) * 85

        out["fm_min"] = mins_2526 if mins_2526 > 0 else None

        base = mins_2526 if mins_2526 > 90 else None
        if base:
            if gls is not None:
                out["fm_gls_per90"] = round(int(gls) / (base / 90), 4)
            if ast is not None:
                out["fm_ast_per90"] = round(int(ast) / (base / 90), 4)

    natl = career_items.get("national team", {})
    natl_entries = natl.get("teamEntries", [])
    morocco_entry = next(
        (e for e in natl_entries if e.get("teamId") == 6262),
        natl_entries[0] if natl_entries else None
    )
    if morocco_entry:
        out["fm_intl_caps"]    = _safe_int(morocco_entry.get("appearances"))
        out["fm_intl_goals"]   = _safe_int(morocco_entry.get("goals"))
        out["fm_intl_assists"] = _safe_int(morocco_entry.get("assists"))

        natl_seasons = natl.get("seasonEntries", [])
        total_intl_apps = sum(_safe_int(s.get("appearances"), 0) for s in natl_seasons)
        intl_mins = total_intl_apps * 85
        if intl_mins > 90 and out.get("fm_intl_goals") is not None:
            out["fm_intl_goals_per90"]   = round(out["fm_intl_goals"] / (intl_mins / 90), 4)
            out["fm_intl_assists_per90"] = round(out["fm_intl_assists"] / (intl_mins / 90), 4)

    first_stats = data.get("firstSeasonStats", {})
    stats_section = first_stats.get("statsSection", {})

    STAT_MAP = {
        "goals":                      "fm_gls_per90",
        "assists":                    "fm_ast_per90",
        "expected_goals":             "fm_xg_per90",
        "non_penalty_xg":             "fm_npxg_per90",
        "expected_assists":           "fm_xa_per90",
        "expected_goals_on_target":   "fm_xgot_per90",
        "chances_created":            "fm_key_passes_per90",
        "ShotsOnTarget":              "fm_shots_on_target",
        "shotsOnTarget":              "fm_shots_on_target",
        "matchstats.headers.tackles": "fm_tkl_per90",
        "tackles":                    "fm_tkl_per90",
        "interceptions":              "fm_int_per90",
        "clearances":                 "fm_clr_per90",
        "saves":                      "fm_saves_per90",
        "savesPercentage":            "fm_save_pct",
        "save_percentage":            "fm_save_pct",
        "goals_conceded":             "fm_ga_per90",
        "goalsConceded":              "fm_ga_per90",
        "goals_conceded_per_game":    "fm_ga_per90",
        "cleanSheets":                "fm_clean_sheets",
        "clean_sheets":               "fm_clean_sheets",
    }

    for group in stats_section.get("items", []):
        for item in group.get("items", []):
            tid = item.get("localizedTitleId", "")
            col = STAT_MAP.get(tid) or STAT_MAP.get(tid.split(".")[-1])
            if col and col not in out:
                per90 = item.get("per90")
                stat_val = item.get("statValue")
                if per90 is not None:
                    out[col] = round(float(per90), 4)
                elif stat_val is not None:
                    try:
                        out[col] = round(float(stat_val), 4)
                    except (TypeError, ValueError):
                        pass

    main_league_stats = data.get("mainLeague", {}).get("stats", [])
    for stat in main_league_stats:
        title = stat.get("localizedTitleId", stat.get("title", "")).lower()
        val   = stat.get("value")
        if val is None:
            continue
        if "goal" in title and "fm_gls_total" not in out:
            out["fm_gls_total"] = _safe_int(val)
        if "assist" in title and "fm_ast_total" not in out:
            out["fm_ast_total"] = _safe_int(val)

    return out


def _is_national(match: dict) -> bool:
    """Check if match is a national team game (not club)."""
    league = match.get("leagueName", "")
    natl_keywords = ["World Cup", "AFCON", "Africa Cup", "Nations", "Friendly", "Qualif"]
    return any(kw in league for kw in natl_keywords)


def _safe_int(val, default=None):
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def run_fotmob_loader(
    seed_path: str = "seeds/players_49.csv",
    target_pids: list[str] | None = None,
) -> list[dict]:
    import csv

    players = {}
    with open(seed_path) as f:
        for row in csv.DictReader(f):
            players[row["player_id"]] = row

    targets = [
        (pid, players[pid]) for pid in (target_pids or list(FOTMOB_IDS.keys()))
        if pid in players and pid in FOTMOB_IDS
    ]

    logger.info("Fotmob loader — %d players | x-mas token generated", len(targets))
    scraper = _make_scraper()
    results = []

    for i, (pid, p) in enumerate(targets, 1):
        name      = p["player_name"]
        fotmob_id = FOTMOB_IDS[pid]

        logger.info("[%d/%d] %s — fotmob_id=%s", i, len(targets), name, fotmob_id)

        out = {"player_id": pid, "fm_fotmob_id": fotmob_id}

        if i % 8 == 0:
            scraper.headers.update({"x-mas": _generate_xmas_token()})
            logger.debug("Token refreshed")

        data = _fetch_next_data(fotmob_id, scraper)
        time.sleep(SLEEP_BETWEEN)

        if not data:
            results.append(out)
            continue

        fetched_name = data.get("name", "")
        if fetched_name and fetched_name.split()[0].lower() not in name.lower():
            logger.warning("  ⚠ Name mismatch: expected '%s', got '%s'", name, fetched_name)

        stats = _extract_stats(data, pid)
        out.update(stats)

        logger.info("  → Gls/90=%s Ast/90=%s Tkl/90=%s Int/90=%s Clr/90=%s intl=%s",
                    out.get("fm_gls_per90","—"),
                    out.get("fm_ast_per90","—"),
                    out.get("fm_tkl_per90","—"),
                    out.get("fm_int_per90","—"),
                    out.get("fm_clr_per90","—"),
                    out.get("fm_intl_caps","—"))

        results.append(out)

    ok = sum(1 for r in results if r.get("fm_gls_per90") is not None or r.get("fm_tkl_per90") is not None)
    logger.info("Fotmob loader complete: %d/%d with stats", ok, len(results))
    return results