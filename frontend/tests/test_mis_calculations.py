"""Tests for template-based MIS report generation."""

from __future__ import annotations

import json

import pandas as pd
from openpyxl import load_workbook

from backend.reports.mis_report_generator import generate_mis_report, get_mis_metrics


def _monthly_pl_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "report_month": pd.Timestamp("2025-04-01 00:00:00", tz="Asia/Kolkata"),
                "invoice_count": 1,
                "bill_count": 1,
                "journal_count": 1,
                "revenue_amount": 100000,
                "expense_amount": 40000,
                "journal_adjustment_amount": 5000,
                "profit_amount": 65000,
            },
            {
                "report_month": pd.Timestamp("2025-05-01 00:00:00", tz="Asia/Kolkata"),
                "invoice_count": 1,
                "bill_count": 1,
                "journal_count": 0,
                "revenue_amount": 50000,
                "expense_amount": 20000,
                "journal_adjustment_amount": 0,
                "profit_amount": 30000,
            },
        ]
    )


def _dashboard_summary_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "generated_at": pd.Timestamp("2026-06-13 00:00:00", tz="UTC"),
                "account_count": 10,
                "contact_count": 5,
                "invoice_count": 2,
                "invoice_total_amount": 150000,
                "bill_count": 2,
                "bill_total_amount": 60000,
                "journal_count": 1,
                "journal_total_amount": 5000,
            }
        ]
    )


def _invoices_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "invoice_number": "INV-001",
                "customer_name": "Tech Mahindra",
                "invoice_date": "2025-04-18",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 100000,
                "reference_number": "TECHM-APR",
                "notes": "TechM milestone billing",
                "raw_json": json.dumps(
                    {
                        "invoice_number": "INV-001",
                        "customer_name": "Tech Mahindra",
                        "date": "2025-04-18",
                        "currency_code": "INR",
                        "total": 100000,
                        "reference_number": "TECHM-APR",
                        "line_items": [{"name": "Tech Mahindra billing", "item_total": 100000}],
                    }
                ),
            },
            {
                "invoice_number": "INV-002",
                "customer_name": "BSM Platform",
                "invoice_date": "2025-05-10",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 50000,
                "reference_number": "BSM-MAY",
                "notes": "Platform AMC",
                "raw_json": json.dumps(
                    {
                        "invoice_number": "INV-002",
                        "customer_name": "BSM Platform",
                        "date": "2025-05-10",
                        "currency_code": "INR",
                        "total": 50000,
                        "reference_number": "BSM-MAY",
                        "line_items": [{"name": "Platform AMC", "item_total": 50000}],
                    }
                ),
            },
        ]
    )


def _bills_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "bill_number": "BILL-001",
                "vendor_name": "Conam Tech",
                "bill_date": "2025-04-12",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 40000,
                "notes": "External delivery",
                "raw_json": json.dumps(
                    {
                        "bill_number": "BILL-001",
                        "vendor_name": "Conam Tech",
                        "date": "2025-04-12",
                        "currency_code": "INR",
                        "total": 40000,
                        "line_items": [{"name": "Conam delivery", "item_total": 40000}],
                    }
                ),
            },
            {
                "bill_number": "BILL-002",
                "vendor_name": "Innov8",
                "bill_date": "2025-05-05",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 20000,
                "notes": "Office rent",
                "raw_json": json.dumps(
                    {
                        "bill_number": "BILL-002",
                        "vendor_name": "Innov8",
                        "date": "2025-05-05",
                        "currency_code": "INR",
                        "total": 20000,
                        "line_items": [{"name": "Rent", "description": "Office rent", "item_total": 20000}],
                    }
                ),
            },
        ]
    )


def _journals_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "journal_number": "JRN-001",
                "journal_date": "2025-04-30",
                "reference_number": "FD-APR",
                "notes": "FD interest credited",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 5000,
                "raw_json": json.dumps(
                    {
                        "journal_number": "JRN-001",
                        "date": "2025-04-30",
                        "reference_number": "FD-APR",
                        "notes": "FD interest credited",
                        "currency_code": "INR",
                        "total": 5000,
                        "line_items": [{"account_name": "Fixed Deposit Interest", "amount": 5000}],
                    }
                ),
            }
        ]
    )


def test_get_mis_metrics_returns_expected_keys():
    result = get_mis_metrics("FY25-26", _monthly_pl_df(), _dashboard_summary_df())

    assert "Revenue" in result
    assert "Profit" in result
    assert result["Financial Year"] == "FY25-26"


def test_generate_mis_report_creates_ceo_format_excel_file(workspace_tmp_path):
    result = generate_mis_report(
        "FY25-26",
        workspace_tmp_path,
        monthly_pl_df=_monthly_pl_df(),
        dashboard_summary_df=_dashboard_summary_df(),
        bills_df=_bills_df(),
        invoices_df=_invoices_df(),
        journals_df=_journals_df(),
    )

    assert result["status"] == "success"
    assert result["report_path"].exists()
    assert result["report_path"].name == "MIS_PL_FY2526_generated.xlsx"

    workbook = load_workbook(result["report_path"], read_only=False)
    assert workbook.sheetnames == ["Quarterly P&L", "Monthly P&L", "COGS Allocation Working"]

    quarterly_ws = workbook["Quarterly P&L"]
    monthly_ws = workbook["Monthly P&L"]
    cogs_ws = workbook["COGS Allocation Working"]

    assert quarterly_ws.freeze_panes == "C5"
    assert monthly_ws.freeze_panes == "C4"
    assert cogs_ws.freeze_panes == "D4"

    assert quarterly_ws["B1"].value.startswith("MIDOFFICE DATA")
    assert monthly_ws["B1"].value.startswith("Monthly P&L Detail")
    assert cogs_ws["B1"].value.startswith("COGS Allocation Detail")

    assert monthly_ws["C5"].value == 100000
    assert monthly_ws["D6"].value == 50000
    assert monthly_ws["C7"].value == 5000
    assert monthly_ws["C8"].value == "=SUM(C5:C7)"
    assert monthly_ws["C10"].value == "='COGS Allocation Working'!E15+25800.0"

    assert quarterly_ws["C6"].value == "=SUM('Monthly P&L'!C5:E5)"
    assert quarterly_ws["G9"].value == "=SUM(G6:G8)"
    assert quarterly_ws["B51"].value == "KEY METRICS SUMMARY"

    assert cogs_ws["Q10"].value == "=SUM(E10:P10)"
    assert cogs_ws["Q15"].value == "=SUM(E15:P15)"
