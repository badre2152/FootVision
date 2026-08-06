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

_NAME_MATCH_THRESHOLD = 80

_TM_PROFILE_URL = "https://www.transfermarkt.us/{slug}/profil/spieler/{tm_id}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.transfermarkt.us/",
    "Accept-Language": "en-GB,en;q=0.9",
}

_session = requests.Session()
_session.headers.update(_HEADERS)


def _parse_market_value(value_str: str) -> float | None:
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
    h1 = soup.select_one("h1.data-header__headline-wrapper")
    if not h1:
        return None
    shirt_span = h1.select_one("span.data-header__shirt-number")
    if shirt_span:
        shirt_span.extract()
    name = re.sub(r"\s+", " ", h1.get_text(separator=" ", strip=True)).strip()
    return name or None


def _names_match(seed_name: str, tm_name: str, threshold: int = _NAME_MATCH_THRESHOLD) -> bool:
    if not seed_name or not tm_name:
        return False
    return fuzz.token_sort_ratio(seed_name.lower(), tm_name.lower()) >= threshold


def _extract_secondary_positions(soup: BeautifulSoup) -> list[str]:
    position_boxes = soup.select("dd.detail-position__position")
    if len(position_boxes) > 1:
        return [p.get_text(strip=True) for p in position_boxes[1:] if p.get_text(strip=True)]
    return []


def fetch_transfermarkt_player(
    tm_id: int | str,
    tm_slug: str,
    player_name: str | None = None,
) -> dict:
    url = _TM_PROFILE_URL.format(slug=tm_slug or "-", tm_id=tm_id)

    for attempt in range(1, 4):
        time.sleep(1.5 * attempt)
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
            break
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
        logger.debug(
            "Could not extract TM page name for tm_id=%s (slug=%s) — "
            "name-validation guard skipped for this player.",
            tm_id, tm_slug,
        )

    mv_tag = soup.select_one("a.data-header__market-value-wrapper")
    if mv_tag:
        mv_raw = mv_tag.get_text(strip=True).split("Last")[0].strip()
        mv = _parse_market_value(mv_raw)
        if mv:
            result["tm_market_value_eur"] = mv

    info_table = soup.select("span.info-table__content")
    labels     = soup.select("span.info-table__content--bold")

    label_map: dict[str, str] = {}
    for i, label_tag in enumerate(labels):
        label_text = label_tag.get_text(strip=True).lower()
        if i < len(info_table):
            label_map[label_text] = info_table[i].get_text(strip=True)

    for row in soup.select("li.data-header__label"):
        label = row.select_one(".data-header__label")
        value = row.select_one(".data-header__content")
        if label and value:
            label_map[label.get_text(strip=True).lower()] = value.get_text(strip=True)

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
            date_match = re.search(r"(\w+ \d+, \d{4}|\d{2}\.\d{2}\.\d{4})", val)
            if date_match:
                result["tm_contract_expiry"] = date_match.group(1)

    secondary_positions = _extract_secondary_positions(soup)
    if secondary_positions:
        result["tm_secondary_positions"] = ", ".join(secondary_positions)
        result["tm_num_positions"] = 1 + len(secondary_positions)
    elif "tm_position" in result:
        result["tm_num_positions"] = 1

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

    assert_min_coverage(records, min_pct=0.80, source="transfermarkt")

    object_name = f"transfermarkt/players_tm_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"