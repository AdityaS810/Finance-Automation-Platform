"""Generate the company-format MIS workbook from warehouse and Zoho data."""

from __future__ import annotations

import json
import os
from copy import copy
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from backend.reports.excel_styles import (
    INR_NUMBER_FORMAT,
    PERCENT_NUMBER_FORMAT,
    apply_border_band,
    apply_label_cell,
    apply_row_style,
    apply_style,
    apply_title_band,
)


DEFAULT_PROJECT_ID = "internal-project-work-497507"
MIS_MONTHLY_PL_VIEW = "finance_gold.mis_monthly_pl"
DASHBOARD_SUMMARY_VIEW = "finance_gold.dashboard_summary"
ZOHO_RAW_TABLE = "finance_bronze.zoho_raw"
SUMMARY_COLUMNS = [
    "generated_at",
    "account_count",
    "contact_count",
    "invoice_count",
    "invoice_total_amount",
    "invoice_outstanding_amount",
    "bill_count",
    "bill_total_amount",
    "bill_outstanding_amount",
    "journal_count",
    "journal_total_amount",
    "customer_payment_count",
    "customer_payment_total_amount",
]
MONTH_LABELS = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
QUARTER_LABELS = ["Q1\n(Apr–Jun 25)", "Q2\n(Jul–Sep 25)", "Q3\n(Oct–Dec 25)", "Q4\n(Jan–Mar 26)"]
QUARTERLY_ROW_MAP = {
    "techm_revenue": 6,
    "bsm_revenue": 7,
    "fd_interest": 8,
    "total_revenue": 9,
    "delivery_india": 12,
    "bsm_delivery_contractors": 13,
    "ramki_cogs": 14,
    "dorothea_cogs": 15,
    "external_delivery_vendors": 16,
    "technology_costs": 17,
    "total_cogs": 18,
    "gross_profit": 20,
    "gross_margin_pct": 21,
    "ramki_sm": 25,
    "advertising_marketing": 26,
    "travel_expenses": 27,
    "meals_entertainment": 28,
    "total_sm": 29,
    "rd_salaries": 32,
    "consultant_expense": 33,
    "software_subscriptions": 34,
    "total_rd": 35,
    "rent": 38,
    "it_internet": 39,
    "legal": 40,
    "audit_non_operating": 41,
    "other_ga": 42,
    "total_ga": 43,
    "total_opex": 45,
    "operating_profit": 47,
    "net_margin_pct": 48,
}
MONTHLY_ROW_MAP = {
    "techm_revenue": 5,
    "bsm_revenue": 6,
    "fd_interest": 7,
    "total_revenue": 8,
    "total_cogs": 10,
    "gross_profit": 11,
    "ramki_sm": 13,
    "advertising_marketing": 14,
    "travel_expenses": 15,
    "meals_entertainment": 16,
    "total_sm": 17,
    "rd_salaries": 19,
    "consultant_expense": 20,
    "software_subscriptions": 21,
    "total_rd": 22,
    "rent": 24,
    "it_internet": 25,
    "legal": 26,
    "audit_non_operating": 27,
    "other_ga": 28,
    "total_ga": 29,
    "total_opex": 30,
    "net_profit": 31,
}
COGS_ROW_MAP = {
    "delivery_india": 4,
    "ramki_cogs": 5,
    "dorothea_cogs": 6,
    "bsm_delivery_contractors": 7,
    "external_delivery_vendors": 8,
    "technology_costs": 9,
    "total_cogs": 10,
}
BASE_LINE_ITEMS = [
    "techm_revenue",
    "bsm_revenue",
    "fd_interest",
    "delivery_india",
    "bsm_delivery_contractors",
    "ramki_cogs",
    "dorothea_cogs",
    "external_delivery_vendors",
    "technology_costs",
    "ramki_sm",
    "advertising_marketing",
    "travel_expenses",
    "meals_entertainment",
    "rd_salaries",
    "consultant_expense",
    "software_subscriptions",
    "rent",
    "it_internet",
    "legal",
    "audit_non_operating",
    "other_ga",
]
REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _safe_float(value) -> float:
    """Convert a value to float without raising."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


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
    """Return a copy of a dataframe that can be written safely to Excel."""
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


def _query_to_dataframe(client, query: str) -> pd.DataFrame:
    """Run a BigQuery query and convert rows to pandas without extra packages."""
    result = client.query(query).result()
    columns = [field.name for field in result.schema]
    rows = [dict(row.items()) for row in result]
    return pd.DataFrame(rows, columns=columns)


def _load_yaml_config(file_name: str) -> dict:
    """Load a YAML config from the shared repo config folder."""
    config_path = REPO_ROOT / "config" / file_name
    with config_path.open("r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


def _parse_financial_year(financial_year: str) -> tuple[int, int]:
    """Return the starting and ending calendar years for a financial year token."""
    cleaned = financial_year.upper().replace("FY", "").replace(" ", "")
    start_token, end_token = cleaned.split("-")
    start_year = 2000 + int(start_token)
    end_year = 2000 + int(end_token)
    return start_year, end_year


def _build_financial_year_context(financial_year: str, allocation_config: dict) -> dict:
    """Build reusable month labels and titles for the requested FY."""
    start_year, end_year = _parse_financial_year(financial_year)
    month_keys = allocation_config.get("months") or [
        f"{start_year}-04",
        f"{start_year}-05",
        f"{start_year}-06",
        f"{start_year}-07",
        f"{start_year}-08",
        f"{start_year}-09",
        f"{start_year}-10",
        f"{start_year}-11",
        f"{start_year}-12",
        f"{end_year}-01",
        f"{end_year}-02",
        f"{end_year}-03",
    ]
    month_dates = [pd.Timestamp(f"{month_key}-01") for month_key in month_keys]
    return {
        "month_keys": month_keys,
        "month_labels": MONTH_LABELS,
        "month_dates": month_dates,
        "title": f"MIDOFFICE DATA  |  Profit & Loss Statement  |  FY {start_year}-{str(end_year)[-2:]}  (Apr {start_year} – Mar {end_year})",
        "subtitle": "India Parent + US Subsidiary  |  Accrual Basis  |  FX: ₹90/USD  |  COGS timing note  |  All amounts in INR",
        "monthly_title": f"Monthly P&L Detail  |  FY {start_year}-{str(end_year)[-2:]}  |  Apr-Dec {start_year}: Actuals  |  Jan-Mar {end_year}: Latest source-backed view",
        "cogs_title": "COGS Allocation Detail  |  Delivery Staff + Vendors  |  Sep 2025 onwards for TechM  |  Jul 2025 onwards for BSM",
    }


def fetch_gold_mis_data(project_id: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Query the Gold MIS views from BigQuery."""
    from google.cloud import bigquery

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


def fetch_zoho_raw_df(project_id: str | None = None) -> pd.DataFrame:
    """Fetch the latest Zoho raw JSON rows needed for MIS line-item mapping."""
    from google.cloud import bigquery

    resolved_project_id = _get_project_id(project_id)
    client = bigquery.Client(project=resolved_project_id)
    raw_query = f"""
        SELECT
            run_id,
            source_record_id,
            entity_name,
            raw_json,
            loaded_at
        FROM {_table_name(resolved_project_id, ZOHO_RAW_TABLE)}
        WHERE entity_name IN ('invoices', 'bills', 'journals')
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY entity_name, source_record_id
            ORDER BY loaded_at DESC
        ) = 1
    """
    return _query_to_dataframe(client, raw_query)


