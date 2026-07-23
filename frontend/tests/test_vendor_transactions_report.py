"""Tests for the Vendor Payments & Transactions workbook."""

from __future__ import annotations

from datetime import date

import pandas as pd
from openpyxl import load_workbook

from backend.reports.vendor_transactions_report import (
    BILL_COLUMNS,
    PAYMENT_AVAILABILITY_NOTE,
    TRANSACTION_COLUMNS,
    TRANSACTION_LINKAGE_UNAVAILABLE_NOTE,
    TRANSACTION_QUERY_COLUMN_MAP,
    _build_transactions_query,
    _rename_query_columns,
    create_vendor_report_workbook,
    safe_exception_details,
)


def _sample_bills() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Organization": "Midoffice India",
                "Country": "IN",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Bill ID": "bill-1",
                "Bill Number": "B-100",
                "Bill Date": date(2026, 7, 1),
                "Due Date": date(2026, 7, 31),
                "Status": "open",
                "Currency": "INR",
                "Bill Amount": 12500,
                "Outstanding Balance": 4500,
                "Source Record ID": "bill-1",
            }
        ]
    )


def _sample_transactions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Organization": "Midoffice India",
                "Vendor/Contact ID": "vendor-1",
                "Vendor/Contact Name": "Example Vendor",
                "Transaction ID": "txn-1",
                "Transaction Date": date(2026, 7, 2),
                "Transaction Type": "expense",
                "Reference Number": "REF-1",
                "Description": "Explicitly linked transaction",
                "Currency": "INR",
                "Amount": 8000,
                "Amount in INR": 8000,
                "Status": "posted",
                "Source Record ID": "txn-1",
            }
        ]
    )


def test_vendor_report_workbook_can_be_created_and_saved(workspace_tmp_path):
    workbook = create_vendor_report_workbook(_sample_bills(), _sample_transactions())
    report_path = workspace_tmp_path / "vendor_report.xlsx"

    workbook.save(report_path)
    reopened = load_workbook(report_path, data_only=False)

    assert report_path.exists()
    assert reopened["Vendor Bills"].max_row == 4
    assert reopened["Vendor Transactions"].max_row == 4


def test_vendor_report_has_expected_sheet_names():
    workbook = create_vendor_report_workbook(_sample_bills(), _sample_transactions())

    assert workbook.sheetnames == [
        "Summary",
        "Vendor Bills",
        "Vendor Transactions",
        "Data Availability",
    ]


def test_missing_payment_fields_are_handled_without_paid_amount_claims():
    bills_without_payment_fields = _sample_bills()
    transactions_without_payment_fields = _sample_transactions().drop(columns=["Amount in INR"])

    workbook = create_vendor_report_workbook(bills_without_payment_fields, transactions_without_payment_fields)
    bill_headers = [cell.value for cell in workbook["Vendor Bills"][3]]
    transaction_headers = [cell.value for cell in workbook["Vendor Transactions"][3]]
    availability_text = " ".join(
        str(cell.value or "") for row in workbook["Data Availability"].iter_rows() for cell in row
    )

    assert "Paid Amount" not in bill_headers
    assert bill_headers == BILL_COLUMNS
    assert transaction_headers == TRANSACTION_COLUMNS
    assert PAYMENT_AVAILABILITY_NOTE in availability_text
    assert workbook["Vendor Transactions"]["K4"].value is None


def test_empty_vendor_data_creates_downloadable_workbook(workspace_tmp_path):
    workbook = create_vendor_report_workbook(pd.DataFrame(), pd.DataFrame())
    report_path = workspace_tmp_path / "empty_vendor_report.xlsx"
    workbook.save(report_path)

    reopened = load_workbook(report_path, data_only=False)

    assert reopened["Summary"]["B12"].value == 0
    assert reopened["Summary"]["B13"].value == 0
    assert [cell.value for cell in reopened["Vendor Bills"][3]] == BILL_COLUMNS
    assert [cell.value for cell in reopened["Vendor Transactions"][3]] == TRANSACTION_COLUMNS


def test_transaction_query_uses_bigquery_safe_result_field_names():
    live_transaction_fields = {
        "source_org_name",
        "source_record_id",
        "transaction_id",
        "transaction_number",
        "transaction_date",
        "reference_number",
        "status",
        "notes",
        "original_currency",
        "transaction_amount",
        "raw_json",
        "amount_inr",
    }

    query = _build_transactions_query("project-1", live_transaction_fields)

    assert "AS `Vendor/Contact ID`" not in query
    assert "AS `Vendor/Contact Name`" not in query
    assert "vendor_contact_id" in query
    assert "vendor_contact_name" in query


def test_bigquery_safe_transaction_fields_are_renamed_for_excel():
    query_dataframe = pd.DataFrame(
        [{column_name: None for column_name in TRANSACTION_QUERY_COLUMN_MAP}]
    )

    report_dataframe = _rename_query_columns(query_dataframe, TRANSACTION_QUERY_COLUMN_MAP)

    assert list(report_dataframe.columns) == TRANSACTION_COLUMNS


def test_long_identifiers_are_formatted_as_excel_text():
    workbook = create_vendor_report_workbook(_sample_bills(), _sample_transactions())

    assert workbook["Vendor Bills"]["C4"].number_format == "@"
    assert workbook["Vendor Bills"]["E4"].number_format == "@"
    assert workbook["Vendor Bills"]["M4"].number_format == "@"
    assert workbook["Vendor Transactions"]["D4"].number_format == "@"
    assert workbook["Vendor Transactions"]["M4"].number_format == "@"


def test_unavailable_transaction_vendor_linkage_is_explicit():
    transactions = _sample_transactions().copy()
    transactions[["Vendor/Contact ID", "Vendor/Contact Name"]] = None

    workbook = create_vendor_report_workbook(
        _sample_bills(),
        transactions,
        availability={"transaction_vendor_fields_available": False},
    )
    availability_text = " ".join(
        str(cell.value or "") for row in workbook["Data Availability"].iter_rows() for cell in row
    )

    assert "Transaction vendor/contact linkage Unavailable" in availability_text
    assert TRANSACTION_LINKAGE_UNAVAILABLE_NOTE in availability_text
    assert workbook["Vendor Transactions"]["B4"].value is None
    assert workbook["Vendor Transactions"]["C4"].value is None


def test_safe_exception_details_keeps_error_and_redacts_credentials():
    error = RuntimeError(
        "Invalid field name Vendor/Contact ID; access_token=secret-value; Authorization:Bearer abc123"
    )

    details = safe_exception_details(error)

    assert "Invalid field name Vendor/Contact ID" in details
    assert "secret-value" not in details
    assert "abc123" not in details
    assert "[redacted]" in details
