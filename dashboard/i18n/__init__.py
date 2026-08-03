# dashboard/i18n/__init__.py
# Football Atlas — Dashboard Localization (Arabic / French / English)
#
# Added for issue #15 (Arabic / French Dashboard Localization).
#
# Scope decision (confirmed with project owner): full bilingual/trilingual
# toggle, not a single fixed-language build. A sidebar language switcher
# lets the user pick English / Français / العربية at any time; the choice
# is stored in st.session_state["lang"] so it persists across page
# navigation (Streamlit reruns the script on every interaction, but
# session_state survives within one browser tab — same pattern already
# used for the sidebar filters in sidebar.py).
#
# Currently fully wired into:
#   - sidebar.py                (branding, filters, language switcher itself)
#   - app.py                    (Executive Dashboard — nav/title + full page)
#   - pages/03_final_squad.py    (highest-value page — full translation)
#   - pages/04_selection_audit.py(full translation, incl. Plotly axis labels)
# and wired for *navigation titles only* (st.title / page_config) on the
# remaining pages, per the minimum scope in 15_localization.md. Expanding
# full in-page translation to the remaining pages is a follow-up (see
# implementation plan step 4 in the spec) — just add more keys below and
# swap the hardcoded strings for t(...) calls the same way.
#
# Usage:
#   from dashboard.i18n import t, render_language_switcher, get_lang
#   render_language_switcher()          # call once in sidebar.py
#   st.title(t("nav.final_squad_title"))
#   st.caption(t("final_squad.dna_caption"))
#   st.warning(t("final_squad.mismatch_warning", mismatches="; ".join(mismatches)))
#
# --- Reorg note ---
# This used to be a single 972-line dashboard/i18n.py with all ~135 x 3
# translated strings inlined as a Python dict (STRINGS). Content and
# behavior are unchanged — the strings themselves now live as data in
# en.json / fr.json / ar.json (this directory), loaded once at import
# time and reassembled into the exact same STRINGS shape
# ({key: {"en": ..., "fr": ..., "ar": ...}}) that t() below expects.
# Editing a translation is now a JSON edit, not a Python code change, and
# a translator without Python knowledge can contribute directly.

import json
from pathlib import Path

import streamlit as st

LANGUAGES = {
    "en": "English",
    "fr": "Français",
    "ar": "العربية",
}

DEFAULT_LANG = "en"

# RTL languages need special CSS handling (see apply_rtl_css below) —
# Streamlit has no built-in RTL support, this is the one language in our
# set that needs it.
RTL_LANGUAGES = {"ar"}

_LOCALE_DIR = Path(__file__).resolve().parent


def _load_strings() -> dict:
    """
    Loads en.json / fr.json / ar.json from this package's directory and
    reassembles them into the {key: {lang: text}} shape the rest of this
    module (t(), etc.) was already written against.
    """
    per_lang = {}
    for lang in LANGUAGES:
        with open(_LOCALE_DIR / f"{lang}.json", encoding="utf-8") as f:
            per_lang[lang] = json.load(f)

    keys = set()
    for lang_dict in per_lang.values():
        keys.update(lang_dict.keys())

    strings = {}
    for key in keys:
        strings[key] = {
            lang: per_lang[lang][key]
            for lang in LANGUAGES
            if key in per_lang[lang]
        }
    return strings


STRINGS = _load_strings()


def get_lang() -> str:
    """Returns the currently selected language code, defaulting to DEFAULT_LANG."""
    return st.session_state.get("lang", DEFAULT_LANG)


def is_rtl(lang: str | None = None) -> bool:
    return (lang or get_lang()) in RTL_LANGUAGES


def t(key: str, **kwargs) -> str:
    """
    Translates `key` into the currently selected language and formats it
    with any keyword arguments given (using str.format).

    Falls back to English, then to the raw key itself, if a translation is
    missing — this keeps a missing string from crashing the whole page.
    """
    lang = get_lang()
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def render_language_switcher() -> None:
    """
    Renders the sidebar language switcher (EN / FR / AR) and applies RTL
    CSS immediately when Arabic is selected. Call once near the top of
    render_sidebar(), before any other translated text is drawn, so the
    rest of the sidebar (and page) picks up the new language on the same
    run.
    """
    st.session_state.setdefault("lang", DEFAULT_LANG)

    codes = list(LANGUAGES.keys())
    current_index = codes.index(st.session_state["lang"]) if st.session_state["lang"] in codes else 0

    selected = st.selectbox(
        t("sidebar.language_label"),
        options=codes,
        index=current_index,
        format_func=lambda code: LANGUAGES[code],
        key="sidebar_language_select",
    )
    if selected != st.session_state["lang"]:
        st.session_state["lang"] = selected
        st.rerun()

    apply_rtl_css()


def apply_rtl_css() -> None:
    """
    Injects RTL layout CSS when Arabic is active.

    Streamlit has no native RTL mode, so this flips text direction and
    alignment for the elements that carry translated prose (titles,
    markdown, captions, alerts, form labels, tabs) while deliberately
    leaving numeric/tabular/chart surfaces (st.metric values, st.dataframe,
    Plotly charts) in LTR — mixing RTL text with LTR numbers is standard
    practice for Arabic UIs and avoids scrambled-looking figures/axes.

    This has NOT been visually verified in a live browser (no browser
    rendering available in this environment) — spec explicitly calls out
    RTL as "the most likely place for silent visual bugs", so please
    smoke-test `streamlit run dashboard/app.py` with the Arabic option
    selected before treating this as done.
    """
    if not is_rtl():
        return
    st.markdown(
        """
        <style>
        /* Flip text-bearing elements to RTL */
        section.main .block-container,
        section[data-testid="stSidebar"] .block-container,
        .stMarkdown, .stCaption, .stAlert, .stException,
        h1, h2, h3, h4, h5, h6, p, label, li {
            direction: rtl;
            text-align: right;
        }
        /* Keep numbers, tables, and charts LTR so figures/axes don't scramble */
        [data-testid="stMetric"], [data-testid="stMetricValue"],
        [data-testid="stMetricDelta"], [data-testid="stDataFrame"],
        [data-testid="stDataFrameResizable"], .js-plotly-plot,
        [data-testid="stTable"], code, pre {
            direction: ltr;
            text-align: left;
        }
        /* st.metric label should still read RTL even though the value is LTR */
        [data-testid="stMetricLabel"] {
            direction: rtl;
            text-align: right;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