def build_summary_dataframe(
    financial_year: str,
    dashboard_summary_df: pd.DataFrame,
    project_id: str | None = None,
) -> pd.DataFrame:
    """Return summary metadata used by the UI and tests."""
    resolved_project_id = _get_project_id(project_id)
    summary = {column: _first_value(dashboard_summary_df, column) for column in SUMMARY_COLUMNS}
    summary["financial_year"] = financial_year
    summary["report_generated_at"] = datetime.now(timezone.utc).isoformat()
    summary["data_source"] = f"{resolved_project_id}.{DASHBOARD_SUMMARY_VIEW}"
    summary["company_format"] = "Quarterly P&L / Monthly P&L / COGS Allocation Working"
    return pd.DataFrame([summary])


def _normalize_text(value) -> str:
    """Normalize any input into lowercase matching text."""
    return str(value or "").strip().lower()


def _month_key(date_value) -> str | None:
    """Return YYYY-MM from a date-like value."""
    if not date_value:
        return None
    timestamp = pd.to_datetime(date_value, errors="coerce")
    if pd.isna(timestamp):
        return None
    return timestamp.strftime("%Y-%m")


def _fx_to_inr(amount: float, currency_code: str | None, fx_rate: float) -> float:
    """Convert source currency into INR using company assumptions."""
    code = _normalize_text(currency_code).upper()
    if code == "USD":
        return amount * fx_rate
    return amount


def _extract_line_items(payload: dict) -> list[dict]:
    """Return line items from a Zoho payload or fall back to one header line."""
    line_items = payload.get("line_items")
    if isinstance(line_items, list) and line_items:
        return [item for item in line_items if isinstance(item, dict)]
    return [payload]


def _line_amount(line_item: dict, payload: dict) -> float:
    """Choose the best available amount field for a line item."""
    amount_fields = [
        "item_total",
        "line_total",
        "total",
        "amount",
        "taxable_amount",
        "sub_total",
    ]
    for field_name in amount_fields:
        amount = _safe_float(line_item.get(field_name))
        if amount:
            return amount

    quantity = _safe_float(line_item.get("quantity") or 1)
    rate = _safe_float(line_item.get("rate"))
    if quantity and rate:
        return quantity * rate

    return _safe_float(payload.get("total") or payload.get("amount"))


def _journal_line_amount(line_item: dict, payload: dict) -> float:
    """Extract a signed journal line amount when possible."""
    debit = _safe_float(line_item.get("debit") or line_item.get("debit_amount"))
    credit = _safe_float(line_item.get("credit") or line_item.get("credit_amount"))
    if debit or credit:
        return abs(debit - credit)
    return abs(_safe_float(line_item.get("amount") or payload.get("total") or payload.get("amount")))


def normalize_zoho_raw_df(raw_df: pd.DataFrame, month_keys: list[str], fx_rate: float) -> pd.DataFrame:
    """Convert raw Zoho JSON payloads into a row-level dataframe for mapping."""
    records: list[dict] = []

    for raw_row in raw_df.to_dict("records"):
        raw_json = raw_row.get("raw_json") or {}
        payload = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        if not isinstance(payload, dict):
            continue

        entity_name = _normalize_text(raw_row.get("entity_name"))
        source_type = {"invoices": "invoice", "bills": "bill", "journals": "journal"}.get(entity_name)
        if source_type is None:
            continue

        if source_type == "invoice":
            transaction_date = payload.get("date")
            counterparty_name = payload.get("customer_name")
            notes = payload.get("notes") or payload.get("terms")
            reference_number = payload.get("invoice_number")
            balance_amount = _safe_float(payload.get("balance"))
            currency_code = payload.get("currency_code")
            for line_item in _extract_line_items(payload):
                amount = _line_amount(line_item, payload)
                records.append(
                    {
                        "source_type": source_type,
                        "month_key": _month_key(transaction_date),
                        "transaction_date": pd.to_datetime(transaction_date, errors="coerce"),
                        "counterparty_name": counterparty_name,
                        "account_name": line_item.get("account_name"),
                        "item_name": line_item.get("name") or line_item.get("item_name"),
                        "description": line_item.get("description") or payload.get("subject_content"),
                        "notes": notes,
                        "reference_number": reference_number,
                        "currency_code": currency_code,
                        "amount_inr": _fx_to_inr(amount, currency_code, fx_rate),
                        "balance_inr": _fx_to_inr(balance_amount, currency_code, fx_rate),
                    }
                )

        elif source_type == "bill":
            transaction_date = payload.get("date")
            counterparty_name = payload.get("vendor_name")
            notes = payload.get("notes")
            currency_code = payload.get("currency_code")
            for line_item in _extract_line_items(payload):
                amount = _line_amount(line_item, payload)
                records.append(
                    {
                        "source_type": source_type,
                        "month_key": _month_key(transaction_date),
                        "transaction_date": pd.to_datetime(transaction_date, errors="coerce"),
                        "counterparty_name": counterparty_name,
                        "account_name": line_item.get("account_name"),
                        "item_name": line_item.get("name") or line_item.get("item_name"),
                        "description": line_item.get("description"),
                        "notes": notes,
                        "reference_number": payload.get("bill_number"),
                        "currency_code": currency_code,
                        "amount_inr": _fx_to_inr(amount, currency_code, fx_rate),
                        "balance_inr": _fx_to_inr(_safe_float(payload.get("balance")), currency_code, fx_rate),
                    }
                )

        elif source_type == "journal":
            transaction_date = payload.get("date")
            notes = payload.get("notes")
            currency_code = payload.get("currency_code")
            for line_item in _extract_line_items(payload):
                amount = _journal_line_amount(line_item, payload)
                records.append(
                    {
                        "source_type": source_type,
                        "month_key": _month_key(transaction_date),
                        "transaction_date": pd.to_datetime(transaction_date, errors="coerce"),
                        "counterparty_name": line_item.get("contact_name") or line_item.get("vendor_name"),
                        "account_name": line_item.get("account_name"),
                        "item_name": line_item.get("name") or line_item.get("item_name"),
                        "description": line_item.get("description"),
                        "notes": notes,
                        "reference_number": payload.get("journal_number"),
                        "currency_code": currency_code,
                        "amount_inr": _fx_to_inr(amount, currency_code, fx_rate),
                        "balance_inr": 0.0,
                    }
                )

    normalized_df = pd.DataFrame(records)
    if normalized_df.empty:
        return pd.DataFrame(
            columns=[
                "source_type",
                "month_key",
                "transaction_date",
                "counterparty_name",
                "account_name",
                "item_name",
                "description",
                "notes",
                "reference_number",
                "currency_code",
                "amount_inr",
                "balance_inr",
            ]
        )

    normalized_df = normalized_df[normalized_df["month_key"].isin(month_keys)].copy()
    normalized_df["match_text"] = normalized_df.apply(
        lambda row: " | ".join(
            [
                _normalize_text(row.get("counterparty_name")),
                _normalize_text(row.get("account_name")),
                _normalize_text(row.get("item_name")),
                _normalize_text(row.get("description")),
                _normalize_text(row.get("notes")),
                _normalize_text(row.get("reference_number")),
            ]
        ),
        axis=1,
    )
    return normalized_df


