import pytest


@pytest.fixture(autouse=True)
def _clear_streamlit_cache_before_every_test():
    try:
        import streamlit as st
    except ImportError:
        yield
        return
    st.cache_data.clear()
    yield
    st.cache_data.clear()