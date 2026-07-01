"""Data Sync page."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import streamlit as st

from backend.services.sync_service import run_zoho_sync
from src.ui import load_css, page_header, section_card
from src.utils.file_helpers import ensure_directories


ensure_directories()
load_css()

page_header(
    "Data Sync",
    "Sync latest accounting data from Zoho Books into BigQuery.",
)

default_end_date = date.today()
default_start_date = default_end_date - timedelta(days=30)

section_card(
    "Sync Window",
    body_html="<p>Select the accounting period to bring into the reporting warehouse.</p>",
)

date_columns = st.columns([1, 1, 1.1], vertical_alignment="bottom")
with date_columns[0]:
    start_date = st.date_input("From Date", value=default_start_date)
with date_columns[1]:
    end_date = st.date_input("To Date", value=default_end_date)
with date_columns[2]:
    sync_clicked = st.button("Sync Latest Data", type="primary", use_container_width=True)

if sync_clicked:
    started_at = datetime.now()
    try:
        result = run_zoho_sync(start_date, end_date)
        completed_at = datetime.now()
        duration_seconds = max(int((completed_at - started_at).total_seconds()), 1)
        st.session_state["data_sync_result"] = {
            **result,
            "completed_at": completed_at,
            "duration": f"{duration_seconds} sec",
        }
        st.session_state.pop("data_sync_error", None)
    except Exception as error:
        st.session_state["data_sync_error"] = str(error)
        st.session_state.pop("data_sync_result", None)

sync_result = st.session_state.get("data_sync_result")
sync_error = st.session_state.get("data_sync_error")

if sync_result:
    section_card(
        "Sync Successful",
        body_html=(
            f"<p>Completed at <strong>{sync_result['completed_at']:%d %b %Y, %I:%M %p}</strong></p>"
            f"<p>Duration <strong>{sync_result['duration']}</strong></p>"
            "<p>US and India Zoho organizations are synced separately, then consolidated in INR for reporting.</p>"
        ),
    )
    st.success(sync_result["message"])
    organization_columns = st.columns(len(sync_result.get("organizations", [])) or 1)
    for index, organization in enumerate(sync_result.get("organizations", [])):
        with organization_columns[index]:
            st.metric(
                organization["org_key"].upper(),
                organization["base_currency"],
                help=organization["organization_name"],
            )
elif sync_error:
    section_card(
        "Sync Not Completed",
        body_html=(
            "<p>The backend sync could not run.</p>"
            "<p>Add the required Zoho and GCP environment variables, then try again.</p>"
        ),
    )
    st.error(sync_error)
else:
    section_card(
        "Integration Status",
        body_html=(
            "<p>Use the button above to run the existing backend Zoho sync.</p>"
            "<p>The current backend still fetches the latest available Zoho records even though the UI shows a date window.</p>"
        ),
    )

section_card(
    "Row Counts by Organization",
    body_html="<p>Latest available volume summary by Zoho organization and entity. Consolidated reporting uses INR values.</p>",
)
if sync_result:
    st.dataframe(sync_result["row_counts"], use_container_width=True, hide_index=True)
else:
    st.info("No backend sync has been run in this session yet.")
