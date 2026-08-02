# tests/test_dashboard_post_hoc_banner.py
#
# Headless AppTest smoke test for spec
# "05_dashboard_decision_vs_audit_labeling.md" (#5).
#
# Acceptance criterion: "Every page reachable from the sidebar that
# contains any post-DECISION_DATE information displays the banner before
# that content, verified by a headless AppTest check that asserts the
# banner text is present when the underlying data contains a post-cutoff
# date."
#
# Runs each dashboard page as a real (headless) Streamlit script via
# st.testing.v1.AppTest — no browser, no running server. Requires the
# `gold/*.csv` fixture files already committed in this repo (top26_*.csv,
# players_scored_*.csv) to exist, same as tests/test_dashboard_data_loader.py
# assumes.

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

from dashboard.components import POST_HOC_BANNER_TITLE

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Pages whose content includes information not knowable as of
# config.DECISION_DATE (real match results / the real announced squad /
# post-tournament audits) — MUST show the banner.
POST_HOC_PAGES = [
    "dashboard/pages/8_competition.py",   # "Real WC2026 Run" tab
    "dashboard/pages/5_selection_audit.py",  # entire page is retrospective
]

# A page that is pure pre-decision Gold output (DSSScore-based squad
# recommendation) — must NOT show the banner. Used as a negative control so
# this test also catches an accidental future call to post_hoc_banner()
# creeping into a genuine decision-support page.
DECISION_SUPPORT_PAGE = "dashboard/app.py"


def _run(app_relpath: str):
    at = AppTest.from_file(str(PROJECT_ROOT / app_relpath))
    at.session_state["_auth_ok"] = True  # spec #6 — bypass auth gate for content tests
    at.run(timeout=60)
    assert not at.exception, f"{app_relpath} raised: {at.exception}"
    return at


@pytest.mark.parametrize("app_relpath", POST_HOC_PAGES)
def test_post_hoc_banner_present_on_retrospective_pages(app_relpath):
    at = _run(app_relpath)
    warning_texts = [w.value for w in at.warning]
    assert any(POST_HOC_BANNER_TITLE in text for text in warning_texts), (
        f"{app_relpath} shows post-DECISION_DATE information but does not "
        f"render the shared post_hoc_banner() (missing '{POST_HOC_BANNER_TITLE}')."
    )


def test_post_hoc_banner_absent_on_decision_support_page():
    at = _run(DECISION_SUPPORT_PAGE)
    warning_texts = [w.value for w in at.warning]
    assert not any(POST_HOC_BANNER_TITLE in text for text in warning_texts), (
        f"{DECISION_SUPPORT_PAGE} is a pre-decision Gold-output page and should "
        "NOT render post_hoc_banner() — if it now legitimately needs to, this "
        "test's page lists need updating, not just deleting the assertion."
    )