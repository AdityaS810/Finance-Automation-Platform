"""Theme helpers and color constants for the app UI."""

from __future__ import annotations

from pathlib import Path

import streamlit as st


APP_BACKGROUND = "#F4F7FC"
SIDEBAR_BACKGROUND = "#1F3E4A"
SIDEBAR_ACTIVE = "#0070C0"
PRIMARY_BUTTON = "#0070C0"
PRIMARY_BUTTON_HOVER = "#254DB1"
CARD_BACKGROUND = "#FFFFFF"
MAIN_HEADING = "#1F3E4A"
BODY_TEXT = "#1F3E4A"
MUTED_TEXT = "#5A7480"
BORDER = "rgba(31, 62, 74, 0.10)"
SUCCESS = "#0FB594"
WARNING = "#00AECF"
ERROR = "#D72422"
INFO = "#0070C0"


def load_css() -> None:
    """Load the shared application stylesheet."""
    css_path = Path(__file__).resolve().parents[2] / "assets" / "styles.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