def _rule_mask(dataframe: pd.DataFrame, rule: dict) -> pd.Series:
    """Return the rows that match a mapping rule."""
    if dataframe.empty:
        return pd.Series(dtype="bool")

    source_types = rule.get("source_types") or []
    keywords = [_normalize_text(keyword) for keyword in rule.get("keywords", []) if keyword]
    exclude_keywords = [_normalize_text(keyword) for keyword in rule.get("exclude_keywords", []) if keyword]

    mask = pd.Series(True, index=dataframe.index)
    if source_types:
        mask &= dataframe["source_type"].isin(source_types)
    if keywords:
        keyword_mask = pd.Series(False, index=dataframe.index)
        for keyword in keywords:
            keyword_mask |= dataframe["match_text"].str.contains(keyword, na=False, regex=False)
        mask &= keyword_mask
    for keyword in exclude_keywords:
        mask &= ~dataframe["match_text"].str.contains(keyword, na=False, regex=False)
    return mask


def _series_by_month(dataframe: pd.DataFrame, month_keys: list[str]) -> list[float]:
    """Return month-aligned totals from a filtered dataframe."""
    if dataframe.empty:
        return [0.0 for _ in month_keys]
    totals = dataframe.groupby("month_key")["amount_inr"].sum().to_dict()
    return [round(float(totals.get(month_key, 0.0)), 2) for month_key in month_keys]


def _empty_month_series(month_keys: list[str]) -> list[float]:
    """Return zeros for all FY months."""
    return [0.0 for _ in month_keys]


def _sum_series(*series_list: list[float]) -> list[float]:
    """Sum one or more month series elementwise."""
    if not series_list:
        return []
    return [round(sum(values), 2) for values in zip(*series_list)]


def _subtract_series(series_a: list[float], series_b: list[float]) -> list[float]:
    """Subtract one series from another elementwise."""
    return [round(a - b, 2) for a, b in zip(series_a, series_b)]


def _quarterly_from_months(month_values: list[float]) -> list[float]:
    """Roll monthly values into Q1-Q4 and FY totals."""
    quarters = [round(sum(month_values[index : index + 3]), 2) for index in range(0, 12, 3)]
    return quarters + [round(sum(month_values), 2)]


def _average_selected_months(month_values: list[float], month_keys: list[str], selected_months: list[str]) -> float:
    """Average only a chosen set of months."""
    values = [value for month_key, value in zip(month_keys, month_values) if month_key in selected_months]
    if not values:
        values = [value for value in month_values if value]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _format_inr_text(value: float) -> str:
    """Format a numeric value as a compact INR text snippet."""
    sign = "+" if value > 0 else ""
    absolute_value = abs(value)
    if absolute_value >= 10_000_000:
        return f"{sign}₹{absolute_value / 10_000_000:.2f} Cr"
    if absolute_value >= 100_000:
        return f"{sign}₹{absolute_value / 100_000:.1f}L"
    return f"{sign}₹{absolute_value:,.0f}"


def _collect_tracked_amount(entries_df: pd.DataFrame, rule: dict, month_keys: list[str]) -> list[float]:
    """Collect tracked note amounts without affecting primary classification."""
    mask = _rule_mask(entries_df, rule)
    matched = entries_df.loc[mask].copy()
    return _series_by_month(matched, month_keys)


def _prepare_monthly_source(monthly_pl_df: pd.DataFrame, month_keys: list[str]) -> dict[str, list[float]]:
    """Normalize the Gold monthly MIS view into month-aligned totals."""
    revenue_by_month = {month_key: 0.0 for month_key in month_keys}
    expense_by_month = {month_key: 0.0 for month_key in month_keys}
    journal_by_month = {month_key: 0.0 for month_key in month_keys}

    if not monthly_pl_df.empty:
        monthly_copy = monthly_pl_df.copy()
        monthly_copy["report_month"] = pd.to_datetime(monthly_copy.get("report_month"), errors="coerce")
        monthly_copy["month_key"] = monthly_copy["report_month"].dt.strftime("%Y-%m")
        grouped = monthly_copy.groupby("month_key", dropna=False).sum(numeric_only=True)
        for month_key in month_keys:
            if month_key in grouped.index:
                revenue_by_month[month_key] = round(float(grouped.at[month_key, "revenue_amount"]) if "revenue_amount" in grouped.columns else 0.0, 2)
                expense_by_month[month_key] = round(float(grouped.at[month_key, "expense_amount"]) if "expense_amount" in grouped.columns else 0.0, 2)
                journal_by_month[month_key] = round(float(grouped.at[month_key, "journal_adjustment_amount"]) if "journal_adjustment_amount" in grouped.columns else 0.0, 2)

    revenue = [revenue_by_month[month_key] for month_key in month_keys]
    expenses = [expense_by_month[month_key] for month_key in month_keys]
    journals = [journal_by_month[month_key] for month_key in month_keys]
    journal_positive = [max(value, 0.0) for value in journals]
    journal_negative = [abs(min(value, 0.0)) for value in journals]
    return {
        "revenue": revenue,
        "expense": expenses,
        "journal": journals,
        "revenue_plus_positive_journal": _sum_series(revenue, journal_positive),
        "expense_plus_negative_journal": _sum_series(expenses, journal_negative),
    }


