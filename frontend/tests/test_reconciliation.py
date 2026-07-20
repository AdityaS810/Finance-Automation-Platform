"""Tests for deterministic backend reconciliation flows."""

from __future__ import annotations

import pandas as pd
import pytest

import backend.reconciliation.bank_reconciliation as bank_reconciliation
import backend.reconciliation.gst_reconciliation as gst_reconciliation
from backend.ai.reconciliation_insights import _parse_ai_response, _rule_based_insight
from backend.reconciliation.bank_reconciliation import run_bank_reconciliation
from backend.reconciliation.gst_reconciliation import reconcile_gst_data, run_gst_reconciliation


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
    cgst_amount: float = 0,
    sgst_amount: float = 0,
    raw_invoice_value: float | None = None,
    source_type: str = "bill",
) -> pd.DataFrame:
    total_tax = igst_amount + cgst_amount + sgst_amount
    invoice_value = taxable_value + total_tax if raw_invoice_value is None else raw_invoice_value
    return pd.DataFrame(
        [
            {
                "books_record_id": f"book-{source_type}-{invoice_number}",
                "books_source_type": source_type,
                "party_name": party_name,
                "gstin": gstin,
                "invoice_number": invoice_number,
                "books_invoice_date": invoice_date,
                "books_taxable_value": taxable_value,
                "books_igst_amount": igst_amount,
                "books_cgst_amount": cgst_amount,
                "books_sgst_amount": sgst_amount,
                "books_tax_amount": total_tax,
                "books_invoice_value": invoice_value,
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
    assert {
        "gemini_suggestion",
        "gemini_confidence",
        "gemini_reason",
        "gemini_recommendation",
        "gemini_candidate_id",
    }.issubset(result["results"].columns)
    assert {"selected_upload_id", "selected_file_name", "reconciliation_timestamp"}.issubset(result["results"].columns)
    assert set(result["results"]["selected_upload_id"]) == {"bank-upload-1"}
    review_rows = result["results"][result["results"]["match_status"].isin(["possible_match", "unmatched"])]
    assert review_rows["ai_summary"].map(_word_count).ge(8).all()
    assert result["ai_status"] in {"enabled", "unavailable", "not_required"}
    assert result["export_path"].name == "bank_reconciliation_results.xlsx"
    assert result["export_path"].exists()


def test_bank_gemini_missing_config_falls_back_without_changing_statuses(monkeypatch, workspace_tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    baseline = bank_reconciliation.reconcile_bank_data(_bank_lines_df(), _accounting_df())

    result = run_bank_reconciliation(
        workspace_tmp_path,
        bank_lines_df=_bank_lines_df(),
        accounting_df=_accounting_df(),
        generate_ai_insights=False,
        use_gemini_suggestions=True,
    )

    assert result["results"]["match_status"].tolist() == baseline["match_status"].tolist()
    review_mask = result["results"]["match_status"].isin(bank_reconciliation.GEMINI_BANK_REVIEW_STATUSES)
    review_rows = result["results"][review_mask]
    matched_rows = result["results"][result["results"]["match_status"] == "matched"]
    assert not review_rows.empty
    assert set(review_rows["gemini_suggestion"]) == {"needs_manual_review"}
    assert set(review_rows["gemini_confidence"]) == {"low"}
    assert set(review_rows["gemini_reason"]) == {"Gemini suggestions are not configured."}
    assert matched_rows["gemini_suggestion"].eq("").all()


def test_bank_reconciliation_deduplicates_accounting_books_only_rows(workspace_tmp_path):
    bank_df = pd.DataFrame(
        [
            {
                "bank_line_id": "bank-unique",
                "upload_id": "bank-upload-dup-test",
                "bank_date": "2026-01-10",
                "bank_narration": "Unrelated bank receipt",
                "debit_amount": 0,
                "credit_amount": 1,
                "balance_amount": 1,
                "bank_amount": 1,
            }
        ]
    )
    accounting_df = pd.DataFrame(
        [
            {
                "accounting_record_id": "acct-razorpay-70000",
                "source_record_id": "zoho-payment-1",
                "source_line_id": "line-1",
                "source_type": "payment",
                "accounting_date": "2026-01-10",
                "accounting_party_name": "Payment to Razopay",
                "reference_number": "RAZORPAY-70000",
                "transaction_number": "PAY-1",
                "transaction_type": "vendor_payment",
                "accounting_amount": -70000,
            },
            {
                "accounting_record_id": "acct-razorpay-70000",
                "source_record_id": "zoho-payment-1",
                "source_line_id": "line-1",
                "source_type": "payment",
                "accounting_date": "2026-01-10",
                "accounting_party_name": "Payment to Razopay",
                "reference_number": "RAZORPAY-70000",
                "transaction_number": "PAY-1",
                "transaction_type": "vendor_payment",
                "accounting_amount": -70000,
            },
        ]
    )

    result = run_bank_reconciliation(
        workspace_tmp_path,
        bank_lines_df=bank_df,
        accounting_df=accounting_df,
        generate_ai_insights=False,
    )

    books_only_rows = result["results"][result["results"]["match_status"] == "books_not_in_bank"]
    assert len(books_only_rows.index) == 1
    assert books_only_rows["accounting_record_id"].tolist() == ["acct-razorpay-70000"]


def test_bank_reconciliation_bank_charge_opposite_sign_is_review_match(workspace_tmp_path):
    bank_df = pd.DataFrame(
        [
            {
                "bank_line_id": "bank-charge-1",
                "upload_id": "bank-upload-charge-test",
                "bank_date": "2026-01-28",
                "bank_narration": "Balance Based Chgs",
                "debit_amount": 5000,
                "credit_amount": 0,
                "balance_amount": 100000,
                "bank_amount": -5000,
            }
        ]
    )
    accounting_df = pd.DataFrame(
        [
            {
                "accounting_record_id": "acct-bank-charge-1",
                "accounting_date": "2026-01-31",
                "accounting_party_name": "Bank Charges",
                "reference_number": "BANK-CHARGE",
                "transaction_number": "JRN-1",
                "transaction_type": "journal",
                "accounting_amount": 5000,
            }
        ]
    )

    result = run_bank_reconciliation(
        workspace_tmp_path,
        bank_lines_df=bank_df,
        accounting_df=accounting_df,
        generate_ai_insights=False,
    )

    row = result["results"].iloc[0]
    assert row["match_status"] == "possible_match"
    assert row["accounting_record_id"] == "acct-bank-charge-1"
    assert row["confidence_score"] < 0.78
    assert "absolute value" in row["match_reason"]
    assert result["summary"]["books_not_in_bank"] == 0
    assert result["summary"]["bank_not_in_books"] == 0


def test_gst_reconciliation_matches_mismatches_and_exports_excel(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_gstr_lines_df(),
        books_gst_df=_books_gst_df(),
    )

    assert result["summary"]["matched"] == 3
    assert result["summary"]["uploaded_gstr_rows"] == 7
    assert result["summary"]["tax_component_mismatch"] == 0
    assert result["summary"]["amount_mismatch"] == 1
    assert result["summary"]["possible_match"] == 1
    assert result["summary"]["missing_in_books"] == 1
    assert result["summary"]["missing_in_gstr"] == 1
    assert len(result["results"].index) == 7
    assert not result["results"].empty
    assert {"ai_summary", "ai_recommendation", "ai_risk_level"}.issubset(result["results"].columns)
    assert {
        "gemini_suggestion",
        "gemini_confidence",
        "gemini_reason",
        "gemini_recommendation",
        "gemini_candidate_id",
    }.issubset(result["results"].columns)
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
        result["results"]["match_status"].isin(
            ["tax_component_mismatch", "amount_mismatch", "possible_match", "missing_in_books", "missing_in_gstr"]
        )
    ]
    assert review_rows["ai_summary"].map(_word_count).ge(8).all()
    assert result["ai_status"] in {"enabled", "unavailable", "not_required"}
    assert result["export_path"].name == "gst_reconciliation_results.xlsx"
    assert result["export_path"].exists()
    workbook = pd.ExcelFile(result["export_path"])
    assert {
        "Summary",
        "Tax Type Mismatches",
        "Amount Mismatches",
        "Missing in Books",
        "Missing in GSTR",
        "Possible Matches",
        "Matched",
        "Technical Audit",
    }.issubset(set(workbook.sheet_names))
    summary_sheet = pd.read_excel(workbook, sheet_name="Summary")
    assert summary_sheet.columns.tolist() == ["Metric", "Value", "Explanation"]
    assert summary_sheet["Metric"].tolist() == [
        "Selected GSTR file",
        "Reconciliation period",
        "Uploaded GSTR rows",
        "Exact matched",
        "Tax type mismatch",
        "Amount mismatch",
        "Possible match",
        "Missing in GSTR",
        "Missing in Books",
    ]
    assert "Books bill not found in uploaded GSTR" in set(summary_sheet["Explanation"].dropna())
    assert "GSTR invoice not found in Zoho/books" in set(summary_sheet["Explanation"].dropna())
    assert "Total matches but IGST/CGST/SGST breakup differs" in set(summary_sheet["Explanation"].dropna())
    assert "Likely same invoice but needs manual review" in set(summary_sheet["Explanation"].dropna())
    assert not {
        "Selected upload ID",
        "Selected upload time",
        "Books rows before period filter",
        "Books rows after period filter",
        "Reconciliation timestamp",
        "Tolerance used",
        "Nearby date tolerance days",
        "AI status",
        "AI rows processed",
    }.intersection(set(summary_sheet["Metric"]))
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


def test_gst_gemini_missing_config_falls_back_without_changing_statuses(monkeypatch, workspace_tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    baseline = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_gstr_lines_df(),
        books_gst_df=_books_gst_df(),
        generate_ai_insights=False,
    )

    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_gstr_lines_df(),
        books_gst_df=_books_gst_df(),
        generate_ai_insights=False,
        use_gemini_suggestions=True,
    )

    assert result["results"]["match_status"].tolist() == baseline["results"]["match_status"].tolist()
    review_mask = result["results"]["match_status"].isin(gst_reconciliation.GEMINI_GST_REVIEW_STATUSES)
    review_rows = result["results"][review_mask]
    matched_rows = result["results"][result["results"]["match_status"] == "matched"]
    assert not review_rows.empty
    assert set(review_rows["gemini_suggestion"]) == {"needs_manual_review"}
    assert set(review_rows["gemini_confidence"]) == {"low"}
    assert set(review_rows["gemini_reason"]) == {"Gemini suggestions are not configured."}
    assert matched_rows["gemini_suggestion"].eq("").all()


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


def test_gst_reconciliation_matches_purchase_invoice_recorded_as_expense(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number="EXP-77", supplier_name="Cloud Vendor"),
        books_gst_df=_single_books_row(invoice_number="EXP-77", party_name="Cloud Vendor", source_type="expense"),
        generate_ai_insights=False,
    )

    row = result["results"].iloc[0]
    assert row["match_status"] == "matched"
    assert row["books_source_type"] == "expense"


def test_gst_missing_in_books_mentions_expenses_when_only_bills_were_synced(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number="GSTR-ONLY"),
        books_gst_df=_single_books_row(invoice_number="OTHER-BILL", taxable_value=2500, igst_amount=450),
        generate_ai_insights=False,
    )

    row = result["results"].loc[result["results"]["match_status"] == "missing_in_books"].iloc[0]
    assert row["action_required"] == "Not found in Bills. Check Expenses or card transactions."


