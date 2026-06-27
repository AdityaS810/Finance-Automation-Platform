"""Tests for deterministic backend reconciliation flows."""

from __future__ import annotations

import pandas as pd
import pytest

from backend.ai.reconciliation_insights import _parse_ai_response
from backend.reconciliation.bank_reconciliation import run_bank_reconciliation
from backend.reconciliation.gst_reconciliation import run_gst_reconciliation


def _word_count(text: str) -> int:
    return len(text.split())


def _bank_lines_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "bank_line_id": "bank-1",
                "upload_id": "bank-upload-1",
                "bank_date": "2026-05-02",
                "bank_narration": "Receipt from Alpha Customer INV-1001",
                "debit_amount": 0,
                "credit_amount": 100000,
                "balance_amount": 125000,
                "bank_amount": 100000,
            },
            {
                "bank_line_id": "bank-2",
                "upload_id": "bank-upload-1",
                "bank_date": "2026-05-04",
                "bank_narration": "Payment to Beta Vendor",
                "debit_amount": 40000,
                "credit_amount": 0,
                "balance_amount": 85000,
                "bank_amount": -40000,
            },
            {
                "bank_line_id": "bank-3",
                "upload_id": "bank-upload-1",
                "bank_date": "2026-05-10",
                "bank_narration": "Unknown UPI credit",
                "debit_amount": 0,
                "credit_amount": 999,
                "balance_amount": 85999,
                "bank_amount": 999,
            },
            {
                "bank_line_id": "bank-3-duplicate",
                "upload_id": "bank-upload-1",
                "bank_date": "2026-05-10",
                "bank_narration": "Unknown UPI credit",
                "debit_amount": 0,
                "credit_amount": 999,
                "balance_amount": 85999,
                "bank_amount": 999,
            },
            {
                "bank_line_id": "opening-1",
                "upload_id": "bank-upload-1",
                "bank_date": "2026-05-01",
                "bank_narration": "Opening Balance",
                "debit_amount": 0,
                "credit_amount": 25000,
                "balance_amount": 25000,
                "bank_amount": 25000,
            },
            {
                "bank_line_id": "opening-duplicate",
                "upload_id": "bank-upload-1",
                "bank_date": "2026-05-01",
                "bank_narration": "Opening Balance",
                "debit_amount": 0,
                "credit_amount": 25000,
                "balance_amount": 25000,
                "bank_amount": 25000,
            },
        ]
    )


def _accounting_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "accounting_record_id": "acct-1",
                "accounting_date": "2026-05-02",
                "accounting_party_name": "Alpha Customer",
                "transaction_number": "INV-1001",
                "transaction_type": "customer_payment",
                "accounting_amount": 100000,
            },
            {
                "accounting_record_id": "acct-2",
                "accounting_date": "2026-05-06",
                "accounting_party_name": "Beta Vendor",
                "transaction_number": "BILL-2001",
                "transaction_type": "bill_payable",
                "accounting_amount": -40000,
            },
        ]
    )


def _gstr_lines_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "gstr_line_id": "gstr-1",
                "upload_id": "gstr-upload-1",
                "supplier_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1001",
                "invoice_date": "2026-05-01",
                "period": "2026-05",
                "taxable_value": 100000,
                "igst_amount": 18000,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 18000,
            },
            {
                "gstr_line_id": "gstr-2",
                "upload_id": "gstr-upload-1",
                "supplier_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1002",
                "invoice_date": "2026-05-02",
                "period": "2026-05",
                "taxable_value": 50000,
                "igst_amount": 9000,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 9000,
            },
            {
                "gstr_line_id": "gstr-2-duplicate",
                "upload_id": "gstr-upload-1",
                "supplier_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1002",
                "invoice_date": "2026-05-02",
                "period": "2026-05",
                "taxable_value": 50000,
                "igst_amount": 9000,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 9000,
            },
            {
                "gstr_line_id": "gstr-4",
                "upload_id": "gstr-upload-1",
                "supplier_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV/1004",
                "invoice_date": "2026-05-04",
                "period": "2026-05",
                "taxable_value": 20000,
                "igst_amount": 3600,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 3600,
            },
            {
                "gstr_line_id": "gstr-3",
                "upload_id": "gstr-upload-1",
                "supplier_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1003",
                "invoice_date": "2026-05-03",
                "period": "2026-05",
                "taxable_value": 30000,
                "igst_amount": 5400,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 5400,
            },
            {
                "gstr_line_id": "gstr-5",
                "upload_id": "gstr-upload-1",
                "supplier_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "P4-5001",
                "invoice_date": "2026-05-06",
                "period": "2026-05",
                "taxable_value": 15000,
                "igst_amount": 2700,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 2700,
            },
            {
                "gstr_line_id": "gstr-6",
                "upload_id": "gstr-upload-1",
                "supplier_name": "Missing Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "NOBOOK-1",
                "invoice_date": "2026-05-07",
                "period": "2026-05",
                "taxable_value": 7000,
                "igst_amount": 1260,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 1260,
            },
            {
                "gstr_line_id": "old-gstr-1",
                "upload_id": "historical-upload",
                "supplier_name": "Historical Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "OLD-1",
                "invoice_date": "2026-04-01",
                "period": "2026-04",
                "taxable_value": 999,
                "igst_amount": 180,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 180,
            },
        ]
    )


