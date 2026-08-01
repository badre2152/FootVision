# tests/conftest.py
#
# Project-wide pytest fixtures.
#
# Added 2026-08-01 alongside spec `22_dashboard_caching.md`.
#
# WHY THIS EXISTS: dashboard/data_loader.py's load_latest_*()/load_all_*()
# functions are now @st.cache_data(ttl=300)-wrapped (spec #22). Because
# they take no arguments, Streamlit's cache key collapses to ONE global
# slot per function — a slot that is NOT scoped to a single test, a single
# test FILE, or a single monkeypatched GOLD_DIR. It lives in a
# process-wide cache for as long as the pytest process runs.
#
# Concretely, without this fixture: test_final_squad_page.py's
# `test_page_runs_without_exception` writes a real gold snapshot and
# calls load_latest_top26() (caching that DataFrame for 5 real minutes).
# A LATER test — in the SAME file, or even a completely different file
# like test_dashboard_post_hoc_banner.py, run later in the same `pytest`
# invocation — that expects an EMPTY or DIFFERENT gold/ directory would
# silently get that first test's stale cached DataFrame back instead of
# actually reading its own fixture data. This is exactly the kind of
# cross-test contamination that makes a test suite lie, and it was caught
# during spec #22's own verification pass, not left as a hidden landmine
# (see CHANGELOG.md for the specifics of what broke and how it was found).
#
# THE FIX: `st.cache_data.clear()` (the module-level call, not a specific
# function's `.clear()`) wipes every @st.cache_data cache in the process.
# Running it before EVERY test, project-wide, via an autouse fixture is
# more robust than remembering to add `fn.clear()` calls to every
# individual test file's fixtures — a future new test file that forgets
# to do so would silently reintroduce this exact bug.

import pytest


@pytest.fixture(autouse=True)
def _clear_streamlit_cache_before_every_test():
    """Autouse — applies to every test in the whole tests/ tree with no
    per-file opt-in required. Only takes effect if streamlit is actually
    importable; environments without it (this module has no other
    Streamlit dependency) skip silently rather than failing collection."""
    try:
        import streamlit as st
    except ImportError:
        yield
        return
    st.cache_data.clear()
    yield
    st.cache_data.clear()
