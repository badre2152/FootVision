# ingestion/loaders/sofascore_loader.py
# Source 13: SofaScore Player Ratings (ADR-013)
#
# HISTORY — read before touching this file:
#   This file previously contained the FlashScore implementation due to a
#   file-naming mixup during dev (see flashscore_loader.py's own header,
#   and run_bronze.py's guarded-import comments referencing this issue).
#   load_sofascore_bronze did not exist anywhere in the codebase.
#
#   The original assumption (ADR-013 comment, still in flashscore_loader.py)
#   was "SofaScore -> 403 Forbidden on every endpoint, confirmed across all
#   49 players". That conclusion was reached by hitting SofaScore's
#   tournament-list/search JSON APIs directly
#   (api.sofascore.com/api/v1/config/unique-tournaments/..., .../search/...)
#   with plain requests — those ARE bot-walled.
#
#   poc_soccerdata_sofascore_v3.py proved a different approach works: fetch
#   the player's own HTML page directly (https://www.sofascore.com/football/
#   player/{slug}/{id}) and parse the __NEXT_DATA__ JSON Next.js embeds
#   server-side. That's normal page HTML, not the JSON API — confirmed
#   working (not bot-walled) against 2 real players (Bounou - goalkeeper,
#   Saibari - midfielder) covering 2 different positions and league types.
#
# CONFIRMED __NEXT_DATA__ SHAPE (from real saved pages, not docs/assumptions):
#   data["props"]["pageProps"]["player"]               -> dict, full bio
#   data["props"]["pageProps"]["transfers"]             -> list[dict]
#   data["props"]["pageProps"]["lastYearSummary"]       -> list[dict]
#   data["props"]["pageProps"]["uniqueTournamentsMap"]  -> dict[str, dict]
#
# lastYearSummary entries come in TWO confirmed shapes:
#   {"type": "event",   "timestamp": int, "value": "6.5", "uniqueTournamentId": 955}
#   {"type": "missing", "timestamp": int}                       <- no value/tournament
# "missing" entries are real (3/59 on Saibari's page) and represent a match
# in the trailing-12-month window the player didn't get rated in (unused
# sub / injury / suspension) — NOT a parse failure. Code that does
# float(entry["value"]) unconditionally will KeyError on real data.
#
# NOT present in __NEXT_DATA__ (confirmed absent): per-tournament appearance
# counts, goals/saves/clean-sheets/xG. Those load client-side via separate
# XHR after hydration and are out of scope here — this loader only sources
# avg_sofascore_rating (overall + per tournament) and a few bio/market
# fields, which is the gap club_stats_csv_loader.py and fbref_loader.py
# leave for the 24/49 non-Big-5 players.
#
# seed_df must contain: 'player_id', 'sofascore_id', 'sofascore_slug'.
# Both id and slug are required to build the URL
# (/football/player/{slug}/{id}) — SofaScore 404s on id-only or
# slug-only paths in manual testing, same pattern already documented for
# FlashScore in flashscore_loader.py.

import json
import logging
import random
import re
import time
from datetime import datetime
from statistics import mean

import pandas as pd
import requests

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

_PLAYER_URL = "https://www.sofascore.com/football/player/{slug}/{sofascore_id}"

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


def _make_driver_for_html_fetch():
    """
    Browser fallback for when plain requests.get() gets blocked (CF
    challenge / bot check) on the player HTML page itself — same
    undetected_chromedriver pattern already used by fbref_loader.py, reused
    here rather than reimplemented. Only invoked if the plain-requests path
    fails; most player pages should not need this since this is normal page
    HTML, not the JSON API that was previously confirmed bot-walled.
    """
    try:
        from ingestion.loaders.fbref_loader import _make_uc_driver
        return _make_uc_driver()
    except Exception:
        import undetected_chromedriver as uc
        return uc.Chrome(version_main=149)  # CF-07: pin to local Chrome version


def _extract_next_data(html: str) -> dict | None:
    """Pull + parse the __NEXT_DATA__ JSON blob out of a SofaScore player
    page. Returns None (not an exception) if the script tag is missing —
    callers must check, since a CF challenge page or redesign would have
    valid HTML but no __NEXT_DATA__."""
    m = _NEXT_DATA_RE.search(html)
    if m is None:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _summarize_ratings(last_year_summary: list[dict], unique_tournaments_map: dict) -> dict:
    """
    Confirmed entry shapes (see module docstring): "event" entries have
    value+uniqueTournamentId, "missing" entries have neither. Any other
    "type" value is logged and skipped rather than assumed safe, in case
    SofaScore adds a third category later.
    """
    rated_values: list[float] = []
    per_tournament: dict[int, list[float]] = {}
    missing_count = 0
    unknown_count = 0

    for entry in last_year_summary:
        etype = entry.get("type")
        if etype == "missing":
            missing_count += 1
            continue
        if etype != "event":
            unknown_count += 1
            continue

        raw_value = entry.get("value")
        tid = entry.get("uniqueTournamentId")
        if raw_value is None or tid is None:
            unknown_count += 1
            continue

        try:
            val = float(raw_value)
        except (TypeError, ValueError):
            unknown_count += 1
            continue

        rated_values.append(val)
        per_tournament.setdefault(tid, []).append(val)

    by_tournament = {}
    for tid, values in per_tournament.items():
        tname = unique_tournaments_map.get(str(tid), {}).get("name", f"unknown_id_{tid}")
        by_tournament[tname] = {
            "matches": len(values),
            "avg_rating": round(mean(values), 2),
        }

    return {
        "sofascore_rating_avg":              round(mean(rated_values), 2) if rated_values else None,
        "sofascore_form_5":                  round(mean(rated_values[:5]), 2) if rated_values else None,
        "sofascore_rated_matches":           len(rated_values),
        "sofascore_missing_rating_matches":  missing_count,
        "sofascore_unknown_entries_skipped": unknown_count,
        "sofascore_rating_by_tournament":    by_tournament,
    }


