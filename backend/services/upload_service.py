"""Backend upload services for bank statements and GSTR files."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery

from backend.gcp.gcs_loader import upload_json_to_gcs


load_dotenv()

MONEY_QUANTIZER = Decimal("0.01")
BANK_MONEY_COLUMNS = ["debit", "credit", "balance", "balance_amount", "amount"]
GSTR_MONEY_COLUMNS = [
    "invoice_value",
    "taxable_value",
    "igst",
    "cgst",
    "sgst",
    "cess",
    "total_tax",
    "total_amount",
]
FILE_UPLOADS_TABLE = "finance_bronze.file_uploads"
BANK_LINES_TABLE = "finance_silver.fact_bank_statement_lines"
GSTR_LINES_TABLE = "finance_silver.fact_gstr_lines"


def _require_environment_variables(variable_names: list[str]) -> tuple[str, str]:
    missing_variables = [name for name in variable_names if not os.getenv(name)]
    if missing_variables:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(sorted(missing_variables))
        )

    return os.getenv("GCP_PROJECT_ID", ""), os.getenv("GCS_RAW_BUCKET", "")


def _table_name(project_id: str, table_name: str) -> str:
    """Return a quoted BigQuery table name."""
    return f"`{project_id}.{table_name}`"


def _delete_rows_by_upload_id(client: bigquery.Client, table_name: str, upload_id: str) -> int:
    """Delete rows for one upload_id from one BigQuery table."""
    query = f"""
        DELETE FROM {table_name}
        WHERE upload_id = @upload_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("upload_id", "STRING", upload_id)]
    )
    query_job = client.query(query, job_config=job_config)
    query_job.result()
    return int(query_job.num_dml_affected_rows or 0)


