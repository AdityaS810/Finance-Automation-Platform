"""Manage MIS Excel row to Zoho account mappings."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


REPORT_TEMPLATE = "MidofficeData_KeyMetrics_PL_FY2526"
DEFAULT_UPDATED_BY = "streamlit_user"
CONFIG_TABLE_NAME = "finance_config.mis_account_mapping"

MAPPING_COLUMNS = [
    "Report Template",
    "Excel Section",
    "Excel Row Label",
    "Zoho Account ID",
    "Zoho Account Code",
    "Zoho Account Name",
    "Organization",
    "Sign Rule",
    "Active/Inactive",
    "Updated At",
    "Updated By",
]

ROW_KEY_TO_LABEL = {
    "techm_billings": "Client Revenue - Professional Services",
    "bsm_revenue": "Client Revenue - Product",
    "fd_interest": "Other Income",
    "delivery_india": "Other G&A",
    "bsm_delivery_contractors": "Other G&A",
    "external_delivery_vendors": "Other G&A",
    "advertising_marketing": "Advertising & Marketing",
    "travel_expenses": "Travel Expenses",
    "meals_entertainment": "Meals & Entertainment",
    "rd_salaries": "R&D Salaries",
    "consultant_expense": "Consultant & Contractor Expense",
    "software_subscriptions": "Software Subs",
    "rent": "Office Rent",
    "it_internet": "IT & Internet",
    "legal": "Legal",
    "audit_non_operating": "Audit & Non-Op",
}

SECTION_TO_SIGN_RULE = {
    "revenue": "positive",
    "expense": "expense_positive",
    "note_tracking": "positive",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def mapping_config_path() -> Path:
    return _repo_root() / "config" / "mis_mapping.yaml"


def _read_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or mapping_config_path()
    if not config_path.exists():
        return {"mis_report": {}}
    with config_path.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {"mis_report": {}}


def _write_config(config: dict[str, Any], path: Path | None = None) -> None:
    config_path = path or mapping_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream, sort_keys=False, allow_unicode=False)


def _status_to_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"active", "true", "yes", "1"}


def _bool_to_status(value: Any) -> str:
    return "Active" if _status_to_bool(value) else "Inactive"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _row_is_active(row: pd.Series) -> bool:
    """Support the persisted status label and the UI's boolean checkbox."""
    if "Active" in row.index:
        return _status_to_bool(row.get("Active"))
    return _status_to_bool(row.get("Active/Inactive"))


def find_duplicate_mis_mapping_groups(dataframe: pd.DataFrame) -> list[dict[str, str]]:
    """Return duplicate active account codes within the same organization."""
    if dataframe.empty:
        return []

    working_df = dataframe.fillna("").copy()
    working_df["_active"] = working_df.apply(_row_is_active, axis=1)
    working_df = working_df[working_df["_active"]].copy()
    if working_df.empty:
        return []

    account_codes = working_df["Zoho Account Code"].map(_clean_text)
    candidates = working_df[account_codes.ne("")].copy()
    candidates["_account_code"] = account_codes[account_codes.ne("")]

    duplicate_groups: list[dict[str, str]] = []
    for group_key, group in candidates.groupby(["Organization", "_account_code"], dropna=False):
        if len(group) < 2:
            continue
        organization, account_code = (_clean_text(value) for value in group_key)
        duplicate_groups.append(
            {
                "organization": organization,
                "account_type": "Code",
                "account_value": account_code,
            }
        )

    return duplicate_groups


