import time
import random
import logging
import platform
import re
import shutil
import subprocess
from io import StringIO
from datetime import datetime

import pandas as pd

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

_FBREF_CLUB_URL = (
    "https://fbref.com/en/players/{fbref_id}/stats/"
)

_STANDARD_STATS_TABLE_IDS = [
    "stats_standard_dom_lg",
    "stats_standard",
    "stats_standard_all_comps",
    "stats_standard_nat_tm",
]

_KEEPER_STATS_TABLE_IDS = [
    "stats_keeper_dom_lg",
    "stats_keeper",
    "stats_keeper_all_comps",
    "stats_keeper_nat_tm",
]

_REQUEST_DELAY = 3.5
_MAX_RETRIES   = 3
_BACKOFF_BASE  = 8

_VERSION_RE = re.compile(r"(\d+)\.\d+\.\d+\.\d+")


def _detect_local_chrome_major_version() -> int | None:
    """
    Best-effort detection of the installed Chrome's major version, so
    uc.Chrome() can be told which chromedriver to download instead of
    guessing "latest". Returns None if detection fails; not fatal.
    """
    system = platform.system()
    candidates: list[list[str]] = []

    if system == "Darwin":
        candidates = [
            ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"],
        ]
    elif system == "Linux":
        for binary in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
            path = shutil.which(binary)
            if path:
                candidates.append([path, "--version"])
    elif system == "Windows":
        for path in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ):
            candidates.append([path, "--version"])

    for cmd in candidates:
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5
            ).stdout
        except Exception:
            continue
        match = _VERSION_RE.search(out)
        if match:
            return int(match.group(1))

    return None


def _is_cloudflare(title: str, html: str) -> bool:
    if title:
        t = title.strip().lower()
        if "just a moment" in t or "attention required" in t:
            return True
    if html and "challenges.cloudflare.com" in html and "<table" not in html.lower():
        return True
    return False


def _fetch_club_table(fbref_id: str, driver, table_ids: list[str]) -> tuple[pd.DataFrame | None, str | None]:
    """
    Fetches the per-90 stats table for one player from their FBref club
    stats page using a Selenium (undetected_chromedriver) driver.

    `table_ids` is tried in order; the first id found on the page wins.
    Returns (DataFrame, table_id) on success, or (None, None) on failure.
    """
    url = _FBREF_CLUB_URL.format(fbref_id=fbref_id)
    time.sleep(_REQUEST_DELAY + random.uniform(0, 1.0))

    html = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            driver.get(url)
            time.sleep(random.uniform(1.5, 2.5))
            title = driver.title
            html  = driver.page_source
        except Exception as e:
            logger.warning("FBref club page error %s (attempt %d): %s",
                           fbref_id, attempt, e)
            if attempt == _MAX_RETRIES:
                return None, None
            time.sleep(_BACKOFF_BASE * attempt)
            continue

        if driver.current_url and "404" in driver.current_url:
            logger.warning("FBref 404 for fbref_id=%s", fbref_id)
            return None, None

        if _is_cloudflare(title, html):
            if attempt == _MAX_RETRIES:
                logger.warning("FBref club: CF block for %s after %d attempts",
                               fbref_id, attempt)
                return None, None
            backoff = _BACKOFF_BASE * (3 ** (attempt - 1)) + random.uniform(0, 3.0)
            logger.warning("FBref club CF challenge %s (attempt %d) — backoff %.1fs",
                           fbref_id, attempt, backoff)
            time.sleep(backoff)
            continue

        break

    if not html:
        return None, None

    if "<!--" in html and "<table" in html:
        html = re.sub(r"<!--(.*?)-->", r"\1", html, flags=re.DOTALL)

    found_ids = sorted(set(re.findall(r'id="(stats_[a-zA-Z0-9_]+)"', html)))
    if found_ids:
        logger.debug("fbref_id page contains stats table ids: %s", found_ids)

    for tid in table_ids:
        try:
            tables = pd.read_html(StringIO(html), attrs={"id": tid})
        except ValueError:
            continue

        if not tables:
            continue

        df = tables[0].copy()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [
                "_".join(str(c).strip() for c in col).strip("_")
                for col in df.columns
            ]

        df = df.dropna(how="all")
        if not df.empty:
            return df, tid

    logger.info(
        "No stats table (tried %s) for fbref_id=%s — page had ids: %s",
        table_ids, fbref_id, found_ids or "[none found in HTML]",
    )
    return None, None