def _books_gst_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "books_record_id": "book-1",
                "party_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1001",
                "books_invoice_date": "2026-05-01",
                "books_taxable_value": 100000,
                "books_igst_amount": 18000,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 18000,
            },
            {
                "books_record_id": "book-2",
                "party_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1002",
                "books_invoice_date": "2026-05-02",
                "books_taxable_value": 55000,
                "books_igst_amount": 9900,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 9900,
            },
            {
                "books_record_id": "book-3",
                "party_name": "Other Supplier",
                "gstin": "27PQRSX5678L1Z2",
                "invoice_number": "INV-9999",
                "books_invoice_date": "2026-05-05",
                "books_taxable_value": 25000,
                "books_igst_amount": 4500,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 4500,
            },
            {
                "books_record_id": "book-4",
                "party_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1004",
                "books_invoice_date": "2026-05-04",
                "books_taxable_value": 20000,
                "books_igst_amount": 3600,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 3600,
            },
            {
                "books_record_id": "book-5",
                "party_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1003",
                "books_invoice_date": "2026-05-05",
                "books_taxable_value": 30000,
                "books_igst_amount": 5400,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 5400,
            },
            {
                "books_record_id": "book-6",
                "party_name": "Alpha Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "ALT-5001",
                "books_invoice_date": "2026-05-07",
                "books_taxable_value": 15000,
                "books_igst_amount": 2700,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 2700,
            },
            {
                "books_record_id": "book-outside-period",
                "party_name": "Outside Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "OUT-2026",
                "books_invoice_date": "2026-06-15",
                "books_taxable_value": 80000,
                "books_igst_amount": 14400,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 14400,
            },
            {
                "books_record_id": "book-3-duplicate",
                "party_name": "Other Supplier",
                "gstin": "27PQRSX5678L1Z2",
                "invoice_number": "INV-9999",
                "books_invoice_date": "2026-05-05",
                "books_taxable_value": 25000,
                "books_igst_amount": 4500,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 4500,
            },
        ]
    )


def _single_gstr_row(
    invoice_number: str = "INV-1",
    gstin: str = "29ABCDE1234F1Z7",
    invoice_date: str = "2026-05-01",
    supplier_name: str = "Alpha Supplier",
    taxable_value: float = 1000,
    igst_amount: float = 180,
) -> pd.DataFrame:
    total_tax = igst_amount
    return pd.DataFrame(
        [
            {
                "gstr_line_id": "gstr-single",
                "upload_id": "gstr-upload-1",
                "supplier_name": supplier_name,
                "gstin": gstin,
                "invoice_number": invoice_number,
                "invoice_date": invoice_date,
                "period": "2026-05",
                "taxable_value": taxable_value,
                "igst_amount": igst_amount,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": total_tax,
                "invoice_value": taxable_value + total_tax,
            },
        ]
    )


def _single_books_row(
    invoice_number: str = "INV-1",
    gstin: str = "29ABCDE1234F1Z7",
    invoice_date: str = "2026-05-01",
    party_name: str = "Alpha Supplier",
    taxable_value: float = 1000,
    igst_amount: float = 180,
) -> pd.DataFrame:
    total_tax = igst_amount
    return pd.DataFrame(
        [
            {
                "books_record_id": "book-single",
                "party_name": party_name,
                "gstin": gstin,
                "invoice_number": invoice_number,
                "books_invoice_date": invoice_date,
                "books_taxable_value": taxable_value,
                "books_igst_amount": igst_amount,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": total_tax,
                "books_invoice_value": taxable_value + total_tax,
            },
        ]
    )


