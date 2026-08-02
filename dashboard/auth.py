# dashboard/auth.py
# Football Atlas — Dashboard Authentication Gate
#
# Added 2026-08-02 — spec `06_authentication.md` (#6).
#
# `grep -rln "authenticate\|login\|password" dashboard/` returned zero
# results before this file: the dashboard, including the interactive Squad
# Builder page that lets anyone reachable preview/change a candidate squad,
# had no access control at all. This closes that gap with the spec's
# minimal-viable option — a single shared password known to the coaching
# staff, session-based (Streamlit's st.session_state, which does not
# survive past the browser session — this is NOT persistent
# unauthenticated access).
#
# CENTRALIZED ON PURPOSE, not copy-pasted per page (the spec's own sample
# snippet is written to be pasted into every page file). This project's own
# CHANGELOG.md documents its single biggest recurring failure mode as
# logic re-typed independently in multiple places and then silently
# drifting/reverting between sessions (see e.g. the `TOP_EXPORT_COLS`
# entry). A duplicated auth check is exactly that risk, applied to the
# single most security-sensitive piece of code in this project — so this
# lives in ONE place and every page imports and calls it. Streamlit
# multipage apps still don't share a gate automatically across pages, so
# require_auth() still has to be CALLED at the top of every page file
# (app.py + every dashboard/pages/*.py) — only the password-check logic
# itself is not duplicated.
#
# FAILS CLOSED: if `.streamlit/secrets.toml` is missing entirely, or exists
# but has no APP_PASSWORD key, this does NOT fall open and grant access —
# it blocks with a visible configuration error. A misconfigured deployment
# must be obviously broken, not silently unprotected (same "no silent
# failure" principle this project already applies everywhere else — see
# docs/architecture.md, CHANGELOG.md).
#
# NOT IMPLEMENTED (spec's own "Better version" section, explicitly out of
# scope for this minimal pass): per-user credentials and an access log for
# accountability (who viewed/changed what). A single shared password gives
# no per-user audit trail. If more than one staff member needs
# distinguishable access, this module needs upgrading first — see
# `06_authentication.md`.

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

SESSION_KEY_OK = "_auth_ok"
SESSION_KEY_CONFIGURED = "_auth_configured"
_PASSWORD_INPUT_KEY = "_auth_password_input"


def _get_configured_password():
    """
    Returns the configured APP_PASSWORD from .streamlit/secrets.toml, or
    None if it isn't configured at all (no secrets.toml, or a secrets.toml
    without that key) — never raises, so the caller can fail closed
    uniformly regardless of which of those two cases it is.
    """
    try:
        return st.secrets["APP_PASSWORD"]
    except (KeyError, StreamlitSecretNotFoundError):
        return None


def _password_entered() -> None:
    expected = _get_configured_password()
    st.session_state[SESSION_KEY_CONFIGURED] = expected is not None

    entered = st.session_state.get(_PASSWORD_INPUT_KEY, "")
    st.session_state[SESSION_KEY_OK] = bool(expected) and (entered == expected)

    # Don't leave the typed password sitting in session_state any longer
    # than needed for the comparison above.
    if _PASSWORD_INPUT_KEY in st.session_state:
        del st.session_state[_PASSWORD_INPUT_KEY]


def require_auth() -> None:
    """
    Call at the very top of every page — app.py and each
    dashboard/pages/*.py — immediately after st.set_page_config() and
    BEFORE render_sidebar() or any Gold data is loaded/rendered.

    Authenticated session (st.session_state["_auth_ok"] is True): returns
    immediately, page continues normally.

    Unauthenticated session: renders only a password prompt and calls
    st.stop() — nothing below this call in the calling page ever executes
    for that script run.
    """
    if st.session_state.get(SESSION_KEY_OK):
        return

    st.title("🔒 Football Atlas")
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