def _filter_latest_season(df: pd.DataFrame, col_resolver) -> pd.DataFrame:
    """Filter to the most recent season row (shared by standard & keeper tables)."""
    season_col = col_resolver(["Season", "season"])
    if season_col and season_col in df.columns:
        df = df[df[season_col].astype(str).str.match(r"\d{4}-\d{4}", na=False)]
    if df.empty:
        return df
    return df.tail(1)


def _make_col_resolver(df: pd.DataFrame):
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
    return _col


def _extract_outfield_fields(df: pd.DataFrame) -> dict:
    """Per-90 outfield stats: goals, assists, xG, xAG, npxG."""
    _col = _make_col_resolver(df)
    df = _filter_latest_season(df, _col)
    if df.empty:
        return {}

    result = {}

    mp_col   = _col(["MP", "Matches"])
    min_col  = _col(["Min", "Minutes"])
    gls_col  = _col(["Gls", "Goals"])
    ast_col  = _col(["Ast", "Assists"])
    xg_col   = _col(["xG"])
    xa_col   = _col(["xAG", "xA"])
    npxg_col = _col(["npxG"])

    if mp_col:
        result["fbref_matches"]      = int(pd.to_numeric(df[mp_col],  errors="coerce").fillna(0).iloc[0])
    if min_col:
        result["fbref_minutes"]      = int(pd.to_numeric(df[min_col], errors="coerce").fillna(0).iloc[0])
    if gls_col:
        result["fbref_goals"]        = int(pd.to_numeric(df[gls_col], errors="coerce").fillna(0).iloc[0])
    if ast_col:
        result["fbref_assists"]      = int(pd.to_numeric(df[ast_col], errors="coerce").fillna(0).iloc[0])
    if xg_col:
        result["fbref_xg"]           = round(float(pd.to_numeric(df[xg_col],   errors="coerce").fillna(0).iloc[0]), 4)
    if xa_col:
        result["fbref_xag"]          = round(float(pd.to_numeric(df[xa_col],   errors="coerce").fillna(0).iloc[0]), 4)
    if npxg_col:
        result["fbref_npxg"]         = round(float(pd.to_numeric(df[npxg_col], errors="coerce").fillna(0).iloc[0]), 4)

    minutes = result.get("fbref_minutes", 0)
    nineties = max(minutes / 90.0, 0.01)
    if "fbref_goals" in result:
        result["fbref_goals_per90"]  = round(result["fbref_goals"]   / nineties, 4)
    if "fbref_assists" in result:
        result["fbref_ast_per90"]    = round(result["fbref_assists"] / nineties, 4)
    if "fbref_xg" in result:
        result["fbref_xg_per90"]     = round(result["fbref_xg"]     / nineties, 4)
    if "fbref_npxg" in result:
        result["fbref_npxg_per90"]   = round(result["fbref_npxg"]   / nineties, 4)

    return result


