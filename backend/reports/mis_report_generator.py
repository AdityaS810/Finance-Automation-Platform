"""Generate management-ready MIS reports from BigQuery Gold layer views."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


DEFAULT_PROJECT_ID = "internal-project-work-497507"
MIS_MONTHLY_PL_VIEW = "finance_gold.mis_monthly_pl"
DASHBOARD_SUMMARY_VIEW = "finance_gold.dashboard_summary"
SILVER_BILLS_VIEW = "finance_silver.fact_bills"
SILVER_INVOICES_VIEW = "finance_silver.fact_invoices"
INR_FORMAT = '"INR "#,##0.00;[Red]-"INR "#,##0.00'
PERCENT_FORMAT = "0.0%"
DATETIME_FORMAT = "dd-mmm-yyyy hh:mm"


load_dotenv()


def _get_project_id(project_id: str | None = None) -> str:
    """Use an explicit project id, then environment, then the known project."""
    return project_id or os.getenv("GCP_PROJECT_ID") or DEFAULT_PROJECT_ID


def _table_name(project_id: str, view_name: str) -> str:
    """Build a fully qualified BigQuery table or view name."""
    return f"`{project_id}.{view_name}`"


def _financial_year_token(financial_year: str) -> str:
    """Convert FY25-26 to FY2526 for readable output file names."""
    return financial_year.replace("-", "").replace(" ", "").upper()


def _utc_now_naive() -> datetime:
    """Return a timezone-naive UTC timestamp that Excel can store."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _format_currency(value: float | int | None) -> str:
    """Return a compact INR value for Streamlit metric cards."""
    amount = float(value or 0)
    if abs(amount) >= 10_000_000:
        return f"INR {amount / 10_000_000:.2f} Cr"
    if abs(amount) >= 100_000:
        return f"INR {amount / 100_000:.2f} L"
    return f"INR {amount:,.0f}"


def _first_value(dataframe: pd.DataFrame, column_name: str, default: Any = 0) -> Any:
    """Read one value from a one-row dataframe without raising on empty data."""
    if dataframe.empty or column_name not in dataframe.columns:
        return default

    value = dataframe.iloc[0][column_name]
    if pd.isna(value):
        return default

    return value


def _safe_sum(dataframe: pd.DataFrame, column_name: str) -> float:
    """Sum a numeric dataframe column and treat missing values as zero."""
    if dataframe.empty or column_name not in dataframe.columns:
        return 0.0
    return float(pd.to_numeric(dataframe[column_name], errors="coerce").fillna(0).sum())


def _safe_count(dataframe: pd.DataFrame, column_name: str) -> int:
    """Return a count from a dataframe column that may be missing."""
    if dataframe.empty or column_name not in dataframe.columns:
        return 0
    return int(pd.to_numeric(dataframe[column_name], errors="coerce").fillna(0).sum())


def _numeric_column(dataframe: pd.DataFrame, column_name: str) -> pd.Series:
    """Return a numeric series, or a zero-filled series when a column is absent."""
    if column_name not in dataframe.columns:
        return pd.Series(0, index=dataframe.index, dtype="float64")
    return pd.to_numeric(dataframe[column_name], errors="coerce").fillna(0)


def _text_column(dataframe: pd.DataFrame, column_name: str, default: str = "") -> pd.Series:
    """Return a text series, or a default-filled series when a column is absent."""
    if column_name not in dataframe.columns:
        return pd.Series(default, index=dataframe.index, dtype="object")
    return dataframe[column_name].fillna(default).astype(str)


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Return a ratio, or zero when the denominator is not usable."""
    if not denominator:
        return 0.0
    return numerator / denominator


def _make_excel_safe_value(value: Any) -> Any:
    """Remove timezone info from individual datetime values for Excel."""
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None:
            return value.tz_convert(None).to_pydatetime()
        return value.to_pydatetime()

    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    return value


def make_excel_safe_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of a dataframe that can be written safely to Excel.

    BigQuery timestamps can arrive as timezone-aware pandas or Python datetime
    values. Excel cannot store timezone-aware datetimes, so this helper converts
    them to timezone-naive UTC values before pandas writes the workbook.
    """
    safe_columns = {}

    for column_name in dataframe.columns:
        column = dataframe[column_name]

        if isinstance(column.dtype, pd.DatetimeTZDtype):
            safe_columns[column_name] = column.dt.tz_convert(None)
        elif column.dtype == "object":
            safe_columns[column_name] = column.map(_make_excel_safe_value)
        else:
            safe_columns[column_name] = column

    return pd.DataFrame(safe_columns, index=dataframe.index)


