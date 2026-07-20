from __future__ import annotations

import pandas as pd
import yaml

from backend.services.mis_mapping_service import (
    load_mis_mapping_dataframe,
    save_mis_mapping_dataframe,
    validate_mis_mapping_dataframe,
)


def test_mis_mapping_validation_requires_row_label_and_active_account():
    dataframe = pd.DataFrame(
        [
            {
                "Report Template": "MidofficeData_KeyMetrics_PL_FY2526",
                "Excel Section": "expense",
                "Excel Row Label": "",
                "Zoho Account ID": "",
                "Zoho Account Code": "",
                "Zoho Account Name": "",
                "Organization": "india",
                "Sign Rule": "expense_positive",
                "Active/Inactive": "Active",
                "Updated At": "",
                "Updated By": "",
            }
        ]
    )

    errors = validate_mis_mapping_dataframe(dataframe)

    assert "Excel Row Label is required" in " ".join(errors)
    assert "Active mappings require a Zoho Account Code or Zoho Account Name" in " ".join(errors)


def test_mis_mapping_validation_allows_blank_inactive_row():
    dataframe = pd.DataFrame(
        [
            {
                "Report Template": "MidofficeData_KeyMetrics_PL_FY2526",
                "Excel Section": "expense",
                "Excel Row Label": "",
                "Zoho Account ID": "",
                "Zoho Account Code": "",
                "Zoho Account Name": "",
                "Organization": "india",
                "Sign Rule": "expense_positive",
                "Active/Inactive": "Inactive",
                "Updated At": "",
                "Updated By": "",
            }
        ]
    )

    assert validate_mis_mapping_dataframe(dataframe) == []


def test_mis_mapping_save_allows_inactive_placeholder_row(tmp_path):
    config_path = tmp_path / "mis_mapping.yaml"
    dataframe = pd.DataFrame(
        [
            {
                "Excel Section": "expense",
                "Excel Row Label": "Office Rent",
                "Zoho Account Code": "",
                "Zoho Account Name": "",
                "Organization": "india",
                "Sign Rule": "expense_positive",
                "Active/Inactive": "Inactive",
            }
        ]
    )

    result = save_mis_mapping_dataframe(dataframe, path=config_path)
    reloaded = load_mis_mapping_dataframe(path=config_path)

    assert result["status"] == "success"
    assert reloaded.iloc[0]["Excel Row Label"] == "Office Rent"
    assert reloaded.iloc[0]["Active/Inactive"] == "Inactive"


def test_mis_mapping_validation_detects_duplicate_active_account_code():
    dataframe = pd.DataFrame(
        [
            {
                "Report Template": "Template A",
                "Excel Row Label": row_label,
                "Zoho Account ID": account_id,
                "Zoho Account Code": "6001",
                "Organization": "india",
                "Active/Inactive": "Active",
            }
            for row_label, account_id in (("Office Rent", "123"), ("Other G&A", "456"))
        ]
    )

    errors = validate_mis_mapping_dataframe(dataframe)

    assert "duplicate active Zoho account mapping" in " ".join(errors)


def test_mis_mapping_load_defaults_missing_active_to_active_when_details_exist(tmp_path):
    config_path = tmp_path / "mis_mapping.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "mis_report": {
                    "account_mappings": [
                        {
                            "excel_row_label": "Office Rent",
                            "zoho_account_code": "6001",
                            "zoho_account_name": "Rent Expense",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    reloaded = load_mis_mapping_dataframe(path=config_path)

    assert reloaded.iloc[0]["Active/Inactive"] == "Active"


def test_mis_mapping_load_defaults_placeholder_without_details_to_inactive(tmp_path):
    config_path = tmp_path / "mis_mapping.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "mis_report": {
                    "account_mappings": [
                        {
                            "excel_row_label": "Office Rent",
                            "zoho_account_code": "",
                            "zoho_account_name": "",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    reloaded = load_mis_mapping_dataframe(path=config_path)

    assert reloaded.iloc[0]["Active/Inactive"] == "Inactive"


def test_mis_mapping_load_forces_explicit_active_placeholder_to_inactive(tmp_path):
    config_path = tmp_path / "mis_mapping.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "mis_report": {
                    "account_mappings": [
                        {
                            "excel_row_label": "Office Rent",
                            "zoho_account_code": "",
                            "zoho_account_name": "",
                            "active": True,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    reloaded = load_mis_mapping_dataframe(path=config_path)

    assert reloaded.iloc[0]["Active/Inactive"] == "Inactive"


def test_mis_mapping_load_preserves_explicit_inactive(tmp_path):
    config_path = tmp_path / "mis_mapping.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "mis_report": {
                    "account_mappings": [
                        {
                            "excel_row_label": "Office Rent",
                            "zoho_account_code": "6001",
                            "active": False,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    reloaded = load_mis_mapping_dataframe(path=config_path)

    assert reloaded.iloc[0]["Active/Inactive"] == "Inactive"


def test_mis_mapping_validation_accepts_active_account_name_without_code():
    dataframe = pd.DataFrame(
        [
            {
                "Excel Row Label": "Office Rent",
                "Zoho Account Code": "",
                "Zoho Account Name": "Rent Expense",
                "Organization": "india",
                "Active/Inactive": "Active",
            }
        ]
    )

    assert validate_mis_mapping_dataframe(dataframe) == []


def test_mis_mapping_save_reloads_account_mapping(tmp_path):
    config_path = tmp_path / "mis_mapping.yaml"
    config_path.write_text(
        yaml.safe_dump({"mis_report": {"expense": {"rent": {"keywords": ["rent"], "source_types": ["bill"]}}}}),
        encoding="utf-8",
    )
    dataframe = pd.DataFrame(
        [
            {
                "Report Template": "MidofficeData_KeyMetrics_PL_FY2526",
                "Excel Section": "expense",
                "Excel Row Label": "Office Rent",
                "Zoho Account ID": "12345",
                "Zoho Account Code": "6001",
                "Zoho Account Name": "Rent Expense",
                "Organization": "india",
                "Sign Rule": "expense_positive",
                "Active/Inactive": "Active",
                "Updated At": "",
                "Updated By": "",
            }
        ]
    )

    result = save_mis_mapping_dataframe(dataframe, updated_by="test_user", path=config_path)
    reloaded = load_mis_mapping_dataframe(path=config_path)

    assert result["status"] == "success"
    assert reloaded.iloc[0]["Excel Row Label"] == "Office Rent"
    assert reloaded.iloc[0]["Zoho Account Code"] == "6001"
    assert reloaded.iloc[0]["Active/Inactive"] == "Active"
