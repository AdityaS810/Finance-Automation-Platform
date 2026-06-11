"""Backend-facing Zoho sync service for the Streamlit app."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

from dotenv import load_dotenv

from backend.gcp.bigquery_loader import load_raw_records_to_bigquery
from backend.gcp.gcs_loader import upload_json_to_gcs
from backend.zoho.extract_zoho import (
    fetch_accounts,
    fetch_contacts,
    fetch_invoices,
    fetch_journals,
)


load_dotenv()


def _require_environment_variables(variable_names: list[str]) -> None:
    missing_variables = [name for name in variable_names if not os.getenv(name)]
    if missing_variables:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(sorted(missing_variables))
        )


def run_zoho_sync(start_date: date, end_date: date) -> dict:
    """Run the existing backend Zoho-to-GCS-to-BigQuery flow."""
    if start_date > end_date:
        raise ValueError("From Date must be on or before To Date.")

    _require_environment_variables(
        [
            "ZOHO_ACCOUNTS_BASE_URL",
            "ZOHO_BOOKS_BASE_URL",
            "ZOHO_CLIENT_ID",
            "ZOHO_CLIENT_SECRET",
            "ZOHO_REFRESH_TOKEN",
            "ZOHO_ORGANIZATION_ID",
            "GCP_PROJECT_ID",
            "GCS_RAW_BUCKET",
        ]
    )

    project_id = os.getenv("GCP_PROJECT_ID", "")
    bucket_name = os.getenv("GCS_RAW_BUCKET", "")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    today = datetime.now(timezone.utc)

    entities = [
        {
            "name": "accounts",
            "fetch_func": fetch_accounts,
            "bq_table": "zoho_accounts_raw",
            "id_field": "account_id",
        },
        {
            "name": "contacts",
            "fetch_func": fetch_contacts,
            "bq_table": "zoho_contacts_raw",
            "id_field": "contact_id",
        },
        {
            "name": "invoices",
            "fetch_func": fetch_invoices,
            "bq_table": "zoho_invoices_raw",
            "id_field": "invoice_id",
        },
        {
            "name": "transactions",
            "fetch_func": fetch_journals,
            "bq_table": "zoho_transactions_raw",
            "id_field": "journal_id",
        },
    ]

    row_counts = []

    for entity in entities:
        records = entity["fetch_func"]()
        gcs_path = (
            f"raw/zoho_books/{entity['name']}/"
            f"year={today.year}/month={today.month:02d}/day={today.day:02d}/"
            f"run_id={run_id}/{entity['name']}.json"
        )

        uploaded_path = upload_json_to_gcs(
            bucket_name=bucket_name,
            destination_blob_name=gcs_path,
            data=records,
        )

        load_raw_records_to_bigquery(
            project_id=project_id,
            dataset_id="finance_bronze",
            table_id=entity["bq_table"],
            records=records,
            run_id=run_id,
            source_system="zoho_books",
            entity_name=entity["name"],
            id_field=entity["id_field"],
        )

        row_counts.append(
            {
                "table": entity["name"],
                "rows_loaded": len(records),
                "gcs_path": uploaded_path,
            }
        )

    return {
        "status": "success",
        "message": (
            "Zoho sync completed successfully. "
            f"The selected window {start_date:%d %b %Y} to {end_date:%d %b %Y} is shown in the UI, "
            "but the current backend extractor still pulls the latest available Zoho records."
        ),
        "synced_at": datetime.now(),
        "row_counts": row_counts,
    }
