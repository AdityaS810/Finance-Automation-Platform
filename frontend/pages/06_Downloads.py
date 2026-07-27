"""Downloads page."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from backend.reports.vendor_transactions_report import (
    fetch_vendor_filter_options,
    generate_vendor_transactions_report,
    safe_exception_details,
)
from src.ui import file_summary_card, load_css, page_header, section_card
from src.utils.cloud_clients import get_bigquery_client
from src.utils.file_helpers import get_output_root, list_output_files


load_css()

page_header(
    "Downloads",
    "Generate vendor workbooks and download MIS or reconciliation outputs.",
)

frontend_root = Path(__file__).resolve().parents[1]
repo_root = Path(__file__).resolve().parents[2]


EXPORT_GROUPS = [
    {
        "title": "Vendor Reconciliation",
        "label": "Vendor Reconciliation Report",
        "empty": "No vendor reconciliation reports are available yet.",
        "keywords": ["vendor_reconciliation"],
    },
    {
        "title": "MIS Reports",
        "label": "MIS Report",
        "empty": "No MIS reports are available yet.",
        "keywords": ["mis"],
    },
    {
        "title": "Bank Reconciliation",
        "label": "Bank Reconciliation Report",
        "empty": "No bank reconciliation reports are available yet.",
        "keywords": ["bank_reconciliation", "bank reconciliation"],
    },
    {
        "title": "GST Reconciliation",
        "label": "GST Reconciliation Report",
        "empty": "No GST reconciliation reports are available yet.",
        "keywords": ["gst_reconciliation", "gst reconciliation"],
    },
    {
        "title": "Other Exports",
        "label": "Export File",
        "empty": "No other exports are available yet.",
        "keywords": [],
    },
]


def _collect_output_files() -> list[dict]:
    """Collect output files from current and legacy output folders."""
    runtime_output_root = get_output_root()
    folders = [
        runtime_output_root,
        runtime_output_root / "reports",
        runtime_output_root / "reconciliation_exports",
        frontend_root / "outputs",
        frontend_root / "outputs" / "reports",
        frontend_root / "outputs" / "reconciliation_exports",
        repo_root / "outputs",
        repo_root / "outputs" / "reports",
        repo_root / "outputs" / "reconciliation_exports",
    ]
    seen_paths = set()
    files = []

    for folder in folders:
        for item in list_output_files(folder):
            file_path = Path(item["path"]).resolve()
            if file_path in seen_paths:
                continue
            seen_paths.add(file_path)
            item["path"] = file_path
            item["extension"] = file_path.suffix.lower()
            files.append(item)

    return sorted(files, key=lambda item: item["last_modified"], reverse=True)


def _group_key(file_item: dict) -> str:
    """Classify an output file into a user-facing report group."""
    file_name = file_item["file_name"].lower()

    if "vendor_reconciliation" in file_name:
        return "Vendor Reconciliation"
    if "mis" in file_name:
        return "MIS Reports"
    if "bank_reconciliation" in file_name or "bank reconciliation" in file_name:
        return "Bank Reconciliation"
    if "gst_reconciliation" in file_name or "gst reconciliation" in file_name:
        return "GST Reconciliation"
    return "Other Exports"


def _latest_main_file(files: list[dict]) -> dict | None:
    """Prefer the latest XLSX file for the main card, then any latest file."""
    xlsx_files = [item for item in files if item["extension"] == ".xlsx"]
    if xlsx_files:
        return xlsx_files[0]
    return files[0] if files else None


def _older_files(files: list[dict], main_file: dict | None) -> list[dict]:
    """Return files that should live in the older exports expander."""
    if main_file is None:
        return files
    return [item for item in files if Path(item["path"]) != Path(main_file["path"])]


def _modified_label(file_item: dict) -> str:
    """Format modified time for cards and tables."""
    return datetime.fromtimestamp(file_item["last_modified"]).strftime("%d %b %Y, %I:%M %p")


def _mime_type(file_path: Path) -> str:
    """Return a useful download MIME type for common export files."""
    if file_path.suffix.lower() == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if file_path.suffix.lower() == ".csv":
        return "text/csv"
    return "application/octet-stream"


def _download_button(file_item: dict, label: str, key_prefix: str) -> None:
    """Render a download button for one file."""
    file_path = Path(file_item["path"])
    with open(file_path, "rb") as download_file:
        st.download_button(
            label=label,
            data=download_file.read(),
            file_name=file_path.name,
            mime=_mime_type(file_path),
            key=f"{key_prefix}_{file_path.name}_{int(file_item['last_modified'])}",
            use_container_width=True,
        )


def _older_exports_table(files: list[dict]) -> pd.DataFrame:
    """Build a compact table for older exports."""
    return pd.DataFrame(
        [
            {
                "File Name": item["file_name"],
                "Type": item["type"],
                "Last Modified": _modified_label(item),
            }
            for item in files
        ]
    )


@st.cache_data(ttl=300, show_spinner=False)
def _vendor_filter_options() -> dict[str, list[str]]:
    """Cache lightweight BigQuery filter values for the vendor report controls."""
    return fetch_vendor_filter_options(client=get_bigquery_client())


section_card(
    "Vendor Reconciliation Report",
    body_html=(
        "<p>Generate a validated workbook from verified vendor-payment, bill, allocation, "
        "bank-transaction, and reconciliation views.</p>"
    ),
)

try:
    vendor_options = _vendor_filter_options()
    filter_options_error = False
except Exception:
    vendor_options = {
        "organizations": [],
        "vendors": [],
        "currencies": [],
        "source_bill_statuses": [],
        "reconciliation_statuses": [],
        "bank_match_statuses": [],
    }
    filter_options_error = True

today = date.today()
filter_row_one = st.columns(2)
with filter_row_one[0]:
    vendor_start_date = st.date_input(
        "Start date",
        value=today.replace(month=1, day=1),
        key="vendor_report_start_date",
    )
with filter_row_one[1]:
    vendor_end_date = st.date_input(
        "End date",
        value=today,
        key="vendor_report_end_date",
    )

filter_row_two = st.columns(3)
with filter_row_two[0]:
    selected_organization = st.selectbox(
        "Organization",
        options=[None, *vendor_options["organizations"]],
        format_func=lambda value: value or "All Organizations",
        key="vendor_report_organization",
    )
with filter_row_two[1]:
    selected_vendor = st.selectbox(
        "Vendor",
        options=[None, *vendor_options["vendors"]],
        format_func=lambda value: value or "All Vendors",
        key="vendor_report_vendor",
    )
with filter_row_two[2]:
    selected_currency = st.selectbox(
        "Currency",
        options=[None, *vendor_options["currencies"]],
        format_func=lambda value: value or "All Currencies",
        key="vendor_report_currency",
    )

filter_row_three = st.columns(3)
with filter_row_three[0]:
    selected_source_bill_status = st.selectbox(
        "Source Bill Status",
        options=[None, *vendor_options["source_bill_statuses"]],
        format_func=lambda value: value or "All Source Statuses",
        key="vendor_report_source_bill_status",
    )
with filter_row_three[1]:
    selected_reconciliation_status = st.selectbox(
        "Reconciliation Status",
        options=[None, *vendor_options["reconciliation_statuses"]],
        format_func=lambda value: value or "All Reconciliation Statuses",
        key="vendor_report_reconciliation_status",
    )
with filter_row_three[2]:
    selected_bank_match_status = st.selectbox(
        "Bank Match Status",
        options=[None, *vendor_options["bank_match_statuses"]],
        format_func=lambda value: value or "All Bank Match Statuses",
        key="vendor_report_bank_match_status",
    )

selected_review_required = st.selectbox(
    "Review Required",
    options=[None, True, False],
    format_func=lambda value: (
        "All Review States"
        if value is None
        else "Yes"
        if value
        else "No"
    ),
    key="vendor_report_review_required",
)

if filter_options_error:
    st.caption(
        "Live filter values are temporarily unavailable. You can still generate the report "
        "after BigQuery credentials are available."
    )

if st.button(
    "Prepare Vendor Reconciliation Excel",
    type="primary",
    key="generate_vendor_reconciliation_report",
):
    if vendor_start_date > vendor_end_date:
        st.session_state["vendor_report_error"] = "Start date must be on or before end date."
        st.session_state.pop("vendor_report_result", None)
        st.session_state.pop("vendor_report_error_details", None)
    else:
        try:
            with st.spinner("Generating vendor workbook from BigQuery..."):
                result = generate_vendor_transactions_report(
                    start_date=vendor_start_date,
                    end_date=vendor_end_date,
                    organization=selected_organization,
                    vendor=selected_vendor,
                    currency=selected_currency,
                    source_bill_status=selected_source_bill_status,
                    reconciliation_status=selected_reconciliation_status,
                    bank_match_status=selected_bank_match_status,
                    review_required=selected_review_required,
                    client=get_bigquery_client(),
                )
            st.session_state["vendor_report_result"] = result
            st.session_state.pop("vendor_report_error", None)
            st.session_state.pop("vendor_report_error_details", None)
        except Exception as error:
            st.session_state["vendor_report_error"] = "The vendor report could not be generated. Please try again."
            st.session_state["vendor_report_error_details"] = safe_exception_details(error)
            st.session_state.pop("vendor_report_result", None)

vendor_report_error = st.session_state.get("vendor_report_error")
vendor_report_error_details = st.session_state.get("vendor_report_error_details")
vendor_report_result = st.session_state.get("vendor_report_result")
if vendor_report_error:
    st.error(vendor_report_error)
    if vendor_report_error_details:
        with st.expander("Technical details", expanded=False):
            st.code(vendor_report_error_details, language="text")
elif vendor_report_result:
    summary = vendor_report_result["summary"]
    reconciliation_counts = summary["reconciliation_counts"]
    count_columns = st.columns(6)
    count_columns[0].metric("Bills", f"{summary['total_bills']:,}")
    count_columns[1].metric("Vendor Payments", f"{summary['total_vendor_payments']:,}")
    count_columns[2].metric(
        "Fully Reconciled",
        f"{reconciliation_counts.get('Fully Reconciled', 0):,}",
    )
    count_columns[3].metric(
        "Bank Pending",
        f"{reconciliation_counts.get('Payment Recorded - Bank Pending', 0):,}",
    )
    count_columns[4].metric(
        "Unpaid",
        f"{reconciliation_counts.get('Unpaid - No Payment Found', 0):,}",
    )
    count_columns[5].metric(
        "Needs Review",
        f"{reconciliation_counts.get('Needs Review', 0):,}",
    )

    if (
        selected_vendor
        and summary["total_bills"] == 0
        and summary["total_vendor_payments"] == 0
    ):
        st.info(
            "No bills or vendor payments were found for this vendor in the selected period."
        )

    if vendor_report_result["total_rows"] == 0:
        st.info(
            "No vendor reconciliation records matched the selected filters. "
            "The workbook still contains all required sheets and headers."
        )
    else:
        st.success(vendor_report_result["message"])

    report_name = vendor_report_result["report_name"]
    st.download_button(
        "Download Vendor Reconciliation Excel",
        data=vendor_report_result["report_bytes"],
        file_name=report_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"download_vendor_report_{report_name}",
        use_container_width=True,
    )
else:
    st.info(
        "Choose filters and prepare the workbook. Empty selections include all available values."
    )


all_files = _collect_output_files()
grouped_files = {group["title"]: [] for group in EXPORT_GROUPS}
for file_item in all_files:
    grouped_files[_group_key(file_item)].append(file_item)

section_card(
    "Latest Downloads",
    body_html="<p>Each section shows the latest XLSX export first. Older and CSV exports remain available in details. Consume outputs are final business-ready files.</p>",
)

for group in EXPORT_GROUPS:
    files = grouped_files[group["title"]]
    section_card(group["title"], body_html=f"<p>{group['label']} downloads.</p>")

    if not files:
        st.info(group["empty"])
        continue

    main_file = _latest_main_file(files)
    older = _older_files(files, main_file)

    if main_file:
        file_summary_card(main_file["file_name"], group["label"])
        st.caption(f"Last modified: {_modified_label(main_file)}")
        _download_button(main_file, f"Download {group['label']}", f"latest_{group['title']}")

    if older:
        with st.expander("Show older exports"):
            st.dataframe(_older_exports_table(older), use_container_width=True, hide_index=True)
            for older_file in older:
                _download_button(older_file, f"Download {older_file['file_name']}", f"older_{group['title']}")
