"""Tests for deterministic backend reconciliation flows."""

from __future__ import annotations

import pandas as pd

from backend.reconciliation.bank_reconciliation import run_bank_reconciliation
from backend.reconciliation.gst_reconciliation import run_gst_reconciliation


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
    assert not result["results"].empty
    assert {"ai_summary", "ai_recommendation", "ai_risk_level"}.issubset(result["results"].columns)
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
    assert not result["results"].empty
    assert {"ai_summary", "ai_recommendation", "ai_risk_level"}.issubset(result["results"].columns)
    assert result["ai_status"] in {"enabled", "unavailable", "not_required"}
    assert result["export_path"].name == "gst_reconciliation_results.xlsx"
    assert result["export_path"].exists()
