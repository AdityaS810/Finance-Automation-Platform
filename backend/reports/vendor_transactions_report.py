"""Generate a vendor bills and transactions workbook from BigQuery finance data."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


DEFAULT_PROJECT_ID = "internal-project-work-497507"
DEFAULT_BIGQUERY_LOCATION = "asia-south1"
CONTACTS_VIEW = "finance_silver.dim_contacts"
BILLS_VIEW = "finance_silver.fact_bills"
TRANSACTIONS_VIEW = "finance_silver.fact_transactions"

PAYMENT_AVAILABILITY_NOTE = (
    "Dedicated vendor payment records are not currently available in the warehouse. "
    "This report contains vendor bills and available vendor-linked transactions."
)
TRANSACTION_LINKAGE_NOTE = (
    "Transactions are not joined to vendors by inference. Vendor/contact fields are populated only "
    "when an explicit vendor_id, contact_id, vendor_name, or contact_name is present in the transaction payload. "
    "Rows without those fields remain in the extract with blank vendor/contact values."
)
TRANSACTION_LINKAGE_UNAVAILABLE_NOTE = (
    "Vendor/contact linkage is unavailable in the selected transaction rows. "
    "fact_transactions has no direct vendor/contact columns, and no explicit vendor_id, contact_id, "
    "vendor_name, or contact_name values were present in the transaction payloads. "
    "Vendor/Contact ID and Vendor/Contact Name are therefore intentionally blank."
)

BILL_COLUMNS = [
    "Organization",
    "Country",
    "Vendor ID",
    "Vendor Name",
    "Bill ID",
    "Bill Number",
    "Bill Date",
    "Due Date",
    "Status",
    "Currency",
    "Bill Amount",
    "Outstanding Balance",
    "Source Record ID",
]

TRANSACTION_COLUMNS = [
    "Organization",
    "Vendor/Contact ID",
    "Vendor/Contact Name",
    "Transaction ID",
    "Transaction Date",
    "Transaction Type",
    "Reference Number",
    "Description",
    "Currency",
    "Amount",
    "Amount in INR",
    "Status",
    "Source Record ID",
]

BILL_QUERY_COLUMN_MAP = {
    "organization": "Organization",
    "country": "Country",
    "vendor_id": "Vendor ID",
    "vendor_name": "Vendor Name",
    "bill_id": "Bill ID",
    "bill_number": "Bill Number",
    "bill_date": "Bill Date",
    "due_date": "Due Date",
    "status": "Status",
    "currency": "Currency",
    "bill_amount": "Bill Amount",
    "outstanding_balance": "Outstanding Balance",
    "source_record_id": "Source Record ID",
}

TRANSACTION_QUERY_COLUMN_MAP = {
    "organization": "Organization",
    "vendor_contact_id": "Vendor/Contact ID",
    "vendor_contact_name": "Vendor/Contact Name",
    "transaction_id": "Transaction ID",
    "transaction_date": "Transaction Date",
    "transaction_type": "Transaction Type",
    "reference_number": "Reference Number",
    "description": "Description",
    "currency": "Currency",
    "amount": "Amount",
    "amount_inr": "Amount in INR",
    "status": "Status",
    "source_record_id": "Source Record ID",
}

TEXT_IDENTIFIER_COLUMNS = {
    "Vendor ID",
    "Bill ID",
    "Bill Number",
    "Vendor/Contact ID",
    "Transaction ID",
    "Reference Number",
    "Source Record ID",
}

load_dotenv()


def _project_id(project_id: str | None = None) -> str:
    return project_id or os.getenv("GCP_PROJECT_ID") or DEFAULT_PROJECT_ID


def _bigquery_location(location: str | None = None) -> str:
    return location or os.getenv("BIGQUERY_LOCATION") or DEFAULT_BIGQUERY_LOCATION


def _table_name(project_id: str, view_name: str) -> str:
    return f"`{project_id}.{view_name}`"


def _schema_fields(client: Any, project_id: str, view_name: str) -> set[str]:
    """Read the live table/view schema before constructing a query."""
    table = client.get_table(f"{project_id}.{view_name}")
    return {field.name for field in table.schema}


def _typed_null(field_type: str) -> str:
    return f"CAST(NULL AS {field_type})"


def _first_column(fields: set[str], names: Iterable[str], field_type: str = "STRING") -> str:
    for name in names:
        if name in fields:
            return f"`{name}`"
    return _typed_null(field_type)


def _json_value(fields: set[str], paths: Iterable[str], field_type: str = "STRING") -> str:
    if "raw_json" not in fields:
        return _typed_null(field_type)
    expressions = [f"JSON_VALUE(raw_json, '{path}')" for path in paths]
    value = expressions[0] if len(expressions) == 1 else f"COALESCE({', '.join(expressions)})"
    if field_type == "STRING":
        return value
    return f"SAFE_CAST({value} AS {field_type})"


def _first_column_or_json(
    fields: set[str],
    columns: Iterable[str],
    json_paths: Iterable[str],
    field_type: str = "STRING",
) -> str:
    column_expression = _first_column(fields, columns, field_type)
    if not column_expression.startswith("CAST(NULL"):
        return column_expression
    return _json_value(fields, json_paths, field_type)


def _organization_expression(fields: set[str]) -> str:
    return _first_column(fields, ["source_org_name", "source_org_key"])


def _source_record_expression(fields: set[str], record_id_column: str) -> str:
    return _first_column(fields, ["source_record_id", record_id_column])


def _query_to_dataframe(
    client: Any,
    query: str,
    parameters: list[Any] | None = None,
    location: str | None = None,
) -> pd.DataFrame:
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(query_parameters=parameters or [])
    result = client.query(
        query,
        job_config=job_config,
        location=_bigquery_location(location),
    ).result()
    columns = [field.name for field in result.schema]
    return pd.DataFrame([dict(row.items()) for row in result], columns=columns)


def _rename_query_columns(dataframe: pd.DataFrame, column_map: dict[str, str]) -> pd.DataFrame:
    """Rename BigQuery-safe snake_case fields to workbook display labels."""
    return dataframe.rename(columns=column_map).reindex(columns=list(column_map.values()))


def safe_exception_details(error: Exception, max_length: int = 4000) -> str:
    """Return useful diagnostics while redacting common credential patterns."""
    message = str(error).strip() or error.__class__.__name__
    message = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?:(?:Bearer|Basic)\s+)?[^\s,;]+",
        r"\1[redacted]",
        message,
    )
    sensitive_assignment = re.compile(
        r"(?i)(access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    )
    message = sensitive_assignment.sub(r"\1\2[redacted]", message)
    return message[:max_length]


def _build_filter_clause(
    date_column: str,
    start_date: date | None,
    end_date: date | None,
    organization: str | None,
    vendor: str | None,
    status: str | None,
    vendor_name_column: str,
) -> str:
    filters: list[str] = []
    if start_date is not None:
        filters.append(f"{date_column} >= @start_date")
    if end_date is not None:
        filters.append(f"{date_column} <= @end_date")
    if organization:
        filters.append("organization = @organization")
    if vendor:
        filters.append(f"{vendor_name_column} = @vendor")
    if status:
        filters.append("status = @status")
    return f"WHERE {' AND '.join(filters)}" if filters else ""


def _build_bills_query(
    project_id: str,
    fields: set[str],
    start_date: date | None = None,
    end_date: date | None = None,
    organization: str | None = None,
    vendor: str | None = None,
    status: str | None = None,
    order_rows: bool = True,
) -> str:
    organization_expression = _organization_expression(fields)
    country_expression = _first_column(fields, ["source_country"])
    vendor_id_expression = _first_column_or_json(fields, ["vendor_id"], ["$.vendor_id"])
    vendor_name_expression = _first_column_or_json(fields, ["vendor_name"], ["$.vendor_name"])
    bill_id_expression = _first_column(fields, ["bill_id", "source_record_id"])
    bill_number_expression = _first_column_or_json(fields, ["bill_number"], ["$.bill_number"])
    bill_date_expression = _first_column_or_json(fields, ["bill_date"], ["$.date"], "DATE")
    due_date_expression = _first_column_or_json(fields, ["due_date"], ["$.due_date"], "DATE")
    status_expression = _first_column_or_json(fields, ["status"], ["$.status"])
    currency_expression = _first_column(fields, ["original_currency", "currency_code", "source_currency"])
    amount_expression = _first_column(fields, ["total_amount", "original_amount"], "NUMERIC")
    balance_expression = _first_column(fields, ["balance_amount"], "NUMERIC")
    source_record_expression = _source_record_expression(fields, "bill_id")
    filter_clause = _build_filter_clause(
        "bill_date", start_date, end_date, organization, vendor, status, "vendor_name"
    )
    order_clause = "ORDER BY bill_date DESC, vendor_name, bill_number" if order_rows else ""

    return f"""
        SELECT
            organization,
            country,
            vendor_id,
            vendor_name,
            bill_id,
            bill_number,
            bill_date,
            due_date,
            status,
            currency,
            bill_amount,
            outstanding_balance,
            source_record_id
        FROM (
            SELECT
                {organization_expression} AS organization,
                {country_expression} AS country,
                {vendor_id_expression} AS vendor_id,
                {vendor_name_expression} AS vendor_name,
                {bill_id_expression} AS bill_id,
                {bill_number_expression} AS bill_number,
                {bill_date_expression} AS bill_date,
                {due_date_expression} AS due_date,
                {status_expression} AS status,
                {currency_expression} AS currency,
                {amount_expression} AS bill_amount,
                {balance_expression} AS outstanding_balance,
                {source_record_expression} AS source_record_id
            FROM {_table_name(project_id, BILLS_VIEW)}
        )
        {filter_clause}
        {order_clause}
    """


def _build_transactions_query(
    project_id: str,
    fields: set[str],
    start_date: date | None = None,
    end_date: date | None = None,
    organization: str | None = None,
    vendor: str | None = None,
    status: str | None = None,
    order_rows: bool = True,
) -> str:
    organization_expression = _organization_expression(fields)
    contact_id_expression = _first_column_or_json(
        fields, ["vendor_id", "contact_id"], ["$.vendor_id", "$.contact_id"]
    )
    contact_name_expression = _first_column_or_json(
        fields, ["vendor_name", "contact_name"], ["$.vendor_name", "$.contact_name"]
    )
    transaction_id_expression = _first_column(fields, ["transaction_id", "source_record_id"])
    transaction_date_expression = _first_column_or_json(
        fields, ["transaction_date"], ["$.transaction_date", "$.journal_date", "$.date"], "DATE"
    )
    transaction_type_expression = _first_column_or_json(
        fields, ["transaction_type"], ["$.transaction_type", "$.type"]
    )
    reference_expression = _first_column_or_json(fields, ["reference_number", "transaction_number"], ["$.reference_number"])
    description_expression = _first_column_or_json(fields, ["notes", "description"], ["$.description", "$.notes"])
    currency_expression = _first_column(fields, ["original_currency", "currency_code", "source_currency"])
    amount_expression = _first_column(fields, ["original_amount", "transaction_amount"], "NUMERIC")
    amount_inr_expression = _first_column(fields, ["amount_inr"], "NUMERIC")
    status_expression = _first_column_or_json(fields, ["status"], ["$.status"])
    source_record_expression = _source_record_expression(fields, "transaction_id")
    filter_clause = _build_filter_clause(
        "transaction_date", start_date, end_date, organization, vendor, status, "vendor_contact_name"
    )
    order_clause = "ORDER BY transaction_date DESC, vendor_contact_name, transaction_id" if order_rows else ""

    return f"""
        SELECT
            organization,
            vendor_contact_id,
            vendor_contact_name,
            transaction_id,
            transaction_date,
            transaction_type,
            reference_number,
            description,
            currency,
            amount,
            amount_inr,
            status,
            source_record_id
        FROM (
            SELECT
                {organization_expression} AS organization,
                {contact_id_expression} AS vendor_contact_id,
                {contact_name_expression} AS vendor_contact_name,
                {transaction_id_expression} AS transaction_id,
                {transaction_date_expression} AS transaction_date,
                {transaction_type_expression} AS transaction_type,
                {reference_expression} AS reference_number,
                {description_expression} AS description,
                {currency_expression} AS currency,
                {amount_expression} AS amount,
                {amount_inr_expression} AS amount_inr,
                {status_expression} AS status,
                {source_record_expression} AS source_record_id
            FROM {_table_name(project_id, TRANSACTIONS_VIEW)}
        )
        {filter_clause}
        {order_clause}
    """


def _build_contacts_query(project_id: str, fields: set[str]) -> str:
    organization_expression = _organization_expression(fields)
    contact_id_expression = _first_column(fields, ["contact_id", "source_record_id"])
    contact_name_expression = _first_column_or_json(
        fields, ["contact_name", "company_name"], ["$.contact_name", "$.company_name"]
    )
    contact_type_expression = _first_column_or_json(fields, ["contact_type"], ["$.contact_type"])
    return f"""
        SELECT
            {organization_expression} AS organization,
            {contact_id_expression} AS contact_id,
            {contact_name_expression} AS contact_name,
            {contact_type_expression} AS contact_type
        FROM {_table_name(project_id, CONTACTS_VIEW)}
    """


def _query_parameters(
    start_date: date | None,
    end_date: date | None,
    organization: str | None,
    vendor: str | None,
    status: str | None,
) -> list[Any]:
    from google.cloud import bigquery

    values = {
        "start_date": ("DATE", start_date),
        "end_date": ("DATE", end_date),
        "organization": ("STRING", organization),
        "vendor": ("STRING", vendor),
        "status": ("STRING", status),
    }
    return [
        bigquery.ScalarQueryParameter(name, value_type, value)
        for name, (value_type, value) in values.items()
        if value is not None
    ]


def fetch_vendor_report_data(
    start_date: date,
    end_date: date,
    organization: str | None = None,
    vendor: str | None = None,
    status: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Fetch bills and transactions after checking the live view schemas."""
    from google.cloud import bigquery

    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")

    resolved_project_id = _project_id(project_id)
    resolved_location = _bigquery_location(location)
    query_client = client or bigquery.Client(project=resolved_project_id, location=resolved_location)
    contact_fields = _schema_fields(query_client, resolved_project_id, CONTACTS_VIEW)
    bill_fields = _schema_fields(query_client, resolved_project_id, BILLS_VIEW)
    transaction_fields = _schema_fields(query_client, resolved_project_id, TRANSACTIONS_VIEW)
    parameters = _query_parameters(start_date, end_date, organization, vendor, status)

    bills = _rename_query_columns(
        _query_to_dataframe(
            query_client,
            _build_bills_query(
                resolved_project_id, bill_fields, start_date, end_date, organization, vendor, status
            ),
            parameters,
            location=resolved_location,
        ),
        BILL_QUERY_COLUMN_MAP,
    )
    transactions = _rename_query_columns(
        _query_to_dataframe(
            query_client,
            _build_transactions_query(
                resolved_project_id, transaction_fields, start_date, end_date, organization, vendor, status
            ),
            parameters,
            location=resolved_location,
        ),
        TRANSACTION_QUERY_COLUMN_MAP,
    )

    vendor_link_values = transactions[["Vendor/Contact ID", "Vendor/Contact Name"]].fillna("")
    transaction_vendor_fields_available = bool(
        vendor_link_values.astype(str).apply(lambda column: column.str.strip().ne("").any()).any()
    )

    availability = {
        "project_id": resolved_project_id,
        "location": resolved_location,
        "contact_fields": sorted(contact_fields),
        "bill_fields": sorted(bill_fields),
        "transaction_fields": sorted(transaction_fields),
        "explicit_payment_records": False,
        "transaction_vendor_fields_available": transaction_vendor_fields_available,
    }
    return bills, transactions, availability


