"""Tests for backend MIS report generation."""

from __future__ import annotations

import pandas as pd
from openpyxl import load_workbook

from backend.reports.mis_report_generator import generate_mis_report, get_mis_metrics


def _monthly_pl_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "report_month": pd.Timestamp("2025-04-01 00:00:00", tz="Asia/Kolkata"),
                "invoice_count": 2,
                "bill_count": 1,
                "journal_count": 1,
                "revenue_amount": 100000,
                "expense_amount": 40000,
                "journal_adjustment_amount": 5000,
                "profit_amount": 65000,
            }
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
                "invoice_total_amount": 100000,
                "bill_count": 1,
                "bill_total_amount": 40000,
                "journal_count": 1,
                "journal_total_amount": 5000,
            }
        ]
    )


def test_get_mis_metrics_returns_expected_keys():
    result = get_mis_metrics("FY25-26", _monthly_pl_df(), _dashboard_summary_df())

    assert "Revenue" in result
    assert "Profit" in result
    assert result["Financial Year"] == "FY25-26"


def test_generate_mis_report_creates_excel_file(workspace_tmp_path):
    result = generate_mis_report(
        "FY25-26",
        workspace_tmp_path,
        monthly_pl_df=_monthly_pl_df(),
        dashboard_summary_df=_dashboard_summary_df(),
    )

    assert result["status"] == "success"
    assert result["report_path"].exists()
    assert result["report_path"].name == "MIS_PL_FY2526_generated.xlsx"

    workbook = load_workbook(result["report_path"], read_only=True)
    assert {"Summary", "Monthly P&L", "Dashboard KPIs"}.issubset(workbook.sheetnames)
    assert workbook["Summary"]["A2"].value.tzinfo is None
    assert workbook["Monthly P&L"]["A2"].value.tzinfo is None
    assert workbook["Dashboard KPIs"]["A2"].value.tzinfo is None
