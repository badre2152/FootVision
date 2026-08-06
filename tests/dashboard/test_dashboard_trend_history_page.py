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
    at.session_state["_auth_ok"] = True
    at.run(timeout=60)
    assert not at.exception, f"{TREND_PAGE} raised: {at.exception}"
    return at


def test_page_loads_without_error():
    _run()


def test_page_shows_pre_cutoff_fix_banner_when_any_snapshot_predates_the_fix():
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