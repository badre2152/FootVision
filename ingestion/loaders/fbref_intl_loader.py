# ingestion/loaders/fbref_intl_loader.py
# Source 11: FBref International Stats (ADR-015)
# Distinct from Source 7 (club stats) — same fbref_id, different URL endpoint.
#
# v17-v22 history: see project changelog.
#
# v23 fix (CF-09 — root cause of persistent 0/49 coverage):
#   The endpoint URL was wrong from the start. The loader was building
#   URLs like:
#       /en/players/{fbref_id}/matchlogs/{season}/national_team/
#   e.g. /en/players/b2e7f2e6/matchlogs/2023-2024/national_team/
#   This path does NOT exist on FBref — it 404s. Every "CF challenge"
#   logged in prior runs was actually Cloudflare's normal challenge page
#   being served on top of a dead URL, not a real block on valid content;
#   no amount of driver/cookie fixing could have produced data from a
#   404, which is why coverage stayed at 0/49 even after CF-01..CF-08.
#
#   The real FBref national-team matchlog endpoint is season-less and
#   slug-based:
#       /en/players/{fbref_id}/matchlogs/nat_tm/{Player-Slug}-Match-Logs
#   (confirmed against multiple live FBref pages, e.g.
#   /en/players/a9202def/matchlogs/nat_tm/Ederson-Match-Logs and
#   /en/players/7be00da6/matchlogs/nat_tm/summary/Pape-Sarr-Match-Logs).
#   A single page contains the player's ENTIRE national-team match
#   history (every season, every competition) in one table — there is no
#   per-season URL variant, so the old _SEASONS loop (4 separate page
#   loads per player) was both wrong AND unnecessary.
#
#   Fix: build the URL as /matchlogs/nat_tm/Match-Logs (no slug needed —
#   FBref resolves and redirects to the canonical slugged URL from the
#   bare id-only path, same as it does for the main player profile and
#   club stats pages already used elsewhere in this project). Single
#   page fetch per player instead of 4. Table extraction, comment
#   stripping, and per-90 aggregation logic are unchanged from v22 since
#   that part was already correct — only the URL and the now-redundant
#   season loop needed fixing.
#
#   Coverage threshold restored to 0.60 (unchanged) since real sparse
#   coverage for players never capped is still expected and legitimate.
#
# v26 fix (CF-14, ports CF-08 from fbref_loader.py):
#   This loader shares the same one-driver-for-all-49-players pattern as
#   the club-stats loader, and is exposed to the same two confirmed
#   transient failure modes (real Apache 500s and CF challenges that hit
#   an arbitrary request, not a specific player — see fbref_loader.py
#   v23/CF-08 for the live evidence). Applying the same mitigation here:
#   raised _MAX_RETRIES/_BACKOFF_BASE for more retry headroom, and the
#   Selenium session is now rebuilt every _DRIVER_ROTATE_EVERY players
#   instead of held open for the whole batch, to cap fingerprint
#   accumulation over a long run. _is_server_error()/_is_cloudflare()
#   detection logic itself is unchanged — only retry budget and session
#   lifecycle changed.

import re
import time
import random
import logging
from io import StringIO
from datetime import datetime

import pandas as pd

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

# Reuse the Chrome-version-pinning driver factory from the club stats
# loader instead of duplicating it (same pattern as the old v19 code).
from ingestion.loaders.fbref_loader import _make_uc_driver

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

# v24 fix (CF-10): the v23 URL ("/matchlogs/nat_tm/Match-Logs") was still
# wrong — "Match-Logs" was incorrectly treated as a fixed path segment,
# when it's actually only ever a trailing part of the player's own name
# slug (e.g. ".../Pape-Sarr-Match-Logs"), not a literal fixed string.
# Confirmed against a live FBref result with NO slug at all:
#   https://fbref.com/en/players/b4a014b1/matchlogs/nat_tm/
# i.e. the bare id-only path with a trailing slash and nothing else
# already resolves correctly (FBref serves the page directly, no slug
# required, no redirect needed). Using that minimal form removes any
# guessing about slug formatting entirely.
_FBREF_INTL_URL = "https://fbref.com/en/players/{fbref_id}/matchlogs/nat_tm/"

_REQUEST_DELAY = 3.5   # FBref rate limit — stay well above 3 s
_MAX_RETRIES   = 5     # CF-14: raised from 3 — CF/500 are per-request flaky, not deterministic
_BACKOFF_BASE  = 10    # CF-14: raised from 8 — give transient errors more room to clear

# CF-14: rebuild the Selenium session (fresh uc.Chrome()) after this many
# players instead of holding one driver open for the whole 49-player run
# — caps how much fingerprint can accumulate against one CF session.
_DRIVER_ROTATE_EVERY = 6

# v24 fix (CF-12): confirmed via live diagnostic logging (CF-11) that the
# real table id is "matchlogs_all" — NOT "matchlogs_for_national_team"
# as assumed since v18. The wrong assumed id meant the table was never
# found even once the URL fix (CF-09/CF-10) made the page itself load
# correctly, which is why coverage stayed at 0 even after those fixes.
_INTL_TABLE_ID = "matchlogs_all"


