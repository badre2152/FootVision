import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

SESSION_KEY_OK = "_auth_ok"
SESSION_KEY_CONFIGURED = "_auth_configured"
_PASSWORD_INPUT_KEY = "_auth_password_input"


def _get_configured_password():
    try:
        return st.secrets["APP_PASSWORD"]
    except (KeyError, StreamlitSecretNotFoundError):
        return None


def _password_entered() -> None:
    expected = _get_configured_password()
    st.session_state[SESSION_KEY_CONFIGURED] = expected is not None

    entered = st.session_state.get(_PASSWORD_INPUT_KEY, "")
    st.session_state[SESSION_KEY_OK] = bool(expected) and (entered == expected)

    if _PASSWORD_INPUT_KEY in st.session_state:
        del st.session_state[_PASSWORD_INPUT_KEY]


def require_auth() -> None:
    if st.session_state.get(SESSION_KEY_OK):
        return

    st.title("🔒 FootVision")
    st.caption("Coach-staff access only. Enter the shared password to continue.")
    st.text_input(
        "Password",
        type="password",
        on_change=_password_entered,
        key=_PASSWORD_INPUT_KEY,
    )

    if st.session_state.get(SESSION_KEY_CONFIGURED) is False:
        st.error(
            "No APP_PASSWORD is configured for this deployment "
            "(.streamlit/secrets.toml). Access is blocked until that's "
            "set — contact whoever manages this deployment."
        )
    elif SESSION_KEY_OK in st.session_state and not st.session_state[SESSION_KEY_OK]:
        st.error("Incorrect password.")

    st.stop()