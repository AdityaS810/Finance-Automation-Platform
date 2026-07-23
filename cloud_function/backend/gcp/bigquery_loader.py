import json
from datetime import datetime, timezone

import pandas as pd
from google.cloud import bigquery


def load_raw_records_to_bigquery(
    project_id: str,
    dataset_id: str,
    table_id: str,
    records: list[dict],
    run_id: str,
    source_system: str,
    entity_name: str,
    id_field: str,
    gcs_uri: str | None = None,
    source_org_key: str | None = None,
    source_org_id: str | None = None,
    source_org_name: str | None = None,
    source_country: str | None = None,
    source_currency: str | None = None,
) -> int:
    """
    Loads raw API records into a BigQuery Raw layer table.
    Each source record is stored as one row with full raw_json.
    """

    if not records:
        print(f"No records found for {entity_name}. Skipping BigQuery load.")
        return 0

    client = bigquery.Client(project=project_id)

    loaded_at = datetime.now(timezone.utc)

    rows = []

    generic_raw_metadata = {
        "source_org_key": source_org_key,
        "source_org_id": source_org_id,
        "source_org_name": source_org_name,
        "source_country": source_country,
        "source_currency": source_currency,
        "gcs_uri": gcs_uri,
    }

    for record in records:
        row = {
            "run_id": run_id,
            "source_system": source_system,
            "entity_name": entity_name,
            "source_record_id": str(record.get(id_field, "")),
            "raw_json": json.dumps(record, default=str),
            "loaded_at": loaded_at,
        }
        if table_id == "zoho_raw":
            row.update(generic_raw_metadata)
        rows.append(row)

    df = pd.DataFrame(rows)

    table_ref = f"{project_id}.{dataset_id}.{table_id}"

    job = client.load_table_from_dataframe(df, table_ref)
    job.result()

    print(f"Loaded {len(df)} rows into {table_ref}")
    return len(df)
