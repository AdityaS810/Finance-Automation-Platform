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

    local_file_path = "data/uploads/gstr/sample_gstr.csv"
    original_file_name = "sample_gstr.csv"

    df = pd.read_csv(local_file_path)

    df["gstr_line_id"] = [str(uuid.uuid4()) for _ in range(len(df))]
    df["upload_id"] = upload_id
    df["created_at"] = uploaded_at

    df["invoice_date"] = pd.to_datetime(df["invoice_date"]).dt.date

    records = df.to_dict(orient="records")

    gcs_path = f"raw/gstr/upload_id={upload_id}/{original_file_name}"

    upload_json_to_gcs(
        bucket_name=bucket_name,
        destination_blob_name=gcs_path.replace(".csv", ".json"),
        data=records,
    )

    client = bigquery.Client(project=project_id)

    upload_tracking_row = [
        {
            "upload_id": upload_id,
            "file_type": "gstr",
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

    gstr_rows = []
    for record in records:
        gstr_rows.append(
            {
                "gstr_line_id": record["gstr_line_id"],
                "upload_id": record["upload_id"],
                "gstr_type": record["gstr_type"],
                "period": record["period"],
                "supplier_gstin": record["supplier_gstin"],
                "supplier_name": record["supplier_name"],
                "invoice_number": record["invoice_number"],
                "invoice_date": str(record["invoice_date"]),
                "taxable_value": float(record["taxable_value"]),
                "igst_amount": float(record["igst_amount"]),
                "cgst_amount": float(record["cgst_amount"]),
                "sgst_amount": float(record["sgst_amount"]),
                "total_tax": float(record["total_tax"]),
                "invoice_value": float(record["invoice_value"]),
                "created_at": record["created_at"].isoformat(),
            }
        )

    gstr_job = client.load_table_from_json(
        gstr_rows,
        f"{project_id}.finance_silver.fact_gstr_lines",
    )
    gstr_job.result()

    print("Sample GSTR file loaded successfully.")
    print(f"Upload ID: {upload_id}")
    print(f"Rows loaded: {len(gstr_rows)}")


if __name__ == "__main__":
    main()