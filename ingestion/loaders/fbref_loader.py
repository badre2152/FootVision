# ingestion/loaders/fbref_loader.py
# Source 7: FBref Club Stats (ADR-007)
# Scrapes per-90 club stats for all 49 players from FBref.
#
# v17 fixes applied:
#   C-01: run_date defaults to datetime.now() inside loader
#   C-02: validate_seed_columns() guard
#   C-07: exact-first column resolution in _col()
#   C-08: assert_min_coverage() at min_pct=0.80
#
# v18 fix (superseded):
#   CF-01: replaced plain requests.get() with Patchright.
#          Patchright's bundled Chromium (channel="chrome" wasn't available
#          locally) was still detected and blocked by FBref's Cloudflare WAF
#          on every player, even after retries — 0/49 coverage in practice.
#
# v19 fix:
#   CF-02: replaced Patchright with undetected_chromedriver (Selenium-based),
#          driving the user's real, locally-installed Google Chrome rather
#          than a bundled/headless Chromium. This carries a much more
#          convincing browser fingerprint and reliably passes FBref's
#          Cloudflare challenge.
#   CF-03: target the standard-stats table by its FBref-stable element id
#          ("stats_standard") instead of assuming it's tables[0] from
#          pd.read_html — the page layout includes other tables (e.g.
#          shooting, summary boxes) above it for some player types
#          (goalkeepers especially), so tables[0] was not reliable.
#   NOTE: undetected_chromedriver requires a real Chrome install (not
#         headless-only Chromium) on the host machine. Install Chrome
#         normally from google.com/chrome before running this loader.
#
# v20 fix:
#   CF-04: "stats_standard" alone is not a stable id across player pages —
#          FBref renders the per-90 table id with a competition-scope
#          suffix that depends on which tab is the default for that
#          player (_dom_lg, _all_comps, _nat_tm, ...). Now tries a list of
#          candidate ids in order instead of a single hardcoded one.
#   CF-05: goalkeepers (P001, P002, P003, P005, P006 in the 49-player
#          pool) do NOT have a "stats_standard*" table at all — FBref
#          renders a completely different table for keepers
#          ("stats_keeper*": Saves, Save%, Clean Sheets, etc. — Gls/Ast/xG
#          are meaningless for a GK and don't appear). This caused 0/6
#          coverage for every keeper in the pool even with the CF-03 id
#          fix, because the loader was only ever looking for an
#          outfield-shaped table.
#          Fix: seed_df now carries a 'position' column (already present
#          in players_49.csv). When position == "GK", the loader targets
#          stats_keeper* ids first and extracts keeper-shaped fields
#          (saves, save%, clean sheets, goals against, etc.) instead of
#          the outfield per-90 fields. Falls back to the other table type
#          if the expected one isn't found, in case position data is
#          stale/wrong, with a warning so it's visible in the logs.
#
# v21 fix:
#   CF-06: uc.Chrome() with no args lets undetected_chromedriver fetch
#          whatever it thinks is the "latest" chromedriver release. In
#          practice this can be AHEAD of the Chrome actually installed
#          locally (e.g. chromedriver targeting 150 while local stable
#          Chrome is still 149.0.7827.156), which throws
#          SessionNotCreatedException immediately on
#          driver = uc.Chrome() — 0/49 before a single request is made.
#          This is an environment/dependency issue, unrelated to the
#          Cloudflare/table-id problems fixed above.
#          Fix: detect the installed Chrome's major version locally
#          (cross-platform: macOS app bundle, Linux binary, Windows
#          registry/path) and pass it to uc.Chrome(version_main=...)
#          explicitly, so it downloads a matching chromedriver instead of
#          guessing "latest". If detection fails for any reason, falls
#          back to the old no-args uc.Chrome() behavior so this doesn't
#          introduce a new hard failure mode.
#          NOTE: if this keeps happening, it usually means Chrome's
#          stable channel is lagging chromedriver's release — either pin
#          version_main manually to your installed Chrome's major version,
#          or update Chrome (chrome://settings/help) and re-run.
#
# v22 fix:
#   CF-07: all 5 goalkeepers in the 49-player pool came back "no data"
#          even after CF-05's keeper-table support, AND even the
#          fallback to stats_standard* failed for them. Root cause:
#          FBref wraps several secondary tables (commonly the
#          non-default-tab stats_standard_* and stats_keeper_* variants)
#          inside literal HTML comments (<!-- <table id="..."> ... -->),
#          only unwrapped client-side by JS after the page settles.
#          pd.read_html() parses raw markup and never sees content inside
#          HTML comments, so attrs={"id": tid} silently returns nothing
#          even when the id is technically present in page_source.
#          Fix: strip HTML comment wrappers (<!-- ... -->) from the page
#          source before handing it to pd.read_html(), so both
#          comment-wrapped and live tables are visible. Also added
#          diagnostics: on a lookup miss, log every stats_* id actually
#          present in the fetched HTML, so future failures show what WAS
#          on the page instead of just confirming what wasn't found.
#          Also flagged in the v18 run: at least one fbref_id classified
#          as "GK" in players_49.csv (e.g. fbref_id=83600050, matching
#          Zakaria El Ouahdi on FBref — a DF-MF, not a goalkeeper) may be
#          a stale/incorrect position tag in the seed CSV. Worth
#          auditing players_49.csv's position column against FBref
#          directly for the 5 IDs flagged "GK" if CF-07 alone doesn't
#          fully resolve coverage for them.

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

