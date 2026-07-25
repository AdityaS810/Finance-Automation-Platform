import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.cloud import bigquery

from backend.zoho.extract_zoho import (
    fetch_accounts,
    fetch_contacts,
    fetch_customer_payments,
    fetch_expenses,
    fetch_invoices,
    fetch_journals,
    fetch_vendor_payments,
)

from backend.gcp.gcs_loader import upload_json_to_gcs
from backend.gcp.bigquery_loader import load_raw_records_to_bigquery

load_dotenv()


SOURCE_SYSTEM = "zoho_books"
BRONZE_DATASET_ID = "finance_bronze"
CANONICAL_RAW_TABLE_ID = "zoho_raw"
# Retained in BigQuery for historical compatibility only. New transaction
# extracts use CANONICAL_RAW_TABLE_ID because fact_transactions reads zoho_raw.
LEGACY_TRANSACTION_RAW_TABLE_ID = "zoho_transactions_raw"
ETL_RUNS_TABLE_ID = "etl_runs"
PERIOD_TRACKING_MESSAGE = (
    "Selected period is tracked for reporting visibility. Current extractor syncs latest available "
    "Zoho records where endpoint filtering is not supported."
)


def _etl_table(project_id: str) -> str:
    return f"{project_id}.{BRONZE_DATASET_ID}.{ETL_RUNS_TABLE_ID}"


def _insert_etl_run_status(
    project_id: str,
    run_id: str,
    status: str,
    started_at: datetime,
    records_loaded: int = 0,
    error_message: str | None = None,
    metadata: dict | None = None,
    triggered_by: str = "cloud_function",
) -> None:
    now = datetime.now(timezone.utc)
    row = {
        "run_id": run_id,
        "source_system": SOURCE_SYSTEM,
        "status": status,
        "started_at": started_at.isoformat(),
        "completed_at": now.isoformat() if status != "running" else None,
        "records_loaded": records_loaded,
        "error_message": error_message,
        "metadata_json": json.dumps(metadata, default=str) if metadata is not None else None,
        "triggered_by": triggered_by,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    errors = bigquery.Client(project=project_id).insert_rows_json(_etl_table(project_id), [row])
    if errors:
        raise RuntimeError(f"Could not record ETL run {run_id}: {errors}")


def get_entity_configs() -> list[dict]:
    """Return Cloud Function entity routing with generic Raw as canonical."""
    return [
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
            "name": "expenses",
            "fetch_func": fetch_expenses,
            "bq_table": CANONICAL_RAW_TABLE_ID,
            "id_field": "expense_id",
        },
        {
            "name": "customer_payments",
            "fetch_func": fetch_customer_payments,
            "bq_table": CANONICAL_RAW_TABLE_ID,
            "id_field": "payment_id",
        },
        {
            "name": "vendor_payments",
            "fetch_func": fetch_vendor_payments,
            "bq_table": CANONICAL_RAW_TABLE_ID,
            "id_field": "payment_id",
        },
        {
            "name": "transactions",
            "fetch_func": fetch_journals,
            "bq_table": CANONICAL_RAW_TABLE_ID,
            "id_field": "journal_id",
        },
    ]


def get_source_organization_metadata() -> dict[str, str | None]:
    """Read non-secret organization lineage fields from Cloud Function env."""
    return {
        "source_org_key": os.getenv("ZOHO_ORG_KEY") or "configured_zoho_organization",
        "source_org_id": os.getenv("ZOHO_ORGANIZATION_ID"),
        "source_org_name": os.getenv("ZOHO_ORGANIZATION_NAME"),
        "source_country": os.getenv("ZOHO_ORGANIZATION_COUNTRY"),
        "source_currency": os.getenv("ZOHO_ORGANIZATION_CURRENCY"),
    }