def test_bank_reconciliation_matches_and_exports_excel(workspace_tmp_path):
    result = run_bank_reconciliation(
        workspace_tmp_path,
        bank_lines_df=_bank_lines_df(),
        accounting_df=_accounting_df(),
    )

    assert result["summary"]["matched"] >= 1
    assert result["summary"]["uploaded_bank_rows"] == 6
    assert result["summary"]["unmatched"] >= 1
    assert result["summary"]["ignored_opening_balance"] == 1
    assert result["summary"]["transaction_records"] == 3
    assert not result["results"].empty
    assert "ignored_opening_balance" in set(result["results"]["match_status"])
    accounting_ids = result["results"].loc[result["results"]["accounting_record_id"] != "", "accounting_record_id"]
    assert accounting_ids.is_unique
    assert {"ai_summary", "ai_recommendation", "ai_risk_level"}.issubset(result["results"].columns)
    assert {"selected_upload_id", "selected_file_name", "reconciliation_timestamp"}.issubset(result["results"].columns)
    assert set(result["results"]["selected_upload_id"]) == {"bank-upload-1"}
    review_rows = result["results"][result["results"]["match_status"].isin(["possible_match", "unmatched"])]
    assert review_rows["ai_summary"].map(_word_count).ge(8).all()
    assert result["ai_status"] in {"enabled", "unavailable", "not_required"}
    assert result["export_path"].name == "bank_reconciliation_results.xlsx"
    assert result["export_path"].exists()


def test_gst_reconciliation_matches_mismatches_and_exports_excel(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_gstr_lines_df(),
        books_gst_df=_books_gst_df(),
    )

    assert result["summary"]["matched"] == 3
    assert result["summary"]["uploaded_gstr_rows"] == 7
    assert result["summary"]["amount_mismatch"] == 1
    assert result["summary"]["possible_match"] == 1
    assert result["summary"]["missing_in_books"] == 1
    assert result["summary"]["missing_in_gstr"] == 1
    assert len(result["results"].index) == 7
    assert not result["results"].empty
    assert {"ai_summary", "ai_recommendation", "ai_risk_level"}.issubset(result["results"].columns)
    assert {
        "match_status",
        "status_label",
        "match_level",
        "confidence_score",
        "source_side",
        "zoho_supplier_name",
        "gstr_supplier_name",
        "zoho_gstin",
        "gstr_gstin",
        "zoho_invoice_number",
        "gstr_invoice_number",
        "zoho_invoice_date",
        "gstr_invoice_date",
        "zoho_taxable_value",
        "gstr_taxable_value",
        "zoho_invoice_value",
        "gstr_invoice_value",
        "difference_amount",
        "supplier_name",
        "party_name",
        "gstin",
        "invoice_number",
        "invoice_date",
        "return_period",
        "taxable_value_gstr",
        "taxable_value_books",
        "igst_gstr",
        "igst_books",
        "cgst_gstr",
        "cgst_books",
        "sgst_gstr",
        "sgst_books",
        "invoice_value_gstr",
        "invoice_value_books",
        "amount_difference",
        "match_reason",
        "action_required",
        "selected_upload_id",
        "selected_file_name",
        "reconciliation_timestamp",
        "selected_upload_min_date",
        "selected_upload_max_date",
        "books_rows_before_period_filter",
        "books_rows_after_period_filter",
    }.issubset(result["results"].columns)
    assert set(result["results"]["selected_upload_id"]) == {"gstr-upload-1"}
    assert result["selected_upload_min_date"] == "2026-05-01"
    assert result["selected_upload_max_date"] == "2026-05-07"
    assert result["books_rows_before_period_filter"] == 8
    assert result["books_rows_after_period_filter"] == 7
    assert "historical-upload" not in set(result["results"].get("gstr_upload_id", pd.Series(dtype=str)).astype(str))
    assert "OUT-2026" not in set(result["results"]["invoice_number"].astype(str))
    assert "invoice date matched" in result["results"].loc[
        result["results"]["match_status"] == "amount_mismatch",
        "match_reason",
    ].iloc[0]
    levels_by_invoice = result["results"].set_index("invoice_number")["match_level"].to_dict()
    statuses_by_invoice = result["results"].set_index("invoice_number")["match_status"].to_dict()
    confidence_by_invoice = result["results"].set_index("invoice_number")["confidence_score"].to_dict()
    assert levels_by_invoice["INV-1001"] == "P1_EXACT"
    assert statuses_by_invoice["INV-1001"] == "matched"
    assert confidence_by_invoice["INV-1001"] == 1.0
    assert levels_by_invoice["INV-1002"] == "P1_EXACT"
    assert statuses_by_invoice["INV-1002"] == "amount_mismatch"
    assert confidence_by_invoice["INV-1002"] == 0.85
    assert levels_by_invoice["INV-1003"] == "P1_EXACT"
    assert statuses_by_invoice["INV-1003"] == "matched"
    assert confidence_by_invoice["INV-1003"] == 1.0
    assert levels_by_invoice["INV/1004"] == "P3_FORMAT"
    assert statuses_by_invoice["INV/1004"] == "matched"
    assert confidence_by_invoice["INV/1004"] == 0.88
    assert levels_by_invoice["P4-5001"] == "P5_AMOUNT_DATE_CANDIDATE"
    assert confidence_by_invoice["P4-5001"] == 0.50
    assert levels_by_invoice["NOBOOK-1"] == "P5_MISSING_IN_BOOKS"
    assert levels_by_invoice["INV-9999"] == "P6_MISSING_IN_GSTR"
    reasons_by_invoice = result["results"].set_index("invoice_number")["match_reason"].to_dict()
    assert "nearby date" in reasons_by_invoice["INV-1003"]
    assert "removing spaces and separators" in reasons_by_invoice["INV/1004"]
    assert "supplier name similarity" in reasons_by_invoice["P4-5001"]
    possible_row = result["results"].loc[result["results"]["match_status"] == "possible_match"].iloc[0]
    assert possible_row["zoho_supplier_name"] == possible_row["party_name"]
    assert possible_row["gstr_supplier_name"] == possible_row["supplier_name"]
    assert possible_row["difference_amount"] == possible_row["amount_difference"]
    assert "Confirm manually before marking as matched" in possible_row["action_required"]
    review_rows = result["results"][
        result["results"]["match_status"].isin(["amount_mismatch", "possible_match", "missing_in_books", "missing_in_gstr"])
    ]
    assert review_rows["ai_summary"].map(_word_count).ge(8).all()
    assert result["ai_status"] in {"enabled", "unavailable", "not_required"}
    assert result["export_path"].name == "gst_reconciliation_results.xlsx"
    assert result["export_path"].exists()
    workbook = pd.ExcelFile(result["export_path"])
    assert {
        "Summary",
        "Amount Mismatches",
        "Missing in Books",
        "Missing in GSTR",
        "Possible Matches",
        "Matched",
        "Technical Audit",
    }.issubset(set(workbook.sheet_names))
    possible_matches_sheet = pd.read_excel(workbook, sheet_name="Possible Matches")
    assert possible_matches_sheet.columns[:15].tolist() == [
        "zoho_supplier_name",
        "gstr_supplier_name",
        "zoho_gstin",
        "gstr_gstin",
        "zoho_invoice_number",
        "gstr_invoice_number",
        "zoho_invoice_date",
        "gstr_invoice_date",
        "zoho_taxable_value",
        "gstr_taxable_value",
        "zoho_invoice_value",
        "gstr_invoice_value",
        "difference_amount",
        "match_reason",
        "action_required",
    ]
    assert not {
        "match_status",
        "match_level",
        "confidence_score",
        "source_side",
        "gstr_line_id",
        "books_record_id",
        "gstr_raw_ids",
        "books_raw_ids",
    }.intersection(possible_matches_sheet.columns)
    technical_audit_sheet = pd.read_excel(workbook, sheet_name="Technical Audit")
    assert {
        "match_status",
        "match_level",
        "confidence_score",
        "source_side",
        "gstr_line_id",
        "books_record_id",
        "gstr_raw_ids",
        "books_raw_ids",
    }.issubset(technical_audit_sheet.columns)


