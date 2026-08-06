from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config

POST_HOC_BANNER_TITLE = "⏱️ POST-HOC AUDIT"


def post_hoc_banner(extra: str | None = None) -> None:
    text = (
        f"{POST_HOC_BANNER_TITLE} — this section uses information from "
        "after the tournament, for validating model accuracy only. "
        "It is NOT what would have been shown before "
        f"**{config.DECISION_DATE.isoformat()}**."
    )
    if extra:
        text += f"\n\n{extra}"
    st.warning(text, icon="⏱️")


PRE_CUTOFF_FIX_BANNER_TITLE = "🧪 PRE-CUTOFF-FIX SNAPSHOT"


def pre_cutoff_fix_banner(extra: str | None = None) -> None:
    text = (
        f"{PRE_CUTOFF_FIX_BANNER_TITLE} — this snapshot was generated before "
        "spec #1's DECISION_DATE-cutoff enforcement existed in the code (see "
        "config.py § 0), so it may contain temporally-leaked data (e.g. "
        "injury/form data from after the actual decision date). Treat it as "
        "directional only, not as a validated decision-support signal. "
        "Re-run the pipeline (`run_bronze.py --as-of ... → run_silver.py → "
        "run_gold.py`) to produce a genuinely cutoff-enforced snapshot."
    )
    if extra:
        text += f"\n\n{extra}"
    st.warning(text, icon="🧪")