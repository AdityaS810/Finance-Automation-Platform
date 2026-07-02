"""MIS Report page."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

from backend.reports.mis_report_generator import generate_mis_report
from backend.reports.report_periods import (
    get_financial_year_dates,
    get_fy_label,
    get_month_periods,
)
from src.ui import file_summary_card, load_css, metric_card, page_header, section_card


load_css()

page_header(
    "MIS Report",
    "Generate dynamic MIS P&L reports for a full FY, month, quarter, half year, or custom date range.",
)

financial_year_options = [2024, 2025, 2026]
period_type_options = {
    "Full Year": "full_year",
    "Month": "month",
    "Quarter": "quarter",
    "Half Year": "half_year",
    "Custom Date Range": "custom",
}
organization_options = {
    "All Organizations": "all",
    "India - Midoffice Data Solutions Private Limited": "india",
    "US - Midoffice Data International, Inc": "us",
}

selector_columns = st.columns([1.1, 1.1, 1.5, 1], vertical_alignment="bottom")
with selector_columns[0]:
    selected_financial_year_start = st.selectbox(
        "Financial Year",
        options=financial_year_options,
        index=1,
        format_func=get_fy_label,
    )
with selector_columns[1]:
    selected_period_type_label = st.selectbox("Period Type", options=list(period_type_options), index=0)
with selector_columns[2]:
    selected_organization_label = st.selectbox("Organization", options=list(organization_options), index=0)

selected_period_type = period_type_options[selected_period_type_label]
selected_month = None
selected_quarter = None
selected_half = None
custom_start_date = None
custom_end_date = None

fy_start_date, fy_end_date = get_financial_year_dates(selected_financial_year_start)
month_periods = get_month_periods(selected_financial_year_start)

if selected_period_type != "full_year":
    detail_columns = st.columns([1, 1], vertical_alignment="bottom")
    if selected_period_type == "month":
        with detail_columns[0]:
            selected_month = st.selectbox(
                "Month",
                options=month_periods,
                format_func=lambda month: month["title_label"],
            )["month_number"]
    elif selected_period_type == "quarter":
        with detail_columns[0]:
            selected_quarter = st.selectbox("Quarter", options=["Q1", "Q2", "Q3", "Q4"], index=0)
    elif selected_period_type == "half_year":
        with detail_columns[0]:
            selected_half = st.selectbox("Half Year", options=["H1", "H2"], index=0)
    elif selected_period_type == "custom":
        with detail_columns[0]:
            custom_start_date = st.date_input(
                "Start Date",
                value=fy_start_date,
                min_value=fy_start_date,
                max_value=fy_end_date,
            )
        with detail_columns[1]:
            custom_end_date = st.date_input(
                "End Date",
                value=fy_end_date,
                min_value=fy_start_date,
                max_value=fy_end_date,
            )

with selector_columns[3]:
    generate_clicked = st.button("Generate MIS Report", type="primary", use_container_width=True)

if generate_clicked:
    try:
        report_output_dir = Path(__file__).resolve().parents[2] / "outputs"
        result = generate_mis_report(
            selected_financial_year_start,
            report_output_dir,
            org_filter=organization_options[selected_organization_label],
            period_type=selected_period_type,
            selected_month=selected_month,
            selected_quarter=selected_quarter,
            selected_half=selected_half,
            custom_start_date=custom_start_date,
            custom_end_date=custom_end_date,
        )
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
        ("Revenue", mis_result["metrics"]["Revenue"], "Selected reporting window", "Success", "RV"),
        ("Expenses", mis_result["metrics"]["Expenses"], "Selected reporting window", "Warning", "EX"),
        ("Profit", mis_result["metrics"]["Profit"], "Revenue less expenses", "Success", "PF"),
        ("Currency", mis_result["metrics"]["Reporting Currency"], "Consolidated reporting", "Info", "INR"),
        ("Journal Adjustments", mis_result["metrics"]["Journal Adjustments"], "Currently derived from detail data", "Info", "JA"),
        ("Invoices", mis_result["metrics"]["Invoices"], "Selected period source rows", "Info", "IN"),
        ("Bills", mis_result["metrics"]["Bills"], "Selected period source rows", "Info", "BL"),
        ("Contacts", mis_result["metrics"]["Contacts"], "Dashboard fallback count", "Info", "CT"),
    ]

    metric_columns = st.columns(4)
    for index, item in enumerate(metric_items):
        with metric_columns[index % 4]:
            metric_card(*item)

report_card_columns = st.columns([1.2, 0.9])
with report_card_columns[0]:
    section_card(
        "Latest Report",
        body_html="<p>The MIS workbook now uses the selected financial year and reporting period to build dynamic headers, month columns, and output filenames.</p>",
    )
    if mis_result and mis_result.get("report_path") and Path(mis_result["report_path"]).exists():
        report_path = mis_result["report_path"]
        file_path = Path(report_path)
        generated_time = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%d %b %Y, %I:%M %p")
        file_summary_card(file_path.name, "MIS Report")
        st.caption(f"Report period: {mis_result['report_period']['header_title']}")
        st.caption(f"Generated time: {generated_time}")
    elif mis_error:
        section_card(
            "Generation Failed",
            body_html="<p>The report could not be generated from the current MIS data sources.</p>",
        )
        st.error(mis_error)
    else:
        section_card(
            "Ready to Generate",
            body_html="<p>Select a financial year and reporting period, then generate the report.</p>",
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
        body_html="<p>Preview the line-item values written into the MIS workbook for the selected reporting period.</p>",
    )
    st.dataframe(mis_result["monthly_preview"], use_container_width=True, hide_index=True)
