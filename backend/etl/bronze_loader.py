"""Helpers for loading raw source records into BigQuery bronze tables."""

from __future__ import annotations

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
    """Load raw API records into a generic BigQuery bronze table.

    The bronze layer keeps one row per source record and stores the complete
    source payload in ``raw_json``. This makes the loader reusable for many
    entities because it does not need to know the business columns in advance.
    """

    if not records:
        print(f"No records found for {entity_name}. Skipping BigQuery load.")
        return 0

    client = bigquery.Client(project=project_id)
    loaded_at = datetime.now(timezone.utc)
    rows = []

    for record in records:
        row = {
            "run_id": run_id,
            "source_system": source_system,
            "entity_name": entity_name,
            "source_record_id": str(record.get(id_field, "")),
            "source_org_key": source_org_key,
            "source_org_id": source_org_id,
            "source_org_name": source_org_name,
            "source_country": source_country,
            "source_currency": source_currency,
            "raw_json": json.dumps(record, default=str),
            "loaded_at": loaded_at,
        }

        # The GCS URI is optional so this loader can still write to older
        # bronze tables that do not have a gcs_uri column.
        if gcs_uri:
            row["gcs_uri"] = gcs_uri

        rows.append(row)

    df = pd.DataFrame(rows)
    table_ref = f"{project_id}.{dataset_id}.{table_id}"

    job = client.load_table_from_dataframe(df, table_ref)
    job.result()

    print(f"Loaded {len(df)} rows into {table_ref}")
    return len(df)

