"""Tests for the Phase 5 Vendor Reconciliation workbook."""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace

import pandas as pd
import pytest
from openpyxl import load_workbook

from backend.reports import vendor_transactions_report as vendor_report
from backend.reports.vendor_transactions_report import (
    BANK_TRANSACTION_COLUMNS,
    DATASET_COLUMNS,
    EXCEPTION_COLUMNS,
    PAYMENT_ALLOCATION_COLUMNS,
    RECONCILIATION_RESULT_COLUMNS,
    TECHNICAL_AUDIT_COLUMNS,
    VENDOR_BILL_COLUMNS,
    VENDOR_MASTER_COLUMNS,
    VENDOR_PAYMENT_COLUMNS,
    WORKBOOK_SHEETS,
    VendorReportValidationError,
    _query_parameters,
    _query_to_dataframe,
    build_vendor_reconciliation_queries,
    create_vendor_report_workbook,
    generate_vendor_transactions_report,
    safe_exception_details,
    summarize_vendor_report,
    validate_vendor_report_data,
)


def _sample_datasets() -> dict[str, pd.DataFrame]:
    datasets = {
        name: pd.DataFrame(columns=columns)
        for name, columns in DATASET_COLUMNS.items()
    }
    datasets["vendor_master"] = pd.DataFrame(
        [
            {
                "Organization": "india",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Company Name": "Example Vendor Pvt Ltd",
                "Vendor Status": "active",
                "Currency": "INR",
                "Outstanding Payable": 100,
                "Has Bills": True,
                "Has Vendor Payments": True,
                "Bill Count": 1,
                "Payment Count": 1,
                "Activity Status": "Reconciliation Activity",
                "_Contact Type": "vendor",
                "_Source Organization ID": "india-id",
            },
            {
                "Organization": "us",
                "Vendor ID": "us-vendor-1",
                "Vendor Name": "Midoffice Solutions Pvt Ltd",
                "Company Name": "Midoffice Solutions Pvt Ltd",
                "Vendor Status": "active",
                "Currency": "USD",
                "Outstanding Payable": 0,
                "Has Bills": False,
                "Has Vendor Payments": False,
                "Bill Count": 0,
                "Payment Count": 0,
                "Activity Status": "No Activity",
                "_Contact Type": "vendor",
                "_Source Organization ID": "us-id",
            },
            {
                "Organization": "us",
                "Vendor ID": "us-vendor-2",
                "Vendor Name": "Misc",
                "Company Name": "Misc",
                "Vendor Status": "active",
                "Currency": "USD",
                "Outstanding Payable": 0,
                "Has Bills": False,
                "Has Vendor Payments": False,
                "Bill Count": 0,
                "Payment Count": 0,
                "Activity Status": "No Activity",
                "_Contact Type": "vendor",
                "_Source Organization ID": "us-id",
            },
        ],
        columns=[
            *VENDOR_MASTER_COLUMNS,
            "_Contact Type",
            "_Source Organization ID",
        ],
    )
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
                "_Bank Transaction Leg Key": "leg-debit",
                "_Source Organization ID": "india-id",
            }
        ],
        columns=[
            *VENDOR_PAYMENT_COLUMNS,
            "_Bank Transaction Leg Key",
            "_Source Organization ID",
        ],
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
                "Bank Data Quality Status": "Valid",
                "Used in Vendor Match": True,
                "Review Required": True,
                "_Bank Transaction Leg Key": "leg-debit",
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
                "Bank Data Quality Status": "Valid",
                "Used in Vendor Match": False,
                "Review Required": False,
                "_Bank Transaction Leg Key": "leg-credit",
            },
        ],
        columns=[*BANK_TRANSACTION_COLUMNS, "_Bank Transaction Leg Key"],
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
                "Allocated Amount": 100,
                "Bank-Verified Amount": 0,
                "Bank-Pending Amount": 100,
                "Remaining Reconciliation Amount": 100,
                "Reconciliation Status": "Payment Recorded - Bank Pending",
                "Reconciliation Reason": "Payment is pending bank review",
                "Review Required": True,
            }
        ],
        columns=RECONCILIATION_RESULT_COLUMNS,
    )
    datasets["exceptions"] = pd.DataFrame(
        [
            {
                "Exception Type": "Payment without bank match",
                "Organization": "india",
                "Vendor ID": "vendor-1",
                "Vendor Name": "Example Vendor",
                "Bill ID": "bill-000000000001",
                "Payment ID": "payment-000000000001",
                "Masked Bank Leg Key": "...ebit",
                "Currency": "INR",
                "Amount": 100,
                "Exception Reason": "Pending manual review",
                "Review Required": True,
                "Review Status": None,
                "Reviewer Comment": None,
            }
        ],
        columns=EXCEPTION_COLUMNS,
    )
    datasets["technical_audit"] = pd.DataFrame(
        [
            {
                "Record Type": "Bill",
                "Source Organization ID": "india-id",
                "Source Record ID": "source-bill-1",
                "Bill ID": "bill-000000000001",
                "Payment ID": None,
                "Allocation Key": None,
                "Bank Transaction Leg Key": None,
                "Run ID": "run-1",
                "Loaded Timestamp": datetime(2026, 7, 4, 10, 0),
                "Mapping/Match Method": "source_bill",
                "Data Quality Status": "Valid",
            }
        ],
        columns=TECHNICAL_AUDIT_COLUMNS,
    )
    return datasets


