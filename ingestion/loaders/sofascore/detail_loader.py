# ingestion/loaders/sofascore_detail_loader.py
# Source: SofaScore — Career + Season Detail (DOM scrape, complements sofascore_loader.py)
#
# HISTORY:
#   Migrated from sofatest/sofascore_loader.py (standalone script) into the
#   standard ingestion/loaders/ pattern so it runs as part of run_bronze.py
#   alongside every other source, instead of as a separate manual script.
#
#   Original standalone script confirmed this approach works end-to-end
#   (career + season tabs, DOM-scraped via body.text + regex parsing) —
#   see sofascore_parsers_career.py / sofascore_parsers_season.py docstrings
#   for the confirmed page structure this relies on.
#
# RELATIONSHIP TO sofascore_loader.py (Source 9 / ADR-013):
#   sofascore_loader.py fetches __NEXT_DATA__ JSON (overall + per-tournament
#   rating, market value, bio) via a direct requests.get() with a browser
#   fallback. This loader is a DIFFERENT extraction path on the SAME site —
#   it DOM-scrapes the #tab:career and #tab:season views (which render
#   client-side, after __NEXT_DATA__ hydration, and are NOT present in
#   __NEXT_DATA__ itself — confirmed in the original POC). It returns
#   season-by-season / tournament-by-tournament breakdowns that
#   sofascore_loader.py cannot provide. Both loaders are kept and run
#   independently; columns are prefixed differently (sofascore_detail_*)
#   to avoid collision in the Silver merge.
#
# DRIVER STRATEGY:
#   Ports the same session-rotation pattern used in fbref_loader.py /
#   fbref_intl_loader.py (CF-14) instead of one driver held open for the
#   whole run — caps fingerprint accumulation over a long batch.
#
# RETRY FIX (post-migration, v2 — dynamic wait):
#   v1 used a fixed time.sleep() per tab, then retried once with another
#   fixed sleep on parse failure. In production this still failed: the
#   first navigation in a fresh driver session took as long as 58s to
#   actually render the table (cold-start / Cloudflare overhead), well
#   past the old 15s wait AND its 10s retry — both attempts read body.text
#   before the table existed, so even the retry came back empty.
#
#   Fixed by replacing the fixed sleep with a WebDriverWait that polls for
#   a concrete signal the table has rendered (the literal "MP" / "Matches"
#   marker text appearing in body.text) instead of guessing a sleep
#   duration. This is faster on the common case (returns as soon as the
#   marker appears, often well under 15s) and more robust on the slow
#   case (polls up to _MAX_WAIT_SECONDS instead of giving up at a fixed
#   ceiling). The old fixed-sleep retry is kept as a final fallback layer
#   in case the marker text appears but the full table hasn't finished
#   rendering yet.

import logging
import time
from datetime import datetime, timezone

import pandas as pd
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.exceptions import MaxRetryError, NewConnectionError

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage
from ingestion.loaders.fbref_loader import _make_uc_driver
from ingestion.loaders.sofascore.parsers_career import parse_career_table
from ingestion.loaders.sofascore.parsers_season import parse_season_table

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

_MIN_WAIT_SECONDS = 3.0     # floor — never read body.text before the page has had a moment
_MAX_WAIT_SECONDS = 40.0    # ceiling — covers the observed 58s cold-start case with margin
_POLL_INTERVAL = 1.0        # how often to re-check body.text while waiting
_RETRY_WAIT_SECONDS = 10.0  # extra fixed wait on retry, only used if the dynamic wait
                             # already saw the marker but the parser still failed
                             # (marker present but table not fully rendered yet)

# CF-14-style session rotation — same rationale as fbref_loader.py /
# fbref_intl_loader.py: cap how much fingerprint can accumulate against
# one browsing session over a long run.
_DRIVER_ROTATE_EVERY = 8


