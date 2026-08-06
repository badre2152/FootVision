import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

streamlit_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = streamlit_testing.AppTest

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"

ALL_DASHBOARD_SCRIPTS = [DASHBOARD_DIR / "app.py"] + sorted(
    (DASHBOARD_DIR / "pages").glob("*.py")
)


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
        import streamlit as st
        import dashboard.auth as auth

        monkeypatch.setattr(auth.st, "secrets", {"SOME_OTHER_KEY": "x"})
        st.session_state.clear()
        st.session_state[auth._PASSWORD_INPUT_KEY] = "anything"

        auth._password_entered()

        assert st.session_state[auth.SESSION_KEY_OK] is False
        assert st.session_state[auth.SESSION_KEY_CONFIGURED] is False

    def test_empty_configured_password_never_matches(self, monkeypatch):
        import streamlit as st
        import dashboard.auth as auth

        monkeypatch.setattr(auth.st, "secrets", {"APP_PASSWORD": ""})
        st.session_state.clear()
        st.session_state[auth._PASSWORD_INPUT_KEY] = ""

        auth._password_entered()

        assert st.session_state[auth.SESSION_KEY_OK] is False


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

        titles = [el.value for el in at.title]
        assert titles == ["🔒 FootVision"], (
            f"{script_path.name}: expected only the auth gate's title, got {titles} "
            "— require_auth() did not block before the page's real content."
        )

        assert len(at.dataframe) == 0, (
            f"{script_path.name}: a dataframe rendered despite no authenticated session"
        )

        assert len(at.text_input) >= 1, (
            f"{script_path.name}: no password input rendered"
        )