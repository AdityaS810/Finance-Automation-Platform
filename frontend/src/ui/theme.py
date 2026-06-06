"""Theme helpers and color constants for the app UI."""

from __future__ import annotations

from pathlib import Path

import streamlit as st


APP_BACKGROUND = "#F6F9FC"
SIDEBAR_BACKGROUND = "#0A2540"
SIDEBAR_ACTIVE = "#635BFF"
PRIMARY_BUTTON = "#635BFF"
PRIMARY_BUTTON_HOVER = "#5147E5"
CARD_BACKGROUND = "#FFFFFF"
MAIN_HEADING = "#0A2540"
BODY_TEXT = "#425466"
MUTED_TEXT = "#6B7C93"
BORDER = "#E6EBF1"
SUCCESS = "#00A86B"
WARNING = "#F59E0B"
ERROR = "#DC2626"
INFO = "#0EA5E9"


def load_css() -> None:
    """Load the shared application stylesheet."""
    css_path = Path(__file__).resolve().parents[2] / "assets" / "styles.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
