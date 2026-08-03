"""
run_sofifa.py — SoFIFA Bronze Loader (Playwright + Stealth edition)
====================================================================
يجلب FIFA player attributes (composure, reactions, aggression/gk_reflexes)
لكل لاعب مغربي في الـ 49 candidates.

sofifa.com محمي بـ Cloudflare Managed Challenge. الحل: Playwright
بمتصفح ظاهر (headless=False) + playwright-stealth، مع استنى ذكي
(polling على الـ <title>) حتى يتحل الـ challenge وحدو.

التشغيل:
    pip install playwright playwright-stealth beautifulsoup4
    playwright install chromium
    python run_sofifa.py
    python run_sofifa.py --dry-run        # عرض فقط بدون جلب
    python run_sofifa.py --limit 10       # أول 10 لاعبين
    python run_sofifa.py --headless       # (غير موصى بيه — Cloudflare كيكتشفو غالبا)

بعد التشغيل:
    python run_silver.py
    python run_gold.py
"""

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

# ── SOFIFA_IDS مؤكدة ─────────────────────────────────────────────────────────
SOFIFA_IDS: dict[str, int] = {
    # GKs
    "P003": 209981,   # Yassine Bounou        — Al Hilal
    "P004": 223573,   # Munir El Kajoui       — RS Berkane
    "P005": 241777,   # Ahmed Reda Tagnaouti  — FAR Rabat

    # DEFs
    "P007": 235212,   # Achraf Hakimi         — PSG
    "P008": 236401,   # Noussair Mazraoui     — Man Utd
    "P010": 275353,   # Zakaria El Ouahdi     — KRC Genk
    "P012": 82240,    # Youssef Belammari     — Al Ahly
    "P014": 235890,   # Marwane Saadane       — Al-Fateh
    "P016": 258490,   # Chadi Riad            — Club Brugge
    "P017": 231633,   # Issa Diop             — Fulham
    "P018": 268728,   # Anass Salah-Eddine    — Ajax
    "P021": 264657,   # Redouane Halhal       — Angers

    # MIDs
    "P023": 224158,   # Sofyan Amrabat        — Real Betis
    "P024": 255125,   # Azzedine Ounahi       — Girona
    "P025": 257504,   # Bilal El Khannouss    — VfB Stuttgart
    "P026": 259480,   # Ismael Saibari        — PSV
    "P027": 262745,   # Neil El Aynaoui       — AS Roma
    "P032": 278901,   # Ayyoub Bouaddi        — LOSC Lille
    "P033": 279702,   # Samir El Mourabet     — RC Strasbourg

    # FWDs
    "P036": 235410,   # Youssef En-Nesyri     — Al Ittihad
    "P037": 264348,   # Soufiane Rahimi       — Al Ain
    "P038": 243586,   # Ayoub El Kaabi        — Olympiacos
    "P039": 231410,   # Brahim Diaz           — Real Madrid
    "P041": 78474,    # Ayoube Amaimouni      — Frankfurt
    "P042": 275048,   # Chemsdine Talbi       — Fenerbahce
    "P045": 70017,    # Yassine Gessime       — Rennes
    "P046": 273666,   # Amine Sbai            — Angers
}

SOFIFA_PLAYER_URL = "https://sofifa.com/player/{pid}/"

# label نص كيبان فالصفحة (lowercase) → اسم العمود عندنا
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
    """يستنى بشكل ذكي حتى يتبدل الـ <title> من صفحة Cloudflare Challenge
    للصفحة الحقيقية ديال اللاعب. كيرجع True إلا عدى، False إلا فشل."""
    for _ in range(max_seconds):
        time.sleep(1)
        title = (page.title() or "").lower()
        if not any(marker in title for marker in CHALLENGE_TITLE_MARKERS):
            time.sleep(1.5)  # وقت إضافي باش يخلص الـ redirect/render كامل
            return True
    return False


def safe_get_content(page: Page, retries: int = 5, delay: float = 1.0) -> str:
    """page.content() ممكن يفشل إلا كانت الصفحة فوسط navigation.
    نعاودو المحاولة بدل ما نطيحو بـ exception."""
    last_err = None
    for _ in range(retries):
        try:
            return page.content()
        except Exception as e:
            last_err = e
            time.sleep(delay)
    logger.warning("  ✗ فشل قراءة المحتوى بعد %d محاولات: %s", retries, last_err)
    return ""


