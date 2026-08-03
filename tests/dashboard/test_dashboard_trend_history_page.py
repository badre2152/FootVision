# tests/test_dashboard_trend_history_page.py
#
# Headless AppTest smoke test for spec "18_historical_trend_view.md" (#18).
#
# Re-created 2026-08-02: this file existed in an earlier session but was
# lost somewhere in a later rewrite of the test suite. Runs
# dashboard/pages/09_trend_history.py as a real (headless) Streamlit script
# via st.testing.v1.AppTest — no browser, no running server. Uses whatever
# real gold/players_scored_*.csv / squad_summary_*.json fixtures are
# already committed in this repo (same assumption as
# tests/test_dashboard_post_hoc_banner.py and friends).

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TREND_PAGE = "dashboard/pages/09_trend_history.py"


def _run():
    at = AppTest.from_file(str(PROJECT_ROOT / TREND_PAGE))
    at.session_state["_auth_ok"] = True  # spec #6 — bypass auth gate for content tests
    at.run(timeout=60)
    assert not at.exception, f"{TREND_PAGE} raised: {at.exception}"
    return at


def test_page_loads_without_error():
    _run()


def test_page_shows_pre_cutoff_fix_banner_when_any_snapshot_predates_the_fix():
    """
    Every real gold/players_scored_*.csv fixture currently checked into
    this repo predates spec #1's implementation (no squad_summary_*.json
    in the repo currently sets decision_date_enforced: true), so the page
    MUST render the pre-cutoff-fix warning rather than silently treating
    that history as validated.
    """
    from dashboard.components import PRE_CUTOFF_FIX_BANNER_TITLE

    at = _run()
    warning_texts = [w.value for w in at.warning]
    assert any(PRE_CUTOFF_FIX_BANNER_TITLE in text for text in warning_texts), (
        f"{TREND_PAGE} did not render pre_cutoff_fix_banner() for pre-fix "
        f"snapshots (missing '{PRE_CUTOFF_FIX_BANNER_TITLE}')."
    )


def test_page_shows_a_player_selector_when_data_exists():
    at = _run()
    assert len(at.selectbox) >= 1, "Expected a player selector (st.selectbox) on the trend page."


def test_page_shows_biggest_movers_section():
    at = _run()
    subheader_texts = [s.value for s in at.subheader]
    assert any("movers" in text.lower() for text in subheader_texts), (
        f"{TREND_PAGE} should have a 'Biggest movers' subheader per spec #18's "
        "Functional Requirement 2."
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
