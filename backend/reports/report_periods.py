"""Helpers for dynamic Indian financial-year reporting periods."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any


PERIOD_TYPES = {"full_year", "month", "quarter", "half_year", "custom"}
QUARTER_ORDER = ("Q1", "Q2", "Q3", "Q4")
HALF_YEAR_ORDER = ("H1", "H2")
CONSOLIDATED_PERIOD_TYPES = {"quarter", "half_year", "full_year"}


def parse_financial_year_start(financial_year_start: int | str) -> int:
    """Accept 2025, FY25-26, or FY 2025-26 and return the start year."""
    if isinstance(financial_year_start, int):
        return financial_year_start

    cleaned = str(financial_year_start).strip().upper().replace(" ", "")
    cleaned = cleaned.replace("/", "-").replace("_", "-")
    if cleaned.startswith("FY"):
        cleaned = cleaned[2:]

    if cleaned.isdigit():
        return int(cleaned) if len(cleaned) == 4 else 2000 + int(cleaned)

    if "-" not in cleaned:
        raise ValueError(f"Unsupported financial year format: {financial_year_start}")

    start_token, _ = cleaned.split("-", maxsplit=1)
    if len(start_token) == 4:
        return int(start_token)
    if len(start_token) == 2:
        return 2000 + int(start_token)
    raise ValueError(f"Unsupported financial year format: {financial_year_start}")


def get_financial_year_dates(financial_year_start: int) -> tuple[date, date]:
    """Return the Apr-Mar date range for an Indian financial year."""
    return date(financial_year_start, 4, 1), date(financial_year_start + 1, 3, 31)


def get_fy_label(financial_year_start: int) -> str:
    """Return a readable FY label like FY 2025-26."""
    return f"FY {financial_year_start}-{str(financial_year_start + 1)[-2:]}"


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _to_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _format_month_range_short(months: list[dict[str, Any]]) -> str:
    start_month = months[0]["start_date"]
    end_month = months[-1]["start_date"]
    if len(months) == 1:
        return start_month.strftime("%b %y")
    return f"{start_month.strftime('%b')}-{end_month.strftime('%b %y')}"


def _format_month_range_long(start_date: date, end_date: date) -> str:
    return f"{start_date.strftime('%b %Y')} – {end_date.strftime('%b %Y')}"


def get_month_periods(financial_year_start: int) -> list[dict[str, Any]]:
    """Return the 12 month buckets inside one Indian financial year."""
    month_specs = [(financial_year_start, month_number) for month_number in range(4, 13)]
    month_specs.extend((financial_year_start + 1, month_number) for month_number in range(1, 4))

    periods: list[dict[str, Any]] = []
    for fiscal_index, (year_number, month_number) in enumerate(month_specs, start=1):
        start_date = date(year_number, month_number, 1)
        periods.append(
            {
                "key": start_date.strftime("%Y-%m"),
                "month_number": month_number,
                "fiscal_month_index": fiscal_index,
                "start_date": start_date,
                "end_date": _end_of_month(year_number, month_number),
                "label": start_date.strftime("%b %y"),
                "short_label": start_date.strftime("%b"),
                "title_label": start_date.strftime("%b %Y"),
                "quarter": QUARTER_ORDER[(fiscal_index - 1) // 3],
                "half_year": HALF_YEAR_ORDER[(fiscal_index - 1) // 6],
            }
        )
    return periods


def get_quarter_periods(financial_year_start: int) -> list[dict[str, Any]]:
    """Return Q1-Q4 period definitions for the selected financial year."""
    months = get_month_periods(financial_year_start)
    periods: list[dict[str, Any]] = []
    for quarter_index, quarter_key in enumerate(QUARTER_ORDER):
        start_index = quarter_index * 3
        quarter_months = months[start_index : start_index + 3]
        periods.append(
            {
                "key": quarter_key,
                "label": quarter_key,
                "start_date": quarter_months[0]["start_date"],
                "end_date": quarter_months[-1]["end_date"],
                "months": quarter_months,
                "month_keys": [month["key"] for month in quarter_months],
                "header_label": f"{quarter_key}\n({_format_month_range_short(quarter_months)})",
                "title_label": f"{quarter_key} {get_fy_label(financial_year_start)}",
                "display_range": _format_month_range_long(
                    quarter_months[0]["start_date"],
                    quarter_months[-1]["end_date"],
                ),
            }
        )
    return periods


def get_half_year_periods(financial_year_start: int) -> list[dict[str, Any]]:
    """Return H1 and H2 period definitions for the selected financial year."""
    months = get_month_periods(financial_year_start)
    periods: list[dict[str, Any]] = []
    for half_index, half_key in enumerate(HALF_YEAR_ORDER):
        start_index = half_index * 6
        half_months = months[start_index : start_index + 6]
        periods.append(
            {
                "key": half_key,
                "label": half_key,
                "start_date": half_months[0]["start_date"],
                "end_date": half_months[-1]["end_date"],
                "months": half_months,
                "month_keys": [month["key"] for month in half_months],
                "title_label": f"{half_key} {get_fy_label(financial_year_start)}",
                "display_range": _format_month_range_long(
                    half_months[0]["start_date"],
                    half_months[-1]["end_date"],
                ),
            }
        )
    return periods


def _validate_custom_period(
    custom_start_date: date | datetime | None,
    custom_end_date: date | datetime | None,
    fy_start_date: date,
    fy_end_date: date,
) -> tuple[date, date]:
    if custom_start_date is None or custom_end_date is None:
        raise ValueError("Custom period requires both start and end dates.")

    start_date = _to_date(custom_start_date)
    end_date = _to_date(custom_end_date)
    if start_date > end_date:
        raise ValueError("Custom period start date cannot be after the end date.")
    if start_date < fy_start_date or end_date > fy_end_date:
        raise ValueError("Custom period must stay within the selected financial year.")
    return start_date, end_date


def _selected_months_for_range(
    months: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    return [
        month
        for month in months
        if month["start_date"] <= end_date and month["end_date"] >= start_date
    ]


def _build_summary_periods(selected_months: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(selected_months) == 1:
        month = selected_months[0]
        return [
            {
                "key": month["key"],
                "label": month["title_label"],
                "header_label": month["label"],
                "month_keys": [month["key"]],
                "months": [month],
                "start_date": month["start_date"],
                "end_date": month["end_date"],
            }
        ]

    summary_periods: list[dict[str, Any]] = []
    for quarter_key in QUARTER_ORDER:
        quarter_months = [month for month in selected_months if month["quarter"] == quarter_key]
        if not quarter_months:
            continue
        summary_periods.append(
            {
                "key": quarter_key,
                "label": quarter_key,
                "header_label": f"{quarter_key}\n({_format_month_range_short(quarter_months)})",
                "month_keys": [month["key"] for month in quarter_months],
                "months": quarter_months,
                "start_date": quarter_months[0]["start_date"],
                "end_date": quarter_months[-1]["end_date"],
            }
        )
    return summary_periods


def get_selected_report_period(
    financial_year_start: int,
    period_type: str,
    selected_month: int | None = None,
    selected_quarter: str | None = None,
    selected_half: str | None = None,
    custom_start_date: date | datetime | None = None,
    custom_end_date: date | datetime | None = None,
) -> dict[str, Any]:
    """Return a selected reporting window plus the month and summary buckets to display."""
    if period_type not in PERIOD_TYPES:
        raise ValueError(f"Unsupported period type: {period_type}")

    fy_start_date, fy_end_date = get_financial_year_dates(financial_year_start)
    fy_label = get_fy_label(financial_year_start)
    months = get_month_periods(financial_year_start)
    quarter_map = {quarter["key"]: quarter for quarter in get_quarter_periods(financial_year_start)}
    half_map = {half["key"]: half for half in get_half_year_periods(financial_year_start)}

    if period_type == "full_year":
        start_date, end_date = fy_start_date, fy_end_date
        period_name = fy_label
        header_title = f"{fy_label} ({_format_month_range_long(start_date, end_date)})"
        file_suffix = "full_year"
    elif period_type == "month":
        selected_period = next((month for month in months if month["month_number"] == selected_month), None)
        if selected_period is None:
            raise ValueError("Please select a valid month inside the financial year.")
        start_date, end_date = selected_period["start_date"], selected_period["end_date"]
        period_name = selected_period["title_label"]
        header_title = f"{selected_period['title_label']} | {fy_label}"
        file_suffix = f"month_{selected_period['key'].replace('-', '_')}"
    elif period_type == "quarter":
        if selected_quarter not in quarter_map:
            raise ValueError("Please select one of Q1, Q2, Q3, or Q4.")
        selected_period = quarter_map[selected_quarter]
        start_date, end_date = selected_period["start_date"], selected_period["end_date"]
        period_name = selected_period["title_label"]
        header_title = f"{period_name} ({selected_period['display_range']})"
        file_suffix = f"quarter_{selected_quarter}"
    elif period_type == "half_year":
        if selected_half not in half_map:
            raise ValueError("Please select H1 or H2.")
        selected_period = half_map[selected_half]
        start_date, end_date = selected_period["start_date"], selected_period["end_date"]
        period_name = selected_period["title_label"]
        header_title = f"{period_name} ({selected_period['display_range']})"
        file_suffix = f"half_year_{selected_half}"
    else:
        start_date, end_date = _validate_custom_period(custom_start_date, custom_end_date, fy_start_date, fy_end_date)
        period_name = f"Custom {fy_label}"
        header_title = f"{period_name} ({start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')})"
        file_suffix = f"custom_{start_date.strftime('%Y_%m_%d')}_to_{end_date.strftime('%Y_%m_%d')}"

    selected_months = _selected_months_for_range(months, start_date, end_date)
    summary_periods = _build_summary_periods(selected_months)

    return {
        "financial_year_start": financial_year_start,
        "financial_year_end": financial_year_start + 1,
        "fy_label": fy_label,
        "period_type": period_type,
        "period_name": period_name,
        "header_title": header_title,
        "file_suffix": file_suffix,
        "start_date": start_date,
        "end_date": end_date,
        "months": selected_months,
        "month_keys": [month["key"] for month in selected_months],
        "summary_periods": summary_periods,
        "summary_headers": [period["header_label"] for period in summary_periods],
        "total_column_label": f"FY\n{financial_year_start}-{str(financial_year_start + 1)[-2:]}"
        if period_type == "full_year"
        else "Total",
    }


def get_consolidated_report_period(
    financial_year_start: int,
    period_type: str,
    selected_quarter: str | None = None,
    selected_half: str | None = None,
) -> dict[str, Any]:
    """Return a Quarter, 6 Months, or 1 Year reporting window.

    Consolidated P&L uses both dates, while the consolidated balance sheet uses
    the returned ``end_date`` as its as-of date.
    """
    if period_type not in CONSOLIDATED_PERIOD_TYPES:
        raise ValueError("Consolidated reports support Quarter, 6 Months, or 1 Year only.")
    return get_selected_report_period(
        financial_year_start,
        period_type,
        selected_quarter=selected_quarter,
        selected_half=selected_half,
    )
