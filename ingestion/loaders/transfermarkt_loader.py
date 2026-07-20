# ingestion/loaders/transfermarkt_loader.py
# Source 4: Transfermarkt — Market Value & Player Metadata
#
# Scrapes current market value, age, position, nationality,
# contract expiry, and agent from each player's TM profile.
# Uses tm_id + tm_slug from seed to build the correct URL.

import logging
import re
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

# FIX (post-v18 review): name-validation guard. Discovered via the seed
# rebuild that ~20/49 tm_id values were invented/wrong (e.g. Bounou's tm_id
# resolved to "Steffen Lang"). assert_min_coverage() cannot catch this —
# a wrong tm_id still returns a full, well-formed record, so coverage
# reports 100% while the *data* belongs to a different player. This guard
# cross-checks the name actually printed on the TM page against the seed's
# player_name before accepting the record.
_NAME_MATCH_THRESHOLD = 80  # rapidfuzz token_sort_ratio (0-100)

_TM_PROFILE_URL = "https://www.transfermarkt.us/{slug}/profil/spieler/{tm_id}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.transfermarkt.us/",
    "Accept-Language": "en-GB,en;q=0.9",
}

# FIX v18 (T-01 diagnostic): reuse one Session across all 49 requests instead
# of a bare requests.get() per call. A fresh, cookie-less client is more
# likely to be served a stripped-down / consent-wall variant of the page by
# TM, which would 200 OK but contain none of the selectors below — exactly
# the silent-failure pattern seen in the v17 run (24/49 with data).
_session = requests.Session()
_session.headers.update(_HEADERS)


def _parse_market_value(value_str: str) -> float | None:
    """
    Converts TM market value string to float in EUR.
    Examples: '€25.00m' → 25_000_000.0  |  '€500k' → 500_000.0
    """
    if not value_str:
        return None
    value_str = value_str.replace("€", "").replace(",", ".").strip()
    try:
        if "m" in value_str.lower():
            return float(re.sub(r"[^\d.]", "", value_str)) * 1_000_000
        elif "k" in value_str.lower():
            return float(re.sub(r"[^\d.]", "", value_str)) * 1_000
        else:
            return float(re.sub(r"[^\d.]", "", value_str))
    except ValueError:
        return None


def _extract_tm_player_name(soup: BeautifulSoup) -> str | None:
    """
    Extracts the player's name from the TM profile page header.

    TM markup (current redesign):
        <h1 class="data-header__headline-wrapper">
            <span class="data-header__shirt-number">10</span>
            Yassine Bounou
        </h1>

    The shirt-number span, if present, must be stripped first — otherwise
    "10 Yassine Bounou" pollutes the fuzzy comparison.
    """
    h1 = soup.select_one("h1.data-header__headline-wrapper")
    if not h1:
        return None
    shirt_span = h1.select_one("span.data-header__shirt-number")
    if shirt_span:
        shirt_span.extract()
    # FIX: default get_text(strip=True) has separator="" — when the name is
    # split across sibling text nodes/tags (e.g. "Yassine" then <strong>Bounou
    # </strong>), the two fragments get concatenated with NO space at all
    # ("YassineBounou"). That tanked every fuzzy-match score below threshold
    # and produced false-positive mismatches on 11/49 players who were
    # actually correct. separator=" " + collapsing repeated whitespace fixes it.
    name = re.sub(r"\s+", " ", h1.get_text(separator=" ", strip=True)).strip()
    return name or None


def _names_match(seed_name: str, tm_name: str, threshold: int = _NAME_MATCH_THRESHOLD) -> bool:
    """
    Fuzzy-compares the seed's player_name against the name scraped from the
    TM page. token_sort_ratio is used (not a plain ratio) because TM slugs
    occasionally reorder name parts — e.g. the seed rebuild surfaced
    "Yassine Gessime" vs TM's "Gessime Yassine" for the same player, which a
    naive ratio would score low but token_sort_ratio scores correctly high.
    """
    if not seed_name or not tm_name:
        return False
    return fuzz.token_sort_ratio(seed_name.lower(), tm_name.lower()) >= threshold