def test_gst_missing_in_books_mentions_all_synced_sources_when_expenses_were_searched(workspace_tmp_path):
    books_df = pd.concat(
        [
            _single_books_row(invoice_number="OTHER-BILL", source_type="bill", taxable_value=2500, igst_amount=450),
            _single_books_row(invoice_number="OTHER-EXP", source_type="expense", taxable_value=2500, igst_amount=450),
        ],
        ignore_index=True,
    )

    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number="GSTR-ONLY"),
        books_gst_df=books_df,
        generate_ai_insights=False,
    )

    row = result["results"].loc[result["results"]["match_status"] == "missing_in_books"].iloc[0]
    assert row["action_required"] == "Not found in any synced books source. Record may need to be entered in Zoho."


@pytest.mark.parametrize(
    ("books_invoice", "gstr_invoice", "expected_status", "reason_fragment"),
    [
        ("#20702", "20702", "matched", "different invoice number format"),
        ("014", "INV-PS-2526-014", "matched", "invoice format normalization"),
        ("17", "25-26/12/HSD17", "possible_match", "prefix/suffix difference"),
        ("0354", "M1/25-26/0354", "possible_match", "prefix/suffix difference"),
        ("#016", "2025-26/016", "possible_match", "prefix/suffix difference"),
        ("STPL/37/2025-26", "STPL/037/2025-26", "possible_match", "leading zero difference"),
    ],
)
def test_gst_invoice_format_safeguards_prevent_missing_in_gstr(
    workspace_tmp_path,
    books_invoice,
    gstr_invoice,
    expected_status,
    reason_fragment,
):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number=gstr_invoice),
        books_gst_df=_single_books_row(invoice_number=books_invoice),
        generate_ai_insights=False,
    )

    assert result["summary"]["missing_in_gstr"] == 0
    assert result["summary"]["missing_in_books"] == 0
    row = result["results"].iloc[0]
    assert row["match_status"] == expected_status
    assert reason_fragment in row["match_reason"]


