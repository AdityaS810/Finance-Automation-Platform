"""Backend-facing Zoho sync service for the Streamlit app."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

from dotenv import load_dotenv

from backend.config.zoho_entities import (
    BRONZE_DATASET_ID,
    RAW_TABLE_ID,
    SOURCE_SYSTEM,
    get_zoho_entity_configs,
)
from backend.config.zoho_organizations import get_zoho_organizations
from backend.etl.bronze_loader import load_raw_records_to_bigquery
from backend.gcp.gcs_loader import upload_json_to_gcs
from backend.zoho.extract_zoho import fetch_entity_records


load_dotenv()


def _require_environment_variables(variable_names: list[str]) -> None:
    missing_variables = [name for name in variable_names if not os.getenv(name)]
    if missing_variables:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(sorted(missing_variables))
        )


def _build_gcs_path(org_key: str, entity_name: str, run_id: str, run_date: datetime) -> str:
    """Build the shared multi-org Zoho raw path used by cloud and UI syncs."""
    return (
        f"raw/zoho_books/{org_key}/{entity_name}/"
        f"year={run_date.year}/month={run_date.month:02d}/day={run_date.day:02d}/"
        f"run_id={run_id}/{entity_name}.json"
    )


def run_zoho_sync(start_date: date, end_date: date) -> dict:
    """Run the multi-org Zoho-to-GCS-to-BigQuery flow."""
    if start_date > end_date:
        raise ValueError("From Date must be on or before To Date.")

    _require_environment_variables(
        [
            "ZOHO_ACCOUNTS_BASE_URL",
            "ZOHO_BOOKS_BASE_URL",
            "ZOHO_CLIENT_ID",
            "ZOHO_CLIENT_SECRET",
            "ZOHO_REFRESH_TOKEN",
            "GCP_PROJECT_ID",
            "GCS_RAW_BUCKET",
        ]
    )

    project_id = os.getenv("GCP_PROJECT_ID", "")
    bucket_name = os.getenv("GCS_RAW_BUCKET", "")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    today = datetime.now(timezone.utc)
    entities = get_zoho_entity_configs()
    organizations = get_zoho_organizations()
    row_counts = []

    for organization in organizations:
        for entity in entities:
            records = fetch_entity_records(entity, organization_id=organization["organization_id"])
            gcs_path = _build_gcs_path(organization["org_key"], entity.name, run_id, today)

            uploaded_path = upload_json_to_gcs(
                bucket_name=bucket_name,
                destination_blob_name=gcs_path,
                data=records,
            )

            rows_loaded = load_raw_records_to_bigquery(
                project_id=project_id,
                dataset_id=BRONZE_DATASET_ID,
                table_id=RAW_TABLE_ID,
                records=records,
                run_id=run_id,
                source_system=SOURCE_SYSTEM,
                entity_name=entity.name,
                id_field=entity.id_field,
                gcs_uri=uploaded_path,
                source_org_key=organization["org_key"],
                source_org_id=organization["organization_id"],
                source_org_name=organization["organization_name"],
                source_country=organization["country"],
                source_currency=organization["base_currency"],
            )

            row_counts.append(
                {
                    "org_key": organization["org_key"],
                    "organization_name": organization["organization_name"],
                    "source_currency": organization["base_currency"],
                    "entity": entity.name,
                    "rows_loaded": rows_loaded,
                    "gcs_path": uploaded_path,
                }
            )

    return {
        "status": "success",
        "message": (
            "Zoho multi-organization sync completed successfully. "
            f"The selected window {start_date:%d %b %Y} to {end_date:%d %b %Y} is shown in the UI, "
            "but the current backend extractor still pulls the latest available Zoho records. "
            "Consolidated reporting is INR-based."
        ),
        "synced_at": datetime.now(),
        "row_counts": row_counts,
        "organizations": organizations,
        "reporting_currency": "INR",
    }