def _query_to_dataframe(client: bigquery.Client, query: str) -> pd.DataFrame:
    """Run a BigQuery query and convert rows to pandas without extra packages."""
    result = client.query(query).result()
    columns = [field.name for field in result.schema]
    rows = [dict(row.items()) for row in result]
    return pd.DataFrame(rows, columns=columns)


def _optional_query_to_dataframe(client: bigquery.Client, query: str, label: str) -> pd.DataFrame:
    """Run an optional reporting query without failing the whole MIS report."""
    try:
        return _query_to_dataframe(client, query)
    except Exception as error:
        print(f"Skipping optional MIS query for {label}: {error}")
        return pd.DataFrame()


def fetch_gold_mis_data(project_id: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Query the required Gold MIS views from BigQuery."""
    resolved_project_id = _get_project_id(project_id)
    client = bigquery.Client(project=resolved_project_id)

    monthly_query = f"""
        SELECT *
        FROM {_table_name(resolved_project_id, MIS_MONTHLY_PL_VIEW)}
        ORDER BY report_month
    """
    dashboard_query = f"""
        SELECT *
        FROM {_table_name(resolved_project_id, DASHBOARD_SUMMARY_VIEW)}
    """

    monthly_pl_df = _query_to_dataframe(client, monthly_query)
    dashboard_summary_df = _query_to_dataframe(client, dashboard_query)

    return monthly_pl_df, dashboard_summary_df


def fetch_optional_silver_mis_data(project_id: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Query Silver detail views used for top parties and exception alerts."""
    resolved_project_id = _get_project_id(project_id)
    client = bigquery.Client(project=resolved_project_id)

    bills_query = f"""
        SELECT
            bill_id,
            bill_number,
            vendor_id,
            vendor_name,
            bill_date,
            status,
            total_amount,
            balance_amount,
            gstin
        FROM {_table_name(resolved_project_id, SILVER_BILLS_VIEW)}
    """
    invoices_query = f"""
        SELECT
            invoice_id,
            invoice_number,
            customer_id,
            customer_name,
            invoice_date,
            status,
            total_amount,
            balance_amount,
            gstin
        FROM {_table_name(resolved_project_id, SILVER_INVOICES_VIEW)}
    """

    bills_df = _optional_query_to_dataframe(client, bills_query, SILVER_BILLS_VIEW)
    invoices_df = _optional_query_to_dataframe(client, invoices_query, SILVER_INVOICES_VIEW)

    return bills_df, invoices_df


def _prepare_monthly_pl(monthly_pl_df: pd.DataFrame) -> pd.DataFrame:
    """Shape Gold monthly P&L rows into a management-friendly table."""
    if monthly_pl_df.empty:
        return pd.DataFrame(
            columns=[
                "Month",
                "Revenue",
                "Expenses",
                "Journal Adjustments",
                "Profit/Loss",
                "Profit Margin %",
                "Revenue MoM %",
                "Expense MoM %",
            ]
        )

    report = monthly_pl_df.copy(deep=True).reset_index(drop=True)
    revenue_amount = _numeric_column(report, "revenue_amount")
    expense_amount = _numeric_column(report, "expense_amount")
    journal_adjustment_amount = _numeric_column(report, "journal_adjustment_amount")

    if "profit_amount" in report.columns:
        profit_amount = _numeric_column(report, "profit_amount")
    else:
        profit_amount = revenue_amount - expense_amount + journal_adjustment_amount

    report_month_source = _text_column(report, "report_month")
    report_month = pd.to_datetime(report_month_source, errors="coerce")
    month_label = report_month.dt.strftime("%b %Y").fillna(report_month_source)
    profit_margin = profit_amount.divide(revenue_amount.replace(0, pd.NA)).fillna(0)
    revenue_mom = revenue_amount.pct_change().fillna(0)
    expense_mom = expense_amount.pct_change().fillna(0)

    monthly_report = pd.DataFrame(
        {
            "Month": month_label,
            "Revenue": revenue_amount,
            "Expenses": expense_amount,
            "Journal Adjustments": journal_adjustment_amount,
            "Profit/Loss": profit_amount,
            "Profit Margin %": profit_margin,
            "Revenue MoM %": revenue_mom,
            "Expense MoM %": expense_mom,
        }
    )

    total_revenue = _safe_sum(monthly_report, "Revenue")
    total_expenses = _safe_sum(monthly_report, "Expenses")
    total_journal_adjustments = _safe_sum(monthly_report, "Journal Adjustments")
    total_profit = _safe_sum(monthly_report, "Profit/Loss")
    totals_row = pd.DataFrame(
        [
            {
                "Month": "Total",
                "Revenue": total_revenue,
                "Expenses": total_expenses,
                "Journal Adjustments": total_journal_adjustments,
                "Profit/Loss": total_profit,
                "Profit Margin %": _safe_ratio(total_profit, total_revenue),
                "Revenue MoM %": 0,
                "Expense MoM %": 0,
            }
        ]
    )

    return pd.concat([monthly_report, totals_row], ignore_index=True)


def _prepare_top_vendors(bills_df: pd.DataFrame) -> pd.DataFrame:
    """Build a top-10 vendor table from Silver bills when available."""
    columns = ["Vendor", "Bill Count", "Total Bill Amount", "Outstanding Amount"]
    if bills_df.empty:
        return pd.DataFrame(columns=columns)

    working_df = pd.DataFrame(
        {
            "vendor_name": _text_column(bills_df, "vendor_name", "Unknown Vendor"),
            "total_amount": _numeric_column(bills_df, "total_amount"),
            "balance_amount": _numeric_column(bills_df, "balance_amount"),
        }
    )

    grouped = (
        working_df.groupby("vendor_name", dropna=False)
        .agg(
            bill_count=("vendor_name", "size"),
            total_bill_amount=("total_amount", "sum"),
            outstanding_amount=("balance_amount", "sum"),
        )
        .reset_index()
        .sort_values("total_bill_amount", ascending=False)
        .head(10)
    )

    grouped.columns = columns
    return grouped


def _prepare_top_customers(invoices_df: pd.DataFrame) -> pd.DataFrame:
    """Build a top-10 customer table from Silver invoices when available."""
    columns = ["Customer", "Invoice Count", "Total Invoice Amount", "Outstanding Amount"]
    if invoices_df.empty:
        return pd.DataFrame(columns=columns)

    working_df = pd.DataFrame(
        {
            "customer_name": _text_column(invoices_df, "customer_name", "Unknown Customer"),
            "total_amount": _numeric_column(invoices_df, "total_amount"),
            "balance_amount": _numeric_column(invoices_df, "balance_amount"),
        }
    )

    grouped = (
        working_df.groupby("customer_name", dropna=False)
        .agg(
            invoice_count=("customer_name", "size"),
            total_invoice_amount=("total_amount", "sum"),
            outstanding_amount=("balance_amount", "sum"),
        )
        .reset_index()
        .sort_values("total_invoice_amount", ascending=False)
        .head(10)
    )

    grouped.columns = columns
    return grouped


def _build_observations(total_revenue: float, total_expenses: float, total_profit: float, monthly_report_df: pd.DataFrame) -> list[str]:
    """Create short management observations from the report numbers."""
    observations = []

    if total_profit < 0:
        observations.append("Business is loss-making for the selected period; review high-cost months and collections.")
    elif total_profit > 0:
        observations.append("Business is profitable for the selected period based on available Gold layer data.")
    else:
        observations.append("Net profit/loss is flat for the selected period.")

    expense_ratio = _safe_ratio(total_expenses, total_revenue)
    if expense_ratio > 0.8:
        observations.append("Expenses exceed 80% of revenue; margin protection needs attention.")
    elif total_revenue > 0:
        observations.append("Expense-to-revenue ratio is within the current management threshold.")

    monthly_rows = monthly_report_df[monthly_report_df["Month"] != "Total"]
    negative_months = monthly_rows[monthly_rows["Profit/Loss"] < 0]
    if not negative_months.empty:
        observations.append(f"{len(negative_months)} month(s) show negative profit and should be reviewed.")

    return observations[:4]


def _prepare_executive_summary(
    financial_year: str,
    monthly_report_df: pd.DataFrame,
    dashboard_summary_df: pd.DataFrame,
    generated_at: datetime,
) -> pd.DataFrame:
    """Create the Executive Summary sheet."""
    total_revenue = _safe_sum(monthly_report_df, "Revenue")
    total_expenses = _safe_sum(monthly_report_df, "Expenses")
    total_profit = _safe_sum(monthly_report_df, "Profit/Loss")
    invoice_count = int(_first_value(dashboard_summary_df, "invoice_count"))
    bill_count = int(_first_value(dashboard_summary_df, "bill_count"))
    journal_count = int(_first_value(dashboard_summary_df, "journal_count"))
    observations = _build_observations(total_revenue, total_expenses, total_profit, monthly_report_df)

    rows = [
        {"Metric": "Report Title", "Value": "Finance Automation Platform MIS Management Report"},
        {"Metric": "Financial Year", "Value": financial_year},
        {"Metric": "Generated Timestamp", "Value": generated_at},
        {"Metric": "Total Revenue", "Value": total_revenue},
        {"Metric": "Total Expenses", "Value": total_expenses},
        {"Metric": "Net Profit/Loss", "Value": total_profit},
        {"Metric": "Invoice Count", "Value": invoice_count},
        {"Metric": "Bill Count", "Value": bill_count},
        {"Metric": "Journal Count", "Value": journal_count},
    ]

    for index, observation in enumerate(observations, start=1):
        rows.append({"Metric": f"Management Observation {index}", "Value": observation})

    return pd.DataFrame(rows)


def _prepare_kpi_dashboard(monthly_report_df: pd.DataFrame, dashboard_summary_df: pd.DataFrame) -> pd.DataFrame:
    """Create a KPI dashboard sheet from Gold summary and monthly P&L data."""
    invoice_count = int(_first_value(dashboard_summary_df, "invoice_count"))
    bill_count = int(_first_value(dashboard_summary_df, "bill_count"))
    invoice_total = float(_first_value(dashboard_summary_df, "invoice_total_amount"))
    bill_total = float(_first_value(dashboard_summary_df, "bill_total_amount"))
    total_revenue = _safe_sum(monthly_report_df, "Revenue")
    total_expenses = _safe_sum(monthly_report_df, "Expenses")

    rows = [
        {"KPI": "Account count", "Value": int(_first_value(dashboard_summary_df, "account_count"))},
        {"KPI": "Contact count", "Value": int(_first_value(dashboard_summary_df, "contact_count"))},
        {"KPI": "Invoice count", "Value": invoice_count},
        {"KPI": "Invoice total amount", "Value": invoice_total},
        {"KPI": "Bill count", "Value": bill_count},
        {"KPI": "Bill total amount", "Value": bill_total},
        {"KPI": "Journal count", "Value": int(_first_value(dashboard_summary_df, "journal_count"))},
        {"KPI": "Journal total amount", "Value": float(_first_value(dashboard_summary_df, "journal_total_amount"))},
        {"KPI": "Average invoice value", "Value": _safe_ratio(invoice_total, invoice_count)},
        {"KPI": "Average bill value", "Value": _safe_ratio(bill_total, bill_count)},
        {"KPI": "Expense-to-revenue ratio", "Value": _safe_ratio(total_expenses, total_revenue)},
    ]

    return pd.DataFrame(rows)


def _prepare_exception_alerts(
    monthly_report_df: pd.DataFrame,
    invoices_df: pd.DataFrame,
    bills_df: pd.DataFrame,
) -> pd.DataFrame:
    """Create an exceptions sheet for management review."""
    rows = []
    monthly_rows = monthly_report_df[monthly_report_df["Month"] != "Total"].copy()

    for _, row in monthly_rows[monthly_rows["Profit/Loss"] < 0].iterrows():
        rows.append(
            {
                "Alert Type": "Negative Profit Month",
                "Severity": "High",
                "Reference": row["Month"],
                "Amount": row["Profit/Loss"],
                "Observation": "Monthly P&L is negative.",
            }
        )

    expense_threshold = monthly_rows["Expenses"].mean() + monthly_rows["Expenses"].std(ddof=0) if not monthly_rows.empty else 0
    high_expense_rows = monthly_rows[
        (monthly_rows["Expenses"] > expense_threshold)
        | (monthly_rows.apply(lambda row: _safe_ratio(row["Expenses"], row["Revenue"]), axis=1) > 0.8)
    ]
    for _, row in high_expense_rows.iterrows():
        rows.append(
            {
                "Alert Type": "High Expense Month",
                "Severity": "Medium",
                "Reference": row["Month"],
                "Amount": row["Expenses"],
                "Observation": "Expenses are unusually high or exceed 80% of revenue.",
            }
        )

    rows.extend(_invoice_exception_rows(invoices_df))
    rows.extend(_bill_exception_rows(bills_df))

    if not rows:
        rows.append(
            {
                "Alert Type": "No Exceptions",
                "Severity": "Info",
                "Reference": "",
                "Amount": None,
                "Observation": "No exception alerts were detected from available data.",
            }
        )

    return pd.DataFrame(rows)


def _invoice_exception_rows(invoices_df: pd.DataFrame) -> list[dict]:
    """Return unpaid and missing-GSTIN invoice alerts when fields are available."""
    if invoices_df.empty:
        return []

    working_df = invoices_df.copy(deep=True).reset_index(drop=True)
    balance_amount = _numeric_column(working_df, "balance_amount")
    status = _text_column(working_df, "status").str.lower()
    invoice_number = _text_column(working_df, "invoice_number")
    customer_name = _text_column(working_df, "customer_name")
    gstin = _text_column(working_df, "gstin").str.strip()
    rows = []

    unpaid_mask = (balance_amount > 0) | status.isin(["unpaid", "overdue", "sent"])
    for index in working_df[unpaid_mask].head(25).index:
        rows.append(
            {
                "Alert Type": "Unpaid Invoice",
                "Severity": "Medium",
                "Reference": invoice_number.loc[index],
                "Amount": balance_amount.loc[index],
                "Observation": f"Outstanding invoice for {customer_name.loc[index]}.",
            }
        )

    for index in working_df[gstin == ""].head(25).index:
        rows.append(
            {
                "Alert Type": "Missing GSTIN",
                "Severity": "Low",
                "Reference": invoice_number.loc[index],
                "Amount": _numeric_column(working_df, "total_amount").loc[index],
                "Observation": f"Invoice GSTIN is missing for {customer_name.loc[index]}.",
            }
        )

    return rows


def _bill_exception_rows(bills_df: pd.DataFrame) -> list[dict]:
    """Return unpaid and missing-GSTIN bill alerts when fields are available."""
    if bills_df.empty:
        return []

    working_df = bills_df.copy(deep=True).reset_index(drop=True)
    balance_amount = _numeric_column(working_df, "balance_amount")
    status = _text_column(working_df, "status").str.lower()
    bill_number = _text_column(working_df, "bill_number")
    vendor_name = _text_column(working_df, "vendor_name")
    gstin = _text_column(working_df, "gstin").str.strip()
    rows = []

    unpaid_mask = (balance_amount > 0) | status.isin(["unpaid", "overdue", "open"])
    for index in working_df[unpaid_mask].head(25).index:
        rows.append(
            {
                "Alert Type": "Unpaid Bill",
                "Severity": "Medium",
                "Reference": bill_number.loc[index],
                "Amount": balance_amount.loc[index],
                "Observation": f"Outstanding bill for {vendor_name.loc[index]}.",
            }
        )

    for index in working_df[gstin == ""].head(25).index:
        rows.append(
            {
                "Alert Type": "Missing GSTIN",
                "Severity": "Low",
                "Reference": bill_number.loc[index],
                "Amount": _numeric_column(working_df, "total_amount").loc[index],
                "Observation": f"Bill GSTIN is missing for {vendor_name.loc[index]}.",
            }
        )

    return rows


def _prepare_data_sources(project_id: str, generated_at: datetime) -> pd.DataFrame:
    """Create a clear data lineage sheet for the report."""
    rows = [
        {
            "Source": f"{project_id}.{MIS_MONTHLY_PL_VIEW}",
            "Layer": "Gold",
            "Purpose": "Monthly revenue, expense, journal adjustment, and profit/loss reporting.",
        },
        {
            "Source": f"{project_id}.{DASHBOARD_SUMMARY_VIEW}",
            "Layer": "Gold",
            "Purpose": "Dashboard counts and high-level totals.",
        },
        {
            "Source": f"{project_id}.{SILVER_BILLS_VIEW}",
            "Layer": "Silver",
            "Purpose": "Top vendors and bill exception alerts when available.",
        },
        {
            "Source": f"{project_id}.{SILVER_INVOICES_VIEW}",
            "Layer": "Silver",
            "Purpose": "Top customers and invoice exception alerts when available.",
        },
        {
            "Source": "finance_bronze.zoho_raw",
            "Layer": "Bronze",
            "Purpose": "Raw append-only Zoho Books source data feeding Silver and Gold views.",
        },
        {
            "Source": "Report generated timestamp",
            "Layer": "Report",
            "Purpose": generated_at,
        },
        {
            "Source": "Layering note",
            "Layer": "Report",
            "Purpose": "Bronze raw data, Silver cleaned data, Gold reporting data.",
        },
    ]

    return pd.DataFrame(rows)


def get_mis_metrics(
    financial_year: str,
    monthly_pl_df: pd.DataFrame | None = None,
    dashboard_summary_df: pd.DataFrame | None = None,
    project_id: str | None = None,
) -> dict:
    """Return Streamlit metric-card values from real Gold layer data."""
    if monthly_pl_df is None or dashboard_summary_df is None:
        monthly_pl_df, dashboard_summary_df = fetch_gold_mis_data(project_id)

    monthly_report_df = _prepare_monthly_pl(monthly_pl_df)
    revenue_amount = _safe_sum(monthly_report_df, "Revenue")
    expense_amount = _safe_sum(monthly_report_df, "Expenses")
    profit_amount = _safe_sum(monthly_report_df, "Profit/Loss")
    journal_adjustment_amount = _safe_sum(monthly_report_df, "Journal Adjustments")

    return {
        "Financial Year": financial_year,
        "Revenue": _format_currency(revenue_amount),
        "Expenses": _format_currency(expense_amount),
        "Profit": _format_currency(profit_amount),
        "Journal Adjustments": _format_currency(journal_adjustment_amount),
        "Invoices": str(int(_first_value(dashboard_summary_df, "invoice_count"))),
        "Bills": str(int(_first_value(dashboard_summary_df, "bill_count"))),
        "Contacts": str(int(_first_value(dashboard_summary_df, "contact_count"))),
    }


def _style_excel_workbook(writer: pd.ExcelWriter) -> None:
    """Apply professional workbook formatting."""
    workbook = writer.book
    header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    total_fill = PatternFill(fill_type="solid", fgColor="D9EAF7")

    for worksheet in workbook.worksheets:
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        for cell in worksheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        headers = [cell.value for cell in worksheet[1]]
        for row in worksheet.iter_rows(min_row=2):
            row_label = str(row[0].value or "").strip().lower()
            if row_label == "total":
                for cell in row:
                    cell.font = Font(bold=True)
                    cell.fill = total_fill

            for cell in row:
                header = headers[cell.column - 1] if cell.column - 1 < len(headers) else ""
                _apply_number_format(worksheet.title, header, row_label, cell)

        for column_cells in worksheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            column_letter = get_column_letter(column_cells[0].column)
            worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 42)