def load_entity_records_to_bronze(
    project_id: str,
    entity: dict,
    records: list[dict],
    run_id: str,
    gcs_uri: str,
    organization_metadata: dict[str, str | None],
) -> int:
    """Load an entity, adding standard lineage fields for generic Raw rows."""
    loader_arguments = {
        "project_id": project_id,
        "dataset_id": BRONZE_DATASET_ID,
        "table_id": entity["bq_table"],
        "records": records,
        "run_id": run_id,
        "source_system": SOURCE_SYSTEM,
        "entity_name": entity["name"],
        "id_field": entity["id_field"],
    }
    if entity["bq_table"] == CANONICAL_RAW_TABLE_ID:
        loader_arguments.update(organization_metadata)
        loader_arguments["gcs_uri"] = gcs_uri

    return load_raw_records_to_bigquery(**loader_arguments)


def main():
    project_id = os.getenv("GCP_PROJECT_ID")
    bucket_name = os.getenv("GCS_RAW_BUCKET")

    if not project_id:
        raise ValueError("GCP_PROJECT_ID is missing in .env")

    if not bucket_name:
        raise ValueError("GCS_RAW_BUCKET is missing in .env")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    today = datetime.now(timezone.utc)
    started_at = today
    total_records_loaded = 0
    run_created = False

    entities = get_entity_configs()
    organization_metadata = get_source_organization_metadata()
    metadata = {
        "from_date": None,
        "to_date": None,
        "selected_ui_from_date": None,
        "selected_ui_to_date": None,
        "entities": [entity["name"] for entity in entities],
        "organizations_synced": ["configured_zoho_organization"],
        "reporting_currency": "INR",
        "extractor_date_filtering": "tracked_period_only",
        "period_tracking_message": PERIOD_TRACKING_MESSAGE,
    }
    row_counts = []

    try:
        _insert_etl_run_status(
            project_id=project_id,
            run_id=run_id,
            status="running",
            started_at=started_at,
            metadata=metadata,
        )
        run_created = True

        for entity in entities:
            print(f"Fetching {entity['name']} from Zoho...")

            data = entity["fetch_func"]()

            print(f"Fetched {len(data)} records for {entity['name']}")

            gcs_path = (
                f"raw/zoho_books/{entity['name']}/"
                f"year={today.year}/month={today.month:02d}/day={today.day:02d}/"
                f"run_id={run_id}/{entity['name']}.json"
            )

            full_gcs_path = upload_json_to_gcs(
                bucket_name=bucket_name,
                destination_blob_name=gcs_path,
                data=data,
            )

            print(f"Uploaded {entity['name']} to GCS: {full_gcs_path}")

            rows_loaded = load_entity_records_to_bronze(
                project_id=project_id,
                entity=entity,
                records=data,
                run_id=run_id,
                gcs_uri=full_gcs_path,
                organization_metadata=organization_metadata,
            )
            total_records_loaded += rows_loaded
            row_counts.append(
                {
                    "org_key": "configured_zoho_organization",
                    "entity": entity["name"],
                    "rows_loaded": rows_loaded,
                    "gcs_path": full_gcs_path,
                }
            )
            print(f"Loaded {rows_loaded} {entity['name']} rows")

        _insert_etl_run_status(
            project_id=project_id,
            run_id=run_id,
            status="success",
            started_at=started_at,
            records_loaded=total_records_loaded,
            metadata={
                **metadata,
                "entity_record_counts": {
                    "configured_zoho_organization": {
                        row["entity"]: row["rows_loaded"] for row in row_counts
                    }
                },
                "records_loaded": total_records_loaded,
            },
        )
    except Exception as error:
        if run_created:
            try:
                _insert_etl_run_status(
                    project_id=project_id,
                    run_id=run_id,
                    status="failed",
                    started_at=started_at,
                    records_loaded=total_records_loaded,
                    error_message=str(error),
                    metadata=metadata,
                )
            except Exception as tracking_error:
                print(f"Could not mark ETL run {run_id} as failed: {tracking_error}")
        raise

    print("Zoho cloud sync completed successfully.")


if __name__ == "__main__":
    main()