@pytest.mark.parametrize(
    ("books_invoice", "gstr_invoice"),
    [
        ("#2271", "WT/25-26/2271"),
        ("014", "INV-PS-2526-014"),
    ],
)
def test_gst_safe_invoice_format_token_matches_are_matched(workspace_tmp_path, books_invoice, gstr_invoice):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number=gstr_invoice),
        books_gst_df=_single_books_row(invoice_number=books_invoice),
        generate_ai_insights=False,
    )

    row = result["results"].iloc[0]
    assert row["match_status"] == "matched"
    assert row["status_label"] == "Matched"
    assert row["match_level"] == "P4_INVOICE_TOKEN"
    assert row["match_reason"] == (
        "Matched using invoice format normalization; GSTIN, date, taxable value, and invoice value match."
    )


@pytest.mark.parametrize(
    ("books_invoice", "gstr_invoice", "books_date", "gstr_date"),
    [
        ("#6935", "TIO26HR100568684", "2026-05-01", "2026-05-01"),
        ("JULY", "TIO26HR100789563", "2026-05-01", "2026-05-04"),
    ],
)
def test_gst_unrelated_invoice_with_gstin_typo_stays_possible_match(
    workspace_tmp_path,
    books_invoice,
    gstr_invoice,
    books_date,
    gstr_date,
):
    result = reconcile_gst_data(
        _single_gstr_row(
            invoice_number=gstr_invoice,
            gstin="29ABCDE1234F1Z7",
            invoice_date=gstr_date,
            supplier_name="Microsoft India",
        ),
        _single_books_row(
            invoice_number=books_invoice,
            gstin="29ABCDEI234F1Z7",
            invoice_date=books_date,
            party_name="Microsoft India",
        ),
    )

    row = result.iloc[0]
    assert row["match_status"] == "possible_match"
    assert row["match_level"] == "P5_AMOUNT_DATE_CANDIDATE"
    assert row["match_reason"] == "Possible match; GSTIN differs, possible manual typo."
    assert row["action_required"] == "Check vendor GSTIN in Zoho/books."


