"""Helpers for selecting uploaded files for reconciliation.

Historical uploads stay in BigQuery for audit. Reconciliation uses one selected
upload at a time so old samples cannot mix with the current file.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery


DEFAULT_PROJECT_ID = "internal-project-work-497507"
UPLOADS_TABLE = "finance_bronze.file_uploads"


load_dotenv()


def get_project_id(project_id: str | None = None) -> str:
    """Resolve the BigQuery project id without requiring UI changes."""
    return project_id or os.getenv("GCP_PROJECT_ID") or DEFAULT_PROJECT_ID


def table_name(project_id: str, table_or_view_name: str) -> str:
    """Build a fully qualified BigQuery table or view name."""
    return f"`{project_id}.{table_or_view_name}`"


def query_to_dataframe(
    client: bigquery.Client,
    query: str,
    parameters: list[bigquery.ScalarQueryParameter] | None = None,
) -> pd.DataFrame:
    """Run a BigQuery query and convert rows to pandas without optional extras."""
    job_config = None
    if parameters:
        job_config = bigquery.QueryJobConfig(query_parameters=parameters)

    result = client.query(query, job_config=job_config).result()
    columns = [field.name for field in result.schema]
    rows = [dict(row.items()) for row in result]
    return pd.DataFrame(rows, columns=columns)


def fetch_reconciliation_uploads(source_type: str, project_id: str | None = None) -> pd.DataFrame:
    """Return successful uploads for one source type, latest first."""
    resolved_project_id = get_project_id(project_id)
    client = bigquery.Client(project=resolved_project_id)
    query = f"""
        SELECT
            upload_id,
            original_file_name AS file_name,
            uploaded_at,
            file_type AS source_type,
            records_parsed AS row_count,
            gcs_raw_path
        FROM {table_name(resolved_project_id, UPLOADS_TABLE)}
        WHERE file_type = @source_type
          AND COALESCE(parse_status, 'success') = 'success'
        QUALIFY ROW_NUMBER() OVER (PARTITION BY upload_id ORDER BY uploaded_at DESC) = 1
        ORDER BY uploaded_at DESC, upload_id DESC
    """
    return query_to_dataframe(
        client,
        query,
        [bigquery.ScalarQueryParameter("source_type", "STRING", source_type)],
    )


def get_upload_metadata(
    source_type: str,
    upload_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    """Return selected upload metadata, defaulting to the latest upload."""
    uploads_df = fetch_reconciliation_uploads(source_type, project_id)
    if uploads_df.empty:
        return None

    if upload_id:
        selected_rows = uploads_df[uploads_df["upload_id"].astype(str) == str(upload_id)]
        if selected_rows.empty:
            return None
        return selected_rows.iloc[0].to_dict()

    return uploads_df.iloc[0].to_dict()
