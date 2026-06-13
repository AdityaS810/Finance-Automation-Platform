"""Generate MIS reports from BigQuery Gold layer views."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


DEFAULT_PROJECT_ID = "internal-project-work-497507"
MIS_MONTHLY_PL_VIEW = "finance_gold.mis_monthly_pl"
DASHBOARD_SUMMARY_VIEW = "finance_gold.dashboard_summary"
SUMMARY_COLUMNS = [
    "generated_at",
    "account_count",
    "contact_count",
    "invoice_count",
    "invoice_total_amount",
    "bill_count",
    "bill_total_amount",
    "journal_count",
    "journal_total_amount",
]


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


def _format_currency(value: float | int | None) -> str:
    """Return a compact INR value for Streamlit metric cards."""
    amount = float(value or 0)
    if abs(amount) >= 10_000_000:
        return f"INR {amount / 10_000_000:.2f} Cr"
    if abs(amount) >= 100_000:
        return f"INR {amount / 100_000:.2f} L"
    return f"INR {amount:,.0f}"


def _first_value(dataframe: pd.DataFrame, column_name: str, default: int | float | str = 0):
    """Read one value from a one-row dataframe without raising on empty data."""
    if dataframe.empty or column_name not in dataframe.columns:
        return default
    value = dataframe.iloc[0][column_name]
    if pd.isna(value):
        return default
    return value


def _make_excel_safe_value(value):
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


def _style_excel_workbook(writer: pd.ExcelWriter) -> None:
    """Apply simple readable formatting to all workbook sheets."""
    workbook = writer.book
    header_fill = PatternFill(fill_type="solid", fgColor="D9EAF7")

    for worksheet in workbook.worksheets:
        worksheet.freeze_panes = "A2"
        for cell in worksheet[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill

        for column_cells in worksheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            column_letter = get_column_letter(column_cells[0].column)
            worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 32)


def _query_to_dataframe(client: bigquery.Client, query: str) -> pd.DataFrame:
    """Run a BigQuery query and convert rows to pandas without extra packages."""
    result = client.query(query).result()
    columns = [field.name for field in result.schema]
    rows = [dict(row.items()) for row in result]
    return pd.DataFrame(rows, columns=columns)


def fetch_gold_mis_data(project_id: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Query the Gold MIS views from BigQuery."""
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


def build_summary_dataframe(
    financial_year: str,
    dashboard_summary_df: pd.DataFrame,
    project_id: str | None = None,
) -> pd.DataFrame:
    """Create the Summary sheet from dashboard KPI data."""
    resolved_project_id = _get_project_id(project_id)
    summary = {column: _first_value(dashboard_summary_df, column) for column in SUMMARY_COLUMNS}
    summary["financial_year"] = financial_year
    summary["report_generated_at"] = datetime.now(timezone.utc).isoformat()
    summary["data_source"] = f"{resolved_project_id}.{DASHBOARD_SUMMARY_VIEW}"
    return pd.DataFrame([summary])


def get_mis_metrics(
    financial_year: str,
    monthly_pl_df: pd.DataFrame | None = None,
    dashboard_summary_df: pd.DataFrame | None = None,
    project_id: str | None = None,
) -> dict:
    """Return Streamlit metric-card values from real Gold layer data."""
    if monthly_pl_df is None or dashboard_summary_df is None:
        monthly_pl_df, dashboard_summary_df = fetch_gold_mis_data(project_id)

    revenue_amount = monthly_pl_df.get("revenue_amount", pd.Series(dtype="float64")).sum()
    expense_amount = monthly_pl_df.get("expense_amount", pd.Series(dtype="float64")).sum()
    profit_amount = monthly_pl_df.get("profit_amount", pd.Series(dtype="float64")).sum()
    journal_adjustment_amount = monthly_pl_df.get("journal_adjustment_amount", pd.Series(dtype="float64")).sum()

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


def generate_mis_report(
    financial_year: str,
    output_dir: str | Path,
    project_id: str | None = None,
    monthly_pl_df: pd.DataFrame | None = None,
    dashboard_summary_df: pd.DataFrame | None = None,
) -> dict:
    """Generate a three-sheet Excel MIS workbook from BigQuery Gold views."""
    if monthly_pl_df is None or dashboard_summary_df is None:
        monthly_pl_df, dashboard_summary_df = fetch_gold_mis_data(project_id)

    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)

    file_name = f"MIS_PL_{_financial_year_token(financial_year)}_generated.xlsx"
    report_path = destination_folder / file_name
    summary_df = build_summary_dataframe(financial_year, dashboard_summary_df, project_id)
    metrics = get_mis_metrics(financial_year, monthly_pl_df, dashboard_summary_df, project_id)
    excel_summary_df = make_excel_safe_dataframe(summary_df)
    excel_monthly_pl_df = make_excel_safe_dataframe(monthly_pl_df)
    excel_dashboard_summary_df = make_excel_safe_dataframe(dashboard_summary_df)

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        excel_summary_df.to_excel(writer, sheet_name="Summary", index=False)
        excel_monthly_pl_df.to_excel(writer, sheet_name="Monthly P&L", index=False)
        excel_dashboard_summary_df.to_excel(writer, sheet_name="Dashboard KPIs", index=False)
        _style_excel_workbook(writer)

    return {
        "status": "success",
        "message": "MIS report generated from BigQuery Gold layer data.",
        "metrics": metrics,
        "report_path": report_path,
        "is_placeholder": False,
        "summary": summary_df,
        "monthly_preview": monthly_pl_df.head(20),
        "dashboard_kpis": dashboard_summary_df,
    }
