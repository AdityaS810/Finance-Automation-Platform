"""Tests for backend MIS report generation."""

from __future__ import annotations

from backend.reports.mis_report_generator import generate_mis_report, get_mis_metrics


def test_get_mis_metrics_returns_expected_keys():
    result = get_mis_metrics("FY25-26")

    assert "Gross Margin FY" in result
    assert result["Financial Year"] == "FY25-26"


def test_generate_mis_report_creates_excel_file(workspace_tmp_path):
    result = generate_mis_report("FY25-26", workspace_tmp_path)

    assert result["status"] == "success"
    assert result["report_path"].exists()