def _extract_keeper_fields(df: pd.DataFrame) -> dict:
    """Goalkeeper stats from the stats_keeper* table: saves, save%, clean sheets, etc."""
    _col = _make_col_resolver(df)
    df = _filter_latest_season(df, _col)
    if df.empty:
        return {}

    result = {}

    mp_col      = _col(["MP", "Matches"])
    min_col     = _col(["Min", "Minutes"])
    ga_col      = _col(["GA", "Goals Against"])
    ga90_col    = _col(["GA90"])
    sota_col    = _col(["SoTA", "Shots on Target Against"])
    saves_col   = _col(["Saves"])
    savepct_col = _col(["Save%"])
    wins_col    = _col(["W", "Wins"])
    draws_col   = _col(["D", "Draws", "Ties"])
    losses_col  = _col(["L", "Losses"])
    cs_col      = _col(["CS", "Clean Sheets"])
    cspct_col   = _col(["CS%"])
    pka_col     = _col(["PKatt"])
    pkallow_col = _col(["PKA", "PKAllowed", "PKconceded"])
    pksaved_col = _col(["PKsv", "PKSaved"])
    pksavepct_col = _col(["PKsave%", "Save%_pk"])

    if mp_col:
        result["fbref_gk_matches"]          = int(pd.to_numeric(df[mp_col],    errors="coerce").fillna(0).iloc[0])
    if min_col:
        result["fbref_gk_minutes"]          = int(pd.to_numeric(df[min_col],   errors="coerce").fillna(0).iloc[0])
    if ga_col:
        result["fbref_gk_goals_against"]    = int(pd.to_numeric(df[ga_col],    errors="coerce").fillna(0).iloc[0])
    if ga90_col:
        result["fbref_gk_ga90"]             = round(float(pd.to_numeric(df[ga90_col], errors="coerce").fillna(0).iloc[0]), 4)
    if sota_col:
        result["fbref_gk_shots_on_target_against"] = int(pd.to_numeric(df[sota_col], errors="coerce").fillna(0).iloc[0])
    if saves_col:
        result["fbref_gk_saves"]            = int(pd.to_numeric(df[saves_col], errors="coerce").fillna(0).iloc[0])
    if savepct_col:
        result["fbref_gk_save_pct"]         = round(float(pd.to_numeric(df[savepct_col], errors="coerce").fillna(0).iloc[0]), 2)
    if wins_col:
        result["fbref_gk_wins"]             = int(pd.to_numeric(df[wins_col],   errors="coerce").fillna(0).iloc[0])
    if draws_col:
        result["fbref_gk_draws"]            = int(pd.to_numeric(df[draws_col],  errors="coerce").fillna(0).iloc[0])
    if losses_col:
        result["fbref_gk_losses"]           = int(pd.to_numeric(df[losses_col], errors="coerce").fillna(0).iloc[0])
    if cs_col:
        result["fbref_gk_clean_sheets"]     = int(pd.to_numeric(df[cs_col],    errors="coerce").fillna(0).iloc[0])
    if cspct_col:
        result["fbref_gk_clean_sheet_pct"]  = round(float(pd.to_numeric(df[cspct_col], errors="coerce").fillna(0).iloc[0]), 2)
    if pka_col:
        result["fbref_gk_pens_faced"]       = int(pd.to_numeric(df[pka_col],    errors="coerce").fillna(0).iloc[0])
    if pkallow_col:
        result["fbref_gk_pens_allowed"]     = int(pd.to_numeric(df[pkallow_col], errors="coerce").fillna(0).iloc[0])
    if pksaved_col:
        result["fbref_gk_pens_saved"]       = int(pd.to_numeric(df[pksaved_col], errors="coerce").fillna(0).iloc[0])
    if pksavepct_col:
        result["fbref_gk_pens_save_pct"]    = round(float(pd.to_numeric(df[pksavepct_col], errors="coerce").fillna(0).iloc[0]), 2)

    minutes = result.get("fbref_gk_minutes", 0)
    nineties = max(minutes / 90.0, 0.01)
    if "fbref_gk_saves" in result:
        result["fbref_gk_saves_per90"]      = round(result["fbref_gk_saves"] / nineties, 4)
    if "fbref_gk_goals_against" in result:
        result["fbref_gk_ga_per90"]         = round(result["fbref_gk_goals_against"] / nineties, 4)

    return result