def test_required_workbook_sheets_and_columns():
    workbook = create_vendor_report_workbook(_sample_datasets())

    assert len(WORKBOOK_SHEETS) == 9
    assert workbook.sheetnames == WORKBOOK_SHEETS
    assert [cell.value for cell in workbook["Vendor Master"][3]] == VENDOR_MASTER_COLUMNS
    assert [cell.value for cell in workbook["Vendor Bills"][3]] == VENDOR_BILL_COLUMNS
    assert [cell.value for cell in workbook["Vendor Payments"][3]] == VENDOR_PAYMENT_COLUMNS
    assert [cell.value for cell in workbook["Technical Audit"][3]] == TECHNICAL_AUDIT_COLUMNS


def test_vendor_master_includes_vendors_with_no_activity_and_correct_flags():
    datasets = _sample_datasets()
    workbook = create_vendor_report_workbook(datasets)
    master = workbook["Vendor Master"]
    rows = list(master.iter_rows(min_row=4, values_only=True))
    headers = [cell.value for cell in master[3]]
    records = [dict(zip(headers, row)) for row in rows]
    us_records = [row for row in records if row["Organization"] == "us"]

    assert {row["Vendor Name"] for row in us_records} == {
        "Midoffice Solutions Pvt Ltd",
        "Misc",
    }
    assert all(row["Has Bills"] is False for row in us_records)
    assert all(row["Has Vendor Payments"] is False for row in us_records)
    assert all(row["Activity Status"] == "No Activity" for row in us_records)


def test_vendor_master_query_excludes_customers_and_uses_vendor_contacts():
    queries = build_vendor_reconciliation_queries(
        "project-1",
        {"start_date": date(2026, 1, 1), "end_date": date(2026, 7, 31)},
    )
    master_query = queries["vendor_master"]

    assert "finance_silver.dim_contacts" in master_query
    assert "LOWER(contact.contact_type) = 'vendor'" in master_query
    assert "NOT EXISTS" in master_query
    assert "TechMahindra" not in master_query


def test_no_activity_vendor_does_not_create_fake_reconciliation_rows():
    datasets = _sample_datasets()
    datasets["vendor_master"] = datasets["vendor_master"].query(
        "Organization == 'us'"
    ).reset_index(drop=True)
    for dataset_name in (
        "vendor_bills",
        "vendor_payments",
        "payment_allocations",
        "reconciliation_results",
        "exceptions",
    ):
        datasets[dataset_name] = datasets[dataset_name].iloc[0:0].copy()

    workbook = create_vendor_report_workbook(datasets)
    summary = summarize_vendor_report(datasets)

    assert workbook["Vendor Master"].max_row == 5
    assert workbook["Vendor Bills"].max_row == 3
    assert workbook["Vendor Payments"].max_row == 3
    assert workbook["Reconciliation Results"].max_row == 3
    assert summary["total_master_vendors"] == 2
    assert summary["vendors_with_reconciliation_activity"] == 0
    assert summary["vendors_with_no_activity"] == 2


def test_source_status_is_separate_and_source_record_not_duplicated_in_bill_sheet():
    workbook = create_vendor_report_workbook(_sample_datasets())
    headers = [cell.value for cell in workbook["Vendor Bills"][3]]

    assert "Source Bill Status" in headers
    assert "Reconciliation Status" in headers
    assert "Source Record ID" not in headers