def _parse_player_html(html: str) -> dict:
    """
    Core extractor shared by both the requests path and the browser-fallback
    path. Returns {} on any structural failure (missing __NEXT_DATA__,
    missing pageProps.player, etc.) rather than raising — callers decide
    whether that means "try the browser fallback" or "give up on this
    player", so this function never throws on bad/unexpected HTML.
    """
    next_data = _extract_next_data(html)
    if next_data is None:
        return {}

    try:
        pp = next_data["props"]["pageProps"]
        player = pp["player"]
    except (KeyError, TypeError):
        return {}

    if not player:
        return {}

    last_year_summary = pp.get("lastYearSummary", [])
    unique_tournaments_map = pp.get("uniqueTournamentsMap", {})
    ratings = _summarize_ratings(last_year_summary, unique_tournaments_map)

    team = player.get("team") or {}
    tournament = team.get("tournament") or {}
    market_value_raw = player.get("proposedMarketValueRaw") or {}

    return {
        "sofascore_position": player.get("position"),
        "sofascore_current_club": team.get("name"),
        "sofascore_current_tournament": tournament.get("name"),
        "sofascore_market_value_eur": market_value_raw.get("value") or player.get("proposedMarketValue"),
        **ratings,
    }


def fetch_sofascore_player(sofascore_id: str, slug: str) -> dict:
    """
    Fetches one SofaScore player page (plain requests first, headless
    browser as fallback if that's blocked) and parses __NEXT_DATA__ for
    rating + bio fields.

    Returns dict with sofascore_* columns, or {} on failure / not found.
    """
    time.sleep(random.uniform(1.0, 2.0))

    url = _PLAYER_URL.format(slug=slug, sofascore_id=sofascore_id)

    html = None
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.info("SofaScore plain requests.get() failed for id=%s (%s) — trying browser fallback", sofascore_id, e)

    result = _parse_player_html(html) if html else {}

    if not result:
        # Either the request failed outright, or it returned HTML without
        # a usable __NEXT_DATA__ (e.g. a CF challenge page). Try the
        # browser fallback before giving up.
        try:
            driver = _make_driver_for_html_fetch()
            try:
                driver.get(url)
                time.sleep(random.uniform(1.5, 2.5))
                html = driver.page_source
            finally:
                driver.quit()
            result = _parse_player_html(html) if html else {}
        except Exception as e:
            logger.warning("SofaScore browser fallback failed for id=%s: %s", sofascore_id, e)
            return {}

    if not result:
        logger.info("SofaScore: no usable data extracted for id=%s slug=%s (url=%s)", sofascore_id, slug, url)

    return result


def load_sofascore_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Fetches SofaScore ratings + bio fields for all players in seed_df,
    then writes raw results to MinIO s3://football-bronze/sofascore/.

    seed_df must contain: 'player_id', 'sofascore_id', 'sofascore_slug'.
    Both id and slug are REQUIRED — SofaScore's player route 404s on a
    slug-only or id-only path (same pattern documented for FlashScore in
    flashscore_loader.py). Use scripts/collect_ids.py --source sofascore to
    populate sofascore_id; sofascore_slug currently has no automated
    resolver (search API confirmed bot-walled) and likely needs to be
    collected manually per player (visit the player's SofaScore page once,
    copy the slug out of the URL) until that's fixed.

    Returns status string: "OK (N/49 players with data)" or "FAILED".
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "sofascore_id", "sofascore_slug"], source="sofascore")

    records = []
    for _, row in seed_df.iterrows():
        sid = row.get("sofascore_id")
        slug = row.get("sofascore_slug")

        if pd.isna(sid) or sid in (None, "") or pd.isna(slug) or not slug:
            records.append({"player_id": row["player_id"]})
            continue

        stats = fetch_sofascore_player(str(int(sid)) if isinstance(sid, float) else str(sid), str(slug))
        records.append({"player_id": row["player_id"], **stats})

    assert_min_coverage(records, min_pct=0.30, source="sofascore")

    object_name = f"sofascore/players_sofascore_{run_date}.json"
    success = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"