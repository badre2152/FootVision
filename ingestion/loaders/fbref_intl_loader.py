import re
import time
import random
import logging
from io import StringIO
from datetime import datetime

import pandas as pd

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage
from ingestion.loaders.fbref_loader import _make_uc_driver

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

_FBREF_INTL_URL = "https://fbref.com/en/players/{fbref_id}/matchlogs/nat_tm/"

_REQUEST_DELAY = 3.5
_MAX_RETRIES   = 5
_BACKOFF_BASE  = 10

_DRIVER_ROTATE_EVERY = 6

_INTL_TABLE_ID = "matchlogs_all"


def _is_cloudflare(title: str, html: str) -> bool:
    if title:
        t = title.strip().lower()
        if "just a moment" in t or "attention required" in t:
            return True
    if html and "challenges.cloudflare.com" in html and "<table" not in html.lower():
        return True
    return False


def _is_404(title: str, html: str) -> bool:
    if title and "page not found" in title.strip().lower():
        return True
    if html and ("Error 404" in html or "we could not find" in html.lower()):
        return True
    return False


def _is_server_error(html: str) -> bool:
    if not html:
        return False
    h = html.lower()
    return "internal server error" in h or "500 internal server error" in h


def _fetch_intl_table(fbref_id: str, driver) -> pd.DataFrame | None:
    """
    Fetches the full national-team matchlog table for one player using an
    undetected_chromedriver driver instance (reused across calls).
    Returns a cleaned DataFrame or None on failure / no data / CF block / 404.
    """
    url = _FBREF_INTL_URL.format(fbref_id=fbref_id)
    time.sleep(_REQUEST_DELAY + random.uniform(0, 1.0))

    html = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            driver.get(url)
            time.sleep(random.uniform(1.5, 2.5))
            title = driver.title
            html  = driver.page_source
        except Exception as e:
            logger.warning(
                "FBref intl page error %s (attempt %d): %s",
                fbref_id, attempt, e,
            )
            if attempt == _MAX_RETRIES:
                return None
            time.sleep(_BACKOFF_BASE * attempt)
            continue

        if _is_404(title, html):
            logger.info(
                "FBref intl: no national-team matchlog page for fbref_id=%s "
                "(player likely never capped, or fbref_id has no nat_tm data)",
                fbref_id,
            )
            return None

        if _is_server_error(html):
            if attempt == _MAX_RETRIES:
                logger.warning(
                    "FBref intl: persistent 500 Internal Server Error for "
                    "fbref_id=%s after %d attempts — this is FBref's own "
                    "backend failing, not a client-side bug. Skipping this "
                    "player; retry later or report to FBref.",
                    fbref_id, attempt,
                )
                # Signal caller to use the manual_intl_caps fallback —
                # the main loop checks the seed for this on empty stats.
                return None
            logger.warning(
                "FBref intl 500 Internal Server Error for fbref_id=%s "
                "(attempt %d/%d) — retrying, this is often transient",
                fbref_id, attempt, _MAX_RETRIES,
            )
            time.sleep(_BACKOFF_BASE * attempt)
            continue

        if _is_cloudflare(title, html):
            if attempt == _MAX_RETRIES:
                logger.warning(
                    "FBref intl: CF block for %s after %d attempts",
                    fbref_id, attempt,
                )
                return None
            backoff = _BACKOFF_BASE * (3 ** (attempt - 1)) + random.uniform(0, 3.0)
            logger.warning(
                "FBref intl CF challenge %s (attempt %d) — backoff %.1fs",
                fbref_id, attempt, backoff,
            )
            time.sleep(backoff)
            continue

        break

    if not html:
        return None

    if "<!--" in html and "<table" in html:
        html = re.sub(r"<!--(.*?)-->", r"\1", html, flags=re.DOTALL)

    if _INTL_TABLE_ID not in html:
        found_ids = re.findall(r'id="([a-zA-Z0-9_]+)"', html)
        candidate_ids = sorted(set(
            i for i in found_ids
            if "match" in i.lower() or "stats" in i.lower() or "nat_tm" in i.lower()
        ))
        logger.info(
            "fbref_id=%s: table id '%s' not found — relevant ids on page: %s",
            fbref_id, _INTL_TABLE_ID, candidate_ids,
        )
        return None

    try:
        tables = pd.read_html(StringIO(html), attrs={"id": _INTL_TABLE_ID})
    except ValueError:
        logger.info("No intl matchlog table for fbref_id=%s", fbref_id)
        return None

    if not tables:
        return None

    df = tables[0].copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            "_".join(str(c).strip() for c in col).strip("_")
            for col in df.columns
        ]

    df = df.dropna(how="all")

    if "Date" in df.columns:
        df = df[df["Date"].astype(str).str.match(r"\d{4}-\d{2}-\d{2}", na=False)]

    return df if not df.empty else None