def test_payment_allocations_do_not_duplicate_and_both_bank_legs_remain():
    datasets = _sample_datasets()
    validations = validate_vendor_report_data(datasets)
    workbook = create_vendor_report_workbook(datasets, validation_results=validations)

    assert validations["duplicate_allocation_keys"] == 0
    assert workbook["Payment Allocations"].max_row == 4
    assert workbook["Bank Transactions"].max_row == 5
    assert {workbook["Bank Transactions"]["F4"].value, workbook["Bank Transactions"]["F5"].value} == {
        "debit",
        "credit",
    }


def test_pending_review_records_remain_pending_and_no_reference_fallback():
    datasets = _sample_datasets()
    validations = validate_vendor_report_data(datasets)

    assert validations["pending_review_status_violations"] == 0
    assert datasets["vendor_payments"].iloc[0]["Bank Match Status"] == "Bank Match Pending Review"


def test_currency_totals_are_separate():
    datasets = _sample_datasets()
    usd = datasets["reconciliation_results"].iloc[0].copy()
    usd["Organization"] = "us"
    usd["Bill ID"] = "us-bill-1"
    usd["Currency"] = "USD"
    usd["Bill Amount"] = 25
    datasets["reconciliation_results"] = pd.concat(
        [datasets["reconciliation_results"], pd.DataFrame([usd])],
        ignore_index=True,
    )

    summary = summarize_vendor_report(datasets)

    assert {row["currency"] for row in summary["currency_totals"]} == {"INR", "USD"}
    assert len(summary["currency_totals"]) == 2


def test_empty_us_vendor_payments_and_empty_datasets_do_not_crash():
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

    assert "finance_silver.fact_transactions" not in query_text
    assert "finance_silver.dim_contacts" in query_text
    assert "finance_silver.fact_bank_transactions" in query_text
    assert "@start_date" in query_text
    assert "@organization" in query_text
    assert "@bank_match_status" in query_text
    assert "Example Vendor" not in query_text
    assert {parameter.name for parameter in parameters} == set(filters)


def test_vendor_filter_options_are_sourced_from_vendor_master(monkeypatch):
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


def test_vendor_master_validation_rejects_customers_duplicates_and_bad_activity():
    datasets = _sample_datasets()
    customer = datasets["vendor_master"].iloc[[0]].copy()
    customer.loc[:, "_Contact Type"] = "customer"
    datasets["vendor_master"] = pd.concat(
        [datasets["vendor_master"], customer],
        ignore_index=True,
    )

    with pytest.raises(
        VendorReportValidationError,
        match="duplicate_vendor_master_ids|non_vendor_master_contacts",
    ):
        validate_vendor_report_data(datasets)

    datasets = _sample_datasets()
    datasets["vendor_master"].loc[0, "Has Bills"] = False
    with pytest.raises(
        VendorReportValidationError,
        match="vendor_master_activity_mismatches",
    ):
        validate_vendor_report_data(datasets)


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

    with pytest.raises(VendorReportValidationError, match="credit_legs_used_as_vendor_payments"):
        validate_vendor_report_data(datasets)


def test_excel_identifiers_are_text_and_audit_contains_source_ids():
    workbook = create_vendor_report_workbook(_sample_datasets())
    bills = workbook["Vendor Bills"]
    payments = workbook["Vendor Payments"]
    audit = workbook["Technical Audit"]

    assert bills["B4"].number_format == "@"
    assert bills["D4"].number_format == "@"
    assert payments["D4"].number_format == "@"
    assert audit["B4"].value == "india-id"
    assert audit["C4"].value == "source-bill-1"
    assert audit["B4"].number_format == "@"


def test_query_to_dataframe_preserves_empty_result_columns():
    class EmptyResult:
        schema = [SimpleNamespace(name="organization"), SimpleNamespace(name="vendor_name")]

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
    )
    reopened = load_workbook(BytesIO(result["report_bytes"]), data_only=False)

    assert result["report_path"] is None
    assert result["report_name"] == "Vendor_Reconciliation_20260101_20260731.xlsx"
    assert reopened.sheetnames == WORKBOOK_SHEETS
    assert result["row_counts"]["Vendor Payments"] == 1