def fetch_attrs(page: Page, sofifa_id: int) -> dict:
    """يجلب attributes من صفحة لاعب SoFIFA عبر Playwright (يعدي Cloudflare)."""
    url = SOFIFA_PLAYER_URL.format(pid=sofifa_id)
    attrs: dict = {"sofifa_id": sofifa_id}

    try:
        page.goto(url, timeout=30000)
    except Exception as e:
        logger.warning("  ✗ goto failed id=%d: %s", sofifa_id, e)
        return {}

    cleared = wait_for_challenge_to_clear(page)
    if not cleared:
        logger.warning("  ✗ Cloudflare challenge ماتعداش id=%d", sofifa_id)
        return {}

    html = safe_get_content(page)
    if not html or len(html) < 50000:
        logger.warning("  ✗ محتوى ناقص/فارغ id=%d (len=%d)", sofifa_id, len(html or ""))
        return {}

    soup = BeautifulSoup(html, "html.parser")

    # ── Overall / Potential ──────────────────────────────────────
    for div in soup.select("div.grid div.col"):
        em = div.find("em")
        sub = div.find("div", class_="sub")
        if em and sub and em.get("title"):
            label = sub.get_text(strip=True).lower()
            if label == "overall rating":
                attrs["sofifa_overall"] = int(em["title"])
            elif label == "potential":
                attrs["sofifa_potential"] = int(em["title"])

    # ── Attributes (composure, reactions, aggression, GK stats...) ─
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
    """composure×0.50 + reactions×0.35 + aggression/gk_reflexes×0.15 × discount 0.70"""
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
    """
    Returns the exact object this loader uploads to MinIO under the
    `sofifa/` Bronze prefix.

    REGRESSION GUARD (see 03_regression_protection.md / CHANGELOG.md):
    This MUST be a flat list of per-player dicts — the same convention
    every other Bronze loader uses (wikidata_loader, fbref_loader,
    fotmob via run_fotmob.py, etc: `upload_json(BUCKET, name, records)`).

    This upload was previously wrapped as {"run_date": ..., "players":
    results} for local-file readability, then that SAME wrapped shape
    was (incorrectly) also used for the MinIO upload. The Silver join
    (ingestion/silver_pipeline.py::join_all_bronze_sources -> _read())
    does `pd.DataFrame(read_latest_json(...))`: given a flat list this
    produces one row per player with a `player_id` column; given the
    wrapped dict it instead produces a DataFrame from the dict's top-level
    keys ("run_date", "players") — no `player_id` column exists, so
    `_read()` returns None, the merge is silently skipped, and every
    `sofifa_*` column is dropped (0/49 coverage) with NO exception raised
    anywhere in the pipeline. Confirmed as the direct cause of that
    exact zero-coverage symptom.

    Kept as its own function (rather than inlined at the call site) so
    tests/test_regression_protection.py::TestSofifaUploadFormat can
    assert on this shape directly without driving a real Playwright
    browser through Cloudflare.
    """
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=3.0,
                         help="وقت الاستنى بين لاعب ولاعب (ثواني)")
    parser.add_argument("--seed", default="seeds/players_49.csv")
    parser.add_argument("--headless", action="store_true",
                         help="خدم بلا نافذة ظاهرة (Cloudflare كيكتشفو غالبا، ماشي موصى بيه)")
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

            # normalize
            for col in ("composure", "reactions", "aggression", "gk_reflexes"):
                raw = attrs.get(f"sofifa_{col}")
                if raw:
                    attrs[f"sofifa_{col}_norm"] = norm_attr(raw)

            # mental
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

    # حفظ
    out_dir = Path("bronze/sofifa")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"players_sofifa_{run_date}.json"
    payload = {"run_date": run_date, "players": results}
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info("Saved locally → %s", out_file)

    # MinIO — MUST be the flat records list (build_minio_upload_payload),
    # NOT the local-file `payload` wrapper above. See that function's
    # docstring: uploading `payload` here silently zeroes out every
    # sofifa_* column downstream in Silver, with no exception raised.
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

    # Coverage
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