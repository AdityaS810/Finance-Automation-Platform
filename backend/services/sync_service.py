"""Backend-facing Zoho sync service for the Streamlit app."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

from dotenv import load_dotenv
from google.api_core.exceptions import Forbidden, Unauthorized
from google.auth.exceptions import DefaultCredentialsError, RefreshError

from backend.config.zoho_entities import (
    BRONZE_DATASET_ID,
    RAW_TABLE_ID,
    SOURCE_SYSTEM,
    get_zoho_entity_configs,
)
from backend.config.zoho_organizations import get_zoho_organizations
from backend.etl.bronze_loader import load_raw_records_to_bigquery
from backend.etl.run_tracker import create_etl_run, fail_etl_run, get_latest_etl_run, list_etl_runs, update_etl_run
from backend.gcp.gcs_loader import upload_json_to_gcs
from backend.zoho.extract_zoho import fetch_entity_records


load_dotenv()


FRIENDLY_AUTH_MESSAGE = "Cloud authentication is required. Please refresh Google authentication and try again."


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


def is_cloud_auth_error(error: Exception) -> bool:
    """Return True when an exception is likely caused by missing/expired GCP auth."""
    auth_types = (DefaultCredentialsError, RefreshError, Forbidden, Unauthorized)
    if isinstance(error, auth_types):
        return True
    message = str(error).lower()
    auth_markers = [
        "could not automatically determine credentials",
        "default credentials",
        "invalid_grant",
        "unauthorized",
        "permission denied",
        "forbidden",
        "credentials",
    ]
    return any(marker in message for marker in auth_markers)


def friendly_sync_error(error: Exception) -> str:
    """Return a business-friendly sync error for the Streamlit UI."""
    if is_cloud_auth_error(error):
        return FRIENDLY_AUTH_MESSAGE
    return "Data sync could not be completed. Please review configuration and try again."


def _base_sync_metadata(start_date: date | None, end_date: date | None, organizations: list[dict], entities: list) -> dict:
    return {
        "from_date": start_date.isoformat() if start_date else None,
        "to_date": end_date.isoformat() if end_date else None,
        "selected_ui_from_date": start_date.isoformat() if start_date else None,
        "selected_ui_to_date": end_date.isoformat() if end_date else None,
        "entities": [entity.name for entity in entities],
        "organizations_synced": [organization["org_key"] for organization in organizations],
        "organizations": [
            {
                "org_key": organization["org_key"],
                "organization_name": organization["organization_name"],
                "country": organization["country"],
                "base_currency": organization["base_currency"],
            }
            for organization in organizations
        ],
        "reporting_currency": "INR",
        "extractor_date_filtering": "tracked_period_only",
        "period_tracking_message": (
            "Selected period is tracked for reporting visibility. Current extractor syncs latest available "
            "Zoho records where endpoint filtering is not supported."
        ),
    }


def _record_count_metadata(row_counts: list[dict]) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for row in row_counts:
        org_key = str(row.get("org_key", "unknown"))
        entity_name = str(row.get("entity", "unknown"))
        counts.setdefault(org_key, {})[entity_name] = int(row.get("rows_loaded") or 0)
    return counts


def get_sync_history(limit: int = 25) -> list[dict]:
    """Read persisted Zoho sync history from BigQuery etl_runs."""
    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        return []
    return list_etl_runs(
        project_id=project_id,
        dataset_id=BRONZE_DATASET_ID,
        source_system=SOURCE_SYSTEM,
        limit=limit,
    )


def get_latest_sync_run() -> dict | None:
    """Read the latest persisted Zoho sync run from BigQuery etl_runs."""
    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        return None
    return get_latest_etl_run(
        project_id=project_id,
        dataset_id=BRONZE_DATASET_ID,
        source_system=SOURCE_SYSTEM,
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
    total_records_loaded = 0
    run_created = False
    metadata = _base_sync_metadata(start_date, end_date, organizations, entities)

    try:
        create_etl_run(
            project_id=project_id,
            dataset_id=BRONZE_DATASET_ID,
            run_id=run_id,
            source_system=SOURCE_SYSTEM,
            triggered_by="streamlit_user",
            metadata=metadata,
        )
        run_created = True

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
                total_records_loaded += rows_loaded

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

        metadata = {
            **metadata,
            "entity_record_counts": _record_count_metadata(row_counts),
            "records_loaded": total_records_loaded,
        }
        update_etl_run(
            project_id=project_id,
            dataset_id=BRONZE_DATASET_ID,
            run_id=run_id,
            records_loaded=total_records_loaded,
            metadata=metadata,
        )
    except Exception as error:
        if run_created:
            try:
                fail_etl_run(
                    project_id=project_id,
                    dataset_id=BRONZE_DATASET_ID,
                    run_id=run_id,
                    error=error,
                    records_loaded=total_records_loaded,
                )
            except Exception:
                pass
        raise

    return {
        "run_id": run_id,
        "status": "success",
        "message": (
            "Zoho multi-organization sync completed successfully. "
            f"The selected window {start_date:%d %b %Y} to {end_date:%d %b %Y} is tracked for reporting visibility. "
            "Current extractor syncs latest available Zoho records where endpoint filtering is not supported. "
            "Consolidated reporting is INR-based."
        ),
        "synced_at": datetime.now(),
        "row_counts": row_counts,
        "records_loaded": total_records_loaded,
        "organizations": organizations,
        "reporting_currency": "INR",
        "from_date": start_date,
        "to_date": end_date,
        "metadata": metadata,
    }