# ── Driver lifecycle helpers ─────────────────────────────────────────────────
#
# ROOT CAUSE (2026-07-01 incident): sofascore_detail failed outright on the
# very first player with ConnectionRefusedError / WinError 10061 talking to
# the driver's own localhost port. The driver had just been created by
# _make_uc_driver() a moment earlier — it never successfully served a single
# command. By that point in the run, dozens of prior Chrome sessions
# (fbref, fbref_intl, sofascore browser-fallback) had already rotated
# through and left behind `OSError: [WinError 6] Descripteur non valide`
# on __del__ — a known undetected_chromedriver/Windows issue where quit()
# doesn't fully release the process handle. That handle leak appears to be
# what starves a freshly spawned session of the socket/handle it needs to
# come up cleanly late in a long run.
#
# This can't be fixed with a longer wait — the connection is refused, not
# slow. The two changes below are what actually fixes it:
#   1. _driver_is_alive(): cheap post-creation probe so a dead-on-arrival
#      driver is caught and retried immediately instead of silently used.
#   2. _safe_quit(): quit() wrapped in try/except so a half-dead driver from
#      a *previous* rotation can't raise and skip cleanup, which is what
#      lets the handle leak compound over the run.
#   Combined with retry-with-fresh-driver around each player fetch (in
#   load_sofascore_detail_bronze), one dead session no longer fails the
#   entire source — only that one player, and only if the retry also fails.

_DRIVER_CREATE_RETRIES = 3
_DRIVER_CREATE_BACKOFF = 5.0

_CONNECTION_ERRORS = (
    ConnectionRefusedError,
    MaxRetryError,
    NewConnectionError,
    WebDriverException,
)


def _safe_quit(driver) -> None:
    """quit() that can never raise — a dead driver's teardown must not
    block/crash the caller's own error handling (see module note above)."""
    if driver is None:
        return
    try:
        driver.quit()
    except Exception as e:
        logger.debug("driver.quit() raised during cleanup (%s) — ignoring", type(e).__name__)


def _driver_is_alive(driver) -> bool:
    """Cheap probe that a freshly-created driver's session is actually
    reachable before we trust it with real work."""
    try:
        _ = driver.title
        return True
    except Exception as e:
        logger.warning("New driver session failed health-check (%s)", type(e).__name__)
        return False


def _make_uc_driver_with_retry(retries: int = _DRIVER_CREATE_RETRIES):
    """_make_uc_driver() wrapped with a post-creation health-check and
    retry — a driver that's dead on arrival is discarded and re-created
    rather than being handed to the caller (see module note above)."""
    last_err = None
    for attempt in range(1, retries + 1):
        driver = None
        try:
            driver = _make_uc_driver()
        except Exception as e:
            last_err = e
            logger.warning(
                "_make_uc_driver() raised on attempt %d/%d (%s) — retrying in %.0fs",
                attempt, retries, type(e).__name__, _DRIVER_CREATE_BACKOFF,
            )
            time.sleep(_DRIVER_CREATE_BACKOFF)
            continue

        if _driver_is_alive(driver):
            return driver

        logger.warning(
            "Driver failed health-check on attempt %d/%d — discarding and retrying in %.0fs",
            attempt, retries, _DRIVER_CREATE_BACKOFF,
        )
        _safe_quit(driver)
        time.sleep(_DRIVER_CREATE_BACKOFF)

    raise RuntimeError(
        f"Could not create a working Selenium driver after {retries} attempts"
    ) from last_err


# ── Driver-safe navigation ──────────────────────────────────────────────────

def _safe_get(driver, url: str) -> None:
    """driver.get() never hangs forever — timeout then window.stop()."""
    try:
        driver.get(url)
    except Exception as e:
        logger.debug("Navigation timeout (%s) on %s — window.stop()", type(e).__name__, url)
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass


def _get_body_text(driver) -> str:
    return driver.find_element(By.TAG_NAME, "body").text


