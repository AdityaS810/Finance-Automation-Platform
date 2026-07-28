"""Tests for the simplified Phase 5 Vendor Reconciliation workbook."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from openpyxl import load_workbook

from backend.reports import vendor_transactions_report as vendor_report
from backend.reports.vendor_transactions_report import (
    ANALYSIS_DISCLAIMER,
    BANK_TRANSACTION_COLUMNS,
    DATASET_COLUMNS,
    INPUT_BANK_TRANSACTION_COLUMNS,
    INPUT_DATASET_COLUMNS,
    INPUT_PAYMENT_ALLOCATION_COLUMNS,
    INPUT_VENDOR_BILL_COLUMNS,
    INPUT_VENDOR_PAYMENT_COLUMNS,
    INPUT_WORKBOOK_SHEETS,
    PAYMENT_ALLOCATION_COLUMNS,
    REPORT_MODE_ANALYSIS,
    REPORT_MODE_INPUT,
    RECONCILIATION_RESULT_COLUMNS,
    VENDOR_BILL_COLUMNS,
    VENDOR_PAYMENT_COLUMNS,
    WORKBOOK_SHEETS,
    ZOHO_PAYMENT_BATCH_COLUMNS,
    VendorReportValidationError,
    _query_parameters,
    _query_to_dataframe,
    build_reconciliation_input_queries,
    build_vendor_reconciliation_queries,
    combine_zoho_payment_batch,
    create_reconciliation_input_workbook,
    create_vendor_report_workbook,
    generate_vendor_transactions_report,
    safe_exception_details,
    summarize_reconciliation_input,
    summarize_vendor_report,
    validate_reconciliation_input_data,
    validate_vendor_report_data,
)


def _sample_datasets() -> dict[str, pd.DataFrame]:
    datasets = {
        name: pd.DataFrame(columns=columns)
        for name, columns in DATASET_COLUMNS.items()
    }
    datasets["vendor_bills"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Bill ID": "bill-000000000001",
                "Bill Number": "B-100",
                "Bill Date": date(2026, 7, 1),
                "Due Date": date(2026, 7, 31),
                "Currency": "INR",
                "Bill Amount": 100,
                "Outstanding Balance": 0,
                "Source Bill Status": "paid",
                "Allocated Payment Count": 1,
                "Allocated Amount": 100,
                "Bank-Verified Amount": 0,
                "Remaining Reconciliation Amount": 100,
                "Reconciliation Status": "Payment Recorded - Bank Pending",
                "Reconciliation Reason": "Payment is pending bank review",
                "Review Required": True,
                "Source Record ID": "source-bill-1",
                "Data Quality Status": "Valid",
            }
        ],
        columns=VENDOR_BILL_COLUMNS,
    )
    datasets["vendor_payments"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Payment ID": "payment-000000000001",
                "Payment Number": "P-100",
                "Payment Date": date(2026, 7, 2),
                "Reference Number": "REF-1",
                "Payment Mode": "banktransfer",
                "Currency": "INR",
                "Payment Amount": 100,
                "Allocated Amount": 100,
                "Unapplied Amount": 0,
                "Allocation Status": "Fully Allocated",
                "Paid-Through Account Name": "Operating Account",
                "Bank Match Status": "Bank Match Pending Review",
                "Bank Match Method": "exact_account_amount_date_window_review",
                "Bank Match Reason": "Manual review required",
                "Bank Transaction Date": date(2026, 7, 3),
                "Bank Amount": 100,
                "Review Required": True,
                "Source Record ID": "source-payment-1",
                "Bank Transaction Leg Key": "leg-debit",
                "Data Quality Status": "Fully Allocated",
            }
        ],
        columns=VENDOR_PAYMENT_COLUMNS,
    )
    datasets["payment_allocations"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Payment ID": "payment-000000000001",
                "Bill ID": "bill-000000000001",
                "Bill Number": "B-100",
                "Bill Payment ID": "bill-payment-1",
                "Currency": "INR",
                "Amount Applied": 100,
                "Payment Date": date(2026, 7, 2),
                "Allocation Key": "allocation-1",
                "Source Record ID": "source-payment-1",
                "Data Quality Status": "Valid",
            }
        ],
        columns=PAYMENT_ALLOCATION_COLUMNS,
    )
    datasets["bank_transactions"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Account Label": "Operating Account",
                "Transaction ID": "transaction-1",
                "Transaction Date": date(2026, 7, 3),
                "Transaction Type": "transfer",
                "Debit/Credit": "debit",
                "Direction": "Outgoing",
                "Currency": "INR",
                "Amount": 100,
                "Signed Amount": -100,
                "Status": "cleared",
                "Multi-Leg Transaction": True,
                "Used in Vendor Match": True,
                "Review Required": True,
                "Source Record ID": "source-bank-1",
                "Bank Transaction Leg Key": "leg-debit",
                "Match Method": "bank_transaction_leg",
                "Data Quality Status": "Valid",
            },
            {
                "Organization": "india",
                "Account Label": "Savings Account",
                "Transaction ID": "transaction-1",
                "Transaction Date": date(2026, 7, 3),
                "Transaction Type": "transfer",
                "Debit/Credit": "credit",
                "Direction": "Incoming",
                "Currency": "INR",
                "Amount": 100,
                "Signed Amount": 100,
                "Status": "cleared",
                "Multi-Leg Transaction": True,
                "Used in Vendor Match": False,
                "Review Required": False,
                "Source Record ID": "source-bank-1",
                "Bank Transaction Leg Key": "leg-credit",
                "Match Method": "bank_transaction_leg",
                "Data Quality Status": "Valid",
            },
        ],
        columns=BANK_TRANSACTION_COLUMNS,
    )
    datasets["reconciliation_results"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Bill ID": "bill-000000000001",
                "Bill Number": "B-100",
                "Bill Date": date(2026, 7, 1),
                "Due Date": date(2026, 7, 31),
                "Currency": "INR",
                "Bill Amount": 100,
                "Source Outstanding Balance": 0,
                "Source Bill Status": "paid",
                "Payment Count": 1,
                "Payment IDs": "payment-000000000001",
                "Latest Payment Date": date(2026, 7, 2),
                "Payment Amount": 100,
                "Allocated Amount": 100,
                "Bank Transaction Date": date(2026, 7, 3),
                "Bank Amount": 100,
                "Bank Match Status": "Bank Match Pending Review",
                "Bank Match Method": "exact_account_amount_date_window_review",
                "Bank Pending Amount": 100,
                "Remaining Reconciliation Amount": 100,
                "Reconciliation Status": "Payment Recorded - Bank Pending",
                "Reconciliation Reason": "Payment is pending bank review",
                "Exception Type": "Payment without bank match",
                "Review Required": True,
                "Review Status": "Pending",
                "Reviewer Comment": None,
            }
        ],
        columns=RECONCILIATION_RESULT_COLUMNS,
    )
    return datasets


def _sample_input_datasets() -> dict[str, pd.DataFrame]:
    datasets = {
        name: pd.DataFrame(columns=columns)
        for name, columns in INPUT_DATASET_COLUMNS.items()
    }
    datasets["vendor_bills"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Bill ID": "bill-000000000001",
                "Bill Number": "B-100",
                "Bill Date": date(2026, 7, 1),
                "Due Date": date(2026, 7, 31),
                "Currency": "INR",
                "Bill Amount": 100,
                "Outstanding Balance": 0,
                "Zoho Bill Status": "paid",
            },
            {
                "Organization": "india",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Bill ID": "bill-000000000002",
                "Bill Number": "B-101",
                "Bill Date": date(2026, 7, 2),
                "Due Date": date(2026, 8, 1),
                "Currency": "INR",
                "Bill Amount": 50,
                "Outstanding Balance": 0,
                "Zoho Bill Status": "paid",
            },
        ],
        columns=INPUT_VENDOR_BILL_COLUMNS,
    )
    datasets["vendor_payments"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Payment ID": "payment-000000000001",
                "Payment Number": "P-100",
                "Payment Date": date(2026, 7, 3),
                "Payment Reference": "REF-1",
                "Payment Mode": "banktransfer",
                "Paid-Through Account Name": "Operating Account",
                "Currency": "INR",
                "Total Payment Amount": 150,
                "Allocated Amount": 150,
                "Unapplied Amount": 0,
                "Allocation Status": "Fully Allocated",
            },
            {
                "Organization": "india",
                "Vendor ID": "vendor-2",
                "Vendor Name": "Unallocated Vendor",
                "Payment ID": "payment-000000000002",
                "Payment Number": "P-101",
                "Payment Date": date(2026, 7, 4),
                "Payment Reference": "REF-2",
                "Payment Mode": "banktransfer",
                "Paid-Through Account Name": "Operating Account",
                "Currency": "INR",
                "Total Payment Amount": 25,
                "Allocated Amount": 0,
                "Unapplied Amount": 25,
                "Allocation Status": "Unallocated",
            },
        ],
        columns=INPUT_VENDOR_PAYMENT_COLUMNS,
    )
    datasets["payment_allocations"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Payment ID": "payment-000000000001",
                "Bill Payment ID": "bill-payment-1",
                "Bill ID": "bill-000000000001",
                "Bill Number": "B-100",
                "Amount Applied": 100,
                "Allocation Key": "allocation-1",
            },
            {
                "Organization": "india",
                "Payment ID": "payment-000000000001",
                "Bill Payment ID": "bill-payment-2",
                "Bill ID": "bill-000000000002",
                "Bill Number": "B-101",
                "Amount Applied": 50,
                "Allocation Key": "allocation-2",
            },
        ],
        columns=INPUT_PAYMENT_ALLOCATION_COLUMNS,
    )
    datasets["bank_transactions"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Account Name": "Operating Account",
                "Transaction ID": "transaction-1",
                "Bank Transaction Leg Key": "leg-debit",
                "Transaction Date": date(2026, 7, 3),
                "Transaction Type": "transfer",
                "Reference Number": "REF-1",
                "Description": "Vendor transfer",
                "Debit/Credit": "debit",
                "Direction": "Outgoing",
                "Currency": "INR",
                "Amount": 150,
                "Signed Amount": -150,
                "Status": "cleared",
                "Multi-Leg Transaction": True,
                "Data Quality Status": "Valid",
            },
            {
                "Organization": "india",
                "Account Name": "Savings Account",
                "Transaction ID": "transaction-1",
                "Bank Transaction Leg Key": "leg-credit",
                "Transaction Date": date(2026, 7, 3),
                "Transaction Type": "transfer",
                "Reference Number": "REF-1",
                "Description": "Vendor transfer",
                "Debit/Credit": "credit",
                "Direction": "Incoming",
                "Currency": "INR",
                "Amount": 150,
                "Signed Amount": 150,
                "Status": "cleared",
                "Multi-Leg Transaction": True,
                "Data Quality Status": "Valid",
            },
        ],
        columns=INPUT_BANK_TRANSACTION_COLUMNS,
    )
    return datasets


def test_required_workbook_sheets_and_columns():
    workbook = create_vendor_report_workbook(_sample_datasets())

    assert len(WORKBOOK_SHEETS) == 6
    assert workbook.sheetnames == WORKBOOK_SHEETS
    assert "Vendor Master" not in workbook.sheetnames
    assert "Exceptions - Review" not in workbook.sheetnames
    assert "Technical Audit" not in workbook.sheetnames
    assert [cell.value for cell in workbook["Vendor Bills"][3]] == VENDOR_BILL_COLUMNS
    assert [cell.value for cell in workbook["Vendor Payments"][3]] == VENDOR_PAYMENT_COLUMNS
    assert [cell.value for cell in workbook["Reconciliation Results"][3]] == (
        RECONCILIATION_RESULT_COLUMNS
    )


def test_report_mode_selector_defaults_to_input_and_analysis_remains_available():
    page_source = (
        Path(__file__).parents[1] / "pages" / "06_Downloads.py"
    ).read_text(encoding="utf-8")

    assert 'st.selectbox(\n    "Report mode"' in page_source
    assert "options=REPORT_MODES" in page_source
    assert "index=0" in page_source
    assert REPORT_MODE_INPUT in page_source
    assert REPORT_MODE_ANALYSIS in page_source


def test_input_workbook_has_exactly_four_sheets_and_no_analysis_statuses():
    workbook = create_reconciliation_input_workbook(
        _sample_input_datasets(),
        filters={"start_date": date(2026, 7, 1), "end_date": date(2026, 7, 31)},
    )

    assert workbook.sheetnames == INPUT_WORKBOOK_SHEETS
    assert [cell.value for cell in workbook["Vendor Bills"][3]] == (
        INPUT_VENDOR_BILL_COLUMNS
    )
    assert [cell.value for cell in workbook["Zoho Payment Batch"][3]] == (
        ZOHO_PAYMENT_BATCH_COLUMNS
    )
    workbook_text = " ".join(
        str(cell.value or "")
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )
    assert "Reconciliation Status" not in workbook_text
    assert "Bank Match Status" not in workbook_text
    assert "Review Required" not in workbook_text


def test_payment_batch_combines_allocations_and_keeps_unallocated_payment():
    datasets = _sample_input_datasets()
    batch = combine_zoho_payment_batch(
        datasets["vendor_payments"],
        datasets["payment_allocations"],
    )

    allocated_rows = batch[
        batch["Payment ID"].eq("payment-000000000001")
    ]
    unallocated_rows = batch[
        batch["Payment ID"].eq("payment-000000000002")
    ]
    assert len(allocated_rows) == 2
    assert set(allocated_rows["Bill ID"]) == {
        "bill-000000000001",
        "bill-000000000002",
    }
    assert len(unallocated_rows) == 1
    assert pd.isna(unallocated_rows.iloc[0]["Bill ID"])


def test_input_bank_legs_remain_distinct_and_ids_are_excel_text():
    workbook = create_reconciliation_input_workbook(_sample_input_datasets())
    bank_sheet = workbook["Bank Transactions"]
    payment_sheet = workbook["Zoho Payment Batch"]

    assert bank_sheet.max_row == 5
    assert {bank_sheet["D4"].value, bank_sheet["D5"].value} == {
        "leg-debit",
        "leg-credit",
    }
    assert bank_sheet["C4"].number_format == "@"
    assert bank_sheet["D4"].number_format == "@"
    assert payment_sheet["D4"].number_format == "@"
    assert payment_sheet["P4"].number_format == "@"


def test_input_queries_are_silver_only_and_parameterized():
    filters = {
        "start_date": date(2026, 7, 1),
        "end_date": date(2026, 7, 31),
        "organization": "india",
        "vendor": "Example Vendor",
        "currency": "INR",
    }
    queries = build_reconciliation_input_queries("project-1", filters)
    query_text = "\n".join(queries.values())

    assert set(queries) == set(INPUT_DATASET_COLUMNS)
    assert "finance_silver.fact_bills" in query_text
    assert "finance_silver.fact_vendor_payments" in query_text
    assert "finance_silver.bridge_vendor_payment_bill_allocations" in query_text
    assert "finance_silver.fact_bank_transactions" in query_text
    assert "finance_silver.fact_transactions" not in query_text
    assert "finance_gold." not in query_text
    assert "@start_date" in query_text
    assert "@organization" in query_text
    assert "Example Vendor" not in query_text


def test_input_blocking_validations_and_empty_us_sources():
    datasets = _sample_input_datasets()
    usd_bank = datasets["bank_transactions"].iloc[0].copy()
    usd_bank["Organization"] = "us"
    usd_bank["Transaction ID"] = "us-transaction-1"
    usd_bank["Bank Transaction Leg Key"] = "us-leg-1"
    usd_bank["Currency"] = "USD"
    datasets["bank_transactions"] = pd.concat(
        [datasets["bank_transactions"], pd.DataFrame([usd_bank])],
        ignore_index=True,
    )

    validations = validate_reconciliation_input_data(datasets)
    summary = summarize_reconciliation_input(datasets)
    assert validations["organizations_kept_separate"] is True
    assert {row["currency"] for row in summary["currency_totals"]} == {
        "INR",
        "USD",
    }

    duplicate = datasets["payment_allocations"].iloc[[0]].copy()
    datasets["payment_allocations"] = pd.concat(
        [datasets["payment_allocations"], duplicate],
        ignore_index=True,
    )
    with pytest.raises(VendorReportValidationError, match="duplicate_allocation_keys"):
        create_reconciliation_input_workbook(datasets)


def test_analysis_summary_contains_finance_review_disclaimer():
    workbook = create_vendor_report_workbook(_sample_datasets())

    assert workbook["Summary"]["A2"].value == ANALYSIS_DISCLAIMER


def test_exception_review_fields_are_in_reconciliation_results():
    workbook = create_vendor_report_workbook(_sample_datasets())
    sheet = workbook["Reconciliation Results"]
    headers = [cell.value for cell in sheet[3]]
    record = dict(zip(headers, [cell.value for cell in sheet[4]]))

    assert headers[-4:] == [
        "Exception Type",
        "Review Required",
        "Review Status",
        "Reviewer Comment",
    ]
    assert record["Exception Type"] == "Payment without bank match"
    assert record["Review Status"] == "Pending"
    assert record["Reviewer Comment"] is None
    assert sheet.data_validations.dataValidation[0].formula1 == '"Pending,In Review,Resolved"'


def test_audit_fields_end_the_relevant_business_sheets():
    workbook = create_vendor_report_workbook(_sample_datasets())

    assert [cell.value for cell in workbook["Vendor Bills"][3]][-2:] == [
        "Source Record ID",
        "Data Quality Status",
    ]
    assert [cell.value for cell in workbook["Vendor Payments"][3]][-3:] == [
        "Source Record ID",
        "Bank Transaction Leg Key",
        "Data Quality Status",
    ]
    assert [cell.value for cell in workbook["Payment Allocations"][3]][-3:] == [
        "Allocation Key",
        "Source Record ID",
        "Data Quality Status",
    ]
    assert [cell.value for cell in workbook["Bank Transactions"][3]][-4:] == [
        "Source Record ID",
        "Bank Transaction Leg Key",
        "Match Method",
        "Data Quality Status",
    ]


def test_payment_allocations_do_not_duplicate_and_both_bank_legs_remain():
    datasets = _sample_datasets()
    validations = validate_vendor_report_data(datasets)
    workbook = create_vendor_report_workbook(datasets, validation_results=validations)

    assert validations["duplicate_allocation_keys"] == 0
    assert validations["duplicate_bank_transaction_leg_keys"] == 0
    assert workbook["Payment Allocations"].max_row == 4
    assert workbook["Bank Transactions"].max_row == 5
    assert {
        workbook["Bank Transactions"]["F4"].value,
        workbook["Bank Transactions"]["F5"].value,
    } == {"debit", "credit"}


def test_pending_review_records_remain_pending_and_no_reference_fallback():
    datasets = _sample_datasets()
    validations = validate_vendor_report_data(datasets)

    assert validations["pending_review_status_violations"] == 0
    assert datasets["vendor_payments"].iloc[0]["Bank Match Status"] == (
        "Bank Match Pending Review"
    )


def test_currency_totals_are_separate():
    datasets = _sample_datasets()
    usd = datasets["reconciliation_results"].iloc[0].copy()
    usd["Organization"] = "us"
    usd["Bill ID"] = "us-bill-1"
    usd["Currency"] = "USD"
    usd["Bill Amount"] = 25
    usd["Payment Amount"] = 25
    usd["Allocated Amount"] = 25
    usd["Bank Pending Amount"] = 25
    datasets["reconciliation_results"] = pd.concat(
        [datasets["reconciliation_results"], pd.DataFrame([usd])],
        ignore_index=True,
    )

    summary = summarize_vendor_report(datasets)

    assert {row["currency"] for row in summary["currency_totals"]} == {"INR", "USD"}
    assert len(summary["currency_totals"]) == 2


def test_empty_datasets_do_not_crash():
    datasets = {
        name: pd.DataFrame(columns=columns)
        for name, columns in DATASET_COLUMNS.items()
    }
    workbook = create_vendor_report_workbook(datasets)

    assert workbook.sheetnames == WORKBOOK_SHEETS
    assert [cell.value for cell in workbook["Vendor Payments"][3]] == VENDOR_PAYMENT_COLUMNS
    assert workbook["Summary"]["B9"].value == 0


def test_queries_use_verified_sources_and_parameterized_filters():
    filters = {
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 7, 31),
        "organization": "india",
        "vendor": "Example Vendor",
        "currency": "INR",
        "source_bill_status": "paid",
        "reconciliation_status": "Payment Recorded - Bank Pending",
        "bank_match_status": "Bank Match Pending Review",
        "review_required": True,
    }
    queries = build_vendor_reconciliation_queries("project-1", filters)
    query_text = "\n".join(queries.values())
    parameters = _query_parameters(**filters)

    assert set(queries) == set(DATASET_COLUMNS)
    assert "finance_silver.fact_transactions" not in query_text
    assert "finance_silver.fact_bank_transactions" in query_text
    assert "finance_gold.vendor_reconciliation_exceptions" in query_text
    assert "@start_date" in query_text
    assert "@organization" in query_text
    assert "@bank_match_status" in query_text
    assert "Example Vendor" not in query_text
    assert {parameter.name for parameter in parameters} == set(filters)


def test_vendor_filter_options_are_sourced_from_dim_contacts(monkeypatch):
    captured = {}

    def fake_query_to_dataframe(client, query, **kwargs):
        captured["query"] = query
        return pd.DataFrame(
            [
                {"option_type": "vendor", "option_value": "Misc"},
                {
                    "option_type": "vendor",
                    "option_value": "Midoffice Solutions Pvt Ltd",
                },
            ]
        )

    monkeypatch.setattr(vendor_report, "_query_to_dataframe", fake_query_to_dataframe)
    options = vendor_report.fetch_vendor_filter_options(
        project_id="project-1",
        client=object(),
    )

    assert options["vendors"] == ["Midoffice Solutions Pvt Ltd", "Misc"]
    assert "finance_silver.dim_contacts" in captured["query"]
    assert "LOWER(contact_type) = 'vendor'" in captured["query"]


def test_blocking_validation_stops_export():
    datasets = _sample_datasets()
    duplicate = datasets["payment_allocations"].iloc[[0]].copy()
    datasets["payment_allocations"] = pd.concat(
        [datasets["payment_allocations"], duplicate],
        ignore_index=True,
    )

    with pytest.raises(VendorReportValidationError, match="duplicate_allocation_keys"):
        create_vendor_report_workbook(datasets)


def test_allocation_exceeding_payment_stops_export():
    datasets = _sample_datasets()
    datasets["payment_allocations"].loc[0, "Amount Applied"] = 101

    with pytest.raises(VendorReportValidationError, match="allocations_exceeding_payment"):
        validate_vendor_report_data(datasets)


def test_credit_leg_cannot_be_used_in_vendor_match():
    datasets = _sample_datasets()
    datasets["bank_transactions"].loc[1, "Used in Vendor Match"] = True

    with pytest.raises(
        VendorReportValidationError,
        match="credit_legs_used_as_vendor_payments",
    ):
        validate_vendor_report_data(datasets)


def test_excel_identifiers_and_business_audit_fields_are_text():
    workbook = create_vendor_report_workbook(_sample_datasets())
    bills = workbook["Vendor Bills"]
    payments = workbook["Vendor Payments"]

    assert bills["B4"].number_format == "@"
    assert bills["D4"].number_format == "@"
    assert bills.cell(4, VENDOR_BILL_COLUMNS.index("Source Record ID") + 1).value == (
        "source-bill-1"
    )
    assert payments["D4"].number_format == "@"
    assert payments.cell(
        4,
        VENDOR_PAYMENT_COLUMNS.index("Bank Transaction Leg Key") + 1,
    ).number_format == "@"


def test_query_to_dataframe_preserves_empty_result_columns():
    class EmptyResult:
        schema = [
            SimpleNamespace(name="organization"),
            SimpleNamespace(name="vendor_name"),
        ]

        def __iter__(self):
            return iter(())

    class EmptyQueryJob:
        def result(self):
            return EmptyResult()

    class FakeClient:
        def query(self, query, **kwargs):
            return EmptyQueryJob()

    result = _query_to_dataframe(FakeClient(), "SELECT organization, vendor_name")

    assert result.empty
    assert list(result.columns) == ["organization", "vendor_name"]


def test_safe_exception_details_redacts_credentials():
    details = safe_exception_details(
        RuntimeError("query failed; access_token=secret; Authorization:Bearer abc123")
    )

    assert "secret" not in details
    assert "abc123" not in details
    assert "[redacted]" in details


def test_generated_report_is_in_memory_and_has_expected_name(monkeypatch):
    datasets = _sample_datasets()
    metadata = {
        "queries": build_vendor_reconciliation_queries(
            "project-1",
            {"start_date": date(2026, 1, 1), "end_date": date(2026, 7, 31)},
        ),
        "filters": {"start_date": date(2026, 1, 1), "end_date": date(2026, 7, 31)},
    }
    monkeypatch.setattr(
        vendor_report,
        "fetch_vendor_report_data",
        lambda **kwargs: (datasets, metadata),
    )

    result = generate_vendor_transactions_report(
        date(2026, 1, 1),
        date(2026, 7, 31),
        report_mode=REPORT_MODE_ANALYSIS,
    )
    reopened = load_workbook(BytesIO(result["report_bytes"]), data_only=False)

    assert result["report_path"] is None
    assert result["report_name"] == (
        "Vendor_Reconciliation_Analysis_20260101_20260731.xlsx"
    )
    assert reopened.sheetnames == WORKBOOK_SHEETS
    assert set(result["row_counts"]) == set(WORKBOOK_SHEETS)
    assert result["row_counts"]["Vendor Payments"] == 1


def test_generated_input_report_has_expected_name_and_sheets(monkeypatch):
    datasets = _sample_input_datasets()
    metadata = {
        "queries": build_reconciliation_input_queries(
            "project-1",
            {"start_date": date(2026, 1, 1), "end_date": date(2026, 7, 31)},
        ),
        "filters": {"start_date": date(2026, 1, 1), "end_date": date(2026, 7, 31)},
    }
    monkeypatch.setattr(
        vendor_report,
        "fetch_reconciliation_input_data",
        lambda **kwargs: (datasets, metadata),
    )

    result = generate_vendor_transactions_report(
        date(2026, 1, 1),
        date(2026, 7, 31),
        report_mode=REPORT_MODE_INPUT,
    )
    reopened = load_workbook(BytesIO(result["report_bytes"]), data_only=False)

    assert result["report_name"] == (
        "Vendor_Reconciliation_Input_20260101_20260731.xlsx"
    )
    assert reopened.sheetnames == INPUT_WORKBOOK_SHEETS
    assert set(result["row_counts"]) == set(INPUT_WORKBOOK_SHEETS)
    assert result["row_counts"]["Zoho Payment Batch"] == 3
