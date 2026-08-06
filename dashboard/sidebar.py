import streamlit as st

from dashboard.i18n import t, render_language_switcher

POSITIONS = ["GK", "DEF", "MID", "FWD"]


def render_sidebar() -> None:
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
    if df is None or "position" not in df.columns or "player_name" not in df.columns:
        return df

    positions = st.session_state.get("filter_positions", list(POSITIONS))
    search = st.session_state.get("filter_search", "").strip().lower()

    filtered = df[df["position"].isin(positions)] if positions else df.iloc[0:0]
    if search:
        filtered = filtered[filtered["player_name"].str.lower().str.contains(search, na=False)]
    return filtered