def _wait_for_marker(driver, marker: str, max_wait: float = _MAX_WAIT_SECONDS) -> bool:
    """
    Polls body.text until `marker` literally appears in it, instead of
    sleeping a fixed duration and hoping. Returns True as soon as the
    marker is seen (often well under max_wait on a fast page), or False
    if max_wait elapses without it appearing.

    Always sleeps at least _MIN_WAIT_SECONDS first — body.text right
    after navigation is sometimes still the previous page's content for
    a brief moment, and we don't want a stale-page false positive.
    """
    time.sleep(_MIN_WAIT_SECONDS)
    try:
        WebDriverWait(driver, max_wait - _MIN_WAIT_SECONDS, poll_frequency=_POLL_INTERVAL).until(
            lambda d: marker in d.find_element(By.TAG_NAME, "body").text
        )
        return True
    except TimeoutException:
        return False


def _wait_for_marker_count(driver, marker: str, min_count: int = 1, max_wait: float = _MAX_WAIT_SECONDS) -> bool:
    """
    Like _wait_for_marker, but requires `marker` to appear at least
    `min_count` times in body.text before returning True.

    Needed for "Matches" specifically: it appears once in the nav bar
    (Matches/Season/Career/Fantasy/Media tabs, present immediately on
    navigation) and once as the real section header further down the
    page (rendered after the stats table hydrates). Waiting for a single
    occurrence was a false positive — it matched the nav tab text before
    the actual section header + stats had rendered, so the parser read
    body.text while the real "Matches" section was still empty/building.
    """
    time.sleep(_MIN_WAIT_SECONDS)
    try:
        WebDriverWait(driver, max_wait - _MIN_WAIT_SECONDS, poll_frequency=_POLL_INTERVAL).until(
            lambda d: d.find_element(By.TAG_NAME, "body").text.count(marker) >= min_count
        )
        return True
    except TimeoutException:
        return False


# ── Core fetch: one player, career + season ─────────────────────────────────

def fetch_sofascore_detail(driver, slug: str, sofascore_id: str) -> dict:
    """
    Scrapes #tab:career and #tab:season for one player.

    Wait strategy per tab:
      1. Navigate, then poll body.text for a marker proving the table has
         rendered ("MP" for career, "Matches" for season) — up to
         _MAX_WAIT_SECONDS, instead of sleeping a fixed duration.
      2. Parse. If that fails even though the marker was seen (table
         partially rendered), fall back to one fixed extra wait
         (_RETRY_WAIT_SECONDS) and retry once before giving up.

    Returns:
        {
            "career": [...] or None,
            "career_error": str or None,
            "season": {...} or None,
            "season_error": str or None,
            "scraped_at": ISO timestamp,
        }
    An error in one tab never blocks the other.
    """
    result = {
        "sofascore_id": sofascore_id,
        "sofascore_slug": slug,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "career": None,
        "career_error": None,
        "season": None,
        "season_error": None,
    }

    base_url = f"https://www.sofascore.com/football/player/{slug}/{sofascore_id}"

    # --- career tab ---
    career_url = f"{base_url}#tab:career"
    _safe_get(driver, career_url)
    marker_seen = _wait_for_marker(driver, "MP")
    if not marker_seen:
        logger.info(
            "career table marker ('MP') not seen within %.0fs for sofascore_id=%s "
            "— parsing anyway, likely to fail",
            _MAX_WAIT_SECONDS, sofascore_id,
        )
    try:
        result["career"] = parse_career_table(_get_body_text(driver))
    except Exception as e:
        logger.info(
            "career parse failed on attempt 1 for sofascore_id=%s (%s) — retrying with fixed wait",
            sofascore_id, type(e).__name__,
        )
        time.sleep(_RETRY_WAIT_SECONDS)
        try:
            result["career"] = parse_career_table(_get_body_text(driver))
        except Exception as e2:
            result["career_error"] = f"{type(e2).__name__}: {e2}"
            logger.warning(
                "career parse failed (after retry) for sofascore_id=%s: %s",
                sofascore_id, result["career_error"],
            )

    # --- season tab ---
    season_url = f"{base_url}#tab:season"
    _safe_get(driver, season_url)
    marker_seen = _wait_for_marker(driver, "Matches")
    if not marker_seen:
        logger.info(
            "season table marker ('Matches') not seen within %.0fs for sofascore_id=%s "
            "— parsing anyway, likely to fail",
            _MAX_WAIT_SECONDS, sofascore_id,
        )
    try:
        result["season"] = parse_season_table(_get_body_text(driver))
    except Exception as e:
        logger.info(
            "season parse failed on attempt 1 for sofascore_id=%s (%s) — retrying with fixed wait",
            sofascore_id, type(e).__name__,
        )
        time.sleep(_RETRY_WAIT_SECONDS)
        try:
            result["season"] = parse_season_table(_get_body_text(driver))
        except Exception as e2:
            result["season_error"] = f"{type(e2).__name__}: {e2}"
            logger.warning(
                "season parse failed (after retry) for sofascore_id=%s: %s",
                sofascore_id, result["season_error"],
            )

    return result


