"""CEO-facing dashboard page."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src.ui import insight_row, load_css, metric_card, page_header, section_card
from src.utils.file_helpers import list_output_files


load_css()

page_header(
    "Finance Automation Platform",
    "Automate data sync, MIS reporting, and reconciliation from one place.",
)

sync_result = st.session_state.get("data_sync_result")
bank_upload_result = st.session_state.get("bank_upload_result")
gstr_upload_result = st.session_state.get("gstr_upload_result")
mis_result = st.session_state.get("mis_report_result")
bank_recon_result = st.session_state.get("bank_recon_result")
gst_recon_result = st.session_state.get("gst_recon_result")

metric_items = [
    (
        "Last Data Sync",
        sync_result["completed_at"].strftime("%d %b %Y") if sync_result else "Not Run Yet",
        sync_result["duration"] if sync_result else "Use Data Sync page",
        "Success" if sync_result else "Info",
        "DS",
    ),
    (
        "Bank Statement Upload",
        str(bank_upload_result["records_parsed"]) if bank_upload_result else "No Upload",
        "Rows saved" if bank_upload_result else "Use Uploads page",
        "Success" if bank_upload_result else "Info",
        "BS",
    ),
    (
        "GSTR Upload",
        str(gstr_upload_result["records_parsed"]) if gstr_upload_result else "No Upload",
        "Rows saved" if gstr_upload_result else "Use Uploads page",
        "Success" if gstr_upload_result else "Info",
        "GR",
    ),
    (
        "MIS Report",
        mis_result["metrics"]["Financial Year"] if mis_result else "Not Generated",
        "Latest output ready" if mis_result and mis_result.get("report_path") else "Use MIS Report page",
        "Success" if mis_result and mis_result.get("report_path") else "Info",
        "MR",
    ),
    (
        "Bank Reconciliation",
        str(bank_recon_result["summary"]["matched_records"]) if bank_recon_result else "Not Run Yet",
        "Matched records" if bank_recon_result else "Use Reconciliation page",
        "Success" if bank_recon_result else "Info",
        "BR",
    ),
    (
        "GST Reconciliation",
        str(gst_recon_result["summary"]["exact_matches"]) if gst_recon_result else "Not Run Yet",
        "Exact matches" if gst_recon_result else "Use Reconciliation page",
        "Success" if gst_recon_result else "Info",
        "GS",
    ),
]

metric_columns = st.columns(6)
for column, (title, value, caption, status, icon) in zip(metric_columns, metric_items):
    with column:
        metric_card(title, value, caption=caption, status=status, icon=icon)

st.markdown("<div style='height: 0.35rem;'></div>", unsafe_allow_html=True)

project_root = Path(__file__).resolve().parents[1]
output_files = list_output_files(project_root / "outputs" / "reports") + list_output_files(project_root / "outputs" / "reconciliation_exports")

activity_rows = []

if sync_result:
    activity_rows.append(
        {
            "Activity": "Data Sync",
            "Details": sync_result["message"],
            "Status": "Success",
            "Time": sync_result["completed_at"].strftime("%d %b %Y, %I:%M %p"),
        }
    )

if bank_upload_result:
    activity_rows.append(
        {
            "Activity": "Bank Statement Upload",
            "Details": f"{bank_upload_result['records_parsed']} rows saved",
            "Status": "Success",
            "Time": "Current session",
        }
    )

if gstr_upload_result:
    activity_rows.append(
        {
            "Activity": "GSTR Upload",
            "Details": f"{gstr_upload_result['records_parsed']} rows saved",
            "Status": "Success",
            "Time": "Current session",
        }
    )

if mis_result and mis_result.get("report_path"):
    activity_rows.append(
        {
            "Activity": "MIS Report",
            "Details": Path(mis_result["report_path"]).name,
            "Status": "Success",
            "Time": "Current session",
        }
    )

if gst_recon_result:
    activity_rows.append(
        {
            "Activity": "GST Reconciliation",
            "Details": gst_recon_result["message"],
            "Status": "In Progress",
            "Time": "Current session",
        }
    )

if output_files:
    latest_output = output_files[0]
    activity_rows.insert(
        0,
        {
            "Activity": "Latest Output",
            "Details": latest_output["file_name"],
            "Status": "Ready",
            "Time": datetime.fromtimestamp(latest_output["last_modified"]).strftime("%d %b %Y, %I:%M %p"),
        },
    )

if not activity_rows:
    activity_rows.append(
        {
            "Activity": "Getting Started",
            "Details": "Run a sync, upload files, or generate a report to see activity here.",
            "Status": "Info",
            "Time": "Not available yet",
        }
    )

content_columns = st.columns([1.3, 0.95])
with content_columns[0]:
    section_card(
        "Recent Activity / Latest Outputs",
        body_html="<p>A concise view of the most recent finance operations activity.</p>",
    )
    st.dataframe(pd.DataFrame(activity_rows), use_container_width=True, hide_index=True)

with content_columns[1]:
    section_card(
        "Key Insights",
        body_html="<p>Headline finance indicators for leadership review.</p>",
    )
    insight_row("Closing Cash", "INR 8.64 Cr", change="+12.4% vs LY", status="success")
    insight_row("Monthly Gross Burn", "INR 1.27 Cr", change="+3.6% vs LY", status="warning")
    insight_row("Cash Runway", "6.8 Months", change="Stable", status="info")
    insight_row("Revenue per India FTE", "INR 24.6 Lakh", change="+8.2% vs LY", status="success")
    insight_row("Gross Margin FY", "31.6%", change="+2.1% vs LY", status="success")