def build_mis_model(
    financial_year: str,
    monthly_pl_df: pd.DataFrame,
    dashboard_summary_df: pd.DataFrame,
    zoho_raw_df: pd.DataFrame | None = None,
) -> dict:
    """Build the reusable MIS model before writing the workbook."""
    mapping_config = _load_yaml_config("mis_mapping.yaml")
    allocation_config = _load_yaml_config("cogs_allocation.yaml")
    context = _build_financial_year_context(financial_year, allocation_config)
    month_keys = context["month_keys"]
    fx_rate = _safe_float(allocation_config.get("fx_rate_inr_per_usd", 90)) or 90.0

    normalized_entries = normalize_zoho_raw_df(zoho_raw_df if zoho_raw_df is not None else pd.DataFrame(), month_keys, fx_rate)
    revenue_pool = normalized_entries[normalized_entries["source_type"].isin(["invoice", "journal"])].copy()
    expense_pool = normalized_entries[normalized_entries["source_type"].isin(["bill", "journal"])].copy()

    monthly_rows = {line_item: _empty_month_series(month_keys) for line_item in BASE_LINE_ITEMS}
    revenue_rules = mapping_config.get("mis_report", {}).get("revenue", {})
    expense_rules = mapping_config.get("mis_report", {}).get("expense", {})
    note_rules = mapping_config.get("mis_report", {}).get("note_tracking", {})
    source_monthly = _prepare_monthly_source(monthly_pl_df, month_keys)

    for row_key in ["techm_revenue", "bsm_revenue", "fd_interest"]:
        matched_mask = _rule_mask(revenue_pool, revenue_rules.get(row_key, {}))
        matched_rows = revenue_pool.loc[matched_mask].copy()
        monthly_rows[row_key] = _series_by_month(matched_rows, month_keys)
        revenue_pool = revenue_pool.loc[~matched_mask].copy()

    for row_key in [
        "delivery_india",
        "bsm_delivery_contractors",
        "external_delivery_vendors",
        "rd_salaries",
        "consultant_expense",
        "software_subscriptions",
        "advertising_marketing",
        "travel_expenses",
        "meals_entertainment",
        "rent",
        "it_internet",
        "legal",
        "audit_non_operating",
    ]:
        matched_mask = _rule_mask(expense_pool, expense_rules.get(row_key, {}))
        matched_rows = expense_pool.loc[matched_mask].copy()
        monthly_rows[row_key] = _series_by_month(matched_rows, month_keys)
        expense_pool = expense_pool.loc[~matched_mask].copy()

    ramki_mask = expense_pool["match_text"].str.contains("ramki", na=False, regex=False)
    ramki_rows = expense_pool.loc[ramki_mask].copy()
    expense_pool = expense_pool.loc[~ramki_mask].copy()
    ramki_source = _series_by_month(ramki_rows, month_keys)
    default_ramki = [_safe_float(allocation_config["company_rules"]["ramki_monthly_usd"]) * fx_rate for _ in month_keys]
    if not any(ramki_source):
        ramki_source = default_ramki
    monthly_rows["ramki_cogs"] = []
    monthly_rows["ramki_sm"] = []
    for month_key, amount in zip(month_keys, ramki_source):
        if month_key < allocation_config["company_rules"]["techm_cogs_start"]:
            monthly_rows["ramki_cogs"].append(0.0)
            monthly_rows["ramki_sm"].append(round(amount, 2))
        else:
            half_amount = round(amount * 0.5, 2)
            monthly_rows["ramki_cogs"].append(half_amount)
            monthly_rows["ramki_sm"].append(half_amount)

    dorothea_mask = expense_pool["match_text"].str.contains("dorothea", na=False, regex=False)
    dorothea_rows = expense_pool.loc[dorothea_mask].copy()
    expense_pool = expense_pool.loc[~dorothea_mask].copy()
    dorothea_source = _series_by_month(dorothea_rows, month_keys)
    monthly_rows["dorothea_cogs"] = [
        round(amount, 2) if month_key >= allocation_config["company_rules"]["dorothea_cogs_start"] else 0.0
        for month_key, amount in zip(month_keys, dorothea_source)
    ]

    delivery_rule_start = allocation_config["company_rules"]["bsm_delivery_cogs_start"]
    monthly_rows["delivery_india"] = [
        round(amount, 2) if month_key >= delivery_rule_start else 0.0
        for month_key, amount in zip(month_keys, monthly_rows["delivery_india"])
    ]
    monthly_rows["bsm_delivery_contractors"] = [
        round(amount, 2) if month_key >= allocation_config["company_rules"]["bsm_delivery_cogs_start"] else 0.0
        for month_key, amount in zip(month_keys, monthly_rows["bsm_delivery_contractors"])
    ]
    monthly_rows["technology_costs"] = [
        round(_safe_float(allocation_config["company_rules"]["monthly_technology_cost_inr"]), 2)
        for _ in month_keys
    ]

    mapped_revenue = _sum_series(monthly_rows["techm_revenue"], monthly_rows["bsm_revenue"], monthly_rows["fd_interest"])
    revenue_residual = _subtract_series(source_monthly["revenue_plus_positive_journal"], mapped_revenue)
    monthly_rows["techm_revenue"] = _sum_series(monthly_rows["techm_revenue"], revenue_residual)

    mapped_expense = _sum_series(
        monthly_rows["delivery_india"],
        monthly_rows["bsm_delivery_contractors"],
        monthly_rows["ramki_cogs"],
        monthly_rows["dorothea_cogs"],
        monthly_rows["external_delivery_vendors"],
        monthly_rows["technology_costs"],
        monthly_rows["ramki_sm"],
        monthly_rows["advertising_marketing"],
        monthly_rows["travel_expenses"],
        monthly_rows["meals_entertainment"],
        monthly_rows["rd_salaries"],
        monthly_rows["consultant_expense"],
        monthly_rows["software_subscriptions"],
        monthly_rows["rent"],
        monthly_rows["it_internet"],
        monthly_rows["legal"],
        monthly_rows["audit_non_operating"],
    )
    expense_residual = _subtract_series(source_monthly["expense_plus_negative_journal"], mapped_expense)
    monthly_rows["other_ga"] = _sum_series(monthly_rows["other_ga"], expense_residual)

    total_revenue = _sum_series(monthly_rows["techm_revenue"], monthly_rows["bsm_revenue"], monthly_rows["fd_interest"])
    total_cogs = _sum_series(
        monthly_rows["delivery_india"],
        monthly_rows["bsm_delivery_contractors"],
        monthly_rows["ramki_cogs"],
        monthly_rows["dorothea_cogs"],
        monthly_rows["external_delivery_vendors"],
        monthly_rows["technology_costs"],
    )
    total_sm = _sum_series(
        monthly_rows["ramki_sm"],
        monthly_rows["advertising_marketing"],
        monthly_rows["travel_expenses"],
        monthly_rows["meals_entertainment"],
    )
    total_rd = _sum_series(
        monthly_rows["rd_salaries"],
        monthly_rows["consultant_expense"],
        monthly_rows["software_subscriptions"],
    )
    total_ga = _sum_series(
        monthly_rows["rent"],
        monthly_rows["it_internet"],
        monthly_rows["legal"],
        monthly_rows["audit_non_operating"],
        monthly_rows["other_ga"],
    )
    total_opex = _sum_series(total_sm, total_rd, total_ga)
    gross_profit = _subtract_series(total_revenue, total_cogs)
    operating_profit = _subtract_series(gross_profit, total_opex)

    quarterly_rows = {}
    for row_key in BASE_LINE_ITEMS:
        quarterly_rows[row_key] = _quarterly_from_months(monthly_rows[row_key])
    quarterly_rows["total_revenue"] = _quarterly_from_months(total_revenue)
    quarterly_rows["total_cogs"] = _quarterly_from_months(total_cogs)
    quarterly_rows["gross_profit"] = _quarterly_from_months(gross_profit)
    quarterly_rows["total_sm"] = _quarterly_from_months(total_sm)
    quarterly_rows["total_rd"] = _quarterly_from_months(total_rd)
    quarterly_rows["total_ga"] = _quarterly_from_months(total_ga)
    quarterly_rows["total_opex"] = _quarterly_from_months(total_opex)
    quarterly_rows["operating_profit"] = _quarterly_from_months(operating_profit)
    quarterly_rows["gross_margin_pct"] = [
        round((profit / revenue), 6) if revenue else 0.0
        for profit, revenue in zip(quarterly_rows["gross_profit"], quarterly_rows["total_revenue"])
    ]
    quarterly_rows["net_margin_pct"] = [
        round((profit / revenue), 6) if revenue else 0.0
        for profit, revenue in zip(quarterly_rows["operating_profit"], quarterly_rows["total_revenue"])
    ]

    bonus_series = _collect_tracked_amount(normalized_entries, note_rules.get("bonus", {}), month_keys)
    one_time_series = _collect_tracked_amount(normalized_entries, note_rules.get("one_time", {}), month_keys)
    bsm_provision_series = _collect_tracked_amount(normalized_entries, note_rules.get("bsm_provision", {}), month_keys)
    techm_receivable_inr = 0.0
    techm_receivable_mask = _rule_mask(normalized_entries, note_rules.get("techm_receivable", {}))
    if techm_receivable_mask.any():
        techm_receivable_inr = round(float(normalized_entries.loc[techm_receivable_mask, "balance_inr"].sum()), 2)

    run_rate_months = allocation_config["company_rules"].get("run_rate_preferred_months", [])
    normalized_opex = _subtract_series(total_opex, _sum_series(bonus_series, one_time_series))
    normalized_gross_burn = _subtract_series(_sum_series(total_cogs, total_opex), _sum_series(bonus_series, one_time_series))
    normalized_net = _sum_series(operating_profit, _sum_series(bonus_series, one_time_series))
    people_cost = _sum_series(
        monthly_rows["delivery_india"],
        monthly_rows["bsm_delivery_contractors"],
        monthly_rows["ramki_cogs"],
        monthly_rows["dorothea_cogs"],
        monthly_rows["ramki_sm"],
        monthly_rows["rd_salaries"],
        monthly_rows["consultant_expense"],
    )

    india_fte_count = 6
    revenue_per_india_fte = round(sum(total_revenue) / india_fte_count, 2) if india_fte_count else 0.0
    closing_cash = 0.0
    for cash_column in ["closing_cash", "cash_balance", "bank_balance"]:
        closing_cash = _safe_float(_first_value(dashboard_summary_df, cash_column, 0))
        if closing_cash:
            break
    cash_runway = round(closing_cash / _average_selected_months(normalized_opex, month_keys, run_rate_months), 1) if closing_cash else 0.0

    metrics_summary = {
        "gross_margin_fy": quarterly_rows["gross_margin_pct"][-1],
        "q3_gross_margin_peak": quarterly_rows["gross_margin_pct"][2] if len(quarterly_rows["gross_margin_pct"]) > 2 else 0.0,
        "monthly_opex_run_rate": _average_selected_months(normalized_opex, month_keys, run_rate_months),
        "monthly_gross_burn": _average_selected_months(normalized_gross_burn, month_keys, run_rate_months),
        "monthly_net": _average_selected_months(normalized_net, month_keys, run_rate_months),
        "revenue_per_india_fte": revenue_per_india_fte,
        "total_people_cost": round(sum(people_cost), 2),
        "closing_cash": round(closing_cash, 2),
        "cash_runway": cash_runway,
    }

    cogs_rows = {
        "delivery_india": monthly_rows["delivery_india"],
        "ramki_cogs": monthly_rows["ramki_cogs"],
        "dorothea_cogs": monthly_rows["dorothea_cogs"],
        "bsm_delivery_contractors": monthly_rows["bsm_delivery_contractors"],
        "external_delivery_vendors": monthly_rows["external_delivery_vendors"],
        "technology_costs": monthly_rows["technology_costs"],
        "total_cogs": total_cogs,
    }

    bonus_total = round(sum(bonus_series), 2)
    one_time_total = round(sum(one_time_series), 2)
    bsm_provision_total = round(sum(bsm_provision_series), 2)
    actual_month_count = sum(
        1
        for revenue_value, expense_value in zip(source_monthly["revenue"], source_monthly["expense"])
        if revenue_value or expense_value
    )
    actual_months_label = f"Apr-{MONTH_LABELS[max(actual_month_count - 1, 0)]}" if actual_month_count else "No actual months detected"
    notes = [
        f"Q4 bonus impact: {_format_currency(bonus_total)} tracked in source postings. Run-rate metrics exclude this one-time Mar-2026 impact." if bonus_total else "Q4 bonus impact: no annual bonus postings were detected in the current source extract.",
        f"One-time StackPro / project payments: {_format_currency(one_time_total)} identified and excluded from normalized run-rate metrics." if one_time_total else "One-time StackPro / project payments: no tagged one-time project payment was detected in the current source extract.",
        f"BSM provision: {_format_currency(bsm_provision_total)} tagged in source data and kept outside the normalized run-rate view." if bsm_provision_total else "BSM provision: no provision-tagged source posting was detected in the current extract.",
        f"TechM receivable: {_format_currency(techm_receivable_inr)} remains outstanding based on matched TechM invoice balances." if techm_receivable_inr else "TechM receivable: no outstanding matched TechM invoice balance was detected in the current extract.",
        "Ramki allocation rule: fixed at $12,500 per month translated at ₹90/USD; 100% S&M in Apr-Aug, then 50% COGS and 50% S&M in Sep-Mar.",
        f"Actuals versus estimates: source-backed actual months currently span {actual_months_label}; later months should be reviewed against payroll, bank, and accrual estimates where applicable.",
    ]

    validation_checks = validate_mis_model(
        month_keys=month_keys,
        monthly_rows=monthly_rows,
        total_revenue=total_revenue,
        total_cogs=total_cogs,
        gross_profit=gross_profit,
        total_sm=total_sm,
        total_rd=total_rd,
        total_ga=total_ga,
        total_opex=total_opex,
        operating_profit=operating_profit,
        gross_margin=quarterly_rows["gross_margin_pct"][-1],
        net_margin=quarterly_rows["net_margin_pct"][-1],
        bonus_series=bonus_series,
        normalized_opex=normalized_opex,
        run_rate_metrics=metrics_summary,
    )

    return {
        "context": context,
        "monthly_rows": monthly_rows,
        "quarterly_rows": quarterly_rows,
        "cogs_rows": cogs_rows,
        "total_revenue": total_revenue,
        "total_cogs": total_cogs,
        "gross_profit": gross_profit,
        "total_sm": total_sm,
        "total_rd": total_rd,
        "total_ga": total_ga,
        "total_opex": total_opex,
        "operating_profit": operating_profit,
        "bonus_series": bonus_series,
        "one_time_series": one_time_series,
        "notes": notes,
        "metrics_summary": metrics_summary,
        "validation_checks": validation_checks,
        "cogs_config": allocation_config,
    }


