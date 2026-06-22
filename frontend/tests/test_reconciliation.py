"""Tests for deterministic backend reconciliation flows."""

from __future__ import annotations

import pandas as pd

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
                "bank_date": "2026-05-02",
                "bank_narration": "Receipt from Alpha Customer INV-1001",
                "bank_amount": 100000,
            },
            {
                "bank_line_id": "bank-2",
                "bank_date": "2026-05-04",
                "bank_narration": "Payment to Beta Vendor",
                "bank_amount": -40000,
            },
            {
                "bank_line_id": "bank-3",
                "bank_date": "2026-05-10",
                "bank_narration": "Unknown UPI credit",
                "bank_amount": 999,
            },
            {
                "bank_line_id": "bank-3-duplicate",
                "bank_date": "2026-05-10",
                "bank_narration": "Unknown UPI credit",
                "bank_amount": 999,
            },
            {
                "bank_line_id": "opening-1",
                "bank_date": "2026-05-01",
                "bank_narration": "Opening Balance",
                "bank_amount": 25000,
            },
            {
                "bank_line_id": "opening-duplicate",
                "bank_date": "2026-05-01",
                "bank_narration": "Opening Balance",
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
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1001",
                "taxable_value": 100000,
                "igst_amount": 18000,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 18000,
            },
            {
                "gstr_line_id": "gstr-2",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1002",
                "taxable_value": 50000,
                "igst_amount": 9000,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 9000,
            },
            {
                "gstr_line_id": "gstr-2-duplicate",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1002",
                "taxable_value": 50000,
                "igst_amount": 9000,
                "cgst_amount": 0,
                "sgst_amount": 0,
                "total_tax": 9000,
            },
        ]
    )


def _books_gst_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "books_record_id": "book-1",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1001",
                "books_taxable_value": 100000,
                "books_igst_amount": 18000,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 18000,
            },
            {
                "books_record_id": "book-2",
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-1002",
                "books_taxable_value": 55000,
                "books_igst_amount": 9900,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 9900,
            },
            {
                "books_record_id": "book-3",
                "gstin": "27PQRSX5678L1Z2",
                "invoice_number": "INV-9999",
                "books_taxable_value": 25000,
                "books_igst_amount": 4500,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 4500,
            },
            {
                "books_record_id": "book-3-duplicate",
                "gstin": "27PQRSX5678L1Z2",
                "invoice_number": "INV-9999",
                "books_taxable_value": 25000,
                "books_igst_amount": 4500,
                "books_cgst_amount": 0,
                "books_sgst_amount": 0,
                "books_tax_amount": 4500,
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
    assert result["summary"]["unmatched"] >= 1
    assert result["summary"]["ignored_opening_balance"] == 1
    assert result["summary"]["transaction_records"] == 3
    assert not result["results"].empty
    assert "ignored_opening_balance" in set(result["results"]["match_status"])
    accounting_ids = result["results"].loc[result["results"]["accounting_record_id"] != "", "accounting_record_id"]
    assert accounting_ids.is_unique
    assert {"ai_summary", "ai_recommendation", "ai_risk_level"}.issubset(result["results"].columns)
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

    assert result["summary"]["matched"] == 1
    assert result["summary"]["mismatch"] == 1
    assert result["summary"]["missing_in_gstr"] == 1
    assert len(result["results"].index) == 3
    assert not result["results"].empty
    assert {"ai_summary", "ai_recommendation", "ai_risk_level"}.issubset(result["results"].columns)
    review_rows = result["results"][result["results"]["match_status"].isin(["mismatch", "missing_in_books", "missing_in_gstr"])]
    assert review_rows["ai_summary"].map(_word_count).ge(8).all()
    assert result["ai_status"] in {"enabled", "unavailable", "not_required"}
    assert result["export_path"].name == "gst_reconciliation_results.xlsx"
    assert result["export_path"].exists()


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
