"""Backend upload services for bank statements and GSTR files."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery

from backend.gcp.gcs_loader import upload_json_to_gcs


load_dotenv()


def _require_environment_variables(variable_names: list[str]) -> tuple[str, str]:
    missing_variables = [name for name in variable_names if not os.getenv(name)]
    if missing_variables:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(sorted(missing_variables))
        )

    return os.getenv("GCP_PROJECT_ID", ""), os.getenv("GCS_RAW_BUCKET", "")


def _get_optional_string_column(dataframe: pd.DataFrame, column_name: str, default_value: str = "") -> pd.Series:
    if column_name in dataframe.columns:
        return dataframe[column_name].fillna(default_value).astype(str)
    return pd.Series([default_value] * len(dataframe.index))


def save_bank_statement_upload(
    dataframe: pd.DataFrame,
    original_file_name: str,
    uploaded_by: str = "streamlit_user",
) -> dict:
    """Save a parsed bank statement using the existing backend storage pattern."""
    project_id, bucket_name = _require_environment_variables(["GCP_PROJECT_ID", "GCS_RAW_BUCKET"])

    required_columns = ["date", "narration", "debit", "credit", "balance"]
    missing_columns = [column for column in required_columns if column not in dataframe.columns]
    if missing_columns:
        raise ValueError("Bank statement is missing columns: " + ", ".join(missing_columns))

    working_df = dataframe.copy()
    working_df["date"] = pd.to_datetime(working_df["date"], errors="coerce")
    if working_df["date"].isna().any():
        raise ValueError("Bank statement contains invalid values in the date column.")

    for column_name in ["debit", "credit", "balance"]:
        working_df[column_name] = pd.to_numeric(working_df[column_name], errors="coerce").fillna(0.0)

    upload_id = str(uuid.uuid4())
    uploaded_at = datetime.now(timezone.utc)

    working_df["bank_line_id"] = [str(uuid.uuid4()) for _ in range(len(working_df.index))]
    working_df["upload_id"] = upload_id
    working_df["bank_name"] = os.getenv("DEFAULT_BANK_NAME", "Uploaded Bank")
    working_df["account_number_masked"] = os.getenv("DEFAULT_BANK_ACCOUNT_MASKED", "Not Provided")
    working_df["raw_row_number"] = range(1, len(working_df.index) + 1)
    working_df["created_at"] = uploaded_at
    working_df["transaction_date"] = working_df["date"].dt.date
    working_df["value_date"] = working_df["date"].dt.date
    working_df["reference_number"] = _get_optional_string_column(working_df, "reference_number")

    records = working_df.to_dict(orient="records")
    gcs_path = f"raw/bank_statements/upload_id={upload_id}/{original_file_name.rsplit('.', 1)[0]}.json"
    full_gcs_path = upload_json_to_gcs(
        bucket_name=bucket_name,
        destination_blob_name=gcs_path,
        data=records,
    )

    client = bigquery.Client(project=project_id)

    upload_tracking_row = [
        {
            "upload_id": upload_id,
            "file_type": "bank_statement",
            "original_file_name": original_file_name,
            "gcs_raw_path": full_gcs_path,
            "uploaded_by": uploaded_by,
            "uploaded_at": uploaded_at.isoformat(),
            "parse_status": "success",
            "records_parsed": len(records),
            "error_message": None,
        }
    ]
    client.load_table_from_json(upload_tracking_row, f"{project_id}.finance_bronze.file_uploads").result()

    bank_rows = []
    for record in records:
        bank_rows.append(
            {
                "bank_line_id": record["bank_line_id"],
                "upload_id": record["upload_id"],
                "bank_name": record["bank_name"],
                "account_number_masked": record["account_number_masked"],
                "transaction_date": str(record["transaction_date"]),
                "value_date": str(record["value_date"]),
                "narration": str(record["narration"]),
                "debit_amount": float(record["debit"]),
                "credit_amount": float(record["credit"]),
                "balance_amount": float(record["balance"]),
                "reference_number": record["reference_number"],
                "raw_row_number": int(record["raw_row_number"]),
                "created_at": record["created_at"].isoformat(),
            }
        )

    client.load_table_from_json(
        bank_rows,
        f"{project_id}.finance_silver.fact_bank_statement_lines",
    ).result()

    return {
        "status": "success",
        "message": "Bank statement uploaded successfully.",
        "upload_id": upload_id,
        "records_parsed": len(bank_rows),
        "gcs_raw_path": full_gcs_path,
    }


def save_gstr_upload(
    dataframe: pd.DataFrame,
    original_file_name: str,
    uploaded_by: str = "streamlit_user",
) -> dict:
    """Save a parsed GSTR file using the existing backend storage pattern."""
    project_id, bucket_name = _require_environment_variables(["GCP_PROJECT_ID", "GCS_RAW_BUCKET"])

    required_columns = ["gstin", "invoice_number", "taxable_value", "igst", "cgst", "sgst", "period"]
    missing_columns = [column for column in required_columns if column not in dataframe.columns]
    if missing_columns:
        raise ValueError("GSTR file is missing columns: " + ", ".join(missing_columns))

    working_df = dataframe.copy()
    numeric_columns = ["taxable_value", "igst", "cgst", "sgst"]
    for column_name in numeric_columns:
        working_df[column_name] = pd.to_numeric(working_df[column_name], errors="coerce").fillna(0.0)

    invoice_date_source = "invoice_date" if "invoice_date" in working_df.columns else "period"
    working_df["invoice_date"] = pd.to_datetime(working_df[invoice_date_source], errors="coerce")
    if working_df["invoice_date"].isna().any():
        raise ValueError(
            "GSTR file contains invalid invoice dates. Add an invoice_date column or use a period value that pandas can read."
        )

    upload_id = str(uuid.uuid4())
    uploaded_at = datetime.now(timezone.utc)

    working_df["gstr_line_id"] = [str(uuid.uuid4()) for _ in range(len(working_df.index))]
    working_df["upload_id"] = upload_id
    working_df["created_at"] = uploaded_at
    working_df["gstr_type"] = _get_optional_string_column(
        working_df,
        "gstr_type",
        os.getenv("DEFAULT_GSTR_TYPE", "GSTR Upload"),
    )
    working_df["supplier_name"] = _get_optional_string_column(working_df, "supplier_name")
    working_df["supplier_gstin"] = working_df["gstin"].astype(str)
    working_df["total_tax"] = working_df["igst"] + working_df["cgst"] + working_df["sgst"]
    working_df["invoice_value"] = working_df["taxable_value"] + working_df["total_tax"]
    working_df["invoice_date"] = working_df["invoice_date"].dt.date

    records = working_df.to_dict(orient="records")
    gcs_path = f"raw/gstr/upload_id={upload_id}/{original_file_name.rsplit('.', 1)[0]}.json"
    full_gcs_path = upload_json_to_gcs(
        bucket_name=bucket_name,
        destination_blob_name=gcs_path,
        data=records,
    )

    client = bigquery.Client(project=project_id)

    upload_tracking_row = [
        {
            "upload_id": upload_id,
            "file_type": "gstr",
            "original_file_name": original_file_name,
            "gcs_raw_path": full_gcs_path,
            "uploaded_by": uploaded_by,
            "uploaded_at": uploaded_at.isoformat(),
            "parse_status": "success",
            "records_parsed": len(records),
            "error_message": None,
        }
    ]
    client.load_table_from_json(upload_tracking_row, f"{project_id}.finance_bronze.file_uploads").result()

    gstr_rows = []
    for record in records:
        gstr_rows.append(
            {
                "gstr_line_id": record["gstr_line_id"],
                "upload_id": record["upload_id"],
                "gstr_type": record["gstr_type"],
                "period": str(record["period"]),
                "supplier_gstin": record["supplier_gstin"],
                "supplier_name": record["supplier_name"],
                "invoice_number": str(record["invoice_number"]),
                "invoice_date": str(record["invoice_date"]),
                "taxable_value": float(record["taxable_value"]),
                "igst_amount": float(record["igst"]),
                "cgst_amount": float(record["cgst"]),
                "sgst_amount": float(record["sgst"]),
                "total_tax": float(record["total_tax"]),
                "invoice_value": float(record["invoice_value"]),
                "created_at": record["created_at"].isoformat(),
            }
        )

    client.load_table_from_json(
        gstr_rows,
        f"{project_id}.finance_silver.fact_gstr_lines",
    ).result()

    return {
        "status": "success",
        "message": "GSTR file uploaded successfully.",
        "upload_id": upload_id,
        "records_parsed": len(gstr_rows),
        "gcs_raw_path": full_gcs_path,
    }
