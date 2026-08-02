from datetime import date

import pandas as pd
import pytest

from backend.reports.vendor_data_pack import (
    BANK_STATEMENT_COLUMNS,
    DATA_PACK_COLUMNS,
    INTERNAL_BANK_ROW_KEY,
    VENDOR_DATA_PACK_SHEETS,
    VendorReportValidationError,
    build_vendor_data_pack_queries,
    select_upload_ending_in,
    validate_vendor_data_pack,
)


def _sample_data():
    frames = {
        name: pd.DataFrame(columns=columns)
        for name, columns in DATA_PACK_COLUMNS.items()
    }
    frames["Vendor Master"] = pd.DataFrame(
        [
            {
                "Vendor ID": "000123",
                "Vendor Name": "Vendor One",
                "Vendor Status": "active",
                "Currency": "INR",
                "GSTIN": "GST123",
                "GST Treatment": "business_gst",
                "Place of Contact": "KA",
                "Email": "ap@example.com",
                "Phone": None,
                "Mobile": None,
                "Organization": "India",
                "Source System": "Zoho Books",
            }
        ]
    )
    frames["Vendor Statement"] = pd.DataFrame(
        [
            {
                "Vendor Name": "Vendor One",
                "Vendor ID": "000123",
                "Bill ID": "000456",
                "Bill Number": "INV-1",
                "Bill Date": date(2026, 1, 5),
                "Due Date": date(2026, 2, 5),
                "Currency": "INR",
                "Taxable Amount": 100,
                "GST/Tax Amount": 18,
                "Bill Total": 118,
                "Amount Paid": 118,
                "Outstanding Balance": 0,
                "Zoho Bill Status": "paid",
            }
        ]
    )
    frames["Zoho Payments"] = pd.DataFrame(
        [
            {
                "Vendor Name": "Vendor One",
                "Vendor ID": "000123",
                "Payment ID": "000789",
                "Payment Date": date(2026, 1, 10),
                "Payment Reference": "REF-1",
                "Payment Amount": 118,
                "Bill ID": "000456",
                "Bill Number": "INV-1",
                "Amount Applied": 118,
                "Unapplied Amount": 0,
            }
        ]
    )
    frames["Bank Statement"] = pd.DataFrame(
        [
            {
                "Transaction Date": date(2026, 1, 11),
                "Value Date": date(2026, 1, 11),
                "Original Narration/Description": "PAY INV-1 / EXACT TEXT",
                "Reference Number": "UTR1",
                "Debit Amount": 118,
                "Credit Amount": None,
                "Signed Amount": -118,
                "Currency": "INR",
                "Account Name": "Bank",
                "Masked Account Number": "********1234",
                "Upload ID Suffix": "1ef6",
                "Source Filename": "statement.xlsx",
                INTERNAL_BANK_ROW_KEY: "row-1",
            }
        ],
        columns=[*BANK_STATEMENT_COLUMNS, INTERNAL_BANK_ROW_KEY],
    )
    return frames


def test_queries_use_only_requested_validated_sources_and_raw_narration():
    queries = build_vendor_data_pack_queries(
        "project-1",
        date(2026, 1, 1),
        date(2026, 3, 31),
        "upload-1ef6",
    )
    text = "\n".join(queries.values())

    assert list(queries) == VENDOR_DATA_PACK_SHEETS
    assert "finance_silver.dim_contacts" in text
    assert "finance_silver.fact_bills" in text
    assert "finance_silver.fact_vendor_payments" in text
    assert "finance_silver.bridge_vendor_payment_bill_allocations" in text
    assert "finance_silver.fact_bank_statement_lines" in text
    assert "finance_silver.fact_bank_transactions" not in text
    assert "finance_gold." not in text
    assert "bank.narration AS `Original Narration/Description`" in text
    assert "CAST(bank.upload_id AS STRING) = @bank_upload_id" in text


def test_data_pack_validations_pass_and_preserve_four_sheet_contract():
    checks = validate_vendor_data_pack(_sample_data())

    assert checks["sheet_count"] == 4
    assert checks["duplicate_vendors"] == 0
    assert checks["duplicate_vendor_statement_bill_ids"] == 0
    assert checks["duplicate_bank_statement_row_keys"] == 0
    assert checks["payment_allocations_missing_bill_number"] == 0
    assert checks["unsafe_account_numbers"] == 0
    assert checks["bank_upload_suffixes"] == ["1ef6"]


@pytest.mark.parametrize(
    ("sheet", "column", "expected_message"),
    [
        ("Vendor Master", "Vendor ID", "duplicate_vendors"),
        ("Vendor Statement", "Bill ID", "duplicate_vendor_statement_bill_ids"),
        ("Bank Statement", INTERNAL_BANK_ROW_KEY, "duplicate_bank_statement_row_keys"),
    ],
)
def test_duplicate_grains_block_export(sheet, column, expected_message):
    frames = _sample_data()
    duplicate = frames[sheet].iloc[[0]].copy()
    frames[sheet] = pd.concat([frames[sheet], duplicate], ignore_index=True)

    with pytest.raises(VendorReportValidationError, match=expected_message):
        validate_vendor_data_pack(frames)


def test_payment_allocation_without_bill_number_blocks_export():
    frames = _sample_data()
    frames["Zoho Payments"].loc[0, "Bill Number"] = None

    with pytest.raises(
        VendorReportValidationError,
        match="payment_allocations_missing_bill_number",
    ):
        validate_vendor_data_pack(frames)


def test_unmasked_account_number_blocks_export():
    frames = _sample_data()
    frames["Bank Statement"].loc[0, "Masked Account Number"] = "1234567890"

    with pytest.raises(VendorReportValidationError, match="unsafe_account_numbers"):
        validate_vendor_data_pack(frames)


def test_upload_suffix_selection_requires_exactly_one_upload():
    uploads = [
        {"upload_id": "abc-1ef6", "period_row_count": 2},
        {"upload_id": "abc-aaaa", "period_row_count": 1},
    ]
    assert select_upload_ending_in(uploads, "1ef6")["upload_id"] == "abc-1ef6"

    with pytest.raises(VendorReportValidationError, match="found 0"):
        select_upload_ending_in(uploads, "bbbb")