# CF-04: FBref does not use one fixed id for the standard-stats table.
# The default tab (and therefore the table id) depends on which
# competition scope FBref considers "primary" for that player's most
# recent activity. Try these in order, stop at the first match.
_STANDARD_STATS_TABLE_IDS = [
    "stats_standard_dom_lg",
    "stats_standard",
    "stats_standard_all_comps",
    "stats_standard_nat_tm",
]

# CF-05: goalkeepers get an entirely different table — same id-suffix
# pattern, different base name.
_KEEPER_STATS_TABLE_IDS = [
    "stats_keeper_dom_lg",
    "stats_keeper",
    "stats_keeper_all_comps",
    "stats_keeper_nat_tm",
]

_REQUEST_DELAY = 3.5   # FBref rate limit — stay well above 3 s
_MAX_RETRIES   = 3
_BACKOFF_BASE  = 8

_VERSION_RE = re.compile(r"(\d+)\.\d+\.\d+\.\d+")


def _detect_local_chrome_major_version() -> int | None:
    """
    CF-06: best-effort detection of the major version of Chrome actually
    installed on this machine, so uc.Chrome() can be told to download a
    matching chromedriver instead of grabbing whatever it thinks is
    "latest" (which can be ahead of what's installed/released).

    Returns the major version as an int, or None if detection fails for
    any reason (missing binary, unexpected output, unsupported OS, ...).
    Detection failing is not fatal — callers should fall back to letting
    undetected_chromedriver auto-detect.
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
        # No single reliable CLI path; try the common install locations.
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
    stats page, using a Selenium (undetected_chromedriver) driver.

    `table_ids` is tried in order; the first id found on the page wins.
    Returns (DataFrame, table_id) on success, or (None, None) on
    failure / no data / CF block / no matching table id.
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

        break  # good page

    if not html:
        return None, None

    # CF-07: FBref frequently ships secondary tables wrapped inside HTML
    # comments (<!-- <table id="..."> ... </table> -->), only unwrapped
    # client-side by JS after the page settles. pd.read_html() never sees
    # commented-out markup, so attrs={"id": tid} silently finds nothing
    # even though the table id is technically "on the page". Unwrap any
    # commented table blocks before searching, so both live and
    # comment-wrapped tables are visible to read_html().
    if "<!--" in html and "<table" in html:
        html = re.sub(r"<!--(.*?)-->", r"\1", html, flags=re.DOTALL)

    # CF-07 diagnostics: log which stats_* table ids actually exist in the
    # fetched HTML, so a "No stats table" failure tells you what WAS
    # there instead of just what wasn't found.
    found_ids = sorted(set(re.findall(r'id="(stats_[a-zA-Z0-9_]+)"', html)))
    if found_ids:
        logger.debug("fbref_id page contains stats table ids: %s", found_ids)

    # CF-04: try each candidate id in order, stop at first match.
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
        # Exact match first (C-07 fix)
        for c in candidates:
            if c in cols:
                return c
        # Substring fallback
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

    # Derive per-90s
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
    """
    CF-05: goalkeeper stats from the stats_keeper* table.
    Gls/Ast/xG don't apply here — pull saves, save%, clean sheets,
    goals-against and penalty-stopping numbers instead.
    """
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

    # Derive per-90s where it makes sense
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
    to look for first — CF-05 fix for goalkeepers, who have no
    stats_standard table at all.
    """
    is_gk = (position or "").strip().upper() == "GK"

    primary_ids  = _KEEPER_STATS_TABLE_IDS if is_gk else _STANDARD_STATS_TABLE_IDS
    fallback_ids = _STANDARD_STATS_TABLE_IDS if is_gk else _KEEPER_STATS_TABLE_IDS

    df, table_id = _fetch_club_table(fbref_id, driver, primary_ids)
    used_keeper_table = is_gk

    if df is None:
        # Fall back to the other table shape in case position data in the
        # seed CSV is stale/wrong for this player — better to grab
        # *something* than nothing, but log it so it's visible.
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
    CF-06: instantiate undetected_chromedriver, pinning version_main to
    the locally detected Chrome major version when possible, so it
    downloads a matching chromedriver instead of grabbing "latest"
    (which can be ahead of what's actually installed/released, causing
    SessionNotCreatedException before any request is even made).
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

    # CF-07: auto-detect failed (common on Windows when chrome.exe does not
    # support --version via subprocess). Rather than letting uc grab "latest"
    # chromedriver (which was 150 while local Chrome is still 149, causing
    # SessionNotCreatedException), fall back to a hard-pinned version.
    # UPDATE THIS NUMBER when you update Chrome (chrome://settings/help).
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
    undetected_chromedriver (real, locally-installed Chrome).
    One driver instance is shared across all players to amortise the CF
    challenge cost.

    seed_df must contain: 'player_id', 'fbref_id'.
    seed_df should also contain 'position' (e.g. "GK"/"DEF"/"MID"/"FWD")
    — CF-05: without it, goalkeepers will be queried with the outfield
    table ids first and may come back empty unnecessarily on every
    attempt before falling back.
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
    driver = _make_uc_driver()  # CF-06: version-pinned driver init

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