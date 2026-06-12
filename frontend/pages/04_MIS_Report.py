"""MIS Report page."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

from backend.reports.mis_report_generator import generate_mis_report, get_mis_metrics
from src.ui import file_summary_card, load_css, metric_card, page_header, section_card


load_css()

page_header(
    "MIS Report",
    "Generate and download FY25-26 MIS P&L reports.",
)

financial_year_options = ["FY24-25", "FY25-26", "FY26-27"]

selector_columns = st.columns([1, 1.2], vertical_alignment="bottom")
with selector_columns[0]:
    selected_financial_year = st.selectbox("Financial Year", options=financial_year_options, index=1)
with selector_columns[1]:
    generate_clicked = st.button("Generate MIS Report", type="primary", use_container_width=True)

if generate_clicked:
    try:
        report_output_dir = Path(__file__).resolve().parents[1] / "outputs" / "reports"
        result = generate_mis_report(selected_financial_year, report_output_dir)
        st.session_state["mis_report_result"] = result
        st.success(result["message"])
    except Exception as error:
        st.error(f"MIS report generation failed: {error}")

mis_result = st.session_state.get("mis_report_result") or {
    "metrics": get_mis_metrics(selected_financial_year),
    "report_path": None,
    "is_placeholder": True,
}

metric_items = [
    ("Gross Margin FY", mis_result["metrics"]["Gross Margin FY"], "Current view", "Success", "GM"),
    ("Monthly OpEx Run-rate", mis_result["metrics"]["Monthly OpEx Run-rate"], "Operating cost trend", "Info", "OE"),
    ("Monthly Gross Burn", mis_result["metrics"]["Monthly Gross Burn"], "Cash usage view", "Warning", "GB"),
    ("Revenue per India FTE", mis_result["metrics"]["Revenue per India FTE"], "Productivity measure", "Success", "RF"),
    ("Total People Cost", mis_result["metrics"]["Total People Cost"], "People spend summary", "Info", "PC"),
    ("Closing Cash", mis_result["metrics"]["Closing Cash"], "Closing liquidity", "Success", "CC"),
    ("Cash Runway", mis_result["metrics"]["Cash Runway"], "Projected runway", "Warning", "CR"),
]

metric_columns = st.columns(4)
for index, item in enumerate(metric_items):
    with metric_columns[index % 4]:
        metric_card(*item)

report_path = mis_result.get("report_path")
report_card_columns = st.columns([1.2, 0.9])
with report_card_columns[0]:
    section_card(
        "Latest Report",
        body_html="<p>The most recent generated MIS output is available below when a file exists.</p>",
    )
    if report_path and Path(report_path).exists():
        file_path = Path(report_path)
        generated_time = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%d %b %Y, %I:%M %p")
        file_summary_card(file_path.name, "MIS Report")
        st.caption(f"Generated time: {generated_time}")
    else:
        section_card(
            "Report Status",
            body_html="<p>Generate the report to create a downloadable Excel file from the backend report module.</p>",
        )

    if mis_result.get("is_placeholder"):
        st.caption("Current MIS metrics still use backend placeholder values until warehouse-based reporting is completed.")

with report_card_columns[1]:
    if report_path and Path(report_path).exists():
        with open(report_path, "rb") as report_file:
            st.download_button(
                "Download Excel",
                data=report_file.read(),
                file_name=Path(report_path).name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