@pytest.mark.parametrize(
    ("invoice_number", "taxable_value", "igst_amount", "raw_total", "gstr_invoice"),
    [
        ("INV-PS-2526-017", 119340, 21481.2, 128887.2, 140821.2),
        ("INV-PS-2526-022", 34425, 6196.5, 37179, 40621.5),
    ],
)
def test_gst_reconciliation_uses_calculated_books_invoice_value_when_zoho_total_is_wrong(
    workspace_tmp_path,
    invoice_number,
    taxable_value,
    igst_amount,
    raw_total,
    gstr_invoice,
):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(
            invoice_number=invoice_number,
            taxable_value=taxable_value,
            igst_amount=igst_amount,
        ),
        books_gst_df=_single_books_row(
            invoice_number=invoice_number,
            taxable_value=taxable_value,
            igst_amount=igst_amount,
            raw_invoice_value=raw_total,
        ),
        generate_ai_insights=False,
    )

    assert result["summary"]["matched"] == 1
    row = result["results"].iloc[0]
    assert row["match_status"] == "matched"
    assert row["zoho_invoice_value"] == pytest.approx(gstr_invoice)
    assert row["raw_invoice_value"] == pytest.approx(raw_total)
    assert row["calculated_invoice_value"] == pytest.approx(gstr_invoice)
    assert row["invoice_value_source"] == "calculated_from_tax_components"
    assert row["match_reason"] == "Matched using calculated books invoice value from taxable + GST components"


