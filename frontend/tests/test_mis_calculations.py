"""Tests for company-format MIS report generation."""

from __future__ import annotations

import json

import pandas as pd
from openpyxl import load_workbook

from backend.reports.mis_report_generator import generate_mis_report, get_mis_metrics


def _monthly_pl_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"report_month": pd.Timestamp("2025-04-01"), "revenue_amount": 0, "expense_amount": 1350000, "journal_adjustment_amount": 10000, "profit_amount": -1340000},
            {"report_month": pd.Timestamp("2025-05-01"), "revenue_amount": 0, "expense_amount": 1129000, "journal_adjustment_amount": 0, "profit_amount": -1129000},
            {"report_month": pd.Timestamp("2025-06-01"), "revenue_amount": 0, "expense_amount": 1125000, "journal_adjustment_amount": 0, "profit_amount": -1125000},
            {"report_month": pd.Timestamp("2025-07-01"), "revenue_amount": 200000, "expense_amount": 1325000, "journal_adjustment_amount": 0, "profit_amount": -1125000},
            {"report_month": pd.Timestamp("2025-08-01"), "revenue_amount": 0, "expense_amount": 1158000, "journal_adjustment_amount": 0, "profit_amount": -1158000},
            {"report_month": pd.Timestamp("2025-09-01"), "revenue_amount": 900000, "expense_amount": 1287500, "journal_adjustment_amount": 0, "profit_amount": -387500},
            {"report_month": pd.Timestamp("2025-10-01"), "revenue_amount": 1000000, "expense_amount": 1240000, "journal_adjustment_amount": 0, "profit_amount": -240000},
            {"report_month": pd.Timestamp("2025-11-01"), "revenue_amount": 0, "expense_amount": 1175000, "journal_adjustment_amount": 0, "profit_amount": -1175000},
            {"report_month": pd.Timestamp("2025-12-01"), "revenue_amount": 900000, "expense_amount": 1125000, "journal_adjustment_amount": 0, "profit_amount": -225000},
            {"report_month": pd.Timestamp("2026-01-01"), "revenue_amount": 0, "expense_amount": 1125000, "journal_adjustment_amount": 0, "profit_amount": -1125000},
            {"report_month": pd.Timestamp("2026-02-01"), "revenue_amount": 900000, "expense_amount": 1134000, "journal_adjustment_amount": 0, "profit_amount": -234000},
            {"report_month": pd.Timestamp("2026-03-01"), "revenue_amount": 162000, "expense_amount": 1193500, "journal_adjustment_amount": -40000, "profit_amount": -1071500},
        ]
    )


def _dashboard_summary_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "generated_at": pd.Timestamp("2026-06-16 00:00:00", tz="UTC"),
                "account_count": 10,
                "contact_count": 5,
                "invoice_count": 5,
                "invoice_total_amount": 3162000,
                "invoice_outstanding_amount": 200000,
                "bill_count": 21,
                "bill_total_amount": 14617000,
                "bill_outstanding_amount": 0,
                "journal_count": 3,
                "journal_total_amount": -30000,
                "customer_payment_count": 4,
                "customer_payment_total_amount": 2800000,
                "cash_balance": 1800000,
            }
        ]
    )


