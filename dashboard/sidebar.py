# dashboard/sidebar.py
# Football Atlas — Shared Sidebar Component
#
# Added 2026-07-14 (second pass). Renders once per page (call
# render_sidebar() at the top of every page file) and exposes
# apply_sidebar_filters(df) so each page can filter its own DataFrame the
# same way, without duplicating filter-UI code four times.
#
# Filter state lives in st.session_state so it persists as the user moves
# between pages (Streamlit reruns the whole script on every interaction,
# but session_state survives across those reruns within one browser tab).
#
# Localization (issue #15, added later): the language switcher lives here
# too, using the same session_state persistence pattern as the filters —
# it's rendered first so the rest of the sidebar (and RTL CSS, for Arabic)
# reflects the choice on the same run. See dashboard/i18n.py.

import streamlit as st

from dashboard.i18n import t, render_language_switcher

POSITIONS = ["GK", "DEF", "MID", "FWD"]


def render_sidebar() -> None:
    """
    Renders branding + language switcher + global filters in the sidebar.
    Call this once near the top of every page file (app.py and each
    dashboard/pages/*.py).
    """
    with st.sidebar:
        render_language_switcher()
        st.divider()

        st.markdown(
            f"""
            <div style="text-align:center; padding: 0.5rem 0 1rem 0;">
                <span style="font-size:2.2rem;">⚽🇲🇦</span>
                <h2 style="margin:0.2rem 0 0 0;">{t("sidebar.brand_title")}</h2>
                <p style="color:#9ca3af; font-size:0.85rem; margin:0;">
                    {t("sidebar.brand_subtitle")}
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.divider()

        st.subheader(t("sidebar.filters_header"))

        st.session_state.setdefault("filter_positions", list(POSITIONS))
        st.session_state.setdefault("filter_search", "")

        st.session_state["filter_positions"] = st.multiselect(
            t("sidebar.position_label"),
            options=POSITIONS,
            default=st.session_state["filter_positions"],
            key="sidebar_position_filter",
        )
        st.session_state["filter_search"] = st.text_input(
            t("sidebar.search_label"),
            value=st.session_state["filter_search"],
            placeholder=t("sidebar.search_placeholder"),
            key="sidebar_search_box",
        )

        if st.button(t("sidebar.reset_button"), width='stretch'):
            st.session_state["filter_positions"] = list(POSITIONS)
            st.session_state["filter_search"] = ""
            st.rerun()

        st.divider()
        st.caption(t("sidebar.footer"))


def apply_sidebar_filters(df):
    """
    Filters a DataFrame using the current sidebar filter state.

    Args:
        df: Any DataFrame with "position" and "player_name" columns
            (top26 / players_scored both qualify).

    Returns:
        Filtered DataFrame (a view/copy — safe to display directly).
        If df is missing either column, returns it UNCHANGED rather than
        raising — filtering is a convenience, not a hard requirement, and
        pages should still work against DataFrames that lack these columns.
    """
    if df is None or "position" not in df.columns or "player_name" not in df.columns:
        return df

    positions = st.session_state.get("filter_positions", list(POSITIONS))
    search = st.session_state.get("filter_search", "").strip().lower()

    filtered = df[df["position"].isin(positions)] if positions else df.iloc[0:0]
    if search:
        filtered = filtered[filtered["player_name"].str.lower().str.contains(search, na=False)]
    return filtered