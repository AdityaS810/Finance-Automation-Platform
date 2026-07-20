"""Tests for dynamic MIS report generation."""

from __future__ import annotations

import json

import pandas as pd
from openpyxl import load_workbook

from backend.reports.mis_report_generator import (
    generate_consolidated_balance_sheet_report,
    generate_consolidated_pl_report,
    generate_mis_report,
    get_mis_metrics,
)


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
                "report_month": pd.Timestamp("2025-07-01 00:00:00", tz="Asia/Kolkata"),
                "invoice_count": 1,
                "bill_count": 1,
                "journal_count": 0,
                "revenue_amount": 50000,
                "expense_amount": 15000,
                "journal_adjustment_amount": 0,
                "profit_amount": 35000,
            },
            {
                "report_month": pd.Timestamp("2026-01-01 00:00:00", tz="Asia/Kolkata"),
                "invoice_count": 1,
                "bill_count": 1,
                "journal_count": 0,
                "revenue_amount": 80000,
                "expense_amount": 20000,
                "journal_adjustment_amount": 0,
                "profit_amount": 60000,
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
                "invoice_count": 3,
                "invoice_total_amount": 230000,
                "bill_count": 6,
                "bill_total_amount": 137000,
                "journal_count": 1,
                "journal_total_amount": 5000,
                "source_org_name": "All Organizations",
                "reporting_currency": "INR",
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
                "invoice_date": "2025-07-10",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 50000,
                "reference_number": "BSM-JUL",
                "notes": "Platform AMC",
                "raw_json": json.dumps(
                    {
                        "invoice_number": "INV-002",
                        "customer_name": "BSM Platform",
                        "date": "2025-07-10",
                        "currency_code": "INR",
                        "total": 50000,
                        "reference_number": "BSM-JUL",
                        "line_items": [{"name": "Platform AMC", "item_total": 50000}],
                    }
                ),
            },
            {
                "invoice_number": "INV-003",
                "customer_name": "Tech Mahindra",
                "invoice_date": "2026-01-08",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 80000,
                "reference_number": "TECHM-JAN",
                "notes": "TechM Q4 billing",
                "raw_json": json.dumps(
                    {
                        "invoice_number": "INV-003",
                        "customer_name": "Tech Mahindra",
                        "date": "2026-01-08",
                        "currency_code": "INR",
                        "total": 80000,
                        "reference_number": "TECHM-JAN",
                        "line_items": [{"name": "Tech Mahindra billing", "item_total": 80000}],
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
                "vendor_name": "Parashar",
                "bill_date": "2025-07-05",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 30000,
                "notes": "Delivery salary",
                "raw_json": json.dumps(
                    {
                        "bill_number": "BILL-002",
                        "vendor_name": "Parashar",
                        "date": "2025-07-05",
                        "currency_code": "INR",
                        "total": 30000,
                        "line_items": [{"name": "Delivery payroll", "description": "Delivery salary", "item_total": 30000}],
                    }
                ),
            },
            {
                "bill_number": "BILL-003",
                "vendor_name": "Kashish",
                "bill_date": "2025-07-11",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 15000,
                "notes": "BSM contractor",
                "raw_json": json.dumps(
                    {
                        "bill_number": "BILL-003",
                        "vendor_name": "Kashish",
                        "date": "2025-07-11",
                        "currency_code": "INR",
                        "total": 15000,
                        "line_items": [{"name": "BSM contractor", "description": "Delivery support", "item_total": 15000}],
                    }
                ),
            },
            {
                "bill_number": "BILL-004",
                "vendor_name": "Ad Vendor",
                "bill_date": "2025-09-05",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 12000,
                "notes": "Marketing campaign",
                "raw_json": json.dumps(
                    {
                        "bill_number": "BILL-002",
                        "vendor_name": "Ad Vendor",
                        "date": "2025-09-05",
                        "currency_code": "INR",
                        "total": 12000,
                        "line_items": [{"name": "Advertising", "description": "Marketing campaign", "item_total": 12000}],
                    }
                ),
            },
            {
                "bill_number": "BILL-005",
                "vendor_name": "Dorothea Stoll",
                "bill_date": "2025-10-05",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 20000,
                "notes": "Account & delivery manager",
                "raw_json": json.dumps(
                    {
                        "bill_number": "BILL-005",
                        "vendor_name": "Dorothea Stoll",
                        "date": "2025-10-05",
                        "currency_code": "INR",
                        "total": 20000,
                        "line_items": [{"name": "Dorothea", "description": "Account & delivery manager", "item_total": 20000}],
                    }
                ),
            },
            {
                "bill_number": "BILL-006",
                "vendor_name": "Innov8",
                "bill_date": "2026-01-05",
                "currency_code": "INR",
                "exchange_rate": 1,
                "total_amount": 20000,
                "notes": "Office rent",
                "raw_json": json.dumps(
                    {
                        "bill_number": "BILL-003",
                        "vendor_name": "Innov8",
                        "date": "2026-01-05",
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
    result = get_mis_metrics(2025, _monthly_pl_df(), _dashboard_summary_df())

    assert "Revenue" in result
    assert "Profit" in result
    assert result["Financial Year"] == "FY 2025-26"
    assert result["Report Period"] == "FY 2025-26"


def test_generate_consolidated_pl_creates_quarter_excel_file(workspace_tmp_path):
    result = generate_consolidated_pl_report(
        2025,
        workspace_tmp_path,
        period_type="quarter",
        selected_quarter="Q1",
        monthly_pl_df=_monthly_pl_df(),
    )

    assert result["status"] == "success"
    assert result["report_path"].exists()
    assert result["report_path"].name == "Consolidated_PL_FY2025_26_quarter_Q1.xlsx"
    assert result["report_period"]["start_date"].isoformat() == "2025-04-01"
    assert result["report_period"]["end_date"].isoformat() == "2025-06-30"
    workbook = load_workbook(result["report_path"], data_only=False)
    worksheet = workbook["Consolidated P&L"]
    assert worksheet["B6"].value == 100000
    assert worksheet["B9"].value == "=B6-B8"
    assert worksheet["B17"].value == "=B12+B14-B16"


def test_generate_consolidated_balance_sheet_creates_as_of_excel_file(workspace_tmp_path):
    balance_sheet_df = pd.DataFrame(
        [
            {
                "as_of_date": "2025-09-30",
                "bank_cash": 250000,
                "receivables": 125000,
                "other_assets": 10000,
                "payables": 80000,
                "tax_liabilities": 15000,
                "equity_retained_earnings": 290000,
            },
            {
                "as_of_date": "2025-12-31",
                "bank_cash": 999999,
                "receivables": 999999,
                "payables": 999999,
            },
        ]
    )
    result = generate_consolidated_balance_sheet_report(
        2025,
        workspace_tmp_path,
        period_type="half_year",
        selected_half="H1",
        balance_sheet_df=balance_sheet_df,
    )

    assert result["status"] == "success"
    assert result["report_path"].exists()
    assert result["report_path"].name == "Consolidated_Balance_Sheet_FY2025_26_half_year_H1.xlsx"
    assert result["as_of_date"].isoformat() == "2025-09-30"
    workbook = load_workbook(result["report_path"], data_only=False)
    worksheet = workbook["Consolidated Balance Sheet"]
    assert worksheet["B7"].value == 250000
    assert worksheet["B8"].value == 125000
    assert worksheet["B10"].value == "=B6"
    assert worksheet["B18"].value == "=B10-B17"


def test_generate_mis_report_creates_full_year_excel_file(workspace_tmp_path):
    result = generate_mis_report(
        2025,
        workspace_tmp_path,
        monthly_pl_df=_monthly_pl_df(),
        dashboard_summary_df=_dashboard_summary_df(),
        bills_df=_bills_df(),
        invoices_df=_invoices_df(),
        journals_df=_journals_df(),
    )

    assert result["status"] == "success"
    assert result["report_path"].exists()
    assert result["report_path"].name == "MIS_PL_FY2025_26_full_year.xlsx"

    workbook = load_workbook(result["report_path"], read_only=False)
    quarterly_ws = workbook["Quarterly P&L"]
    monthly_ws = workbook["Monthly P&L"]
    cogs_ws = workbook["COGS Allocation Working"]

    assert quarterly_ws["B1"].value == "MIDOFFICE DATA  |  Profit & Loss Statement  |  FY 2025-26 (Apr 2025 – Mar 2026)"
    assert monthly_ws["B1"].value.startswith("Monthly P&L Detail  |  FY 2025-26 (Apr 2025 – Mar 2026)")
    assert monthly_ws.max_row == 34
    assert monthly_ws["C3"].value == "Apr 25"
    assert monthly_ws["N3"].value == "Mar 26"
    assert monthly_ws["B5"].value == "Client Revenue - Professional Services"
    assert monthly_ws["B6"].value == "Client Revenue - Product"
    assert monthly_ws["B7"].value == "Other Income"
    assert monthly_ws["B10"].value == "Offshore COGS"
    assert monthly_ws["B11"].value == "Onshore COGS"
    assert monthly_ws["B16"].value == "Onshore Consultant Experience"
    assert monthly_ws["B23"].value == "Consultant & Contractor Expense"
    assert monthly_ws["B27"].value == "Office Rent"
    assert monthly_ws["C5"].value == 100000
    assert monthly_ws["C7"].value == 5000
    assert monthly_ws["C10"].value == 40000
    assert monthly_ws["F10"].value == 45000
    assert monthly_ws["I11"].value == 582500
    assert monthly_ws["C12"].value == 25800
    assert monthly_ws["C13"].value == "=SUM(C10:C12)"
    assert monthly_ws["C14"].value == "=C8-C13"
    assert monthly_ws["C33"].value == "=SUM(C20,C25,C32)"
    assert monthly_ws["C34"].value == "=C14-C33"
    assert quarterly_ws["C4"].value == "Q1\n(Apr-Jun 25)"
    assert quarterly_ws["F4"].value == "Q4\n(Jan-Mar 26)"
    assert quarterly_ws["G4"].value == "FY\n2025-26"
    assert quarterly_ws["B6"].value == "  Client Revenue - Professional Services"
    assert quarterly_ws["B7"].value == "  Client Revenue - Product"
    assert quarterly_ws["B8"].value == "  Other Income"
    assert quarterly_ws["B12"].value == "  Offshore COGS"
    assert quarterly_ws["B13"].value == "  Onshore COGS"
    assert quarterly_ws["B25"].value == "      Onshore Consultant Experience"
    assert quarterly_ws["B33"].value == "      Consultant & Contractor Expense"
    assert quarterly_ws["B38"].value == "      Office Rent"
    assert quarterly_ws["B14"].value in (None, "")
    assert quarterly_ws["B15"].value in (None, "")
    assert quarterly_ws["B16"].value in (None, "")
    assert quarterly_ws.row_dimensions[14].hidden is True
    assert quarterly_ws.row_dimensions[15].hidden is True
    assert quarterly_ws.row_dimensions[16].hidden is True
    assert quarterly_ws["D12"].value == "=SUM('COGS Allocation Working'!H4:J9,'COGS Allocation Working'!H12:J14)"
    assert quarterly_ws["E13"].value == "=SUM('COGS Allocation Working'!K10:M11)"
    assert cogs_ws["Q15"].value == "=SUM(E15:P15)"
    visible_labels = [monthly_ws.cell(row, 2).value for row in range(1, monthly_ws.max_row + 1)]
    assert "Rent (Innov8 co-working)" not in visible_labels
    assert "Consultant & Contractor Expense (StackPro + R&D vendors)" not in visible_labels
    assert "Ramki — S&M Allocation (100% Apr-Aug | 50% Sep-Mar)" not in visible_labels


def test_generate_mis_report_creates_quarter_report(workspace_tmp_path):
    result = generate_mis_report(
        2025,
        workspace_tmp_path,
        period_type="quarter",
        selected_quarter="Q2",
        monthly_pl_df=_monthly_pl_df(),
        dashboard_summary_df=_dashboard_summary_df(),
        bills_df=_bills_df(),
        invoices_df=_invoices_df(),
        journals_df=_journals_df(),
    )

    workbook = load_workbook(result["report_path"], read_only=False)
    quarterly_ws = workbook["Quarterly P&L"]
    monthly_ws = workbook["Monthly P&L"]

    assert result["report_path"].name == "MIS_PL_FY2025_26_quarter_Q2.xlsx"
    assert "Q2 FY 2025-26" in quarterly_ws["B1"].value
    assert quarterly_ws["C4"].value == "Q2\n(Jul-Sep 25)"
    assert quarterly_ws.column_dimensions["D"].hidden is True
    assert monthly_ws["C3"].value == "Jul 25"
    assert monthly_ws["E3"].value == "Sep 25"
    assert monthly_ws.column_dimensions["F"].hidden is True


def test_generate_mis_report_creates_half_year_report(workspace_tmp_path):
    result = generate_mis_report(
        "FY25-26",
        workspace_tmp_path,
        period_type="half_year",
        selected_half="H1",
        monthly_pl_df=_monthly_pl_df(),
        dashboard_summary_df=_dashboard_summary_df(),
        bills_df=_bills_df(),
        invoices_df=_invoices_df(),
        journals_df=_journals_df(),
    )

    workbook = load_workbook(result["report_path"], read_only=False)
    quarterly_ws = workbook["Quarterly P&L"]
    monthly_ws = workbook["Monthly P&L"]

    assert result["report_path"].name == "MIS_PL_FY2025_26_half_year_H1.xlsx"
    assert "H1 FY 2025-26" in quarterly_ws["B1"].value
    assert quarterly_ws["C4"].value == "Q1\n(Apr-Jun 25)"
    assert quarterly_ws["D4"].value == "Q2\n(Jul-Sep 25)"
    assert quarterly_ws.column_dimensions["E"].hidden is True
    assert monthly_ws["C3"].value == "Apr 25"
    assert monthly_ws["H3"].value == "Sep 25"
    assert monthly_ws.column_dimensions["I"].hidden is True
