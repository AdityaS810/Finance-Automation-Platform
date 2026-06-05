import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from backend.zoho.extract_zoho import (
    fetch_accounts,
    fetch_contacts,
    fetch_invoices,
    fetch_journals,
)

from backend.gcp.gcs_loader import upload_json_to_gcs
from backend.gcp.bigquery_loader import load_raw_records_to_bigquery

load_dotenv()


def main():
    project_id = os.getenv("GCP_PROJECT_ID")
    bucket_name = os.getenv("GCS_RAW_BUCKET")

    if not project_id:
        raise ValueError("GCP_PROJECT_ID is missing in .env")

    if not bucket_name:
        raise ValueError("GCS_RAW_BUCKET is missing in .env")

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

        load_raw_records_to_bigquery(
            project_id=project_id,
            dataset_id="finance_bronze",
            table_id=entity["bq_table"],
            records=data,
            run_id=run_id,
            source_system="zoho_books",
            entity_name=entity["name"],
            id_field=entity["id_field"],
        )

    print("Zoho cloud sync completed successfully.")


if __name__ == "__main__":
    main()