def _delete_upload_tracking_row(client: bigquery.Client, project_id: str, upload_id: str, file_type: str) -> int:
    """Delete the upload registry row for one selected uploaded file."""
    query = f"""
        DELETE FROM {_table_name(project_id, FILE_UPLOADS_TABLE)}
        WHERE upload_id = @upload_id
          AND file_type = @file_type
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("upload_id", "STRING", upload_id),
            bigquery.ScalarQueryParameter("file_type", "STRING", file_type),
        ]
    )
    query_job = client.query(query, job_config=job_config)
    query_job.result()
    return int(query_job.num_dml_affected_rows or 0)


def _delete_uploaded_input_rows(upload_id: str, source_table: str, file_type: str) -> dict:
    """Delete one uploaded reconciliation input from BigQuery storage."""
    if not str(upload_id or "").strip():
        raise ValueError("Upload ID is required.")

    project_id, _bucket_name = _require_environment_variables(["GCP_PROJECT_ID"])
    client = bigquery.Client(project=project_id)
    cleaned_upload_id = str(upload_id).strip()

    input_rows_deleted = _delete_rows_by_upload_id(
        client,
        _table_name(project_id, source_table),
        cleaned_upload_id,
    )
    tracking_rows_deleted = _delete_upload_tracking_row(client, project_id, cleaned_upload_id, file_type)

    return {
        "status": "success",
        "message": "Upload deleted successfully.",
        "upload_id": cleaned_upload_id,
        "source_type": file_type,
        "input_rows_deleted": input_rows_deleted,
        "tracking_rows_deleted": tracking_rows_deleted,
    }


def delete_bank_upload(upload_id: str) -> dict:
    """Delete only the selected bank statement upload rows."""
    return _delete_uploaded_input_rows(upload_id, BANK_LINES_TABLE, "bank_statement")


def delete_gstr_upload(upload_id: str) -> dict:
    """Delete only the selected GSTR upload rows."""
    return _delete_uploaded_input_rows(upload_id, GSTR_LINES_TABLE, "gstr")


def _get_optional_string_column(dataframe: pd.DataFrame, column_name: str, default_value: str = "") -> pd.Series:
    if column_name in dataframe.columns:
        return dataframe[column_name].fillna(default_value).astype(str)
    return pd.Series([default_value] * len(dataframe.index))


def _to_money_decimal(value: object) -> Decimal:
    """Convert a parsed currency value to a 2-decimal Decimal.

    BigQuery NUMERIC rejects binary float artifacts such as
    141.98999999999998. Decimal quantization keeps upload values currency-safe.
    """
    if value is None:
        return Decimal("0.00")

    try:
        if pd.isna(value):
            return Decimal("0.00")
    except (TypeError, ValueError):
        pass

    text_value = str(value).strip()
    if text_value.lower() in {"", "nan", "none", "nat"}:
        return Decimal("0.00")

    is_negative_parentheses = text_value.startswith("(") and text_value.endswith(")")
    cleaned_value = text_value.strip("()")
    cleaned_value = (
        cleaned_value.replace("₹", "")
        .replace(",", "")
        .replace(" ", "")
        .replace("\n", "")
        .replace("\r", "")
    )
    cleaned_value = cleaned_value.replace("CR", "").replace("Cr", "").replace("cr", "")
    cleaned_value = cleaned_value.replace("DR", "").replace("Dr", "").replace("dr", "")
    cleaned_value = re.sub(r"[^0-9.\-]", "", cleaned_value)

    try:
        decimal_value = Decimal(cleaned_value)
    except (InvalidOperation, ValueError):
        decimal_value = Decimal("0.00")

    if is_negative_parentheses:
        decimal_value = -abs(decimal_value)

    return decimal_value.quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def _money_string(value: object) -> str:
    """Return a BigQuery NUMERIC-safe currency string with exactly 2 decimals."""
    return format(_to_money_decimal(value), ".2f")


def _money_column_names(dataframe: pd.DataFrame, explicit_columns: list[str]) -> list[str]:
    """Find known currency columns and any parsed column ending in _amount."""
    money_columns = set(explicit_columns)
    money_columns.update(column for column in dataframe.columns if str(column).endswith("_amount"))
    return [column for column in dataframe.columns if column in money_columns]


def _clean_money_columns(dataframe: pd.DataFrame, explicit_columns: list[str]) -> pd.DataFrame:
    """Return a copy where money columns are fixed 2-decimal strings."""
    working_df = dataframe.copy()
    for column_name in _money_column_names(working_df, explicit_columns):
        working_df = working_df.astype({column_name: "object"})
        working_df.loc[:, column_name] = working_df[column_name].apply(_money_string)
    return working_df


def _sum_money_values(row: pd.Series, column_names: list[str]) -> Decimal:
    """Sum currency fields using Decimal so tax totals do not drift."""
    total = Decimal("0.00")
    for column_name in column_names:
        if column_name in row.index:
            total += _to_money_decimal(row[column_name])
    return total.quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


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
    print(f"[Upload Service] Bank upload input rows: {len(working_df.index)}")
    print(f"[Upload Service] First 5 bank upload rows: {working_df.head(5).to_dict(orient='records')}")
    working_df["date"] = pd.to_datetime(working_df["date"], errors="coerce")
    if working_df["date"].isna().any():
        raise ValueError("Bank statement contains invalid values in the date column.")

    for column_name in ["debit", "credit", "balance"]:
        working_df[column_name] = pd.to_numeric(working_df[column_name], errors="coerce").fillna(0.0)

    working_df = _clean_money_columns(working_df, BANK_MONEY_COLUMNS)

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
    print(f"[Upload Service] Bank rows prepared for save: {len(records)}")
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
                "debit_amount": _money_string(record["debit"]),
                "credit_amount": _money_string(record["credit"]),
                "balance_amount": _money_string(record["balance"]),
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
        "file_name": original_file_name,
        "uploaded_at": uploaded_at.isoformat(),
        "source_type": "bank_statement",
        "row_count": len(bank_rows),
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

    if "cess" not in working_df.columns:
        working_df["cess"] = "0.00"

    working_df = _clean_money_columns(working_df, GSTR_MONEY_COLUMNS)
    tax_columns = ["igst", "cgst", "sgst", "cess"]
    working_df["total_tax"] = working_df.apply(
        lambda row: _money_string(_sum_money_values(row, tax_columns)),
        axis=1,
    )
    working_df["invoice_value"] = working_df.apply(
        lambda row: _money_string(_to_money_decimal(row["taxable_value"]) + _to_money_decimal(row["total_tax"])),
        axis=1,
    )
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
                "taxable_value": _money_string(record["taxable_value"]),
                "igst_amount": _money_string(record["igst"]),
                "cgst_amount": _money_string(record["cgst"]),
                "sgst_amount": _money_string(record["sgst"]),
                "total_tax": _money_string(record["total_tax"]),
                "invoice_value": _money_string(record["invoice_value"]),
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
        "file_name": original_file_name,
        "uploaded_at": uploaded_at.isoformat(),
        "source_type": "gstr",
        "row_count": len(gstr_rows),
        "records_parsed": len(gstr_rows),
        "gcs_raw_path": full_gcs_path,
    }