def _extract_secondary_positions(soup: BeautifulSoup) -> list[str]:
    """
    Attempts to extract a player's secondary/"other" positions from their
    TM profile page.

    ⚠️ UNTESTED AGAINST LIVE TRANSFERMARKT — see the call-site comment
    inside fetch_transfermarkt_player() for why, and the verification
    steps required before trusting this in production scoring.

    Tries a known TM markup pattern (current redesign, as of the last
    time this codebase's author checked TM's HTML by hand — could easily
    be stale by the time you read this, TM changes markup often):

        <div class="detail-position">
            <div class="detail-position__position">Centre-Back</div>
            <div class="detail-position__position">Right-Back</div>
            ...
        </div>

    First match = main position (already captured elsewhere as
    tm_position), any additional matches = secondary positions.

    Returns:
        List of secondary position strings (empty list if none found /
        selector didn't match — caller treats that as "1 position" via
        a safe default, NOT as missing data).
    """
    position_boxes = soup.select("div.detail-position__position")
    if len(position_boxes) > 1:
        return [p.get_text(strip=True) for p in position_boxes[1:] if p.get_text(strip=True)]
    return []


def fetch_transfermarkt_player(
    tm_id: int | str,
    tm_slug: str,
    player_name: str | None = None,
) -> dict:
    """
    Scrapes metadata and market value from one Transfermarkt profile page.

    v18: added retry (3 attempts, exponential backoff) for 502/timeout errors
    which are transient infrastructure issues on TM's CDN, not permanent blocks.

    Name-validation guard: if player_name is supplied, the name printed on
    the fetched TM page is fuzzy-matched against it. On mismatch (wrong
    tm_id in the seed — e.g. an invented/stale ID resolving to a different
    player) the function returns {} and logs a warning, rather than silently
    returning another player's data under this player_id. If the name
    selector itself fails to find anything (e.g. TM changes markup again),
    the check is skipped — a stale selector should not block otherwise-valid
    data, it should just forgo the extra safety net.
    """
    url = _TM_PROFILE_URL.format(slug=tm_slug or "-", tm_id=tm_id)

    for attempt in range(1, 4):
        time.sleep(1.5 * attempt)  # rate limit + backoff
        try:
            resp = _session.get(url, timeout=30)
            if resp.status_code in (403, 404):
                logger.warning("TM %d for tm_id=%s (slug=%s)", resp.status_code, tm_id, tm_slug)
                return {}
            if resp.status_code == 502:
                logger.warning("TM 502 for tm_id=%s (attempt %d/3) — retrying", tm_id, attempt)
                time.sleep(8 * attempt)
                continue
            resp.raise_for_status()
            break  # success
        except requests.exceptions.Timeout:
            logger.warning("TM timeout for tm_id=%s (attempt %d/3)", tm_id, attempt)
            if attempt == 3:
                return {}
            time.sleep(8 * attempt)
            continue
        except requests.RequestException as e:
            logger.warning("TM fetch failed for tm_id=%s: %s", tm_id, e)
            return {}
    else:
        logger.warning("TM fetch failed after 3 attempts for tm_id=%s", tm_id)
        return {}

    soup = BeautifulSoup(resp.content, "html.parser")
    result: dict = {}

    # ── Name-validation guard ───────────────────────────────────────────────
    # Must run BEFORE market value / info-table extraction: if tm_id is wrong,
    # there is no point parsing (and trusting) fields from the wrong player.
    tm_name = _extract_tm_player_name(soup)
    if player_name and tm_name and not _names_match(player_name, tm_name):
        logger.warning(
            "TM name mismatch for tm_id=%s (slug=%s): seed expects '%s' but TM "
            "page shows '%s' — likely wrong tm_id in seed. Returning {} instead "
            "of mismatched data.",
            tm_id, tm_slug, player_name, tm_name,
        )
        return {}
    elif player_name and not tm_name:
        # Selector found nothing — don't block on it, just note it for debugging.
        logger.debug(
            "Could not extract TM page name for tm_id=%s (slug=%s) — "
            "name-validation guard skipped for this player.",
            tm_id, tm_slug,
        )

    # ── Market value ──────────────────────────────────────────────────────────
    mv_tag = soup.select_one("a.data-header__market-value-wrapper")
    if mv_tag:
        mv_raw = mv_tag.get_text(strip=True).split("Last")[0].strip()
        mv = _parse_market_value(mv_raw)
        if mv:
            result["tm_market_value_eur"] = mv

    # ── Info table (age, position, foot, height, contract) ────────────────────
    info_table = soup.select("span.info-table__content")
    labels     = soup.select("span.info-table__content--bold")

    label_map: dict[str, str] = {}
    for i, label_tag in enumerate(labels):
        label_text = label_tag.get_text(strip=True).lower()
        # Find the corresponding value (next sibling content span)
        if i < len(info_table):
            label_map[label_text] = info_table[i].get_text(strip=True)

    # Fallback: parse all definition-list rows
    for row in soup.select("li.data-header__label"):
        label = row.select_one(".data-header__label")
        value = row.select_one(".data-header__content")
        if label and value:
            label_map[label.get_text(strip=True).lower()] = value.get_text(strip=True)

    # Extract known fields
    for key, val in label_map.items():
        if "age" in key and val.isdigit():
            result["tm_age"] = int(val)
        elif "position" in key:
            result["tm_position"] = val
        elif "foot" in key:
            result["tm_foot"] = val
        elif "height" in key:
            height_match = re.search(r"(\d{3})", val.replace(",", ""))
            if height_match:
                result["tm_height_cm"] = int(height_match.group(1))
        elif "contract" in key or "expires" in key:
            # Try to parse date
            date_match = re.search(r"(\w+ \d+, \d{4}|\d{2}\.\d{2}\.\d{4})", val)
            if date_match:
                result["tm_contract_expiry"] = date_match.group(1)

    # ── Secondary positions (FlexibilityScore raw input) — 2026-07-14 ────────
    # ⚠️ UNTESTED AGAINST LIVE TRANSFERMARKT. Written from known TM markup
    # patterns, NOT verified with a live fetch (transfermarkt.com is not
    # reachable from the sandbox this was written in — only raw.
    # githubusercontent.com/pypi/npm domains are). VERIFY on a machine with
    # real network access before trusting tm_num_positions in scoring:
    #   1. Run this loader for 3-5 known-versatile players (e.g. a
    #      full-back who also plays winger).
    #   2. Print the raw soup around ".detail-position" and confirm the
    #      selectors below actually match — TM's markup changes over time
    #      (see the T-01 diagnostic comment above re: the main position
    #      selectors already needing an update once).
    #   3. If selectors don't match, log.debug the page title/length like
    #      the existing "0 fields parsed" guard does, don't guess further.
    secondary_positions = _extract_secondary_positions(soup)
    if secondary_positions:
        result["tm_secondary_positions"] = ", ".join(secondary_positions)
        result["tm_num_positions"] = 1 + len(secondary_positions)
    elif "tm_position" in result:
        # Main position parsed, no secondary found (or selector didn't
        # match) — default to 1. This is the SAFE assumption: treating
        # "no secondary position found" as "plays only 1 position" is
        # neutral, not a penalty, and matches what happens for the 38/49
        # players never covered by this loader at all (imputed the same
        # way downstream).
        result["tm_num_positions"] = 1

    # FIX v18 (T-01 diagnostic): the v17 run silently returned {} for ~25/49
    # players with no indication of *why*. If we got a 200 response but
    # extracted nothing, log it explicitly along with a content fingerprint
    # so we can tell apart "selectors are stale" from "TM served a degraded
    # page" (e.g. cookie-consent wall, geo-redirect, A/B markup variant).
    if not result:
        title = soup.title.get_text(strip=True) if soup.title else "(no <title>)"
        logger.warning(
            "TM page reachable but 0 fields parsed for tm_id=%s (slug=%s) | "
            "status=%s len=%dB title=%r",
            tm_id, tm_slug, resp.status_code, len(resp.content), title,
        )

    return result