def test_gst_reconciliation_does_not_treat_null_books_tax_as_zero(workspace_tmp_path):
    gstr_df = pd.DataFrame(
        [
            {
                "gstr_line_id": "gstr-null-tax-check",
                "upload_id": "gstr-upload-1",
                "supplier_name": "Null Tax Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "NULL-TAX-1",
                "invoice_date": "2026-05-01",
                "period": "2026-05",
                "taxable_value": 1000,
                "igst_amount": 0,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 0,
                "invoice_value": 1000,
            },
        ]
    )
    books_df = pd.DataFrame(
        [
            {
                "books_record_id": "book-null-tax-check",
                "party_name": "Null Tax Supplier",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "NULL-TAX-1",
                "books_invoice_date": "2026-05-01",
                "books_taxable_value": 1000,
                "books_igst_amount": None,
                "books_cgst_amount": None,
                "books_sgst_amount": None,
                "books_tax_amount": None,
                "books_invoice_value": 1000,
            },
        ]
    )

    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=gstr_df,
        books_gst_df=books_df,
        generate_ai_insights=False,
    )

    assert result["summary"]["matched"] == 0
    assert result["summary"]["amount_mismatch"] == 1
    row = result["results"].iloc[0]
    assert row["match_level"] == "P1_EXACT"
    assert "missing" in row["match_reason"]