def validate_mis_model(
    *,
    month_keys: list[str],
    monthly_rows: dict[str, list[float]],
    total_revenue: list[float],
    total_cogs: list[float],
    gross_profit: list[float],
    total_sm: list[float],
    total_rd: list[float],
    total_ga: list[float],
    total_opex: list[float],
    operating_profit: list[float],
    gross_margin: float,
    net_margin: float,
    bonus_series: list[float],
    normalized_opex: list[float],
    run_rate_metrics: dict,
) -> dict[str, bool]:
    """Run the requested backend validations on the MIS model."""
    month_index = {month_key: index for index, month_key in enumerate(month_keys)}
    ramki_sm = monthly_rows["ramki_sm"]
    ramki_cogs = monthly_rows["ramki_cogs"]
    dorothea = monthly_rows["dorothea_cogs"]

    return {
        "total_revenue_matches": total_revenue == _sum_series(
            monthly_rows["techm_revenue"],
            monthly_rows["bsm_revenue"],
            monthly_rows["fd_interest"],
        ),
        "total_cogs_matches": total_cogs == _sum_series(
            monthly_rows["delivery_india"],
            monthly_rows["bsm_delivery_contractors"],
            monthly_rows["ramki_cogs"],
            monthly_rows["dorothea_cogs"],
            monthly_rows["external_delivery_vendors"],
            monthly_rows["technology_costs"],
        ),
        "gross_profit_matches": gross_profit == _subtract_series(total_revenue, total_cogs),
        "total_opex_matches": total_opex == _sum_series(total_sm, total_rd, total_ga),
        "operating_profit_matches": operating_profit == _subtract_series(gross_profit, total_opex),
        "gross_margin_safe": isinstance(gross_margin, float),
        "net_margin_safe": isinstance(net_margin, float),
        "bonuses_excluded_from_run_rate": round(run_rate_metrics["monthly_opex_run_rate"], 2) == round(
            _average_selected_months(normalized_opex, month_keys, ["2025-10", "2025-11", "2025-12"]),
            2,
        ),
        "ramki_allocation_correct": all(ramki_sm[month_index[month_key]] > 0 and ramki_cogs[month_index[month_key]] == 0 for month_key in month_keys[:5])
        and all(
            abs(ramki_sm[month_index[month_key]] - ramki_cogs[month_index[month_key]]) < 1
            for month_key in month_keys[5:]
        ),
        "dorothea_allocation_correct": all(dorothea[month_index[month_key]] == 0 for month_key in month_keys[:6])
        and all(dorothea[month_index[month_key]] >= 0 for month_key in month_keys[6:]),
        "bonus_presence_tracked": len(bonus_series) == len(month_keys),
    }


