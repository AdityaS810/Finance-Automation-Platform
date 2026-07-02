"""Generate the company MIS workbook from BigQuery and Zoho detail data."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


DEFAULT_PROJECT_ID = "internal-project-work-497507"
MIS_MONTHLY_PL_VIEW = "finance_gold.mis_monthly_pl"
DASHBOARD_SUMMARY_VIEW = "finance_gold.dashboard_summary"
BRONZE_RAW_VIEW = "finance_bronze.zoho_raw"
FX_RATE_DEFAULT = 90.0

MONTHLY_TEMPLATE_SHEET = "Monthly P&L"
QUARTERLY_TEMPLATE_SHEET = "Quarterly P&L"
COGS_TEMPLATE_SHEET = "COGS Allocation Working"
TEMPLATE_FILE_NAME = "MidofficeData_KeyMetrics_PL_FY2526.xlsx"


load_dotenv()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _template_path() -> Path:
    return _repo_root() / "templates" / TEMPLATE_FILE_NAME


def _mis_mapping_path() -> Path:
    return _repo_root() / "config" / "mis_mapping.yaml"


def _cogs_config_path() -> Path:
    return _repo_root() / "config" / "cogs_allocation.yaml"


def _get_project_id(project_id: str | None = None) -> str:
    """Use an explicit project id, then environment, then the known project."""
    return project_id or os.getenv("GCP_PROJECT_ID") or DEFAULT_PROJECT_ID


def _table_name(project_id: str, view_name: str) -> str:
    """Build a fully qualified BigQuery table or view name."""
    return f"`{project_id}.{view_name}`"


def _normalise_org_filter(org_filter: str | None = None) -> str:
    """Map UI labels and missing values to safe Gold/Silver org keys."""
    if not org_filter:
        return "all"
    normalised = str(org_filter).strip().lower()
    aliases = {
        "all organizations": "all",
        "all": "all",
        "india": "india",
        "in": "india",
        "us": "us",
        "u.s.": "us",
        "usa": "us",
        "u.s.a": "us",
    }
    return aliases.get(normalised, "all")


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


def _format_inr_short(value: float | int | None) -> str:
    """Return a human-readable rupee string for note blocks."""
    amount = float(value or 0)
    if abs(amount) >= 10_000_000:
        return f"₹{amount / 10_000_000:.2f} Cr"
    if abs(amount) >= 100_000:
        return f"₹{amount / 100_000:.1f}L"
    return f"₹{amount:,.0f}"


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


def _query_to_dataframe(client, query: str) -> pd.DataFrame:
    """Run a BigQuery query and convert rows to pandas without extra packages."""
    result = client.query(query).result()
    columns = [field.name for field in result.schema]
    rows = [dict(row.items()) for row in result]
    return pd.DataFrame(rows, columns=columns)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def _parse_financial_year(financial_year: str) -> tuple[int, int]:
    cleaned = financial_year.upper().replace(" ", "")
    if not cleaned.startswith("FY") or "-" not in cleaned:
        raise ValueError(f"Unsupported financial year format: {financial_year}")

    start_token, end_token = cleaned[2:].split("-", maxsplit=1)
    start_year = 2000 + int(start_token)
    end_year = 2000 + int(end_token)
    return start_year, end_year


def _financial_year_months(financial_year: str, configured_months: list[str] | None = None) -> list[str]:
    if configured_months:
        start_year, _ = _parse_financial_year(financial_year)
        if configured_months[0].startswith(str(start_year)):
            return configured_months

    start_year, end_year = _parse_financial_year(financial_year)
    months = [f"{start_year}-{month:02d}" for month in range(4, 13)]
    months.extend(f"{end_year}-{month:02d}" for month in range(1, 4))
    return months


def _month_labels(months: list[str]) -> dict[str, str]:
    labels = {}
    for month_key in months:
        month_date = pd.Timestamp(f"{month_key}-01")
        labels[month_key] = month_date.strftime("%b")
    return labels


def _quarter_months(months: list[str]) -> dict[str, list[str]]:
    return {
        "Q1": months[0:3],
        "Q2": months[3:6],
        "Q3": months[6:9],
        "Q4": months[9:12],
    }


def _month_column_map(start_column_index: int, months: list[str]) -> dict[str, int]:
    return {month_key: start_column_index + index for index, month_key in enumerate(months)}


def _zero_month_map(months: list[str]) -> dict[str, float]:
    return {month_key: 0.0 for month_key in months}


def _round_currency(value: float) -> float:
    return round(float(value or 0), 2)


def _normalize_month(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m")


def _to_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_raw_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _get_line_items(raw_payload: dict[str, Any]) -> list[dict[str, Any]]:
    line_items = raw_payload.get("line_items")
    if isinstance(line_items, list):
        return [item for item in line_items if isinstance(item, dict)]
    return []


def _extract_amount_from_line_item(line_item: dict[str, Any]) -> float:
    for key in ("item_total", "total", "line_total", "amount", "net_amount", "total_amount"):
        amount = _to_float(line_item.get(key), default=0.0)
        if amount:
            return amount

    rate = _to_float(line_item.get("rate"), default=0.0)
    quantity = _to_float(line_item.get("quantity"), default=1.0)
    if rate:
        return rate * quantity

    return 0.0


def _convert_to_inr(amount: float, currency_code: str | None, exchange_rate: Any, fx_rate_default: float) -> float:
    normalized_amount = _to_float(amount, default=0.0)
    if not normalized_amount:
        return 0.0

    currency = _to_string(currency_code).strip().upper()
    rate = _to_float(exchange_rate, default=0.0)
    if currency in {"", "INR"}:
        return normalized_amount
    if currency in {"USD", "US$"}:
        return normalized_amount * (rate or fx_rate_default)
    if rate and not currency:
        return normalized_amount * rate
    return normalized_amount


def fetch_gold_mis_data(project_id: str | None = None, org_filter: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Query the required Gold MIS views from BigQuery."""
    from google.cloud import bigquery

    resolved_project_id = _get_project_id(project_id)
    selected_org_key = _normalise_org_filter(org_filter)
    client = bigquery.Client(project=resolved_project_id)

    monthly_query = f"""
        SELECT *
        FROM {_table_name(resolved_project_id, MIS_MONTHLY_PL_VIEW)}
        WHERE source_org_key = '{selected_org_key}'
        ORDER BY report_month
    """
    dashboard_query = f"""
        SELECT *
        FROM {_table_name(resolved_project_id, DASHBOARD_SUMMARY_VIEW)}
        WHERE source_org_key = '{selected_org_key}'
    """

    monthly_pl_df = _query_to_dataframe(client, monthly_query)
    dashboard_summary_df = _query_to_dataframe(client, dashboard_query)
    return monthly_pl_df, dashboard_summary_df


