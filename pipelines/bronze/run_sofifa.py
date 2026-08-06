import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page
from playwright_stealth import Stealth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_sofifa")

SOFIFA_IDS: dict[str, int] = {
    "P003": 209981,
    "P004": 223573,
    "P005": 241777,
    "P007": 235212,
    "P008": 236401,
    "P010": 275353,
    "P012": 82240,
    "P014": 235890,
    "P016": 258490,
    "P017": 231633,
    "P018": 268728,
    "P021": 264657,
    "P023": 224158,
    "P024": 255125,
    "P025": 257504,
    "P026": 259480,
    "P027": 262745,
    "P032": 278901,
    "P033": 279702,
    "P036": 235410,
    "P037": 264348,
    "P038": 243586,
    "P039": 231410,
    "P041": 78474,
    "P042": 275048,
    "P045": 70017,
    "P046": 273666,
}

SOFIFA_PLAYER_URL = "https://sofifa.com/player/{pid}/"

LABEL_MAP = {
    "composure":           "composure",
    "reactions":            "reactions",
    "aggression":            "aggression",
    "interceptions":          "interceptions",
    "standing tackle":         "standing_tackle",
    "gk diving":                "gk_diving",
    "gk handling":               "gk_handling",
    "gk reflexes":                 "gk_reflexes",
}

CHALLENGE_TITLE_MARKERS = ("moment", "instant", "attention", "waiting", "checking")


def wait_for_challenge_to_clear(page: Page, max_seconds: int = 25) -> bool:
    for _ in range(max_seconds):
        time.sleep(1)
        title = (page.title() or "").lower()
        if not any(marker in title for marker in CHALLENGE_TITLE_MARKERS):
            time.sleep(1.5)
            return True
    return False


def safe_get_content(page: Page, retries: int = 5, delay: float = 1.0) -> str:
    last_err = None
    for _ in range(retries):
        try:
            return page.content()
        except Exception as e:
            last_err = e
            time.sleep(delay)
    logger.warning("  ✗ Failed to read content after %d retries: %s", retries, last_err)
    return ""


def fetch_attrs(page: Page, sofifa_id: int) -> dict:
    url = SOFIFA_PLAYER_URL.format(pid=sofifa_id)
    attrs: dict = {"sofifa_id": sofifa_id}

    try:
        page.goto(url, timeout=30000)
    except Exception as e:
        logger.warning("  ✗ goto failed id=%d: %s", sofifa_id, e)
        return {}

    cleared = wait_for_challenge_to_clear(page)
    if not cleared:
        logger.warning("  ✗ Cloudflare challenge not cleared id=%d", sofifa_id)
        return {}

    html = safe_get_content(page)
    if not html or len(html) < 50000:
        logger.warning("  ✗ Incomplete/empty content id=%d (len=%d)", sofifa_id, len(html or ""))
        return {}

    soup = BeautifulSoup(html, "html.parser")

    for div in soup.select("div.grid div.col"):
        em = div.find("em")
        sub = div.find("div", class_="sub")
        if em and sub and em.get("title"):
            label = sub.get_text(strip=True).lower()
            if label == "overall rating":
                attrs["sofifa_overall"] = int(em["title"])
            elif label == "potential":
                attrs["sofifa_potential"] = int(em["title"])

    for p in soup.select("div.grid.attribute p"):
        spans = p.find_all("span")
        if len(spans) < 2:
            continue
        value_span, label_span = spans[0], spans[-1]
        em = value_span.find("em")
        if not em or not em.get("title"):
            continue
        label = label_span.get_text(strip=True).lower()
        col = LABEL_MAP.get(label)
        if col:
            try:
                attrs[f"sofifa_{col}"] = int(em["title"])
            except ValueError:
                pass

    return attrs


def norm_attr(val: int, lo: int = 40, hi: int = 95) -> float | None:
    if not val or val <= 0:
        return None
    return round((max(lo, min(hi, val)) - lo) / (hi - lo), 4)


def mental_score(attrs: dict, pos: str) -> float | None:
    DISCOUNT = 0.70
    POS_MED = {"GK": 0.3192, "DEF": 0.3755, "MID": 0.4007, "FWD": 0.4007}

    c = attrs.get("sofifa_composure")
    r = attrs.get("sofifa_reactions")
    a = attrs.get("sofifa_gk_reflexes") if pos == "GK" else attrs.get("sofifa_aggression")

    vals = [
        (norm_attr(c) if c else None, 0.50),
        (norm_attr(r) if r else None, 0.35),
        (norm_attr(a) if a else None, 0.15),
    ]
    avail = [(v, w) for v, w in vals if v is not None]
    if not avail:
        return None
    tw = sum(w for _, w in avail)
    tv = sum(v * w for v, w in avail)
    raw = tv / tw
    med = POS_MED.get(pos, 0.4007)
    return round(raw * DISCOUNT + med * (1 - DISCOUNT), 4)


