"""Repo-root Streamlit entrypoint for the Finance Automation Platform."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


APP_ROOT = Path(__file__).resolve().parent
FRONTEND_ROOT = APP_ROOT / "frontend"

if str(FRONTEND_ROOT) not in sys.path:
    sys.path.insert(0, str(FRONTEND_ROOT))

from src.ui import load_css
from src.utils.env_loader import load_environment_variables
from src.utils.file_helpers import ensure_directories


load_environment_variables()
ensure_directories()

st.set_page_config(
    page_title="Finance Automation Platform",
    page_icon=":bar_chart:",
    layout="wide",
    initial_sidebar_state="expanded",
)

load_css()

navigation = st.navigation(
    [
        st.Page("frontend/pages/01_Dashboard.py", title="Dashboard", default=True),
        st.Page("frontend/pages/02_Data_Sync.py", title="Data Sync"),
        st.Page("frontend/pages/03_Uploads.py", title="Uploads"),
        st.Page("frontend/pages/04_MIS_Report.py", title="MIS Report"),
        st.Page("frontend/pages/05_Reconciliation.py", title="Reconciliation"),
        st.Page("frontend/pages/06_Downloads.py", title="Downloads"),
    ],
    position="sidebar",
)

navigation.run()