def test_gst_reconciliation_flags_tax_component_type_difference_even_when_totals_match(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(taxable_value=1000, igst_amount=180),
        books_gst_df=_single_books_row(taxable_value=1000, igst_amount=0, cgst_amount=90, sgst_amount=90),
        generate_ai_insights=False,
    )

    assert result["summary"]["matched"] == 0
    assert result["summary"]["tax_component_mismatch"] == 1
    assert result["summary"]["amount_mismatch"] == 0
    row = result["results"].iloc[0]
    assert row["match_status"] == "tax_component_mismatch"
    assert row["status_label"] == "Tax Type Mismatch"
    assert row["zoho_invoice_value"] == pytest.approx(row["gstr_invoice_value"])
    assert row["match_reason"] == "Total amount matches, but GST tax component type differs between books and GSTR."
    assert row["action_required"] == "Check place of supply and GST tax treatment in Zoho/books."


def test_gst_reconciliation_keeps_taxable_or_invoice_value_difference_as_amount_mismatch(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(invoice_number="19510", taxable_value=131285, igst_amount=23631.3),
        books_gst_df=_single_books_row(invoice_number="19510", taxable_value=131250, igst_amount=23625),
        generate_ai_insights=False,
    )

    row = result["results"].iloc[0]
    assert result["summary"]["tax_component_mismatch"] == 0
    assert result["summary"]["amount_mismatch"] == 1
    assert row["match_status"] == "amount_mismatch"


def test_gst_reconciliation_exact_tax_components_same_is_matched(workspace_tmp_path):
    result = run_gst_reconciliation(
        workspace_tmp_path,
        gstr_lines_df=_single_gstr_row(taxable_value=15000, igst_amount=2700),
        books_gst_df=_single_books_row(taxable_value=15000, igst_amount=2700),
        generate_ai_insights=False,
    )

    row = result["results"].iloc[0]
    assert result["summary"]["matched"] == 1
    assert result["summary"]["tax_component_mismatch"] == 0
    assert row["match_status"] == "matched"


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

    assert parsed["ai_summary"] == "Status possible_match has amount gap 3764.00; verify whether charges, TDS, or grouping explain the gap."
    assert parsed["ai_recommendation"] == "Verify voucher against amount gap 3764.00; approve only if party or reference matches."
    assert parsed["ai_risk_level"] == "high"
    assert _word_count(parsed["ai_summary"]) >= 8
    assert "```" not in parsed["ai_summary"]
    assert "{" not in parsed["ai_summary"]
    assert "}" not in parsed["ai_summary"]


def test_bank_not_in_books_risk_is_amount_aware():
    small_row = pd.Series(
        {
            "match_status": "bank_not_in_books",
            "bank_amount": -8.06,
            "match_reason": "Bank statement transaction was not found in accounting records.",
        }
    )
    large_row = pd.Series(
        {
            "match_status": "bank_not_in_books",
            "bank_amount": -1000,
            "match_reason": "Bank statement transaction was not found in accounting records.",
        }
    )

    small_insight = _rule_based_insight(small_row, "bank")
    large_insight = _rule_based_insight(large_row, "bank")

    assert small_insight["ai_risk_level"] == "medium"
    assert "bank charge, fee, GST charge" in small_insight["ai_recommendation"]
    assert large_insight["ai_risk_level"] == "high"
    assert "payment, expense, or journal" in large_insight["ai_recommendation"]


def test_books_not_in_bank_risk_is_amount_aware():
    small_row = pd.Series(
        {
            "match_status": "books_not_in_bank",
            "accounting_amount": 999.99,
            "match_reason": "Accounting-side transaction was not found in uploaded bank statement.",
        }
    )
    large_row = pd.Series(
        {
            "match_status": "books_not_in_bank",
            "accounting_amount": 1000,
            "match_reason": "Accounting-side transaction was not found in uploaded bank statement.",
        }
    )

    assert _rule_based_insight(small_row, "bank")["ai_risk_level"] == "medium"
    assert _rule_based_insight(large_row, "bank")["ai_risk_level"] == "high"