def build_minio_upload_payload(results: list[dict]) -> list[dict]:
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=3.0,
                         help="Wait time between players (seconds)")
    parser.add_argument("--seed", default="seeds/players_49.csv")
    parser.add_argument("--headless", action="store_true",
                         help="Run in headless mode (Cloudflare often detects this, not recommended)")
    parser.add_argument(
        "--as-of", default=None,
        help=(
            "YYYY-MM-DD decision-date cutoff (config.DECISION_DATE by default). "
            "NOTE: SoFIFA loader currently has no date-relative filtering logic "
            "(ratings are point-in-time snapshots, not a lookback window) — this "
            "flag is accepted for CLI consistency across Bronze entrypoints "
            "(01_temporal_cutoff.md req. #3) and logged, but does not yet change "
            "loader behavior. Revisit if SoFIFA ever adds season-history scraping."
        ),
    )
    args = parser.parse_args()

    from config import DECISION_DATE
    as_of = args.as_of or DECISION_DATE.isoformat()
    run_date = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.info("=" * 60)
    logger.info("SoFIFA Bronze Loader (Playwright) — run_date: %s | as-of: %s", run_date, as_of)
    logger.info("targets: %d sofifa IDs configured", len(SOFIFA_IDS))
    logger.info("=" * 60)

    seed = pd.read_csv(args.seed)
    pid_to_row = seed.set_index("player_id").to_dict("index")

    targets = [(pid, sfid) for pid, sfid in SOFIFA_IDS.items() if pid in pid_to_row]
    if args.limit:
        targets = targets[: args.limit]

    if args.dry_run:
        logger.info("DRY RUN — %d players:", len(targets))
        for pid, sfid in targets:
            name = pid_to_row.get(pid, {}).get("player_name", "?")
            logger.info("  %s → %s  sofifa_id=%d", pid, name, sfid)
        return

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        Stealth().use_sync(page)

        for i, (pid, sfid) in enumerate(targets, 1):
            row = pid_to_row.get(pid, {})
            name = row.get("player_name", pid)
            pos = row.get("position", "MID")

            logger.info("[%d/%d] %s — sofifa_id=%d", i, len(targets), name, sfid)

            attrs = fetch_attrs(page, sfid)

            if len(attrs) <= 1:
                logger.warning("  ✗ no attributes")
                results.append({"player_id": pid})
                time.sleep(args.delay)
                continue

            for col in ("composure", "reactions", "aggression", "gk_reflexes"):
                raw = attrs.get(f"sofifa_{col}")
                if raw:
                    attrs[f"sofifa_{col}_norm"] = norm_attr(raw)

            m = mental_score(attrs, pos)
            if m:
                attrs["sofifa_mental_score"] = m

            logger.info(
                "  → ovr=%-3s comp=%-3s react=%-3s aggr/gkref=%-3s mental=%.4f",
                attrs.get("sofifa_overall", "?"),
                attrs.get("sofifa_composure", "?"),
                attrs.get("sofifa_reactions", "?"),
                attrs.get("sofifa_aggression") or attrs.get("sofifa_gk_reflexes", "?"),
                m or 0,
            )

            results.append({"player_id": pid, "player_name": name, **attrs})
            time.sleep(args.delay)

        browser.close()

    out_dir = Path("bronze/sofifa")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"players_sofifa_{run_date}.json"
    payload = {"run_date": run_date, "players": results}
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info("Saved locally → %s", out_file)

    try:
        from clients.minio_client import upload_json
        upload_json(
            "football-bronze",
            f"sofifa/players_sofifa_{run_date}.json",
            build_minio_upload_payload(results),
        )
        logger.info("Uploaded → s3://football-bronze/sofifa/players_sofifa_%s.json", run_date)
    except Exception as e:
        logger.warning("MinIO upload skipped: %s", e)

    covered = sum(1 for r in results if "sofifa_composure" in r)
    mental = sum(1 for r in results if "sofifa_mental_score" in r)
    n = len(targets)

    logger.info("=" * 60)
    logger.info("COVERAGE REPORT:")
    logger.info("  sofifa_composure     %s %d/%d",
                 "█" * int(covered / n * 15) + "░" * (15 - int(covered / n * 15)), covered, n)
    logger.info("  sofifa_mental_score  %s %d/%d",
                 "█" * int(mental / n * 15) + "░" * (15 - int(mental / n * 15)), mental, n)
    logger.info("  (remaining %d players have no sofifa_id — MentalScore stays imputed)",
                 49 - n)
    logger.info("=" * 60)
    logger.info("Next: python run_silver.py → python run_gold.py")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()