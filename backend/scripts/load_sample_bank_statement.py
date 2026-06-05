import os
import uuid
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery

from backend.gcp.gcs_loader import upload_json_to_gcs

load_dotenv()


def main():
    project_id = os.getenv("GCP_PROJECT_ID")
    bucket_name = os.getenv("GCS_RAW_BUCKET")

    upload_id = str(uuid.uuid4())
    uploaded_at = datetime.now(timezone.utc)

    local_file_path = "data/uploads/bank_statements/sample_bank_statement.csv"
    original_file_name = "sample_bank_statement.csv"

    df = pd.read_csv(local_file_path)

    df["bank_line_id"] = [str(uuid.uuid4()) for _ in range(len(df))]
    df["upload_id"] = upload_id
    df["bank_name"] = "Test Bank"
    df["account_number_masked"] = "XXXX1234"
    df["raw_row_number"] = range(1, len(df) + 1)
    df["created_at"] = uploaded_at

    df["transaction_date"] = pd.to_datetime(df["transaction_date"]).dt.date
    df["value_date"] = pd.to_datetime(df["value_date"]).dt.date

    records = df.to_dict(orient="records")

    gcs_path = f"raw/bank_statements/upload_id={upload_id}/{original_file_name}"

    upload_json_to_gcs(
        bucket_name=bucket_name,
        destination_blob_name=gcs_path.replace(".csv", ".json"),
        data=records,
    )

    client = bigquery.Client(project=project_id)

    upload_tracking_row = [
        {
            "upload_id": upload_id,
            "file_type": "bank_statement",
            "original_file_name": original_file_name,
            "gcs_raw_path": f"gs://{bucket_name}/{gcs_path}",
            "uploaded_by": "local_test",
            "uploaded_at": uploaded_at.isoformat(),
            "parse_status": "success",
            "records_parsed": len(records),
            "error_message": None,
        }
    ]

    upload_job = client.load_table_from_json(
        upload_tracking_row,
        f"{project_id}.finance_bronze.file_uploads",
    )
    upload_job.result()

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
                "narration": record["narration"],
                "debit_amount": float(record["debit_amount"]),
                "credit_amount": float(record["credit_amount"]),
                "balance_amount": float(record["balance_amount"]),
                "reference_number": record["reference_number"],
                "raw_row_number": int(record["raw_row_number"]),
                "created_at": record["created_at"].isoformat(),
            }
        )

    bank_job = client.load_table_from_json(
        bank_rows,
        f"{project_id}.finance_silver.fact_bank_statement_lines",
    )
    bank_job.result()

    print("Sample bank statement loaded successfully.")
    print(f"Upload ID: {upload_id}")
    print(f"Rows loaded: {len(bank_rows)}")


if __name__ == "__main__":
    main()