def fetch_vendor_filter_options(
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> dict[str, list[str]]:
    """Return organization, vendor, and status values for report filters."""
    from google.cloud import bigquery

    resolved_project_id = _project_id(project_id)
    resolved_location = _bigquery_location(location)
    query_client = client or bigquery.Client(project=resolved_project_id, location=resolved_location)
    contact_fields = _schema_fields(query_client, resolved_project_id, CONTACTS_VIEW)
    bill_fields = _schema_fields(query_client, resolved_project_id, BILLS_VIEW)
    transaction_fields = _schema_fields(query_client, resolved_project_id, TRANSACTIONS_VIEW)
    contacts_query = _build_contacts_query(resolved_project_id, contact_fields)
    bills_query = _build_bills_query(resolved_project_id, bill_fields, order_rows=False)
    transactions_query = _build_transactions_query(resolved_project_id, transaction_fields, order_rows=False)

    options_query = f"""
        WITH contacts AS ({contacts_query}), bills AS ({bills_query}), transactions AS ({transactions_query})
        SELECT 'organization' AS option_type, organization AS option_value FROM bills
        UNION DISTINCT
        SELECT 'organization', organization FROM transactions
        UNION DISTINCT
        SELECT 'organization', organization FROM contacts
        UNION DISTINCT
        SELECT 'vendor', vendor_name FROM bills
        UNION DISTINCT
        SELECT 'vendor', vendor_contact_name FROM transactions
        UNION DISTINCT
        SELECT 'vendor', contact_name FROM contacts WHERE LOWER(COALESCE(contact_type, '')) LIKE '%vendor%'
        UNION DISTINCT
        SELECT 'status', status FROM bills
        UNION DISTINCT
        SELECT 'status', status FROM transactions
    """
    options_df = _query_to_dataframe(query_client, options_query, location=resolved_location)
    output: dict[str, list[str]] = {"organizations": [], "vendors": [], "statuses": []}
    key_map = {"organization": "organizations", "vendor": "vendors", "status": "statuses"}
    for option_type, group in options_df.dropna(subset=["option_value"]).groupby("option_type"):
        key = key_map.get(str(option_type))
        if key:
            output[key] = sorted({str(value).strip() for value in group["option_value"] if str(value).strip()})
    return output


def _clean_dataframe(dataframe: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    if dataframe is None:
        return pd.DataFrame(columns=columns)
    return dataframe.copy().reindex(columns=columns)


def _excel_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime().replace(tzinfo=None)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _style_title(worksheet: Any, title: str, width: int) -> None:
    worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=width)
    cell = worksheet.cell(1, 1, title)
    cell.fill = PatternFill("solid", fgColor="1F2D4E")
    cell.font = Font(color="FFFFFF", bold=True, size=15)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    worksheet.row_dimensions[1].height = 26


def _write_detail_sheet(worksheet: Any, title: str, dataframe: pd.DataFrame, date_columns: set[str]) -> None:
    _style_title(worksheet, title, len(dataframe.columns))
    header_row = 3
    border = Border(bottom=Side(style="thin", color="9CA3AF"))
    for column_index, column_name in enumerate(dataframe.columns, start=1):
        cell = worksheet.cell(header_row, column_index, column_name)
        cell.fill = PatternFill("solid", fgColor="2F5496")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    for row_index, row in enumerate(dataframe.itertuples(index=False, name=None), start=header_row + 1):
        for column_index, value in enumerate(row, start=1):
            cell = worksheet.cell(row_index, column_index, _excel_value(value))
            column_name = dataframe.columns[column_index - 1]
            if column_name in date_columns and cell.value is not None:
                cell.number_format = "yyyy-mm-dd"
            elif column_name in TEXT_IDENTIFIER_COLUMNS:
                cell.number_format = "@"
            elif column_name in {"Bill Amount", "Outstanding Balance", "Amount", "Amount in INR"}:
                cell.number_format = '#,##0.00;[Red](#,##0.00);-'
                cell.alignment = Alignment(horizontal="right")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="top")
            if row_index % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="EBF3FB")

    worksheet.freeze_panes = "A4"
    worksheet.auto_filter.ref = f"A3:{get_column_letter(len(dataframe.columns))}{max(header_row, header_row + len(dataframe))}"
    worksheet.sheet_view.showGridLines = False
    for column_index, column_name in enumerate(dataframe.columns, start=1):
        max_length = len(column_name)
        for value in dataframe.iloc[:, column_index - 1].head(200):
            if not pd.isna(value):
                max_length = max(max_length, len(str(value)))
        worksheet.column_dimensions[get_column_letter(column_index)].width = min(max(max_length + 2, 12), 32)