def load_transfermarkt_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Fetches market value and metadata from Transfermarkt for all 49 players.
    All players have tm_id and tm_slug — expect ~49/49 coverage.

    Each fetched record's name is fuzzy-validated against the seed's
    player_name before being accepted (see fetch_transfermarkt_player).
    This catches wrong/invented tm_id values in the seed that would
    otherwise silently return another player's data under this player_id.

    Writes results to s3://football-bronze/transfermarkt/

    Args:
        seed_df:  Must contain columns: player_id, tm_id, tm_slug, player_name
        run_date: Timestamp string; auto-generated if empty

    Returns:
        Status string: "OK (N/49 players with data)" or "FAILED"
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "tm_id", "tm_slug", "player_name"], source="transfermarkt")

    records = []
    for _, row in seed_df.iterrows():
        tm_id  = row.get("tm_id")
        tm_slug = row.get("tm_slug") or "-"
        player_name = row.get("player_name")

        if pd.isna(tm_id) or not tm_id:
            records.append({"player_id": row["player_id"]})
            continue

        stats = fetch_transfermarkt_player(int(tm_id), str(tm_slug), player_name=player_name)
        records.append({"player_id": row["player_id"], **stats})

    # All 49 have tm_id → expect ~90%+ coverage
    assert_min_coverage(records, min_pct=0.80, source="transfermarkt")

    object_name = f"transfermarkt/players_tm_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"