def fetch_fbref_club_stats(fbref_id: str, driver, position: str = "") -> dict:
    """
    Extracts per-90 club stats for one player.
    `driver` is a Selenium (undetected_chromedriver) driver reused across calls.
    `position` (e.g. "GK", "DEF", "MID", "FWD") selects which table shape
    to look for first.
    """
    is_gk = (position or "").strip().upper() == "GK"

    primary_ids  = _KEEPER_STATS_TABLE_IDS if is_gk else _STANDARD_STATS_TABLE_IDS
    fallback_ids = _STANDARD_STATS_TABLE_IDS if is_gk else _KEEPER_STATS_TABLE_IDS

    df, table_id = _fetch_club_table(fbref_id, driver, primary_ids)
    used_keeper_table = is_gk

    if df is None:
        df, table_id = _fetch_club_table(fbref_id, driver, fallback_ids)
        used_keeper_table = not is_gk
        if df is not None:
            logger.warning(
                "fbref_id=%s: position='%s' but matched table_id=%s instead — "
                "check players_49.csv position field",
                fbref_id, position or "UNK", table_id,
            )

    if df is None:
        logger.info("%s fbref_id=%s -> no data", "GK" if is_gk else position or "UNK", fbref_id)
        return {}

    if used_keeper_table:
        return _extract_keeper_fields(df)
    return _extract_outfield_fields(df)


def _make_uc_driver():
    """
    Instantiates undetected_chromedriver, pinning version_main to the
    locally detected Chrome major version when possible.
    """
    import undetected_chromedriver as uc

    major = _detect_local_chrome_major_version()
    if major is not None:
        logger.info("Detected local Chrome major version %d — pinning chromedriver to match", major)
        try:
            return uc.Chrome(version_main=major)
        except Exception as e:
            logger.warning(
                "uc.Chrome(version_main=%d) failed (%s) — falling back to "
                "auto-detected driver version. If this keeps happening, "
                "update Chrome or pin version_main manually.",
                major, e,
            )

    _FALLBACK_CHROME_VERSION = 149
    logger.warning(
        "Could not detect local Chrome version — falling back to hard-pinned "
        "version_main=%d. If you see a version mismatch error, update Chrome "
        "(chrome://settings/help) and set _FALLBACK_CHROME_VERSION in "
        "fbref_loader.py to match your new major version.",
        _FALLBACK_CHROME_VERSION,
    )
    return uc.Chrome(version_main=_FALLBACK_CHROME_VERSION)


def load_fbref_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Fetches FBref club stats for all players in seed_df using
    undetected_chromedriver (real, locally-installed Chrome). One driver
    instance is shared across all players to amortise the CF challenge cost.

    seed_df must contain: 'player_id', 'fbref_id'.
    seed_df should also contain 'position' (e.g. "GK"/"DEF"/"MID"/"FWD").
    Returns status string: "OK (N/49 players with data)" or "FAILED".
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "fbref_id"], source="fbref")

    has_position = "position" in seed_df.columns
    if not has_position:
        logger.warning(
            "seed_df has no 'position' column — goalkeepers will be probed "
            "with outfield table ids first on every player (slower, and "
            "relies on the stats_standard*/stats_keeper* fallback)."
        )

    try:
        import undetected_chromedriver as uc  # noqa: F401 (import check only)
    except ImportError:
        logger.error(
            "undetected_chromedriver not installed. Run: "
            "pip install undetected-chromedriver"
        )
        return "FAILED"

    records = []
    driver = _make_uc_driver()

    try:
        for _, row in seed_df.iterrows():
            fid = row.get("fbref_id")
            if pd.isna(fid) or not fid or str(fid).strip() == "":
                records.append({"player_id": row["player_id"]})
                continue
            position = row.get("position", "") if has_position else ""
            stats = fetch_fbref_club_stats(str(fid), driver, position=position)
            records.append({"player_id": row["player_id"], **stats})
    finally:
        driver.quit()

    assert_min_coverage(records, min_pct=0.80, source="fbref")

    object_name = f"fbref/players_fbref_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"