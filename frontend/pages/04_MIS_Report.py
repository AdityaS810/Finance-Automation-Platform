"""MIS Report page."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

from backend.reports.mis_report_generator import generate_mis_report
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
        report_output_dir = Path(__file__).resolve().parents[2] / "outputs"
        result = generate_mis_report(selected_financial_year, report_output_dir)
        st.session_state["mis_report_result"] = result
        st.session_state.pop("mis_report_error", None)
        st.success(result["message"])
    except Exception as error:
        st.session_state["mis_report_error"] = str(error)
        st.session_state.pop("mis_report_result", None)

mis_result = st.session_state.get("mis_report_result")
mis_error = st.session_state.get("mis_report_error")

if mis_result:
    metric_items = [
        ("Revenue", mis_result["metrics"]["Revenue"], "Gold monthly P&L", "Success", "RV"),
        ("Expenses", mis_result["metrics"]["Expenses"], "Gold monthly P&L", "Warning", "EX"),
        ("Profit", mis_result["metrics"]["Profit"], "Revenue less expenses", "Success", "PF"),
        ("Journal Adjustments", mis_result["metrics"]["Journal Adjustments"], "Gold journal totals", "Info", "JA"),
        ("Invoices", mis_result["metrics"]["Invoices"], "Dashboard KPI count", "Info", "IN"),
        ("Bills", mis_result["metrics"]["Bills"], "Dashboard KPI count", "Info", "BL"),
        ("Contacts", mis_result["metrics"]["Contacts"], "Dashboard KPI count", "Info", "CT"),
    ]

    metric_columns = st.columns(4)
    for index, item in enumerate(metric_items):
        with metric_columns[index % 4]:
            metric_card(*item)

report_card_columns = st.columns([1.2, 0.9])
with report_card_columns[0]:
    section_card(
        "Latest Report",
        body_html="<p>The MIS workbook is generated in the company MIS format from BigQuery Gold and Zoho-backed data.</p>",
    )
    if mis_result and mis_result.get("report_path") and Path(mis_result["report_path"]).exists():
        report_path = mis_result["report_path"]
        file_path = Path(report_path)
        generated_time = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%d %b %Y, %I:%M %p")
        file_summary_card(file_path.name, "MIS Report")
        st.caption(f"Generated time: {generated_time}")
    elif mis_error:
        section_card(
            "Generation Failed",
            body_html="<p>The report could not be generated from BigQuery Gold layer data.</p>",
        )
        st.error(mis_error)
    else:
        section_card(
            "Ready to Generate",
            body_html="<p>Select a financial year and generate the report to query the Gold layer.</p>",
        )

with report_card_columns[1]:
    if mis_result and mis_result.get("report_path") and Path(mis_result["report_path"]).exists():
        report_path = mis_result["report_path"]
        with open(report_path, "rb") as report_file:
            st.download_button(
                "Download Excel",
                data=report_file.read(),
                file_name=Path(report_path).name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

if mis_result:
    section_card(
        "Monthly MIS Preview",
        body_html="<p>Preview the line-item values written into the CEO-format MIS workbook from Zoho and BigQuery-backed data.</p>",
    )
    st.dataframe(mis_result["monthly_preview"], use_container_width=True, hide_index=True)
