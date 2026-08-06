import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from streamlit.testing.v1 import AppTest

from dashboard.components import POST_HOC_BANNER_TITLE

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

POST_HOC_PAGES = [
    "dashboard/pages/08_competition.py",
    "dashboard/pages/04_selection_audit.py",
]

DECISION_SUPPORT_PAGE = "dashboard/app.py"


def _run(app_relpath: str):
    at = AppTest.from_file(str(PROJECT_ROOT / app_relpath))
    at.session_state["_auth_ok"] = True
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