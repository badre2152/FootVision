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

_MIN_WAIT_SECONDS = 3.0
_MAX_WAIT_SECONDS = 40.0
_POLL_INTERVAL = 1.0
_RETRY_WAIT_SECONDS = 10.0

_DRIVER_ROTATE_EVERY = 8

_DRIVER_CREATE_RETRIES = 3
_DRIVER_CREATE_BACKOFF = 5.0

_CONNECTION_ERRORS = (
    ConnectionRefusedError,
    MaxRetryError,
    NewConnectionError,
    WebDriverException,
)


def _safe_quit(driver) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except Exception as e:
        logger.debug("driver.quit() raised during cleanup (%s) — ignoring", type(e).__name__)


def _driver_is_alive(driver) -> bool:
    try:
        _ = driver.title
        return True
    except Exception as e:
        logger.warning("New driver session failed health-check (%s)", type(e).__name__)
        return False


def _make_uc_driver_with_retry(retries: int = _DRIVER_CREATE_RETRIES):
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


def _safe_get(driver, url: str) -> None:
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
    time.sleep(_MIN_WAIT_SECONDS)
    try:
        WebDriverWait(driver, max_wait - _MIN_WAIT_SECONDS, poll_frequency=_POLL_INTERVAL).until(
            lambda d: marker in d.find_element(By.TAG_NAME, "body").text
        )
        return True
    except TimeoutException:
        return False


def _wait_for_marker_count(driver, marker: str, min_count: int = 1, max_wait: float = _MAX_WAIT_SECONDS) -> bool:
    time.sleep(_MIN_WAIT_SECONDS)
    try:
        WebDriverWait(driver, max_wait - _MIN_WAIT_SECONDS, poll_frequency=_POLL_INTERVAL).until(
            lambda d: d.find_element(By.TAG_NAME, "body").text.count(marker) >= min_count
        )
        return True
    except TimeoutException:
        return False


def fetch_sofascore_detail(driver, slug: str, sofascore_id: str) -> dict:
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


def load_sofascore_detail_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
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