def _legacy_rule_rows(mis_config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for section in ("revenue", "expense", "note_tracking"):
        section_rules = mis_config.get(section, {})
        if not isinstance(section_rules, dict):
            continue
        for row_key in section_rules:
            rows.append(
                {
                    "Report Template": REPORT_TEMPLATE,
                    "Excel Section": section,
                    "Excel Row Label": ROW_KEY_TO_LABEL.get(row_key, row_key.replace("_", " ").title()),
                    "Zoho Account ID": "",
                    "Zoho Account Code": "",
                    "Zoho Account Name": "",
                    "Organization": "all",
                    "Sign Rule": SECTION_TO_SIGN_RULE.get(section, "positive"),
                    "Active/Inactive": "Inactive",
                    "Updated At": "",
                    "Updated By": "legacy_yaml_rule",
                }
            )
    return rows


def _account_mapping_to_row(mapping: dict[str, Any]) -> dict[str, Any]:
    has_account_details = bool(
        _clean_text(mapping.get("zoho_account_code"))
        or _clean_text(mapping.get("zoho_account_name"))
    )
    requested_active = mapping.get("active") if "active" in mapping else has_account_details
    # Placeholder rows remain available to map later, but cannot be active
    # until at least one Zoho account identifier has been supplied.
    is_active = has_account_details and _status_to_bool(requested_active)
    return {
        "Report Template": _clean_text(mapping.get("report_template")) or REPORT_TEMPLATE,
        "Excel Section": _clean_text(mapping.get("excel_section")) or "expense",
        "Excel Row Label": _clean_text(mapping.get("excel_row_label")),
        "Zoho Account ID": _clean_text(mapping.get("zoho_account_id")),
        "Zoho Account Code": _clean_text(mapping.get("zoho_account_code")),
        "Zoho Account Name": _clean_text(mapping.get("zoho_account_name")),
        "Organization": _clean_text(mapping.get("organization")) or "all",
        "Sign Rule": _clean_text(mapping.get("sign_rule")) or "positive",
        "Active/Inactive": _bool_to_status(is_active),
        "Updated At": _clean_text(mapping.get("updated_at")),
        "Updated By": _clean_text(mapping.get("updated_by")),
    }


def load_mis_mapping_dataframe(path: Path | None = None) -> pd.DataFrame:
    """Load editable account mappings, with legacy YAML rules as read-only context rows."""
    config = _read_config(path)
    mis_config = config.get("mis_report", {})
    account_mappings = mis_config.get("account_mappings", [])
    rows = [_account_mapping_to_row(mapping) for mapping in account_mappings if isinstance(mapping, dict)]

    if not rows:
        rows = _legacy_rule_rows(mis_config)

    return pd.DataFrame(rows, columns=MAPPING_COLUMNS)


def validate_mis_mapping_dataframe(dataframe: pd.DataFrame) -> list[str]:
    """Return business validation messages for an edited mapping table."""
    errors = []
    if dataframe.empty:
        return errors

    working_df = dataframe.fillna("")
    for index, row in working_df.iterrows():
        row_number = index + 1
        is_active = _row_is_active(row)
        row_label = _clean_text(row.get("Excel Row Label"))
        account_code = _clean_text(row.get("Zoho Account Code"))
        account_name = _clean_text(row.get("Zoho Account Name"))

        if is_active and not row_label:
            errors.append(f"Row {row_number}: Excel Row Label is required for active mappings.")
        if is_active and not account_code and not account_name:
            errors.append(f"Row {row_number}: Active mappings require a Zoho Account Code or Zoho Account Name.")

    duplicate_groups = find_duplicate_mis_mapping_groups(working_df)
    if duplicate_groups:
        errors.append(
            f"{len(duplicate_groups)} duplicate active Zoho account mapping(s) found. "
            "The same account code cannot be mapped to multiple Excel rows for the same organization."
        )

    return errors


def save_mis_mapping_dataframe(
    dataframe: pd.DataFrame,
    updated_by: str = DEFAULT_UPDATED_BY,
    path: Path | None = None,
) -> dict[str, Any]:
    """Persist edited account mappings into the existing MIS YAML config."""
    errors = validate_mis_mapping_dataframe(dataframe)
    if errors:
        return {"status": "error", "errors": errors, "saved_count": 0}

    config = _read_config(path)
    mis_config = config.setdefault("mis_report", {})
    now = datetime.now(timezone.utc).isoformat()
    mappings = []

    for _, row in dataframe.fillna("").iterrows():
        row_label = _clean_text(row.get("Excel Row Label"))
        account_id = _clean_text(row.get("Zoho Account ID"))
        account_code = _clean_text(row.get("Zoho Account Code"))
        account_name = _clean_text(row.get("Zoho Account Name"))
        if not any([row_label, account_id, account_code, account_name]):
            continue

        mappings.append(
            {
                "report_template": _clean_text(row.get("Report Template")) or REPORT_TEMPLATE,
                "excel_section": _clean_text(row.get("Excel Section")) or "expense",
                "excel_row_label": row_label,
                "zoho_account_id": account_id,
                "zoho_account_code": account_code,
                "zoho_account_name": account_name,
                "organization": _clean_text(row.get("Organization")) or "all",
                "sign_rule": _clean_text(row.get("Sign Rule")) or "positive",
                "active": _row_is_active(row),
                "updated_at": now,
                "updated_by": _clean_text(row.get("Updated By")) or updated_by,
            }
        )

    mis_config["account_mappings"] = mappings
    mis_config.setdefault("storage_note", f"Fallback local config for future migration to {CONFIG_TABLE_NAME}.")
    _write_config(config, path)
    return {"status": "success", "errors": [], "saved_count": len(mappings), "updated_at": now}