def fetch_intl_stats(fbref_id: str, driver) -> dict:
    """
    Aggregates a player's full national-team match history into per-90
    rates and absolute totals. `driver` is an undetected_chromedriver
    instance reused across calls.
    """
    df = _fetch_intl_table(fbref_id, driver)
    if df is None:
        return {}

    def _col(candidates: list[str]) -> str | None:
        cols = list(df.columns)
        for c in candidates:
            if c in cols:
                return c
        for c in candidates:
            matches = [col for col in cols if c in col]
            if matches:
                return matches[0]
        return None

    def _to_float(s: pd.Series) -> pd.Series:
        return pd.to_numeric(s, errors="coerce").fillna(0)

    gls_col = _col(["Gls", "Goals"])
    ast_col = _col(["Ast", "Assists"])
    min_col = _col(["Min", "Minutes"])

    mins = _to_float(df[min_col]) if min_col else pd.Series([0] * len(df))
    gls  = _to_float(df[gls_col]) if gls_col else pd.Series([0] * len(df))
    ast  = _to_float(df[ast_col]) if ast_col else pd.Series([0] * len(df))

    total_minutes      = float(mins.sum())
    nineties           = max(total_minutes / 90.0, 0.01)
    actual_appearances = int((mins > 0).sum())
    total_goals        = float(gls.sum())
    total_ast          = float(ast.sum())

    return {
        "intl_caps":          actual_appearances,
        "intl_minutes_total": int(total_minutes),
        "intl_goals_total":   int(total_goals),
        "intl_ast_total":     int(total_ast),
        "intl_goals_per90":   round(total_goals / nineties, 4),
        "intl_ast_per90":     round(total_ast   / nineties, 4),
    }


def load_intl_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Fetches FBref international stats for all players in seed_df using
    undetected_chromedriver (real, locally-installed Chrome).

    seed_df must contain: 'player_id', 'fbref_id'.
    Returns status string: "OK (N/49 players with data)" or "FAILED".
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "fbref_id"], source="fbref_intl")

    try:
        import undetected_chromedriver as uc  # noqa: F401
    except ImportError:
        logger.error(
            "undetected_chromedriver not installed. Run: "
            "pip install undetected-chromedriver"
        )
        return "FAILED"

    records = []
    driver  = _make_uc_driver()
    players_since_rotation = 0

    try:
        for _, row in seed_df.iterrows():
            fid = row.get("fbref_id")
            if pd.isna(fid) or not fid or str(fid).strip() == "":
                records.append({"player_id": row["player_id"]})
                continue

            if players_since_rotation >= _DRIVER_ROTATE_EVERY:
                logger.info(
                    "Rotating Selenium session after %d players "
                    "(reset fingerprint accumulation)",
                    players_since_rotation,
                )
                driver.quit()
                time.sleep(random.uniform(2.0, 4.0))
                driver = _make_uc_driver()
                players_since_rotation = 0

            stats = fetch_intl_stats(str(fid), driver)

            # If FBref failed (stats={}), fall back to manual_intl_caps
            # from the seed if available.
            if not stats:
                manual_caps = row.get("manual_intl_caps")
                if manual_caps is not None and not pd.isna(manual_caps):
                    stats = {
                        "intl_caps":           int(manual_caps),
                        "intl_goals_per90":    float(row.get("manual_intl_goals", 0) or 0),
                        "intl_ast_per90":      float(row.get("manual_intl_ast", 0) or 0),
                        "intl_minutes_total":  None,
                        "_source":             "manual_seed_override",
                    }
                    logger.info(
                        "  → manual fallback: %s caps=%d (seed override)",
                        row.get("player_name", fid), int(manual_caps),
                    )

            records.append({"player_id": row["player_id"], **(stats if isinstance(stats, dict) else {})})
            players_since_rotation += 1
    finally:
        driver.quit()

    assert_min_coverage(records, min_pct=0.60, source="fbref_intl")

    object_name = f"fbref_intl/players_intl_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"