def _apply_number_format(sheet_name: str, header: str, row_label: str, cell) -> None:
    """Apply currency, percent, and datetime formats by sheet and column."""
    header_text = str(header or "").lower()
    label_text = str(row_label or "").lower()

    if isinstance(cell.value, datetime):
        cell.number_format = DATETIME_FORMAT

    currency_headers = ["revenue", "expenses", "adjustments", "profit/loss", "amount", "outstanding"]
    if any(token in header_text for token in currency_headers):
        cell.number_format = INR_FORMAT

    if "%" in header_text or "ratio" in header_text:
        cell.number_format = PERCENT_FORMAT

    if sheet_name in {"Executive Summary", "KPI Dashboard"} and header_text == "value":
        if any(token in label_text for token in ["revenue", "expense", "profit", "amount", "value"]):
            cell.number_format = INR_FORMAT
        elif "ratio" in label_text or "margin" in label_text:
            cell.number_format = PERCENT_FORMAT


def generate_mis_report(
    financial_year: str,
    output_dir: str | Path,
    project_id: str | None = None,
    monthly_pl_df: pd.DataFrame | None = None,
    dashboard_summary_df: pd.DataFrame | None = None,
    bills_df: pd.DataFrame | None = None,
    invoices_df: pd.DataFrame | None = None,
) -> dict:
    """Generate a professional Excel MIS workbook from BigQuery reporting views."""
    should_fetch_optional_silver = monthly_pl_df is None or dashboard_summary_df is None
    if monthly_pl_df is None or dashboard_summary_df is None:
        monthly_pl_df, dashboard_summary_df = fetch_gold_mis_data(project_id)

    resolved_project_id = _get_project_id(project_id)
    if should_fetch_optional_silver and (bills_df is None or invoices_df is None):
        fetched_bills_df, fetched_invoices_df = fetch_optional_silver_mis_data(resolved_project_id)
        bills_df = fetched_bills_df if bills_df is None else bills_df
        invoices_df = fetched_invoices_df if invoices_df is None else invoices_df

    bills_df = bills_df if bills_df is not None else pd.DataFrame()
    invoices_df = invoices_df if invoices_df is not None else pd.DataFrame()
    generated_at = _utc_now_naive()

    monthly_report_df = _prepare_monthly_pl(monthly_pl_df)
    executive_summary_df = _prepare_executive_summary(
        financial_year=financial_year,
        monthly_report_df=monthly_report_df,
        dashboard_summary_df=dashboard_summary_df,
        generated_at=generated_at,
    )
    kpi_dashboard_df = _prepare_kpi_dashboard(monthly_report_df, dashboard_summary_df)
    top_vendors_df = _prepare_top_vendors(bills_df)
    top_customers_df = _prepare_top_customers(invoices_df)
    exceptions_df = _prepare_exception_alerts(monthly_report_df, invoices_df, bills_df)
    data_sources_df = _prepare_data_sources(resolved_project_id, generated_at)
    metrics = get_mis_metrics(financial_year, monthly_pl_df, dashboard_summary_df, project_id)

    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)
    report_path = destination_folder / f"MIS_PL_{_financial_year_token(financial_year)}_generated.xlsx"

    workbook_sheets = {
        "Executive Summary": executive_summary_df,
        "Monthly P&L": monthly_report_df,
        "KPI Dashboard": kpi_dashboard_df,
        "Top Vendors": top_vendors_df,
        "Top Customers": top_customers_df,
        "Exceptions Alerts": exceptions_df,
        "Data Sources": data_sources_df,
    }

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        for sheet_name, dataframe in workbook_sheets.items():
            make_excel_safe_dataframe(dataframe).to_excel(writer, sheet_name=sheet_name, index=False)
        _style_excel_workbook(writer)

    return {
        "status": "success",
        "message": "MIS management report generated from BigQuery Gold layer data.",
        "metrics": metrics,
        "report_path": report_path,
        "is_placeholder": False,
        "summary": executive_summary_df,
        "monthly_preview": monthly_report_df.head(20),
        "dashboard_kpis": kpi_dashboard_df,
    }