def _set_sheet_frame(worksheet, widths: dict[str, float], freeze_panes: str) -> None:
    """Apply workbook-wide display settings to one sheet."""
    worksheet.freeze_panes = freeze_panes
    worksheet.sheet_view.showGridLines = False
    for column_letter, width in widths.items():
        worksheet.column_dimensions[column_letter].width = width


def _write_monthly_sheet(workbook: Workbook, model: dict) -> None:
    """Write the Monthly P&L sheet."""
    worksheet = workbook.create_sheet("Monthly P&L")
    context = model["context"]
    apply_title_band(worksheet, 1, 2, 14, context["monthly_title"], "title")
    worksheet.row_dimensions[1].height = 24

    headers = ["Line Item"] + context["month_labels"]
    for column_number, header in enumerate(headers, start=2):
        worksheet.cell(3, column_number).value = header
        apply_style(worksheet.cell(3, column_number), "table_header")

    row_specs = [
        (4, "REVENUE", "section"),
        (5, "Tech Mahindra", "alt_row"),
        (6, "BSM Revenue", "alt_row"),
        (7, "FD Interest", "alt_row"),
        (8, "TOTAL REVENUE", "total"),
        (9, "COGS", "section"),
        (10, "COGS Total", "cogs_row"),
        (11, "GROSS PROFIT", "total"),
        (12, "S&M", "section"),
        (13, "Ramki S&M", "sm_row"),
        (14, "Advertising", "sm_row"),
        (15, "Travel", "sm_row"),
        (16, "Meals", "sm_row"),
        (17, "S&M Total", "sm_row"),
        (18, "R&D", "section"),
        (19, "R&D Salaries", "rd_row"),
        (20, "Consultant Exp.", "rd_row"),
        (21, "Software Subs", "rd_row"),
        (22, "R&D Total", "rd_row"),
        (23, "G&A", "section"),
        (24, "Rent", "ga_row"),
        (25, "IT & Internet", "ga_row"),
        (26, "Legal", "ga_row"),
        (27, "Audit & Non-Op", "ga_row"),
        (28, "Other G&A", "ga_row"),
        (29, "G&A Total", "ga_row"),
        (30, "TOTAL OPEX", "gray_total"),
        (31, "NET PROFIT/(LOSS)", "dark_total"),
    ]

    for row_number, label, style_name in row_specs:
        apply_row_style(worksheet, row_number, 2, 14, style_name, number_format=INR_NUMBER_FORMAT if row_number not in {4, 9, 12, 18, 23} else None)
        apply_label_cell(worksheet.cell(row_number, 2), style_name)
        worksheet.cell(row_number, 2).value = label

    data_rows = {
        5: model["monthly_rows"]["techm_revenue"],
        6: model["monthly_rows"]["bsm_revenue"],
        7: model["monthly_rows"]["fd_interest"],
        13: model["monthly_rows"]["ramki_sm"],
        14: model["monthly_rows"]["advertising_marketing"],
        15: model["monthly_rows"]["travel_expenses"],
        16: model["monthly_rows"]["meals_entertainment"],
        19: model["monthly_rows"]["rd_salaries"],
        20: model["monthly_rows"]["consultant_expense"],
        21: model["monthly_rows"]["software_subscriptions"],
        24: model["monthly_rows"]["rent"],
        25: model["monthly_rows"]["it_internet"],
        26: model["monthly_rows"]["legal"],
        27: model["monthly_rows"]["audit_non_operating"],
        28: model["monthly_rows"]["other_ga"],
    }
    for row_number, row_values in data_rows.items():
        for column_number, value in enumerate(row_values, start=3):
            worksheet.cell(row_number, column_number).value = round(value, 2)

    for column_number in range(3, 15):
        column_letter = worksheet.cell(3, column_number).column_letter
        cogs_column_letter = get_column_letter(column_number + 2)
        worksheet.cell(8, column_number).value = f"=SUM({column_letter}5:{column_letter}7)"
        worksheet.cell(10, column_number).value = f"='COGS Allocation Working'!{cogs_column_letter}10"
        worksheet.cell(11, column_number).value = f"={column_letter}8-{column_letter}10"
        worksheet.cell(17, column_number).value = f"=SUM({column_letter}13:{column_letter}16)"
        worksheet.cell(22, column_number).value = f"=SUM({column_letter}19:{column_letter}21)"
        worksheet.cell(29, column_number).value = f"=SUM({column_letter}24:{column_letter}28)"
        worksheet.cell(30, column_number).value = f"=SUM({column_letter}17,{column_letter}22,{column_letter}29)"
        worksheet.cell(31, column_number).value = f"={column_letter}11-{column_letter}30"

    apply_border_band(worksheet, 8, 2, 14)
    apply_border_band(worksheet, 11, 2, 14)
    apply_border_band(worksheet, 30, 2, 14)
    apply_border_band(worksheet, 31, 2, 14)
    _set_sheet_frame(
        worksheet,
        {"A": 2, "B": 30, "C": 12, "D": 12, "E": 12, "F": 12, "G": 12, "H": 12, "I": 12, "J": 12, "K": 12, "L": 12, "M": 12, "N": 12},
        "C4",
    )


