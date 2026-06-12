import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from backend.config.zoho_entities import (
    BRONZE_DATASET_ID,
    RAW_TABLE_ID,
    SOURCE_SYSTEM,
    get_zoho_entity_configs,
)
from backend.etl.bronze_loader import load_raw_records_to_bigquery
from backend.etl.run_tracker import create_etl_run, fail_etl_run, update_etl_run
from backend.gcp.gcs_loader import upload_json_to_gcs
from backend.zoho.extract_zoho import fetch_entity_records

load_dotenv()


def build_gcs_path(entity_name: str, run_id: str, run_date: datetime) -> str:
    """Create a predictable partitioned path for a raw Zoho export file."""

    return (
        f"raw/zoho_books/{entity_name}/"
        f"year={run_date.year}/month={run_date.month:02d}/day={run_date.day:02d}/"
        f"run_id={run_id}/{entity_name}.json"
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
    total_records_loaded = 0
    run_created = False

    try:
        # Create the audit row before extraction starts. If the process fails
        # later, fail_etl_run updates this same row with the error message.
        create_etl_run(
            project_id=project_id,
            dataset_id=BRONZE_DATASET_ID,
            run_id=run_id,
            source_system=SOURCE_SYSTEM,
            metadata={"entities": [entity.name for entity in entities]},
        )
        run_created = True

        for entity in entities:
            print(f"Fetching {entity.name} from Zoho...")

            data = fetch_entity_records(entity)

            print(f"Fetched {len(data)} records for {entity.name}")

            gcs_path = build_gcs_path(
                entity_name=entity.name,
                run_id=run_id,
                run_date=today,
            )

            full_gcs_path = upload_json_to_gcs(
                bucket_name=bucket_name,
                destination_blob_name=gcs_path,
                data=data,
            )

            print(f"Uploaded {entity.name} to GCS: {full_gcs_path}")

            rows_loaded = load_raw_records_to_bigquery(
                project_id=project_id,
                dataset_id=BRONZE_DATASET_ID,
                table_id=RAW_TABLE_ID,
                records=data,
                run_id=run_id,
                source_system=SOURCE_SYSTEM,
                entity_name=entity.name,
                id_field=entity.id_field,
                gcs_uri=full_gcs_path,
            )
            total_records_loaded += rows_loaded

        update_etl_run(
            project_id=project_id,
            dataset_id=BRONZE_DATASET_ID,
            run_id=run_id,
            records_loaded=total_records_loaded,
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