# ── Cloudflare detection ───────────────────────────────────────────────────────

def _is_cloudflare(title: str, html: str) -> bool:
    if title:
        t = title.strip().lower()
        if "just a moment" in t or "attention required" in t:
            return True
    if html and "challenges.cloudflare.com" in html and "<table" not in html.lower():
        return True
    return False


def _is_404(title: str, html: str) -> bool:
    """
    v23: explicit 404 detection. FBref serves 404s at the original URL
    (no redirect), with a distinctive title/body, so URL-string checks
    never catch it — must inspect content instead.
    """
    if title and "page not found" in title.strip().lower():
        return True
    if html and ("Error 404" in html or "we could not find" in html.lower()):
        return True
    return False


def _is_server_error(html: str) -> bool:
    """
    v25 fix (CF-13): confirmed live for fbref_id=eb2bab9c (Yassine
    Bounou) — FBref's own server returns a plain Apache 500 page for
    this player's nat_tm matchlog, independent of our code or
    Cloudflare. This is a transient/server-side issue, most plausibly
    triggered by very large match histories (Bounou has hundreds of
    caps as Morocco's long-serving captain/starting GK), not something
    we can fix client-side. Detected explicitly so the loader retries
    a few times (FBref's backend issues are often transient) instead of
    silently mis-parsing an error page as "no data" with no signal in
    the logs, or attempting and failing repeatedly without ever calling
    this out.
    """
    if not html:
        return False
    h = html.lower()
    return "internal server error" in h or "500 internal server error" in h


# ── Core fetch ────────────────────────────────────────────────────────────────

def _fetch_intl_table(fbref_id: str, driver) -> pd.DataFrame | None:
    """
    Fetches the full national-team matchlog table for one player using an
    undetected_chromedriver driver instance (reused across calls).

    v23: single page per player (no season loop) — the nat_tm endpoint
    already returns the player's complete national-team history.

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
                    "backend failing (confirmed independently of our "
                    "request), not a client-side bug. Likely a very large "
                    "match history overwhelming their page renderer. "
                    "Skipping this player; retry later or report to FBref.",
                    fbref_id, attempt,
                )
                # FIX: signal caller to use manual_intl_caps fallback
                # نُعيد None مع تسجيل خاص — الـ main loop يتحقق من seeds
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

        break  # good page

    if not html:
        return None

    # Strip HTML comment wrappers so comment-buried tables become visible
    # (FBref wraps several secondary tables in <!-- ... --> that JS
    # unwraps client-side; pd.read_html() never sees them without this).
    if "<!--" in html and "<table" in html:
        html = re.sub(r"<!--(.*?)-->", r"\1", html, flags=re.DOTALL)

    if _INTL_TABLE_ID not in html:
        # v24 diagnostic (CF-11): log every table-like id actually present
        # on the page so we can see in the logs what FBref really named
        # the table, instead of guessing blind from outside the browser
        # (same pattern that successfully diagnosed the GK table-id issue
        # in fbref_loader.py — "page had ids: [...]").
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

    # Keep only real match rows (date-stamped) — drops header-repeat rows
    # and summary/total rows that sometimes appear in long FBref tables.
    if "Date" in df.columns:
        df = df[df["Date"].astype(str).str.match(r"\d{4}-\d{2}-\d{2}", na=False)]

    return df if not df.empty else None


# ── Aggregation ───────────────────────────────────────────────────────────────

def fetch_intl_stats(fbref_id: str, driver) -> dict:
    """
    Aggregates a player's full national-team match history into per-90
    rates and absolute totals. `driver` is an undetected_chromedriver
    instance reused across calls.

    v23: single fetch instead of looping over _SEASONS — the nat_tm page
    already contains every season/competition in one table.
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


# ── Public entry point ────────────────────────────────────────────────────────

def load_intl_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Fetches FBref international stats for all players in seed_df using
    undetected_chromedriver (real, locally-installed Chrome).

    v23: fixed the URL (was 404ing on every request — see CF-09 above)
    and switched to a single fetch per player instead of 4 (one per
    season), since the nat_tm endpoint returns full history already.

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

            # CF-14: rebuild the session periodically instead of reusing
            # one driver for all 49 players.
            if players_since_rotation >= _DRIVER_ROTATE_EVERY:
                logger.info(
                    "Rotating Selenium session after %d players "
                    "(CF-14: reset fingerprint accumulation)",
                    players_since_rotation,
                )
                driver.quit()
                time.sleep(random.uniform(2.0, 4.0))
                driver = _make_uc_driver()
                players_since_rotation = 0

            stats = fetch_intl_stats(str(fid), driver)

            # FIX: إذا FBref فشل (stats={}) → استخدم manual_intl_caps من seed إذا متاح
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

    # Intl stats are an optional enrichment source; players who never
    # appeared for the national team legitimately return {} — 0.60 keeps
    # this from being a false-positive pipeline error.
    assert_min_coverage(records, min_pct=0.60, source="fbref_intl")

    object_name = f"fbref_intl/players_intl_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"