def fetch_detailed_mis_data(
    project_id: str | None = None,
    org_filter: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch latest invoice, bill, and journal raw payloads for MIS classification."""
    from google.cloud import bigquery

    resolved_project_id = _get_project_id(project_id)
    selected_org_key = _normalise_org_filter(org_filter)
    client = bigquery.Client(project=resolved_project_id)
    org_filter_sql = "" if selected_org_key == "all" else f"AND source_org_key = '{selected_org_key}'"

    invoice_query = f"""
        WITH latest AS (
            SELECT
                run_id,
                source_record_id,
                loaded_at,
                raw_json
            FROM {_table_name(resolved_project_id, BRONZE_RAW_VIEW)}
            WHERE entity_name = 'invoices'
                {org_filter_sql}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY source_record_id ORDER BY loaded_at DESC) = 1
        )
        SELECT
            run_id,
            source_record_id AS invoice_id,
            JSON_VALUE(raw_json, '$.invoice_number') AS invoice_number,
            JSON_VALUE(raw_json, '$.customer_name') AS customer_name,
            SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS invoice_date,
            JSON_VALUE(raw_json, '$.currency_code') AS currency_code,
            SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
            SAFE_CAST(JSON_VALUE(raw_json, '$.total') AS NUMERIC) AS total_amount,
            JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
            JSON_VALUE(raw_json, '$.notes') AS notes,
            raw_json,
            loaded_at
        FROM latest
    """
    bill_query = f"""
        WITH latest AS (
            SELECT
                run_id,
                source_record_id,
                loaded_at,
                raw_json
            FROM {_table_name(resolved_project_id, BRONZE_RAW_VIEW)}
            WHERE entity_name = 'bills'
                {org_filter_sql}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY source_record_id ORDER BY loaded_at DESC) = 1
        )
        SELECT
            run_id,
            source_record_id AS bill_id,
            JSON_VALUE(raw_json, '$.bill_number') AS bill_number,
            JSON_VALUE(raw_json, '$.vendor_name') AS vendor_name,
            SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS bill_date,
            JSON_VALUE(raw_json, '$.currency_code') AS currency_code,
            SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
            SAFE_CAST(JSON_VALUE(raw_json, '$.total') AS NUMERIC) AS total_amount,
            JSON_VALUE(raw_json, '$.notes') AS notes,
            raw_json,
            loaded_at
        FROM latest
    """
    journal_query = f"""
        WITH latest AS (
            SELECT
                run_id,
                source_record_id,
                loaded_at,
                raw_json
            FROM {_table_name(resolved_project_id, BRONZE_RAW_VIEW)}
            WHERE entity_name = 'journals'
                {org_filter_sql}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY source_record_id ORDER BY loaded_at DESC) = 1
        )
        SELECT
            run_id,
            source_record_id AS journal_id,
            JSON_VALUE(raw_json, '$.journal_number') AS journal_number,
            SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS journal_date,
            JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
            JSON_VALUE(raw_json, '$.notes') AS notes,
            JSON_VALUE(raw_json, '$.currency_code') AS currency_code,
            SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
            COALESCE(
                SAFE_CAST(JSON_VALUE(raw_json, '$.total') AS NUMERIC),
                SAFE_CAST(JSON_VALUE(raw_json, '$.amount') AS NUMERIC)
            ) AS total_amount,
            raw_json,
            loaded_at
        FROM latest
    """

    return (
        _query_to_dataframe(client, invoice_query),
        _query_to_dataframe(client, bill_query),
        _query_to_dataframe(client, journal_query),
    )


def _candidate_text_map(row: pd.Series, raw_payload: dict[str, Any], line_item: dict[str, Any] | None = None) -> dict[str, str]:
    line_item = line_item or {}
    return {
        "counterparty_name": " ".join(
            filter(
                None,
                [
                    _to_string(row.get("customer_name")),
                    _to_string(row.get("vendor_name")),
                    _to_string(raw_payload.get("customer_name")),
                    _to_string(raw_payload.get("vendor_name")),
                    _to_string(raw_payload.get("contact_name")),
                ],
            )
        ),
        "description": " ".join(
            filter(
                None,
                [
                    _to_string(line_item.get("description")),
                    _to_string(raw_payload.get("description")),
                ],
            )
        ),
        "item_name": " ".join(
            filter(
                None,
                [
                    _to_string(line_item.get("item_name")),
                    _to_string(line_item.get("name")),
                    _to_string(line_item.get("product_name")),
                    _to_string(line_item.get("line_item_name")),
                ],
            )
        ),
        "notes": " ".join(
            filter(
                None,
                [
                    _to_string(row.get("notes")),
                    _to_string(raw_payload.get("notes")),
                    _to_string(raw_payload.get("memo")),
                ],
            )
        ),
        "reference_number": " ".join(
            filter(
                None,
                [
                    _to_string(row.get("reference_number")),
                    _to_string(row.get("invoice_number")),
                    _to_string(row.get("bill_number")),
                    _to_string(row.get("journal_number")),
                    _to_string(raw_payload.get("reference_number")),
                ],
            )
        ),
        "account_name": " ".join(
            filter(
                None,
                [
                    _to_string(line_item.get("account_name")),
                    _to_string(line_item.get("account")),
                    _to_string(line_item.get("name")),
                ],
            )
        ),
    }


def _match_rule(text_map: dict[str, str], rule: dict[str, Any]) -> bool:
    match_fields = rule.get("match_fields", [])
    haystack = " ".join(text_map.get(field, "") for field in match_fields).lower()
    if not haystack:
        return False

    exclude_keywords = [keyword.lower() for keyword in rule.get("exclude_keywords", [])]
    if any(keyword in haystack for keyword in exclude_keywords):
        return False

    keywords = [keyword.lower() for keyword in rule.get("keywords", [])]
    return any(keyword in haystack for keyword in keywords)


def _classify_candidate(text_map: dict[str, str], source_type: str, rules: dict[str, Any]) -> str | None:
    for rule_key, rule in rules.items():
        if source_type not in rule.get("source_types", []):
            continue
        if _match_rule(text_map, rule):
            return rule_key
    return None


def _iter_detail_candidates(
    dataframe: pd.DataFrame,
    source_type: str,
    date_column: str,
    amount_column: str,
    valid_months: set[str],
    fx_rate_default: float,
):
    if dataframe.empty:
        return

    for _, row in dataframe.iterrows():
        raw_payload = _parse_raw_payload(row.get("raw_json"))
        month_key = _normalize_month(row.get(date_column) or raw_payload.get("date"))
        if month_key not in valid_months:
            continue

        currency_code = _to_string(row.get("currency_code") or raw_payload.get("currency_code"))
        exchange_rate = row.get("exchange_rate") if row.get("exchange_rate") is not None else raw_payload.get("exchange_rate")
        header_amount = _convert_to_inr(row.get(amount_column), currency_code, exchange_rate, fx_rate_default)

        line_items = _get_line_items(raw_payload)
        if source_type in {"bill", "journal"} and line_items:
            yielded_line = False
            for line_item in line_items:
                line_amount = _extract_amount_from_line_item(line_item)
                if not line_amount and len(line_items) == 1:
                    line_amount = _to_float(row.get(amount_column), default=0.0)
                if not line_amount:
                    continue

                yielded_line = True
                yield {
                    "month": month_key,
                    "source_type": source_type,
                    "amount_inr": _convert_to_inr(line_amount, currency_code, exchange_rate, fx_rate_default),
                    "text_map": _candidate_text_map(row, raw_payload, line_item),
                }

            if yielded_line:
                continue

        yield {
            "month": month_key,
            "source_type": source_type,
            "amount_inr": header_amount,
            "text_map": _candidate_text_map(row, raw_payload, None),
        }


def _aggregate_rule_totals(
    months: list[str],
    fx_rate_default: float,
    invoices_df: pd.DataFrame,
    bills_df: pd.DataFrame,
    journals_df: pd.DataFrame,
    rules: dict[str, Any],
) -> dict[str, dict[str, float]]:
    totals = {rule_key: _zero_month_map(months) for rule_key in rules}
    valid_months = set(months)

    for candidate in _iter_detail_candidates(invoices_df, "invoice", "invoice_date", "total_amount", valid_months, fx_rate_default) or []:
        category = _classify_candidate(candidate["text_map"], candidate["source_type"], rules)
        if category:
            totals[category][candidate["month"]] += candidate["amount_inr"]

    for candidate in _iter_detail_candidates(bills_df, "bill", "bill_date", "total_amount", valid_months, fx_rate_default) or []:
        category = _classify_candidate(candidate["text_map"], candidate["source_type"], rules)
        if category:
            totals[category][candidate["month"]] += candidate["amount_inr"]

    for candidate in _iter_detail_candidates(journals_df, "journal", "journal_date", "total_amount", valid_months, fx_rate_default) or []:
        category = _classify_candidate(candidate["text_map"], candidate["source_type"], rules)
        if category:
            totals[category][candidate["month"]] += candidate["amount_inr"]

    for rule_months in totals.values():
        for month_key, value in list(rule_months.items()):
            rule_months[month_key] = _round_currency(value)

    return totals


def _aggregate_cogs_detail(
    months: list[str],
    fx_rate_default: float,
    bills_df: pd.DataFrame,
    journals_df: pd.DataFrame,
    company_rules: dict[str, Any],
) -> dict[str, dict[str, float]]:
    cogs_rows = {
        "parashar": _zero_month_map(months),
        "sangeeth": _zero_month_map(months),
        "jitin": _zero_month_map(months),
        "dipti": _zero_month_map(months),
        "dhanashri": _zero_month_map(months),
        "pinkesh": _zero_month_map(months),
        "ramki": _zero_month_map(months),
        "dorothea": _zero_month_map(months),
        "bsm_crew": _zero_month_map(months),
        "conam_tech": _zero_month_map(months),
        "external_vendors": _zero_month_map(months),
        "technology_costs": _zero_month_map(months),
    }

    row_rules = {
        "parashar": {"source_types": ["bill", "journal"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["parashar"]},
        "sangeeth": {"source_types": ["bill", "journal"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["sangeeth"]},
        "jitin": {"source_types": ["bill", "journal"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["jitin"]},
        "dipti": {"source_types": ["bill", "journal"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["dipti"]},
        "dhanashri": {"source_types": ["bill", "journal"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["dhanashri"]},
        "pinkesh": {"source_types": ["bill", "journal"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["pinkesh"]},
        "dorothea": {"source_types": ["bill", "journal"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["dorothea"]},
        "bsm_crew": {"source_types": ["bill", "journal"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["kashish", "priyanka", "nitin chandani"]},
        "conam_tech": {"source_types": ["bill"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["conam"]},
        "external_vendors": {"source_types": ["bill"], "match_fields": ["counterparty_name", "description", "item_name", "notes", "account_name"], "keywords": ["biltzentech", "altysys", "external delivery"]},
    }

    valid_months = set(months)
    for candidate in _iter_detail_candidates(bills_df, "bill", "bill_date", "total_amount", valid_months, fx_rate_default) or []:
        row_key = _classify_candidate(candidate["text_map"], candidate["source_type"], row_rules)
        if row_key:
            cogs_rows[row_key][candidate["month"]] += candidate["amount_inr"]

    for candidate in _iter_detail_candidates(journals_df, "journal", "journal_date", "total_amount", valid_months, fx_rate_default) or []:
        row_key = _classify_candidate(candidate["text_map"], candidate["source_type"], row_rules)
        if row_key:
            cogs_rows[row_key][candidate["month"]] += candidate["amount_inr"]

    ramki_monthly_inr = _to_float(company_rules.get("ramki_monthly_usd"), default=12_500) * fx_rate_default
    tech_cost_inr = _to_float(company_rules.get("monthly_technology_cost_inr"), default=25_800)
    techm_start = _to_string(company_rules.get("techm_cogs_start")) or "2025-09"
    dorothea_start = _to_string(company_rules.get("dorothea_cogs_start")) or "2025-10"

    for month_key in months:
        if month_key >= techm_start:
            cogs_rows["ramki"][month_key] = _round_currency(ramki_monthly_inr * 0.5)
        if month_key >= dorothea_start:
            cogs_rows["dorothea"][month_key] = _round_currency(cogs_rows["dorothea"][month_key])
        cogs_rows["technology_costs"][month_key] = _round_currency(tech_cost_inr)

    for row_months in cogs_rows.values():
        for month_key, value in list(row_months.items()):
            row_months[month_key] = _round_currency(value)

    return cogs_rows


def _build_monthly_line_items(
    months: list[str],
    mapped_totals: dict[str, dict[str, float]],
    cogs_rows: dict[str, dict[str, float]],
    company_rules: dict[str, Any],
) -> dict[str, dict[str, float]]:
    monthly_items = {
        "techm_billings": mapped_totals.get("techm_billings", _zero_month_map(months)),
        "bsm_revenue": mapped_totals.get("bsm_revenue", _zero_month_map(months)),
        "fd_interest": mapped_totals.get("fd_interest", _zero_month_map(months)),
        "ramki_sm": _zero_month_map(months),
        "advertising_marketing": mapped_totals.get("advertising_marketing", _zero_month_map(months)),
        "travel_expenses": mapped_totals.get("travel_expenses", _zero_month_map(months)),
        "meals_entertainment": mapped_totals.get("meals_entertainment", _zero_month_map(months)),
        "rd_salaries": mapped_totals.get("rd_salaries", _zero_month_map(months)),
        "consultant_expense": mapped_totals.get("consultant_expense", _zero_month_map(months)),
        "software_subscriptions": mapped_totals.get("software_subscriptions", _zero_month_map(months)),
        "rent": mapped_totals.get("rent", _zero_month_map(months)),
        "it_internet": mapped_totals.get("it_internet", _zero_month_map(months)),
        "legal": mapped_totals.get("legal", _zero_month_map(months)),
        "audit_non_operating": mapped_totals.get("audit_non_operating", _zero_month_map(months)),
        "other_ga": _zero_month_map(months),
        "cogs_total": _zero_month_map(months),
    }

    ramki_monthly_inr = _to_float(company_rules.get("ramki_monthly_usd"), default=12_500) * _to_float(company_rules.get("fx_rate_inr_per_usd"), default=FX_RATE_DEFAULT)
    techm_start = _to_string(company_rules.get("techm_cogs_start")) or "2025-09"

    for month_key in months:
        monthly_items["ramki_sm"][month_key] = _round_currency(ramki_monthly_inr if month_key < techm_start else ramki_monthly_inr * 0.5)
        monthly_items["other_ga"][month_key] = _round_currency(
            mapped_totals.get("delivery_india", _zero_month_map(months)).get(month_key, 0)
            + mapped_totals.get("bsm_delivery_contractors", _zero_month_map(months)).get(month_key, 0)
            + mapped_totals.get("external_delivery_vendors", _zero_month_map(months)).get(month_key, 0)
            - sum(
                cogs_rows[row_key].get(month_key, 0)
                for row_key in ("parashar", "sangeeth", "jitin", "dipti", "dhanashri", "pinkesh", "bsm_crew", "conam_tech", "external_vendors")
            )
        )
        monthly_items["cogs_total"][month_key] = _round_currency(
            sum(
                cogs_rows[row_key].get(month_key, 0)
                for row_key in ("parashar", "sangeeth", "jitin", "dipti", "dhanashri", "pinkesh", "ramki", "dorothea", "bsm_crew", "conam_tech", "external_vendors")
            )
            + cogs_rows["technology_costs"].get(month_key, 0)
        )

    for month_key in months:
        if monthly_items["other_ga"][month_key] < 0:
            monthly_items["other_ga"][month_key] = 0.0

    return monthly_items


def _build_monthly_preview_dataframe(months: list[str], month_labels: dict[str, str], monthly_items: dict[str, dict[str, float]]) -> pd.DataFrame:
    line_item_order = [
        ("Tech Mahindra", "techm_billings"),
        ("BSM Revenue", "bsm_revenue"),
        ("FD Interest", "fd_interest"),
        ("COGS Total", "cogs_total"),
        ("Ramki S&M", "ramki_sm"),
        ("Advertising", "advertising_marketing"),
        ("Travel", "travel_expenses"),
        ("Meals", "meals_entertainment"),
        ("R&D Salaries", "rd_salaries"),
        ("Consultant Exp.", "consultant_expense"),
        ("Software Subs", "software_subscriptions"),
        ("Rent", "rent"),
        ("IT & Internet", "it_internet"),
        ("Legal", "legal"),
        ("Audit & Non-Op", "audit_non_operating"),
        ("Other G&A", "other_ga"),
    ]

    preview_rows = []
    for label, item_key in line_item_order:
        row = {"Line Item": label}
        for month_key in months:
            row[month_labels[month_key]] = _round_currency(monthly_items[item_key][month_key])
        preview_rows.append(row)

    return pd.DataFrame(preview_rows)


def _quarter_formula_from_monthly(month_columns: dict[str, int], target_months: list[str], row_number: int) -> str:
    start_letter = get_column_letter(month_columns[target_months[0]])
    end_letter = get_column_letter(month_columns[target_months[-1]])
    return f"=SUM('{MONTHLY_TEMPLATE_SHEET}'!{start_letter}{row_number}:{end_letter}{row_number})"


def _quarter_formula_from_cogs(cogs_columns: dict[str, int], target_months: list[str], start_row: int, end_row: int | None = None) -> str:
    end_row = end_row or start_row
    start_letter = get_column_letter(cogs_columns[target_months[0]])
    end_letter = get_column_letter(cogs_columns[target_months[-1]])
    return f"=SUM('{COGS_TEMPLATE_SHEET}'!{start_letter}{start_row}:{end_letter}{end_row})"


def _set_template_titles(
    quarterly_ws,
    monthly_ws,
    cogs_ws,
    financial_year: str,
    months: list[str],
    fx_rate: float,
    company_rules: dict[str, Any],
) -> None:
    start_year, end_year = _parse_financial_year(financial_year)
    title_year = f"FY {start_year}-{str(end_year)[-2:]}"
    quarterly_ws["B1"] = f"MIDOFFICE DATA  |  Profit & Loss Statement  |  {title_year}  (Apr {start_year} – Mar {end_year})"
    quarterly_ws["B2"] = (
        "India Parent (Midoffice Data Solutions Pvt Ltd) + US Subsidiary (Midoffice Data International Inc.)"
        f"  |  Accrual Basis  |  FX: ₹{int(fx_rate)}/USD  |  COGS: {_to_string(company_rules.get('techm_cogs_start') or '2025-09')} onwards  |  All amounts in INR (₹)"
    )

    month_dates = [pd.Timestamp(f"{month_key}-01") for month_key in months]
    monthly_ws["B1"] = (
        f"Monthly P&L Detail  |  {title_year}  |  "
        f"{month_dates[0].strftime('%b %Y')} – {month_dates[-1].strftime('%b %Y')}  |  Automated from Zoho + BigQuery"
    )
    cogs_ws["B1"] = (
        "COGS Allocation Detail  |  Delivery Staff + Vendors  |  "
        f"TechM COGS from {_to_string(company_rules.get('techm_cogs_start') or '2025-09')}  |  "
        f"BSM delivery from {_to_string(company_rules.get('bsm_delivery_cogs_start') or '2025-07')}"
    )

    quarter_month_map = _quarter_months(months)
    for column_offset, (quarter_label, quarter_month_keys) in enumerate(quarter_month_map.items(), start=3):
        start_month = pd.Timestamp(f"{quarter_month_keys[0]}-01")
        end_month = pd.Timestamp(f"{quarter_month_keys[-1]}-01")
        quarterly_ws.cell(4, column_offset).value = f"{quarter_label}\n({start_month.strftime('%b')}–{end_month.strftime('%b %y')})"

    quarterly_ws["G4"] = f"FY\n{start_year}-{str(end_year)[-2:]}"
    quarterly_ws["H4"] = "% Rev"

    labels = _month_labels(months)
    month_start_columns = _month_column_map(3, months)
    for month_key, column_number in month_start_columns.items():
        monthly_ws.cell(3, column_number).value = labels[month_key]

    cogs_month_columns = _month_column_map(5, months)
    for month_key, column_number in cogs_month_columns.items():
        cogs_ws.cell(3, column_number).value = labels[month_key]


def _populate_monthly_sheet(monthly_ws, months: list[str], monthly_items: dict[str, dict[str, float]], cogs_rows: dict[str, dict[str, float]]) -> None:
    month_columns = _month_column_map(3, months)
    row_map = {
        "techm_billings": 5,
        "bsm_revenue": 6,
        "fd_interest": 7,
        "ramki_sm": 13,
        "advertising_marketing": 14,
        "travel_expenses": 15,
        "meals_entertainment": 16,
        "rd_salaries": 19,
        "consultant_expense": 20,
        "software_subscriptions": 21,
        "rent": 24,
        "it_internet": 25,
        "legal": 26,
        "audit_non_operating": 27,
        "other_ga": 28,
    }

    for item_key, row_number in row_map.items():
        for month_key, column_number in month_columns.items():
            monthly_ws.cell(row=row_number, column=column_number).value = _round_currency(monthly_items[item_key][month_key])

    tech_cost_per_month = cogs_rows["technology_costs"]
    cogs_month_columns = _month_column_map(5, months)
    for month_key, column_number in month_columns.items():
        cogs_total_column = get_column_letter(cogs_month_columns[month_key])
        current_letter = get_column_letter(column_number)
        monthly_ws[f"{current_letter}8"] = f"=SUM({current_letter}5:{current_letter}7)"
        monthly_ws[f"{current_letter}10"] = f"='{COGS_TEMPLATE_SHEET}'!{cogs_total_column}15+{tech_cost_per_month[month_key]}"
        monthly_ws[f"{current_letter}11"] = f"={current_letter}8-{current_letter}10"
        monthly_ws[f"{current_letter}17"] = f"=SUM({current_letter}13:{current_letter}16)"
        monthly_ws[f"{current_letter}22"] = f"=SUM({current_letter}19:{current_letter}21)"
        monthly_ws[f"{current_letter}29"] = f"=SUM({current_letter}24:{current_letter}28)"
        monthly_ws[f"{current_letter}30"] = f"=SUM({current_letter}17,{current_letter}22,{current_letter}29)"
        monthly_ws[f"{current_letter}31"] = f"={current_letter}11-{current_letter}30"


def _populate_cogs_sheet(cogs_ws, months: list[str], cogs_rows: dict[str, dict[str, float]]) -> None:
    month_columns = _month_column_map(5, months)
    row_map = {
        "parashar": 4,
        "sangeeth": 5,
        "jitin": 6,
        "dipti": 7,
        "dhanashri": 8,
        "pinkesh": 9,
        "ramki": 10,
        "dorothea": 11,
        "bsm_crew": 12,
        "conam_tech": 13,
        "external_vendors": 14,
    }

    for row_key, row_number in row_map.items():
        for month_key, column_number in month_columns.items():
            cogs_ws.cell(row=row_number, column=column_number).value = _round_currency(cogs_rows[row_key][month_key])
        first_letter = get_column_letter(month_columns[months[0]])
        last_letter = get_column_letter(month_columns[months[-1]])
        cogs_ws[f"Q{row_number}"] = f"=SUM({first_letter}{row_number}:{last_letter}{row_number})"

    cogs_ws["D15"] = None
    for month_key, column_number in month_columns.items():
        current_letter = get_column_letter(column_number)
        cogs_ws[f"{current_letter}15"] = f"=SUM({current_letter}4:{current_letter}14)"
    cogs_ws["Q15"] = "=SUM(E15:P15)"


def _populate_quarterly_sheet(
    quarterly_ws,
    months: list[str],
    company_rules: dict[str, Any],
) -> None:
    month_columns = _month_column_map(3, months)
    cogs_columns = _month_column_map(5, months)
    quarter_groups = list(_quarter_months(months).items())

    monthly_row_map = {
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
    cogs_formula_rows = {
        12: (4, 9),
        13: (12, 12),
        14: (10, 10),
        15: (11, 11),
        16: (13, 14),
    }
    percent_rows = [6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18, 20, 25, 26, 27, 28, 29, 32, 33, 34, 35, 38, 39, 40, 41, 42, 43, 45, 47]
    tech_cost_per_month = _to_float(company_rules.get("monthly_technology_cost_inr"), default=25_800)

    for quarter_index, (_, quarter_month_keys) in enumerate(quarter_groups, start=3):
        quarter_letter = get_column_letter(quarter_index)
        for quarterly_row, monthly_row in monthly_row_map.items():
            quarterly_ws[f"{quarter_letter}{quarterly_row}"] = _quarter_formula_from_monthly(month_columns, quarter_month_keys, monthly_row)

        for quarterly_row, (cogs_row_start, cogs_row_end) in cogs_formula_rows.items():
            quarterly_ws[f"{quarter_letter}{quarterly_row}"] = _quarter_formula_from_cogs(cogs_columns, quarter_month_keys, cogs_row_start, cogs_row_end)

        quarterly_ws[f"{quarter_letter}17"] = f"={tech_cost_per_month}*{len(quarter_month_keys)}"
        quarterly_ws[f"{quarter_letter}9"] = f"=SUM({quarter_letter}6:{quarter_letter}8)"
        quarterly_ws[f"{quarter_letter}18"] = f"=SUM({quarter_letter}12:{quarter_letter}17)"
        quarterly_ws[f"{quarter_letter}20"] = f"={quarter_letter}9-{quarter_letter}18"
        quarterly_ws[f"{quarter_letter}21"] = f"=IFERROR({quarter_letter}20/{quarter_letter}9,0)"
        quarterly_ws[f"{quarter_letter}29"] = f"=SUM({quarter_letter}25:{quarter_letter}28)"
        quarterly_ws[f"{quarter_letter}35"] = f"=SUM({quarter_letter}32:{quarter_letter}34)"
        quarterly_ws[f"{quarter_letter}43"] = f"=SUM({quarter_letter}38:{quarter_letter}42)"
        quarterly_ws[f"{quarter_letter}45"] = f"=SUM({quarter_letter}29,{quarter_letter}35,{quarter_letter}43)"
        quarterly_ws[f"{quarter_letter}47"] = f"={quarter_letter}20-{quarter_letter}45"
        quarterly_ws[f"{quarter_letter}48"] = f"=IFERROR({quarter_letter}47/{quarter_letter}9,0)"

    for row_number in [6, 7, 8, 12, 13, 14, 15, 16, 17, 25, 26, 27, 28, 32, 33, 34, 38, 39, 40, 41, 42]:
        quarterly_ws[f"G{row_number}"] = f"=SUM(C{row_number}:F{row_number})"

    quarterly_ws["G9"] = "=SUM(G6:G8)"
    quarterly_ws["G18"] = "=SUM(G12:G17)"
    quarterly_ws["G20"] = "=G9-G18"
    quarterly_ws["G21"] = "=IFERROR(AVERAGE(C21:F21),0)"
    quarterly_ws["G29"] = "=SUM(G25:G28)"
    quarterly_ws["G35"] = "=SUM(G32:G34)"
    quarterly_ws["G43"] = "=SUM(G38:G42)"
    quarterly_ws["G45"] = "=SUM(G29,G35,G43)"
    quarterly_ws["G47"] = "=SUM(C47:F47)"
    quarterly_ws["G48"] = "=IFERROR(G47/G9,0)"

    for row_number in percent_rows:
        quarterly_ws[f"H{row_number}"] = f"=IFERROR(G{row_number}/$G$9,0)"
    quarterly_ws["H21"] = None
    quarterly_ws["H48"] = None


def _populate_key_metrics_and_notes(
    quarterly_ws,
    monthly_ws,
    months: list[str],
    dashboard_summary_df: pd.DataFrame,
    note_totals: dict[str, dict[str, float]],
    cogs_rows: dict[str, dict[str, float]],
    company_rules: dict[str, Any],
) -> None:
    quarterly_ws["B53"] = "Peak Quarter Gross Margin"
    quarterly_ws["B55"] = "Monthly Gross Burn (run-rate)"
    quarterly_ws["B56"] = "Monthly Net (run-rate)"
    quarterly_ws["B57"] = "Revenue per Delivery FTE"
    quarterly_ws["B58"] = "Total People Cost (FY)"

    preferred_months = company_rules.get("run_rate_preferred_months") or months[6:9]
    valid_run_rate_months = [month_key for month_key in preferred_months if month_key in months] or months[6:9]
    monthly_columns = _month_column_map(3, months)
    run_rate_start = get_column_letter(monthly_columns[valid_run_rate_months[0]])
    run_rate_end = get_column_letter(monthly_columns[valid_run_rate_months[-1]])

    quarterly_ws["C52"] = '=TEXT(G21,"0.0%")'
    quarterly_ws["D52"] = "FY gross margin based on automated revenue and COGS roll-ups."
    quarterly_ws["C53"] = '=TEXT(MAX(C21:F21),"0.0%")'
    quarterly_ws["D53"] = "Highest quarterly gross margin from the generated quarterly view."
    quarterly_ws["C54"] = f'=TEXT(AVERAGE(\'{MONTHLY_TEMPLATE_SHEET}\'!{run_rate_start}30:{run_rate_end}30),"₹#,##0")'
    quarterly_ws["D54"] = f"Run-rate uses {', '.join(pd.Timestamp(f'{month_key}-01').strftime('%b %Y') for month_key in valid_run_rate_months)}."
    quarterly_ws["C55"] = (
        f'=TEXT(AVERAGE(\'{MONTHLY_TEMPLATE_SHEET}\'!{run_rate_start}10:{run_rate_end}10)'
        f'+AVERAGE(\'{MONTHLY_TEMPLATE_SHEET}\'!{run_rate_start}30:{run_rate_end}30),"₹#,##0")'
    )
    quarterly_ws["D55"] = "Gross burn is average monthly COGS plus average monthly operating expense."
    quarterly_ws["C56"] = f'=TEXT(AVERAGE(\'{MONTHLY_TEMPLATE_SHEET}\'!{run_rate_start}31:{run_rate_end}31),"₹#,##0")'
    quarterly_ws["D56"] = "Monthly net run-rate excludes one-time items only through the chosen run-rate months."
    quarterly_ws["C57"] = '=TEXT(IFERROR(G9/MAX(COUNTA(\'COGS Allocation Working\'!B4:B9),1),0),"₹#,##0")'
    quarterly_ws["D57"] = "Simple revenue-per-delivery-head view using the named delivery team rows in the allocation sheet."
    quarterly_ws["C58"] = '=TEXT(SUM(G12:G15,G25,G32),"₹#,##0")'
    quarterly_ws["D58"] = "People cost view includes delivery people cost, Ramki S&M, and R&D salaries."
    quarterly_ws["C59"] = "-"
    quarterly_ws["D59"] = "Closing cash is left blank until balance-sheet or bank balance data is added to the MIS pipeline."
    quarterly_ws["C60"] = "-"
    quarterly_ws["D60"] = "Cash runway is left blank until cash balances are sourced automatically."

    bonus_total = sum(note_totals.get("bonus", {}).values())
    one_time_total = sum(note_totals.get("one_time", {}).values())
    bsm_provision_total = sum(note_totals.get("bsm_provision", {}).values())
    techm_receivable_total = sum(note_totals.get("techm_receivable", {}).values())
    invoice_count = int(_first_value(dashboard_summary_df, "invoice_count"))
    bill_count = int(_first_value(dashboard_summary_df, "bill_count"))
    journal_count = int(_first_value(dashboard_summary_df, "journal_count"))
    ramki_total = sum(cogs_rows["ramki"].values()) + sum(monthly_ws.cell(13, column_number).value or 0 for column_number in range(3, 15))

    quarterly_ws["B62"] = (
        f"⚑ Bonus review: {_format_inr_short(bonus_total)} identified against the MIS note-tracking rules. "
        "One-time bonus items remain visible in P&L and are excluded only from run-rate interpretation."
    )
    quarterly_ws["B63"] = (
        f"⚑ One-time expense review: {_format_inr_short(one_time_total)} flagged from source descriptions and notes."
    )
    quarterly_ws["B64"] = (
        f"★ BSM provision watchlist: {_format_inr_short(bsm_provision_total)} flagged through configured MIS keyword rules."
    )
    quarterly_ws["B65"] = (
        f"     TechM receivable watchlist: {_format_inr_short(techm_receivable_total)} tagged from invoice metadata for follow-up."
    )
    quarterly_ws["B66"] = (
        f"     Ramki assumption applied at $12,500/month with 100% S&M Apr-Aug and 50% COGS / 50% S&M from Sep onward. "
        f"Generated annual Ramki cost: {_format_inr_short(ramki_total)}."
    )
    quarterly_ws["B67"] = (
        f"     Workbook generated from Zoho/BigQuery detail rows. Source counts included in this run: "
        f"{invoice_count} invoices, {bill_count} bills, {journal_count} journals."
    )


def _build_metrics(
    financial_year: str,
    monthly_sheet_preview: pd.DataFrame,
    dashboard_summary_df: pd.DataFrame,
) -> dict[str, str]:
    revenue_rows = monthly_sheet_preview[monthly_sheet_preview["Line Item"].isin(["Tech Mahindra", "BSM Revenue", "FD Interest"])]
    expense_rows = monthly_sheet_preview[monthly_sheet_preview["Line Item"].isin(["COGS Total", "Ramki S&M", "Advertising", "Travel", "Meals", "R&D Salaries", "Consultant Exp.", "Software Subs", "Rent", "IT & Internet", "Legal", "Audit & Non-Op", "Other G&A"])]
    numeric_columns = [column_name for column_name in monthly_sheet_preview.columns if column_name != "Line Item"]

    revenue_total = float(revenue_rows[numeric_columns].sum().sum()) if not revenue_rows.empty else 0.0
    expense_total = float(expense_rows[numeric_columns].sum().sum()) if not expense_rows.empty else 0.0
    profit_total = revenue_total - expense_total

    return {
        "Financial Year": financial_year,
        "Revenue": _format_currency(revenue_total),
        "Expenses": _format_currency(expense_total),
        "Profit": _format_currency(profit_total),
        "Journal Adjustments": _format_currency(0),
        "Invoices": str(int(_first_value(dashboard_summary_df, "invoice_count"))),
        "Bills": str(int(_first_value(dashboard_summary_df, "bill_count"))),
        "Contacts": str(int(_first_value(dashboard_summary_df, "contact_count"))),
    }


def get_mis_metrics(
    financial_year: str,
    monthly_pl_df: pd.DataFrame | None = None,
    dashboard_summary_df: pd.DataFrame | None = None,
    project_id: str | None = None,
) -> dict[str, str]:
    """Return Streamlit metric-card values from real Gold layer data."""
    if monthly_pl_df is None or dashboard_summary_df is None:
        monthly_pl_df, dashboard_summary_df = fetch_gold_mis_data(project_id)

    revenue_amount = _safe_sum(monthly_pl_df, "revenue_amount")
    expense_amount = _safe_sum(monthly_pl_df, "expense_amount")
    journal_adjustment_amount = _safe_sum(monthly_pl_df, "journal_adjustment_amount")
    profit_amount = _safe_sum(monthly_pl_df, "profit_amount") or (revenue_amount - expense_amount + journal_adjustment_amount)

    return {
        "Financial Year": financial_year,
        "Organization": _first_value(dashboard_summary_df, "source_org_name", "All Organizations"),
        "Reporting Currency": _first_value(dashboard_summary_df, "reporting_currency", "INR"),
        "Revenue": _format_currency(revenue_amount),
        "Expenses": _format_currency(expense_amount),
        "Profit": _format_currency(profit_amount),
        "Journal Adjustments": _format_currency(journal_adjustment_amount),
        "Invoices": str(int(_first_value(dashboard_summary_df, "invoice_count"))),
        "Bills": str(int(_first_value(dashboard_summary_df, "bill_count"))),
        "Contacts": str(int(_first_value(dashboard_summary_df, "contact_count"))),
    }


def _prepare_dashboard_kpis(metrics: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame([{"KPI": key, "Value": value} for key, value in metrics.items()])


def _generate_template_workbook(
    template_path: Path,
    destination_path: Path,
    financial_year: str,
    months: list[str],
    mapped_totals: dict[str, dict[str, float]],
    cogs_rows: dict[str, dict[str, float]],
    dashboard_summary_df: pd.DataFrame,
    company_rules: dict[str, Any],
    note_totals: dict[str, dict[str, float]],
) -> None:
    workbook = load_workbook(template_path)
    quarterly_ws = workbook[QUARTERLY_TEMPLATE_SHEET]
    monthly_ws = workbook[MONTHLY_TEMPLATE_SHEET]
    cogs_ws = workbook[COGS_TEMPLATE_SHEET]

    fx_rate = _to_float(company_rules.get("fx_rate_inr_per_usd"), default=FX_RATE_DEFAULT)
    monthly_items = _build_monthly_line_items(months, mapped_totals, cogs_rows, company_rules)

    _set_template_titles(quarterly_ws, monthly_ws, cogs_ws, financial_year, months, fx_rate, company_rules)
    _populate_cogs_sheet(cogs_ws, months, cogs_rows)
    _populate_monthly_sheet(monthly_ws, months, monthly_items, cogs_rows)
    _populate_quarterly_sheet(quarterly_ws, months, company_rules)
    _populate_key_metrics_and_notes(quarterly_ws, monthly_ws, months, dashboard_summary_df, note_totals, cogs_rows, company_rules)

    quarterly_ws.freeze_panes = "C5"
    monthly_ws.freeze_panes = "C4"
    cogs_ws.freeze_panes = "D4"
    workbook._sheets = [quarterly_ws, monthly_ws, cogs_ws]
    workbook.calculation.forceFullCalc = True
    workbook.calculation.fullCalcOnLoad = True
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination_path)


def generate_mis_report(
    financial_year: str,
    output_dir: str | Path,
    project_id: str | None = None,
    org_filter: str | None = None,
    monthly_pl_df: pd.DataFrame | None = None,
    dashboard_summary_df: pd.DataFrame | None = None,
    bills_df: pd.DataFrame | None = None,
    invoices_df: pd.DataFrame | None = None,
    journals_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Generate the company MIS workbook from detailed Zoho/BigQuery data."""
    template_path = _template_path()
    if not template_path.exists():
        raise FileNotFoundError(f"MIS template workbook not found at {template_path}")

    mapping_config = _read_yaml(_mis_mapping_path()).get("mis_report", {})
    cogs_config = _read_yaml(_cogs_config_path())
    company_rules = dict(cogs_config.get("company_rules", {}))
    company_rules["fx_rate_inr_per_usd"] = _to_float(cogs_config.get("fx_rate_inr_per_usd"), default=FX_RATE_DEFAULT)
    months = _financial_year_months(financial_year, cogs_config.get("months"))

    if monthly_pl_df is None or dashboard_summary_df is None:
        try:
            monthly_pl_df, dashboard_summary_df = fetch_gold_mis_data(project_id, org_filter=org_filter)
        except Exception:
            monthly_pl_df = monthly_pl_df if monthly_pl_df is not None else pd.DataFrame()
            dashboard_summary_df = dashboard_summary_df if dashboard_summary_df is not None else pd.DataFrame()

    if invoices_df is None or bills_df is None or journals_df is None:
        fetched_invoices_df, fetched_bills_df, fetched_journals_df = fetch_detailed_mis_data(project_id, org_filter=org_filter)
        invoices_df = fetched_invoices_df if invoices_df is None else invoices_df
        bills_df = fetched_bills_df if bills_df is None else bills_df
        journals_df = fetched_journals_df if journals_df is None else journals_df

    invoices_df = invoices_df if invoices_df is not None else pd.DataFrame()
    bills_df = bills_df if bills_df is not None else pd.DataFrame()
    journals_df = journals_df if journals_df is not None else pd.DataFrame()

    fx_rate_default = company_rules["fx_rate_inr_per_usd"]
    revenue_rules = mapping_config.get("revenue", {})
    expense_rules = mapping_config.get("expense", {})
    note_rules = mapping_config.get("note_tracking", {})

    mapped_totals = _aggregate_rule_totals(months, fx_rate_default, invoices_df, bills_df, journals_df, {**revenue_rules, **expense_rules})
    note_totals = _aggregate_rule_totals(months, fx_rate_default, invoices_df, bills_df, journals_df, note_rules)
    cogs_rows = _aggregate_cogs_detail(months, fx_rate_default, bills_df, journals_df, company_rules)
    monthly_items = _build_monthly_line_items(months, mapped_totals, cogs_rows, company_rules)
    monthly_preview_df = _build_monthly_preview_dataframe(months, _month_labels(months), monthly_items)

    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)
    report_path = destination_folder / f"MIS_PL_{_financial_year_token(financial_year)}_generated.xlsx"

    _generate_template_workbook(
        template_path=template_path,
        destination_path=report_path,
        financial_year=financial_year,
        months=months,
        mapped_totals=mapped_totals,
        cogs_rows=cogs_rows,
        dashboard_summary_df=dashboard_summary_df,
        company_rules=company_rules,
        note_totals=note_totals,
    )

    metrics = _build_metrics(financial_year, monthly_preview_df, dashboard_summary_df)
    return {
        "status": "success",
        "message": "MIS report generated in the company MIS workbook format.",
        "metrics": metrics,
        "report_path": report_path,
        "is_placeholder": False,
        "summary": monthly_preview_df,
        "monthly_preview": monthly_preview_df,
        "dashboard_kpis": _prepare_dashboard_kpis(metrics),
    }


def main() -> None:
    """CLI entrypoint for local MIS workbook generation."""
    project_root = _repo_root()
    result = generate_mis_report("FY25-26", project_root / "outputs")
    print(f"Generated MIS report: {result['report_path']}")