# ── Public entry point ───────────────────────────────────────────────────────

def load_sofascore_detail_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Fetches career + season detail from SofaScore for all players in seed_df
    using undetected_chromedriver (DOM scrape of #tab:career / #tab:season).

    seed_df must contain: 'player_id', 'sofascore_id', 'sofascore_slug'.
    Returns status string: "OK (N/49 players with data)" or "FAILED".
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(
        seed_df, ["player_id", "sofascore_id", "sofascore_slug"], source="sofascore_detail"
    )

    try:
        import undetected_chromedriver as uc  # noqa: F401
    except ImportError:
        logger.error(
            "undetected_chromedriver not installed. Run: "
            "pip install undetected-chromedriver"
        )
        return "FAILED"

    records = []
    driver = _make_uc_driver_with_retry()
    players_since_rotation = 0

    try:
        for _, row in seed_df.iterrows():
            sid = row.get("sofascore_id")
            slug = row.get("sofascore_slug")
            if pd.isna(sid) or not str(sid).strip() or pd.isna(slug) or not str(slug).strip():
                records.append({"player_id": row["player_id"]})
                logger.info("No sofascore_id/slug for %s — skipping.", row["player_id"])
                continue

            if players_since_rotation >= _DRIVER_ROTATE_EVERY:
                logger.info(
                    "Rotating Selenium session after %d players "
                    "(cap fingerprint accumulation)",
                    players_since_rotation,
                )
                _safe_quit(driver)
                driver = _make_uc_driver_with_retry()
                players_since_rotation = 0

            # Retry once with a brand-new driver if the current session has
            # died mid-run (e.g. WinError 10061 — see module note above).
            # A dead session must cost this one player a retry, not fail
            # the entire source the way it did on 2026-07-01.
            try:
                detail = fetch_sofascore_detail(driver, str(slug), str(sid))
            except _CONNECTION_ERRORS as e:
                logger.warning(
                    "Driver session appears dead for sofascore_id=%s (%s) — "
                    "recreating driver and retrying this player once",
                    sid, type(e).__name__,
                )
                _safe_quit(driver)
                driver = _make_uc_driver_with_retry()
                players_since_rotation = 0
                try:
                    detail = fetch_sofascore_detail(driver, str(slug), str(sid))
                except _CONNECTION_ERRORS as e2:
                    logger.warning(
                        "Retry after driver recreation also failed for "
                        "sofascore_id=%s (%s) — skipping this player",
                        sid, type(e2).__name__,
                    )
                    records.append({"player_id": row["player_id"]})
                    continue

            record = {"player_id": row["player_id"]}
            if detail.get("career") is not None:
                record["sofascore_detail_career"] = detail["career"]
            if detail.get("season") is not None:
                record["sofascore_detail_season"] = detail["season"]
            records.append(record)
            players_since_rotation += 1
    finally:
        _safe_quit(driver)

    assert_min_coverage(records, min_pct=0.50, source="sofascore_detail")

    object_name = f"sofascore_detail/players_sofascore_detail_{run_date}.json"
    success = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"