def create_vendor_report_workbook(
    bills: pd.DataFrame | None,
    transactions: pd.DataFrame | None,
    filters: dict[str, Any] | None = None,
    availability: dict[str, Any] | None = None,
) -> Workbook:
    """Create a four-sheet vendor workbook, including safe empty-data output."""
    bill_data = _clean_dataframe(bills, BILL_COLUMNS)
    transaction_data = _clean_dataframe(transactions, TRANSACTION_COLUMNS)
    selected_filters = filters or {}
    availability = availability or {}

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    vendor_bills = workbook.create_sheet("Vendor Bills")
    vendor_transactions = workbook.create_sheet("Vendor Transactions")
    data_availability = workbook.create_sheet("Data Availability")

    _style_title(summary, "Vendor Payments & Transactions", 4)
    summary.sheet_view.showGridLines = False
    summary.column_dimensions["A"].width = 28
    summary.column_dimensions["B"].width = 30
    summary.column_dimensions["C"].width = 22
    summary.column_dimensions["D"].width = 22
    summary["A3"] = PAYMENT_AVAILABILITY_NOTE
    summary.merge_cells("A3:D4")
    summary["A3"].alignment = Alignment(wrap_text=True, vertical="top")
    summary["A3"].fill = PatternFill("solid", fgColor="FFF2CC")
    summary["A3"].font = Font(italic=True, color="7F6000")

    summary_rows = [
        ("Generated At (UTC)", datetime.now(timezone.utc).replace(tzinfo=None)),
        ("Start Date", selected_filters.get("start_date")),
        ("End Date", selected_filters.get("end_date")),
        ("Organization", selected_filters.get("organization") or "All Organizations"),
        ("Vendor", selected_filters.get("vendor") or "All Vendors"),
        ("Status", selected_filters.get("status") or "All Statuses"),
        ("Vendor Bill Rows", len(bill_data)),
        ("Vendor Transaction Rows", len(transaction_data)),
    ]
    for row_number, (label, value) in enumerate(summary_rows, start=6):
        summary.cell(row_number, 1, label).font = Font(bold=True, color="1F2D4E")
        summary.cell(row_number, 2, _excel_value(value))
        if isinstance(value, datetime):
            summary.cell(row_number, 2).number_format = "yyyy-mm-dd hh:mm"
        elif isinstance(value, date):
            summary.cell(row_number, 2).number_format = "yyyy-mm-dd"

    summary["A16"] = "Interpretation"
    summary["A16"].font = Font(bold=True, color="FFFFFF")
    summary["A16"].fill = PatternFill("solid", fgColor="2F5496")
    summary.merge_cells("A16:D16")
    summary["A17"] = (
        "Bill amounts and outstanding balances are bill measures, not paid amounts. "
        "Transaction amounts are shown separately and currencies are not combined."
    )
    summary.merge_cells("A17:D18")
    summary["A17"].alignment = Alignment(wrap_text=True, vertical="top")

    _write_detail_sheet(vendor_bills, "Vendor Bills", bill_data, {"Bill Date", "Due Date"})
    _write_detail_sheet(
        vendor_transactions,
        "Vendor Transactions",
        transaction_data,
        {"Transaction Date"},
    )

    _style_title(data_availability, "Data Availability", 3)
    data_availability.sheet_view.showGridLines = False
    transaction_linkage_available = bool(availability.get("transaction_vendor_fields_available"))
    availability_rows = [
        ("Source", "Availability", "Details"),
        (CONTACTS_VIEW, "Available", "Vendor/contact master data is available for filter and reference use."),
        (BILLS_VIEW, "Available", "Bills are shown with bill amount and outstanding balance; these are not paid totals."),
        (
            TRANSACTIONS_VIEW,
            "Available",
            "Transaction identifiers, dates, references, descriptions, currencies, amounts, INR amounts, and statuses are exported when present.",
        ),
        (
            "Transaction vendor/contact linkage",
            "Limited" if transaction_linkage_available else "Unavailable",
            TRANSACTION_LINKAGE_NOTE if transaction_linkage_available else TRANSACTION_LINKAGE_UNAVAILABLE_NOTE,
        ),
        ("Dedicated vendor payments", "Not available", PAYMENT_AVAILABILITY_NOTE),
        (
            "Amount in INR",
            "Available" if "amount_inr" in set(availability.get("transaction_fields", [])) else "Not confirmed/blank",
            "Populated only when the live transaction view exposes amount_inr.",
        ),
        (
            "Schema inspection",
            "Runtime",
            "The report service reads live BigQuery schemas before selecting fields; missing optional fields are exported as blanks.",
        ),
    ]
    for row_index, row in enumerate(availability_rows, start=3):
        for column_index, value in enumerate(row, start=1):
            cell = data_availability.cell(row_index, column_index, value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if row_index == 3:
                cell.fill = PatternFill("solid", fgColor="2F5496")
                cell.font = Font(color="FFFFFF", bold=True)
            elif row_index % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="EBF3FB")
    data_availability.freeze_panes = "A4"
    data_availability.column_dimensions["A"].width = 34
    data_availability.column_dimensions["B"].width = 22
    data_availability.column_dimensions["C"].width = 90
    for row_number in range(4, 11):
        data_availability.row_dimensions[row_number].height = 44

    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    return workbook


