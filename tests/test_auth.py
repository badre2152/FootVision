# tests/test_auth.py
#
# Tests for spec `06_authentication.md` (#6) — dashboard/auth.py and its
# wiring into app.py + every dashboard/pages/*.py.
#
# Two layers, matching the spec's own Acceptance Criteria:
#   1. Unit tests on dashboard.auth's password-check logic directly (no
#      Streamlit script execution needed) — fast, precise, covers the
#      fail-closed behavior for missing/misconfigured secrets.
#   2. AppTest smoke tests, one per real page file (app.py + every
#      dashboard/pages/*.py) confirming the spec's literal Acceptance
#      Criterion: "Any page load without a valid session shows only the
#      password prompt, no player data." — run against every page, not
#      just one, because 06_authentication.md explicitly requires the
#      check to be replicated at the top of EVERY page file, and this
#      project's CHANGELOG.md shows exactly this kind of per-file
#      duplication (require_auth() being imported+called correctly on
#      every file) is where things silently regress between sessions.

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

streamlit_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = streamlit_testing.AppTest

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"

# Every page Streamlit actually serves: app.py (the entry point) plus every
# file under dashboard/pages/. Built from disk, not hand-typed, so a new
# page file added later is automatically covered by this test instead of
# silently having no auth-gate coverage.
ALL_DASHBOARD_SCRIPTS = [DASHBOARD_DIR / "app.py"] + sorted(
    (DASHBOARD_DIR / "pages").glob("*.py")
)


# ─────────────────────────────────────────────────────────────────────────
# Layer 1 — unit tests on the password-check logic itself
# ─────────────────────────────────────────────────────────────────────────

class TestPasswordCheckLogic:

    def test_correct_password_authenticates(self, monkeypatch):
        import streamlit as st
        import dashboard.auth as auth

        monkeypatch.setattr(auth.st, "secrets", {"APP_PASSWORD": "correct-horse"})
        st.session_state.clear()
        st.session_state[auth._PASSWORD_INPUT_KEY] = "correct-horse"

        auth._password_entered()

        assert st.session_state[auth.SESSION_KEY_OK] is True
        assert auth._PASSWORD_INPUT_KEY not in st.session_state, (
            "typed password must not linger in session_state after the check"
        )

    def test_wrong_password_does_not_authenticate(self, monkeypatch):
        import streamlit as st
        import dashboard.auth as auth

        monkeypatch.setattr(auth.st, "secrets", {"APP_PASSWORD": "correct-horse"})
        st.session_state.clear()
        st.session_state[auth._PASSWORD_INPUT_KEY] = "wrong-guess"

        auth._password_entered()

        assert st.session_state[auth.SESSION_KEY_OK] is False

    def test_missing_secrets_file_fails_closed(self, monkeypatch):
        """No .streamlit/secrets.toml at all — must not silently grant access."""
        import streamlit as st
        import dashboard.auth as auth
        from streamlit.errors import StreamlitSecretNotFoundError

        class _RaisingSecrets:
            def __getitem__(self, key):
                raise StreamlitSecretNotFoundError("no secrets.toml found")

        monkeypatch.setattr(auth.st, "secrets", _RaisingSecrets())
        st.session_state.clear()
        st.session_state[auth._PASSWORD_INPUT_KEY] = "anything"

        auth._password_entered()

        assert st.session_state[auth.SESSION_KEY_OK] is False
        assert st.session_state[auth.SESSION_KEY_CONFIGURED] is False

    def test_secrets_file_without_app_password_key_fails_closed(self, monkeypatch):
        """secrets.toml exists but has no APP_PASSWORD entry — same fail-closed rule."""
        import streamlit as st
        import dashboard.auth as auth

        monkeypatch.setattr(auth.st, "secrets", {"SOME_OTHER_KEY": "x"})
        st.session_state.clear()
        st.session_state[auth._PASSWORD_INPUT_KEY] = "anything"

        auth._password_entered()

        assert st.session_state[auth.SESSION_KEY_OK] is False
        assert st.session_state[auth.SESSION_KEY_CONFIGURED] is False

    def test_empty_configured_password_never_matches(self, monkeypatch):
        """An accidentally-blank APP_PASSWORD ("") must not authenticate an
        empty/unfilled input — bool(expected) guards against a falsy
        configured secret acting as an open gate."""
        import streamlit as st
        import dashboard.auth as auth

        monkeypatch.setattr(auth.st, "secrets", {"APP_PASSWORD": ""})
        st.session_state.clear()
        st.session_state[auth._PASSWORD_INPUT_KEY] = ""

        auth._password_entered()

        assert st.session_state[auth.SESSION_KEY_OK] is False


# ─────────────────────────────────────────────────────────────────────────
# Layer 2 — every real page, unauthenticated, via AppTest
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "script_path", ALL_DASHBOARD_SCRIPTS, ids=[p.name for p in ALL_DASHBOARD_SCRIPTS]
)
class TestEveryPageBlocksUnauthenticatedAccess:

    def test_no_session_shows_only_password_prompt(self, script_path):
        at = AppTest.from_file(str(script_path))
        at.run()

        assert not at.exception, (
            f"{script_path.name} raised instead of cleanly st.stop()-ing: "
            f"{[str(e) for e in at.exception]}"
        )

        # The gate's own st.title() ("🔒 Football Atlas") must be the ONLY
        # title rendered — the real page's own st.title() call sits after
        # require_auth() in every file, so if it appears, the gate didn't
        # actually stop execution.
        titles = [el.value for el in at.title]
        assert titles == ["🔒 Football Atlas"], (
            f"{script_path.name}: expected only the auth gate's title, got {titles} "
            "— require_auth() did not block before the page's real content."
        )

        # No player data must have rendered — the spec's literal
        # Acceptance Criterion.
        assert len(at.dataframe) == 0, (
            f"{script_path.name}: a dataframe rendered despite no authenticated session"
        )

        # A password field must be present.
        assert len(at.text_input) >= 1, (
            f"{script_path.name}: no password input rendered"
        )