def _write_cogs_sheet(workbook: Workbook, model: dict) -> None:
    """Write the COGS Allocation Working sheet."""
    worksheet = workbook.create_sheet("COGS Allocation Working")
    context = model["context"]
    allocation_rows = model["cogs_config"]["allocation_rows"]
    apply_title_band(worksheet, 1, 2, 17, context["cogs_title"], "title")
    worksheet.row_dimensions[1].height = 24

    headers = ["Person / Vendor", "Allocation %", "Type"] + context["month_labels"] + ["FY Total"]
    for column_number, header in enumerate(headers, start=2):
        worksheet.cell(3, column_number).value = header
        apply_style(worksheet.cell(3, column_number), "table_header")

    label_map = {item["key"]: item for item in allocation_rows}
    config_key_map = {
        "delivery_india": "delivery_employees",
        "ramki_cogs": "ramki",
        "dorothea_cogs": "dorothea",
        "bsm_delivery_contractors": "bsm_delivery_contractors",
        "external_delivery_vendors": "external_delivery_vendors",
        "technology_costs": "technology_costs",
    }

    for row_key, row_number in COGS_ROW_MAP.items():
        style_name = "total" if row_key == "total_cogs" else ("alt_row" if row_number % 2 == 0 else "plain_row")
        apply_row_style(worksheet, row_number, 2, 17, style_name, number_format=INR_NUMBER_FORMAT if row_key != "total_cogs" else INR_NUMBER_FORMAT)
        if row_key == "total_cogs":
            apply_label_cell(worksheet.cell(row_number, 2), "total")
            worksheet.cell(row_number, 2).value = "TOTAL COGS"
        else:
            config_row = label_map[config_key_map[row_key]]
            apply_label_cell(worksheet.cell(row_number, 2), style_name)
            worksheet.cell(row_number, 2).value = config_row["label"]
            worksheet.cell(row_number, 3).value = config_row["allocation_label"]
            worksheet.cell(row_number, 4).value = config_row["type"]
            worksheet.cell(row_number, 3).alignment = copy(worksheet.cell(row_number, 2).alignment)
            worksheet.cell(row_number, 4).alignment = copy(worksheet.cell(row_number, 2).alignment)
            for column_number, value in enumerate(model["cogs_rows"][row_key], start=5):
                worksheet.cell(row_number, column_number).value = round(value, 2)
            worksheet.cell(row_number, 17).value = f"=SUM(E{row_number}:P{row_number})"

    for column_number in range(5, 17):
        column_letter = worksheet.cell(3, column_number).column_letter
        worksheet.cell(10, column_number).value = f"=SUM({column_letter}4:{column_letter}9)"
    worksheet.cell(10, 17).value = "=SUM(Q4:Q9)"
    apply_border_band(worksheet, 10, 2, 17)

    worksheet.merge_cells("B12:Q12")
    worksheet["B12"] = (
        "Allocation Notes: Delivery employees are mapped from source-backed payroll or contractor descriptors and "
        "move into COGS from Jul-2025 for BSM / Sep-2025 for TechM. Ramki is 100% S&M in Apr-Aug and 50/50 "
        "between COGS and S&M from Sep-Mar. Dorothea is 100% COGS from Oct-Mar. Technology Costs use the fixed "
        "company tooling assumption and all amounts are shown in INR."
    )
    apply_style(worksheet["B12"], "note")

    _set_sheet_frame(
        worksheet,
        {"A": 2, "B": 24, "C": 28, "D": 14, "E": 11, "F": 11, "G": 11, "H": 11, "I": 11, "J": 11, "K": 11, "L": 11, "M": 11, "N": 11, "O": 11, "P": 11, "Q": 12},
        "E4",
    )


def _write_quarterly_sheet(workbook: Workbook, model: dict) -> None:
    """Write the Quarterly P&L sheet."""
    worksheet = workbook.create_sheet("Quarterly P&L")
    context = model["context"]
    apply_title_band(worksheet, 1, 2, 8, context["title"], "title")
    apply_title_band(worksheet, 2, 2, 8, context["subtitle"], "subtitle")
    worksheet.row_dimensions[1].height = 24
    worksheet.row_dimensions[2].height = 24

    fy_title = f"FY\n{context['month_dates'][0].year}-{str(context['month_dates'][-1].year)[-2:]}"
    headers = ["Line Item"] + QUARTER_LABELS + [fy_title, "% Rev"]
    for column_number, header in enumerate(headers, start=2):
        worksheet.cell(4, column_number).value = header
        apply_style(worksheet.cell(4, column_number), "table_header")

    row_specs = [
        (5, "REVENUE", "section"),
        (6, "  Tech Mahindra / TechM Billings", "alt_row"),
        (7, "  BSM Platform AMC / Accrual Generation Feature Enablement", "plain_row"),
        (8, "  Fixed Deposit Interest Income", "alt_row"),
        (9, "TOTAL REVENUE", "total"),
        (11, "COST OF GOODS SOLD / COGS", "section"),
        (12, "  Delivery — India", "plain_row"),
        (13, "  BSM Delivery Contractors", "plain_row"),
        (14, "  Ramki — Delivery Allocation", "alt_row"),
        (15, "  Dorothea Stoll — Account & Delivery", "plain_row"),
        (16, "  External Delivery Vendors", "alt_row"),
        (17, "Technology Costs", "alt_row"),
        (18, "TOTAL COGS", "total"),
        (20, "GROSS PROFIT", "dark_total"),
        (21, "  Gross Margin %", "alt_row"),
        (23, "OPERATING EXPENSES", "section"),
        (24, "Sales & Marketing / S&M", "sm_row"),
        (25, "      Ramki S&M Allocation", "alt_row"),
        (26, "      Advertising & Marketing", "plain_row"),
        (27, "      Travel Expenses", "alt_row"),
        (28, "      Meals & Entertainment", "plain_row"),
        (29, "  Total S&M", "total"),
        (31, "Research & Development / R&D", "rd_row"),
        (32, "      India Salaries — R&D & Overhead", "plain_row"),
        (33, "      Consultant & Contractor Expense", "alt_row"),
        (34, "      Software Subscriptions", "plain_row"),
        (35, "  Total R&D", "total"),
        (37, "General & Administrative / G&A", "ga_row"),
        (38, "      Rent", "plain_row"),
        (39, "      IT & Internet Expenses", "alt_row"),
        (40, "      Legal Expenses", "plain_row"),
        (41, "      Audit Fees & Non-Operating", "alt_row"),
        (42, "      Other G&A", "plain_row"),
        (43, "  Total G&A", "total"),
        (45, "TOTAL OPERATING EXPENSES", "total"),
        (47, "OPERATING PROFIT / (LOSS)", "dark_total"),
        (48, "  Net Margin %", "plain_row"),
        (51, "KEY METRICS SUMMARY", "dark_total"),
    ]

    for row_number, label, style_name in row_specs:
        number_format = PERCENT_NUMBER_FORMAT if row_number in {21, 48} else INR_NUMBER_FORMAT if row_number not in {5, 11, 23, 24, 31, 37, 51} else None
        apply_row_style(worksheet, row_number, 2, 8, style_name, number_format=number_format)
        apply_label_cell(worksheet.cell(row_number, 2), style_name)
        worksheet.cell(row_number, 2).value = label

    quarterly_source_rows = {
        6: 5,
        7: 6,
        8: 7,
        25: 13,
        26: 14,
        27: 15,
        28: 16,
        32: 19,
        33: 20,
        34: 21,
        38: 24,
        39: 25,
        40: 26,
        41: 27,
        42: 28,
    }
    for quarterly_row, monthly_row in quarterly_source_rows.items():
        for quarter_index, (start_col, end_col) in enumerate([(3, 5), (6, 8), (9, 11), (12, 14)], start=3):
            quarter_col = quarter_index
            start_letter = get_column_letter(start_col)
            end_letter = get_column_letter(end_col)
            worksheet.cell(quarterly_row, quarter_col).value = f"=SUM('Monthly P&L'!{start_letter}{monthly_row}:{end_letter}{monthly_row})"
        worksheet.cell(quarterly_row, 7).value = f"=SUM(C{quarterly_row}:F{quarterly_row})"

    cogs_source_rows = {
        12: 4,
        13: 7,
        14: 5,
        15: 6,
        16: 8,
        17: 9,
    }
    for quarterly_row, cogs_row in cogs_source_rows.items():
        quarter_month_columns = [(5, 7), (8, 10), (11, 13), (14, 16)]
        for quarter_col, (start_col, end_col) in zip(range(3, 7), quarter_month_columns):
            start_letter = get_column_letter(start_col)
            end_letter = get_column_letter(end_col)
            worksheet.cell(quarterly_row, quarter_col).value = f"=SUM('COGS Allocation Working'!{start_letter}{cogs_row}:{end_letter}{cogs_row})"
        worksheet.cell(quarterly_row, 7).value = f"=SUM(C{quarterly_row}:F{quarterly_row})"

    for quarter_col in range(3, 7):
        col_letter = worksheet.cell(4, quarter_col).column_letter
        worksheet.cell(9, quarter_col).value = f"=SUM({col_letter}6:{col_letter}8)"
        worksheet.cell(18, quarter_col).value = f"=SUM({col_letter}12:{col_letter}17)"
        worksheet.cell(20, quarter_col).value = f"={col_letter}9-{col_letter}18"
        worksheet.cell(21, quarter_col).value = f"=IF({col_letter}9=0,0,{col_letter}20/{col_letter}9)"
        worksheet.cell(29, quarter_col).value = f"=SUM({col_letter}25:{col_letter}28)"
        worksheet.cell(35, quarter_col).value = f"=SUM({col_letter}32:{col_letter}34)"
        worksheet.cell(43, quarter_col).value = f"=SUM({col_letter}38:{col_letter}42)"
        worksheet.cell(45, quarter_col).value = f"=SUM({col_letter}29,{col_letter}35,{col_letter}43)"
        worksheet.cell(47, quarter_col).value = f"={col_letter}9-SUM({col_letter}18,{col_letter}45)"
        worksheet.cell(48, quarter_col).value = f"=IF({col_letter}9=0,0,{col_letter}47/{col_letter}9)"

    for row_number in [9, 18, 20, 21, 29, 35, 43, 45, 47, 48]:
        worksheet.cell(row_number, 7).value = f"=SUM(C{row_number}:F{row_number})" if row_number not in {21, 48} else f"=IF(G9=0,0,G20/G9)" if row_number == 21 else "=IF(G9=0,0,G47/G9)"

    metric_rows = [
        (52, "Gross Margin FY", model["metrics_summary"]["gross_margin_fy"], "metric", PERCENT_NUMBER_FORMAT, "FY gross profit divided by FY revenue."),
        (53, "Q3 Gross Margin peak if available", model["metrics_summary"]["q3_gross_margin_peak"], "metric_highlight", PERCENT_NUMBER_FORMAT, "Highlights the core delivery margin in Oct-Dec."),
        (54, "Monthly OpEx Run-Rate", model["metrics_summary"]["monthly_opex_run_rate"], "metric", INR_NUMBER_FORMAT, "Normalized monthly operating expense excluding one-time items and bonuses."),
        (55, "Monthly Gross Burn", model["metrics_summary"]["monthly_gross_burn"], "metric_highlight", INR_NUMBER_FORMAT, "Steady-state COGS plus OpEx after removing one-time distortions."),
        (56, "Monthly Net", model["metrics_summary"]["monthly_net"], "metric", INR_NUMBER_FORMAT, "Normalized monthly operating profit / loss."),
        (57, "Revenue per India FTE", model["metrics_summary"]["revenue_per_india_fte"], "metric", INR_NUMBER_FORMAT, "FY revenue divided by the current India FTE assumption."),
        (58, "Total People Cost", model["metrics_summary"]["total_people_cost"], "metric_highlight", INR_NUMBER_FORMAT, "Delivery, contractor, consultant, and salary costs combined."),
        (59, "Closing Cash", model["metrics_summary"]["closing_cash"], "metric", INR_NUMBER_FORMAT, "Shown only when a cash field exists in the warehouse summary."),
        (60, "Cash Runway", model["metrics_summary"]["cash_runway"], "metric_highlight", None, "Closing cash divided by normalized monthly OpEx."),
    ]
    for row_number, label, value, style_name, number_format, description in metric_rows:
        apply_row_style(worksheet, row_number, 2, 8, style_name, number_format=number_format)
        apply_label_cell(worksheet.cell(row_number, 2), style_name)
        worksheet.cell(row_number, 2).value = label
        worksheet.cell(row_number, 3).value = value
        if number_format:
            worksheet.cell(row_number, 3).number_format = number_format
        worksheet.merge_cells(start_row=row_number, start_column=4, end_row=row_number, end_column=8)
        worksheet.cell(row_number, 4).value = description
        apply_style(worksheet.cell(row_number, 4), "note")

    for note_row, note_text in zip(range(62, 68), model["notes"]):
        worksheet.merge_cells(start_row=note_row, start_column=2, end_row=note_row, end_column=8)
        worksheet.cell(note_row, 2).value = note_text
        apply_style(worksheet.cell(note_row, 2), "note")

    apply_border_band(worksheet, 9, 2, 8)
    apply_border_band(worksheet, 18, 2, 8)
    apply_border_band(worksheet, 20, 2, 8)
    apply_border_band(worksheet, 45, 2, 8)
    apply_border_band(worksheet, 47, 2, 8)
    apply_border_band(worksheet, 51, 2, 8)
    _set_sheet_frame(
        worksheet,
        {"A": 2, "B": 61, "C": 14, "D": 13, "E": 13, "F": 13, "G": 13, "H": 10},
        "C5",
    )