def generate_vendor_transactions_report(
    start_date: date,
    end_date: date,
    organization: str | None = None,
    vendor: str | None = None,
    status: str | None = None,
    destination_folder: str | Path | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Fetch BigQuery data, create the workbook, and save it for download."""
    bills, transactions, availability = fetch_vendor_report_data(
        start_date=start_date,
        end_date=end_date,
        organization=organization,
        vendor=vendor,
        status=status,
        project_id=project_id,
        location=location,
        client=client,
    )
    output_folder = Path(destination_folder or Path(__file__).resolve().parents[2] / "frontend" / "outputs" / "reports")
    output_folder.mkdir(parents=True, exist_ok=True)
    report_path = output_folder / f"Vendor_Payments_Transactions_{start_date:%Y%m%d}_{end_date:%Y%m%d}.xlsx"
    workbook = create_vendor_report_workbook(
        bills,
        transactions,
        filters={
            "start_date": start_date,
            "end_date": end_date,
            "organization": organization,
            "vendor": vendor,
            "status": status,
        },
        availability=availability,
    )
    workbook.save(report_path)
    return {
        "report_path": report_path,
        "bill_rows": len(bills),
        "transaction_rows": len(transactions),
        "total_rows": len(bills) + len(transactions),
        "message": "Vendor Payments & Transactions report generated successfully.",
        "availability_note": PAYMENT_AVAILABILITY_NOTE,
    }
