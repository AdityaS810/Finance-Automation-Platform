import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from backend.config.zoho_entities import (
    BRONZE_DATASET_ID,
    RAW_TABLE_ID,
    SOURCE_SYSTEM,
    get_zoho_entity_configs,
)
from backend.config.zoho_organizations import get_zoho_organizations
from backend.etl.bronze_loader import load_raw_records_to_bigquery
from backend.etl.run_tracker import create_etl_run, fail_etl_run, update_etl_run
from backend.gcp.gcs_loader import upload_json_to_gcs
from backend.zoho.extract_zoho import fetch_entity_records

load_dotenv()

CANONICAL_RAW_TABLE_ID = RAW_TABLE_ID


def build_gcs_path(org_key: str, entity_name: str, run_id: str, run_date: datetime) -> str:
    """Create a predictable partitioned path for a raw Zoho export file."""

    return (
        f"raw/zoho_books/{org_key}/{entity_name}/"
        f"year={run_date.year}/month={run_date.month:02d}/day={run_date.day:02d}/"
        f"run_id={run_id}/{entity_name}.json"
    )


def load_entity_records_to_bronze(
    project_id: str,
    entity,
    records: list[dict],
    run_id: str,
    gcs_uri: str,
    organization: dict,
) -> int:
    """Load one entity into the canonical generic Bronze table.

    ``finance_bronze.zoho_raw`` is the canonical source for Enrich views,
    including transaction records when a transactions entity is requested.
    """
    return load_raw_records_to_bigquery(
        project_id=project_id,
        dataset_id=BRONZE_DATASET_ID,
        table_id=CANONICAL_RAW_TABLE_ID,
        records=records,
        run_id=run_id,
        source_system=SOURCE_SYSTEM,
        entity_name=entity.name,
        id_field=entity.id_field,
        gcs_uri=gcs_uri,
        source_org_key=organization["org_key"],
        source_org_id=organization["organization_id"],
        source_org_name=organization["organization_name"],
        source_country=organization["country"],
        source_currency=organization["base_currency"],
    )


def main():
    project_id = os.getenv("GCP_PROJECT_ID")
    bucket_name = os.getenv("GCS_RAW_BUCKET")

    if not project_id:
        raise ValueError("GCP_PROJECT_ID is missing in .env")

    if not bucket_name:
        raise ValueError("GCS_RAW_BUCKET is missing in .env")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    today = datetime.now(timezone.utc)
    entities = get_zoho_entity_configs()
    organizations = get_zoho_organizations()
    total_records_loaded = 0
    run_created = False
    row_counts = []
    metadata = {
        "from_date": None,
        "to_date": None,
        "selected_ui_from_date": None,
        "selected_ui_to_date": None,
        "entities": [entity.name for entity in entities],
        "organizations_synced": [organization["org_key"] for organization in organizations],
        "reporting_currency": "INR",
        "extractor_date_filtering": "tracked_period_only",
        "period_tracking_message": (
            "Selected period is tracked for reporting visibility. Current extractor syncs latest available "
            "Zoho records where endpoint filtering is not supported."
        ),
    }

    try:
        # Create the audit row before extraction starts. If the process fails
        # later, fail_etl_run updates this same row with the error message.
        create_etl_run(
            project_id=project_id,
            dataset_id=BRONZE_DATASET_ID,
            run_id=run_id,
            source_system=SOURCE_SYSTEM,
            metadata=metadata,
        )
        run_created = True

        for organization in organizations:
            org_key = organization["org_key"]
            organization_id = organization["organization_id"]
            print(f"Starting Zoho sync for {organization['organization_name']} ({org_key})...")

            for entity in entities:
                print(f"Fetching {entity.name} from Zoho org {org_key}...")

                data = fetch_entity_records(entity, organization_id=organization_id)

                print(f"Fetched {len(data)} records for {entity.name} from org {org_key}")

                gcs_path = build_gcs_path(
                    org_key=org_key,
                    entity_name=entity.name,
                    run_id=run_id,
                    run_date=today,
                )

                full_gcs_path = upload_json_to_gcs(
                    bucket_name=bucket_name,
                    destination_blob_name=gcs_path,
                    data=data,
                )

                print(f"Uploaded {entity.name} for org {org_key} to GCS: {full_gcs_path}")

                rows_loaded = load_entity_records_to_bronze(
                    project_id=project_id,
                    entity=entity,
                    records=data,
                    run_id=run_id,
                    gcs_uri=full_gcs_path,
                    organization=organization,
                )
                total_records_loaded += rows_loaded
                row_counts.append(
                    {
                        "org_key": org_key,
                        "entity": entity.name,
                        "rows_loaded": rows_loaded,
                    }
                )

        entity_record_counts = {}
        for row in row_counts:
            entity_record_counts.setdefault(row["org_key"], {})[row["entity"]] = row["rows_loaded"]

        update_etl_run(
            project_id=project_id,
            dataset_id=BRONZE_DATASET_ID,
            run_id=run_id,
            records_loaded=total_records_loaded,
            metadata={
                **metadata,
                "entity_record_counts": entity_record_counts,
                "records_loaded": total_records_loaded,
            },
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
            except Exception as tracking_error:
                print(f"Could not mark ETL run {run_id} as failed: {tracking_error}")
        raise

    print("Zoho cloud sync completed successfully.")


if __name__ == "__main__":
    main()