def test_gst_invoice_number_case_and_spacing_matches(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number=" inv  77 "),
        books_gst_df=_single_books_row(invoice_number="INV 77"),
        generate_ai_insights=False,
    )

    row = result["results"].iloc[0]
    assert row["match_status"] == "matched"
    assert row["match_level"] == "P1_EXACT"


def test_gst_invoice_number_separator_format_difference_matches(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number="INV/77"),
        books_gst_df=_single_books_row(invoice_number="INV-77"),
        generate_ai_insights=False,
    )

    row = result["results"].iloc[0]
    assert row["match_status"] == "matched"
    assert row["match_level"] == "P3_FORMAT"


def test_gst_invoice_with_different_gstin_is_possible_match_not_missing(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number="INV-77", gstin="27PQRSX5678L1Z2"),
        books_gst_df=_single_books_row(invoice_number="INV-77", gstin="29ABCDE1234F1Z7"),
        generate_ai_insights=False,
    )

    row = result["results"].iloc[0]
    assert row["match_status"] == "possible_match"
    assert row["match_level"] == "P4_WEAK_INVOICE"
    assert "Check GSTIN difference" in row["action_required"]
    assert "Confirm manually before marking as matched" in row["action_required"]
    assert result["summary"]["missing_in_gstr"] == 0


def test_gst_invoice_with_amount_difference_is_amount_mismatch(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number="INV-77", taxable_value=1200, igst_amount=216),
        books_gst_df=_single_books_row(invoice_number="INV-77", taxable_value=1000, igst_amount=180),
        generate_ai_insights=False,
    )

    row = result["results"].iloc[0]
    assert row["match_status"] == "amount_mismatch"
    assert row["match_level"] == "P1_EXACT"


def test_gst_invoice_absent_from_selected_gstr_is_missing_in_gstr(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(
            invoice_number="OTHER-1",
            supplier_name="Different Supplier",
            taxable_value=3000,
            igst_amount=540,
        ),
        books_gst_df=_single_books_row(invoice_number="INV-77"),
        generate_ai_insights=False,
    )

    statuses = set(result["results"]["match_status"])
    assert "missing_in_gstr" in statuses
    assert result["summary"]["missing_in_gstr"] == 1


def test_gst_reconciliation_requires_selected_upload_invoice_dates(workspace_tmp_path):
    gstr_df = _gstr_lines_df()
    gstr_df["invoice_date"] = ""

    with pytest.raises(RuntimeError, match="Could not determine GSTR period from uploaded invoice dates."):
        run_gst_reconciliation(
            workspace_tmp_path,
            gstr_lines_df=gstr_df,
            books_gst_df=_books_gst_df(),
        )


def test_ai_response_parser_strips_markdown_and_json_artifacts():
    row = pd.Series(
        {
            "match_status": "possible_match",
            "match_reason": "amount differs by 3764.00; weak narration/party text similarity",
        }
    )
    parsed = _parse_ai_response(
        """```json
        {"ai_summary": "Amount differs by 3764.00 and narration similarity is weak, so this needs review.", "ai_recommendation": "Check receipt reference before approval.", "ai_risk_level": "medium"}
        ```""",
        row,
        "bank",
    )

    assert parsed["ai_summary"] == "Amount differs by 3764.00 and narration similarity is weak, so this needs review."
    assert parsed["ai_recommendation"] == "Check receipt reference before approval."
    assert parsed["ai_risk_level"] == "high"
    assert _word_count(parsed["ai_summary"]) >= 8
    assert "```" not in parsed["ai_summary"]
    assert "{" not in parsed["ai_summary"]


def test_ai_response_parser_has_clean_fallback_for_bad_json():
    row = pd.Series(
        {
            "match_status": "possible_match",
            "match_reason": "amount differs by 3764.00; weak narration/party text similarity",
        }
    )
    parsed = _parse_ai_response('```json {"ai_summary": "Bank", "ai_recommendation": "Review", "ai_risk_level": "low"} ```', row, "bank")

    assert parsed["ai_summary"] == "Amount differs by 3764.00 and narration/party similarity is weak, so this needs review."
    assert parsed["ai_recommendation"] == "Verify bank narration against voucher and party ledger."
    assert parsed["ai_risk_level"] == "high"
    assert _word_count(parsed["ai_summary"]) >= 8
    assert "```" not in parsed["ai_summary"]
    assert "{" not in parsed["ai_summary"]
    assert "}" not in parsed["ai_summary"]
