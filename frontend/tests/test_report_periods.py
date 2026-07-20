"""Tests for dynamic MIS report period helpers."""

from __future__ import annotations

from datetime import date

import pytest

from backend.reports.report_periods import (
    get_consolidated_report_period,
    get_fy_label,
    get_selected_report_period,
    parse_financial_year_start,
)


def test_consolidated_report_periods_support_quarter_six_months_and_full_year():
    quarter = get_consolidated_report_period(2025, "quarter", selected_quarter="Q3")
    half_year = get_consolidated_report_period(2025, "half_year", selected_half="H2")
    full_year = get_consolidated_report_period(2025, "full_year")

    assert (quarter["start_date"], quarter["end_date"]) == (date(2025, 10, 1), date(2025, 12, 31))
    assert (half_year["start_date"], half_year["end_date"]) == (date(2025, 10, 1), date(2026, 3, 31))
    assert (full_year["start_date"], full_year["end_date"]) == (date(2025, 4, 1), date(2026, 3, 31))


def test_parse_financial_year_start_accepts_short_and_long_labels():
    assert parse_financial_year_start("FY25-26") == 2025
    assert parse_financial_year_start("FY 2024-25") == 2024
    assert parse_financial_year_start(2026) == 2026


def test_full_year_period_for_fy_2025_26():
    report_period = get_selected_report_period(2025, "full_year")

    assert get_fy_label(2025) == "FY 2025-26"
    assert report_period["start_date"] == date(2025, 4, 1)
    assert report_period["end_date"] == date(2026, 3, 31)
    assert report_period["month_keys"][0] == "2025-04"
    assert report_period["month_keys"][-1] == "2026-03"
    assert report_period["summary_headers"] == [
        "Q1\n(Apr-Jun 25)",
        "Q2\n(Jul-Sep 25)",
        "Q3\n(Oct-Dec 25)",
        "Q4\n(Jan-Mar 26)",
    ]


def test_full_year_period_for_fy_2024_25():
    report_period = get_selected_report_period(2024, "full_year")

    assert report_period["fy_label"] == "FY 2024-25"
    assert report_period["start_date"] == date(2024, 4, 1)
    assert report_period["end_date"] == date(2025, 3, 31)
    assert report_period["month_keys"][0] == "2024-04"
    assert report_period["month_keys"][-1] == "2025-03"


def test_q1_period_for_fy_2025_26():
    report_period = get_selected_report_period(2025, "quarter", selected_quarter="Q1")

    assert report_period["period_name"] == "Q1 FY 2025-26"
    assert report_period["start_date"] == date(2025, 4, 1)
    assert report_period["end_date"] == date(2025, 6, 30)
    assert report_period["month_keys"] == ["2025-04", "2025-05", "2025-06"]


def test_q4_period_crosses_into_next_calendar_year():
    report_period = get_selected_report_period(2025, "quarter", selected_quarter="Q4")

    assert report_period["start_date"] == date(2026, 1, 1)
    assert report_period["end_date"] == date(2026, 3, 31)
    assert report_period["month_keys"] == ["2026-01", "2026-02", "2026-03"]
    assert report_period["summary_headers"] == ["Q4\n(Jan-Mar 26)"]


def test_half_year_periods_for_fy_2025_26():
    h1_period = get_selected_report_period(2025, "half_year", selected_half="H1")
    h2_period = get_selected_report_period(2025, "half_year", selected_half="H2")

    assert h1_period["month_keys"] == ["2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09"]
    assert h2_period["month_keys"] == ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]
    assert h1_period["summary_headers"] == ["Q1\n(Apr-Jun 25)", "Q2\n(Jul-Sep 25)"]
    assert h2_period["summary_headers"] == ["Q3\n(Oct-Dec 25)", "Q4\n(Jan-Mar 26)"]


def test_single_month_period_for_april_2025():
    report_period = get_selected_report_period(2025, "month", selected_month=4)

    assert report_period["period_name"] == "Apr 2025"
    assert report_period["start_date"] == date(2025, 4, 1)
    assert report_period["end_date"] == date(2025, 4, 30)
    assert report_period["summary_headers"] == ["Apr 25"]


def test_custom_period_within_financial_year():
    report_period = get_selected_report_period(
        2025,
        "custom",
        custom_start_date=date(2025, 7, 1),
        custom_end_date=date(2025, 11, 30),
    )

    assert report_period["month_keys"] == ["2025-07", "2025-08", "2025-09", "2025-10", "2025-11"]
    assert report_period["summary_headers"] == ["Q2\n(Jul-Sep 25)", "Q3\n(Oct-Nov 25)"]


def test_custom_period_outside_financial_year_raises_clear_error():
    with pytest.raises(ValueError, match="within the selected financial year"):
        get_selected_report_period(
            2025,
            "custom",
            custom_start_date=date(2025, 3, 31),
            custom_end_date=date(2025, 4, 30),
        )