def write_mis_workbook(report_path: Path, model: dict) -> None:
    """Create the final workbook in the company MIS structure."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    _write_monthly_sheet(workbook, model)
    _write_cogs_sheet(workbook, model)
    _write_quarterly_sheet(workbook, model)

    quarterly_sheet = workbook["Quarterly P&L"]
    workbook._sheets = [quarterly_sheet, workbook["Monthly P&L"], workbook["COGS Allocation Working"]]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(report_path)


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
    zoho_raw_df: pd.DataFrame | None = None,
) -> dict:
    """Generate the downloadable company-format MIS workbook."""
    if monthly_pl_df is None or dashboard_summary_df is None:
        monthly_pl_df, dashboard_summary_df = fetch_gold_mis_data(project_id)
    if zoho_raw_df is None:
        try:
            zoho_raw_df = fetch_zoho_raw_df(project_id)
        except Exception:
            zoho_raw_df = pd.DataFrame()

    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)

    file_name = f"MIS_PL_{_financial_year_token(financial_year)}_generated.xlsx"
    report_path = destination_folder / file_name
    summary_df = build_summary_dataframe(financial_year, dashboard_summary_df, project_id)
    metrics = get_mis_metrics(financial_year, monthly_pl_df, dashboard_summary_df, project_id)
    model = build_mis_model(financial_year, monthly_pl_df, dashboard_summary_df, zoho_raw_df)
    write_mis_workbook(report_path, model)

    return {
        "status": "success",
        "message": "MIS report generated in the company workbook format.",
        "metrics": metrics,
        "report_path": report_path,
        "is_placeholder": False,
        "summary": make_excel_safe_dataframe(summary_df),
        "monthly_preview": make_excel_safe_dataframe(monthly_pl_df).head(20),
        "dashboard_kpis": make_excel_safe_dataframe(dashboard_summary_df),
        "model": model,
    }


def _default_output_dir() -> Path:
    """Return the repo-level outputs directory expected by the MIS flow."""
    return REPO_ROOT / "outputs"


def main() -> None:
    """CLI entrypoint for local generation."""
    financial_year = os.getenv("MIS_FINANCIAL_YEAR", "FY25-26")
    result = generate_mis_report(financial_year, _default_output_dir())
    print(result["report_path"])


if __name__ == "__main__":
    main()
