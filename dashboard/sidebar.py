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

import streamlit as st

POSITIONS = ["GK", "DEF", "MID", "FWD"]


def render_sidebar() -> None:
    """
    Renders branding + global filters in the sidebar. Call this once near
    the top of every page file (app.py and each dashboard/pages/*.py).
    """
    with st.sidebar:
        st.markdown(
            """
            <div style="text-align:center; padding: 0.5rem 0 1rem 0;">
                <span style="font-size:2.2rem;">⚽🇲🇦</span>
                <h2 style="margin:0.2rem 0 0 0;">Football Atlas</h2>
                <p style="color:#9ca3af; font-size:0.85rem; margin:0;">
                    Morocco World Cup 2026 — Decision Support System
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.divider()

        st.subheader("🔍 Filters")

        st.session_state.setdefault("filter_positions", list(POSITIONS))
        st.session_state.setdefault("filter_search", "")

        st.session_state["filter_positions"] = st.multiselect(
            "Position",
            options=POSITIONS,
            default=st.session_state["filter_positions"],
            key="sidebar_position_filter",
        )
        st.session_state["filter_search"] = st.text_input(
            "Search player name",
            value=st.session_state["filter_search"],
            placeholder="e.g. Hakimi",
            key="sidebar_search_box",
        )

        if st.button("Reset filters", width='stretch'):
            st.session_state["filter_positions"] = list(POSITIONS)
            st.session_state["filter_search"] = ""
            st.rerun()

        st.divider()
        st.caption(
            "Built for the Football Atlas graduation project. "
            "Data: FBref, Understat, SofaScore, StatsBomb Open Data, "
            "Transfermarkt, FlashScore, Fotmob, SoFIFA, Wikidata."
        )


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