def _zoho_raw_df() -> pd.DataFrame:
    raw_rows = [
        {
            "entity_name": "journals",
            "raw_json": {
                "date": "2025-04-15",
                "currency_code": "INR",
                "journal_number": "JR-001",
                "notes": "Fixed deposit interest for April",
                "line_items": [{"account_name": "Fixed Deposit Interest Income", "description": "FD interest", "credit": 10000}],
            },
        },
        {
            "entity_name": "journals",
            "raw_json": {
                "date": "2026-03-31",
                "currency_code": "INR",
                "journal_number": "JR-002",
                "notes": "Annual performance bonus",
                "line_items": [{"account_name": "Payroll", "description": "Performance bonus", "debit": 50000}],
            },
        },
        {
            "entity_name": "invoices",
            "raw_json": {
                "date": "2025-07-05",
                "currency_code": "INR",
                "invoice_number": "INV-BSM-01",
                "customer_name": "BSM Platform",
                "balance": 0,
                "line_items": [{"name": "Platform AMC", "description": "BSM platform AMC", "item_total": 200000}],
            },
        },
        {
            "entity_name": "invoices",
            "raw_json": {
                "date": "2025-09-20",
                "currency_code": "INR",
                "invoice_number": "INV-TECHM-01",
                "customer_name": "Tech Mahindra",
                "balance": 0,
                "line_items": [{"name": "TechM Billings", "description": "TechM milestone billing", "item_total": 900000}],
            },
        },
        {
            "entity_name": "invoices",
            "raw_json": {
                "date": "2025-10-20",
                "currency_code": "INR",
                "invoice_number": "INV-TECHM-02",
                "customer_name": "Tech Mahindra",
                "balance": 200000,
                "line_items": [{"name": "TechM Billings", "description": "TechM milestone billing", "item_total": 1000000}],
            },
        },
        {
            "entity_name": "invoices",
            "raw_json": {
                "date": "2025-12-15",
                "currency_code": "INR",
                "invoice_number": "INV-TECHM-03",
                "customer_name": "Tech Mahindra",
                "balance": 0,
                "line_items": [{"name": "TechM Billings", "description": "TechM milestone billing", "item_total": 900000}],
            },
        },
        {
            "entity_name": "invoices",
            "raw_json": {
                "date": "2026-02-10",
                "currency_code": "INR",
                "invoice_number": "INV-TECHM-04",
                "customer_name": "Tech Mahindra",
                "balance": 0,
                "line_items": [{"name": "TechM Billings", "description": "TechM milestone billing", "item_total": 900000}],
            },
        },
        {
            "entity_name": "invoices",
            "raw_json": {
                "date": "2026-03-10",
                "currency_code": "INR",
                "invoice_number": "INV-BSM-02",
                "customer_name": "BSM Platform",
                "balance": 0,
                "line_items": [{"name": "Accrual Generation", "description": "Accrual generation feature enablement", "item_total": 162000}],
            },
        },
    ]

    for month in range(4, 13):
        raw_rows.append(
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": f"2025-{month:02d}-02",
                    "currency_code": "USD",
                    "bill_number": f"BILL-RAMKI-{month}",
                    "vendor_name": "Ramki Consulting",
                    "line_items": [{"description": "Ramki monthly leadership retainer", "amount": 12500}],
                },
            }
        )
    for month in range(1, 4):
        raw_rows.append(
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": f"2026-{month:02d}-02",
                    "currency_code": "USD",
                    "bill_number": f"BILL-RAMKI-2026-{month}",
                    "vendor_name": "Ramki Consulting",
                    "line_items": [{"description": "Ramki monthly leadership retainer", "amount": 12500}],
                },
            }
        )

    raw_rows.extend(
        [
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-07-10",
                    "currency_code": "INR",
                    "bill_number": "BILL-DEL-01",
                    "vendor_name": "Parashar Payroll",
                    "line_items": [{"description": "Parashar delivery salary", "amount": 100000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-08-10",
                    "currency_code": "INR",
                    "bill_number": "BILL-DEL-02",
                    "vendor_name": "Parashar Payroll",
                    "line_items": [{"description": "Parashar delivery salary", "amount": 100000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-09-10",
                    "currency_code": "INR",
                    "bill_number": "BILL-DEL-03",
                    "vendor_name": "Parashar Payroll",
                    "line_items": [{"description": "Parashar delivery salary", "amount": 100000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-10-10",
                    "currency_code": "USD",
                    "bill_number": "BILL-DOR-01",
                    "vendor_name": "Dorothea Stoll",
                    "line_items": [{"description": "Account and delivery support", "amount": 1000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-11-10",
                    "currency_code": "USD",
                    "bill_number": "BILL-DOR-02",
                    "vendor_name": "Dorothea Stoll",
                    "line_items": [{"description": "Account and delivery support", "amount": 1000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-07-12",
                    "currency_code": "INR",
                    "bill_number": "BILL-BSM-01",
                    "vendor_name": "BSM Crew",
                    "line_items": [{"description": "BSM contractor support", "amount": 50000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-10-12",
                    "currency_code": "INR",
                    "bill_number": "BILL-CONAM-01",
                    "vendor_name": "Conam Tech",
                    "line_items": [{"description": "External delivery vendor", "amount": 70000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-04-20",
                    "currency_code": "INR",
                    "bill_number": "BILL-SAL-01",
                    "vendor_name": "Pareek Payroll",
                    "line_items": [{"description": "R&D salary payout", "amount": 120000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-04-22",
                    "currency_code": "INR",
                    "bill_number": "BILL-CONS-01",
                    "vendor_name": "StackPro Solutions",
                    "line_items": [{"description": "StackPro one-time project payment", "amount": 190000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-08-22",
                    "currency_code": "INR",
                    "bill_number": "BILL-MKT-01",
                    "vendor_name": "LinkedIn Ads",
                    "line_items": [{"description": "Advertising campaign", "amount": 25000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-10-24",
                    "currency_code": "INR",
                    "bill_number": "BILL-TRV-01",
                    "vendor_name": "Corporate Travel Desk",
                    "line_items": [{"description": "Travel to customer site", "amount": 15000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-11-24",
                    "currency_code": "INR",
                    "bill_number": "BILL-MEAL-01",
                    "vendor_name": "Team Dinner",
                    "line_items": [{"description": "Meals & entertainment", "amount": 5000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-08-26",
                    "currency_code": "INR",
                    "bill_number": "BILL-SW-01",
                    "vendor_name": "OpenAI",
                    "line_items": [{"description": "Software subscription", "amount": 8000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-04-28",
                    "currency_code": "INR",
                    "bill_number": "BILL-RENT-01",
                    "vendor_name": "Innov8",
                    "line_items": [{"description": "Office rent", "amount": 30000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2026-01-18",
                    "currency_code": "INR",
                    "bill_number": "BILL-IT-01",
                    "vendor_name": "Airtel Broadband",
                    "line_items": [{"description": "Internet charges", "amount": 7000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2026-02-18",
                    "currency_code": "INR",
                    "bill_number": "BILL-LGL-01",
                    "vendor_name": "Legal Partner",
                    "line_items": [{"description": "Legal review", "amount": 9000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2026-03-18",
                    "currency_code": "INR",
                    "bill_number": "BILL-AUD-01",
                    "vendor_name": "Stat Audit",
                    "line_items": [{"description": "Audit fee", "amount": 6000}],
                },
            },
            {
                "entity_name": "bills",
                "raw_json": {
                    "date": "2025-05-16",
                    "currency_code": "INR",
                    "bill_number": "BILL-OTH-01",
                    "vendor_name": "General Admin",
                    "line_items": [{"description": "miscellaneous office expense", "amount": 4000}],
                },
            },
        ]
    )

    dataframe = pd.DataFrame(raw_rows)
    dataframe["run_id"] = "test-run"
    dataframe["source_record_id"] = [f"src-{index}" for index in range(len(dataframe))]
    dataframe["loaded_at"] = pd.Timestamp("2026-06-16 00:00:00", tz="UTC")
    dataframe["raw_json"] = dataframe["raw_json"].map(json.dumps)
    return dataframe


def test_get_mis_metrics_returns_expected_keys():
    result = get_mis_metrics("FY25-26", _monthly_pl_df(), _dashboard_summary_df())

    assert "Revenue" in result
    assert "Profit" in result
    assert result["Financial Year"] == "FY25-26"


def test_generate_mis_report_creates_company_format_workbook(workspace_tmp_path):
    result = generate_mis_report(
        "FY25-26",
        workspace_tmp_path,
        monthly_pl_df=_monthly_pl_df(),
        dashboard_summary_df=_dashboard_summary_df(),
        zoho_raw_df=_zoho_raw_df(),
    )

    assert result["status"] == "success"
    assert result["report_path"].exists()
    assert result["report_path"].name == "MIS_PL_FY2526_generated.xlsx"

    workbook = load_workbook(result["report_path"], data_only=False)
    assert workbook.sheetnames == ["Quarterly P&L", "Monthly P&L", "COGS Allocation Working"]

    quarterly_sheet = workbook["Quarterly P&L"]
    monthly_sheet = workbook["Monthly P&L"]
    cogs_sheet = workbook["COGS Allocation Working"]

    assert [quarterly_sheet["B4"].value, quarterly_sheet["C4"].value, quarterly_sheet["D4"].value, quarterly_sheet["E4"].value, quarterly_sheet["F4"].value, quarterly_sheet["G4"].value, quarterly_sheet["H4"].value] == [
        "Line Item",
        "Q1\n(Apr–Jun 25)",
        "Q2\n(Jul–Sep 25)",
        "Q3\n(Oct–Dec 25)",
        "Q4\n(Jan–Mar 26)",
        "FY\n2025-26",
        "% Rev",
    ]
    assert [monthly_sheet.cell(3, column_number).value for column_number in range(2, 15)] == [
        "Line Item",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
        "Jan",
        "Feb",
        "Mar",
    ]
    assert [cogs_sheet.cell(3, column_number).value for column_number in range(2, 18)] == [
        "Person / Vendor",
        "Allocation %",
        "Type",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
        "Jan",
        "Feb",
        "Mar",
        "FY Total",
    ]

    assert monthly_sheet["C8"].value == "=SUM(C5:C7)"
    assert monthly_sheet["C10"].value == "='COGS Allocation Working'!E10"
    assert quarterly_sheet["C9"].value == "=SUM(C6:C8)"
    assert quarterly_sheet["C18"].value == "=SUM(C12:C17)"
    assert quarterly_sheet["C20"].value == "=C9-C18"
    assert quarterly_sheet["C45"].value == "=SUM(C29,C35,C43)"
    assert quarterly_sheet["C47"].value == "=C9-SUM(C18,C45)"

    model = result["model"]
    assert model["validation_checks"]["total_revenue_matches"] is True
    assert model["validation_checks"]["total_cogs_matches"] is True
    assert model["validation_checks"]["gross_profit_matches"] is True
    assert model["validation_checks"]["total_opex_matches"] is True
    assert model["validation_checks"]["operating_profit_matches"] is True
    assert model["validation_checks"]["gross_margin_safe"] is True
    assert model["validation_checks"]["net_margin_safe"] is True
    assert model["validation_checks"]["bonuses_excluded_from_run_rate"] is True
    assert model["validation_checks"]["ramki_allocation_correct"] is True
    assert model["validation_checks"]["dorothea_allocation_correct"] is True

    assert model["monthly_rows"]["ramki_sm"][:5] == [1125000.0] * 5
    assert model["monthly_rows"]["ramki_cogs"][:5] == [0.0] * 5
    assert model["monthly_rows"]["ramki_sm"][5:] == [562500.0] * 7
    assert model["monthly_rows"]["ramki_cogs"][5:] == [562500.0] * 7
    assert model["monthly_rows"]["dorothea_cogs"][:6] == [0.0] * 6
    assert model["monthly_rows"]["dorothea_cogs"][6] == 90000.0

    assert "bonus" in quarterly_sheet["B62"].value.lower()
    assert "stackpro" in quarterly_sheet["B63"].value.lower()
