"""Track ETL run status in BigQuery."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery


DEFAULT_ETL_RUNS_TABLE_ID = "etl_runs"
RUNNING_STATUS = "running"
SUCCESS_STATUS = "success"
FAILED_STATUS = "failed"
UNKNOWN_SOURCE_SYSTEM = "unknown"


def _utc_now() -> datetime:
    """Return one timezone-aware UTC timestamp for audit columns."""
    return datetime.now(timezone.utc)


def _table_ref(project_id: str, dataset_id: str, table_id: str) -> str:
    """Build a BigQuery table reference from separate names."""
    return f"{project_id}.{dataset_id}.{table_id}"


def _metadata_to_json(metadata: dict[str, Any] | None) -> str | None:
    """Convert metadata into a JSON string BigQuery can store."""
    if metadata is None:
        return None
    return json.dumps(metadata, default=str)


def _timestamp_to_json(value: Any) -> str | None:
    """Convert BigQuery timestamp values into JSON-friendly strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _insert_run_row(
    client: bigquery.Client,
    table_ref: str,
    run_id: str,
    row: dict[str, Any],
    action_name: str,
) -> None:
    """Append one status row to etl_runs and surface insert errors."""
    errors = client.insert_rows_json(table_ref, [row])
    if errors:
        raise RuntimeError(f"Could not {action_name} ETL run {run_id}: {errors}")


def _get_latest_run_context(
    client: bigquery.Client,
    project_id: str,
    dataset_id: str,
    table_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Read the latest known values for a run without mutating BigQuery rows."""

    table = _table_ref(project_id, dataset_id, table_id)
    query = f"""
        SELECT
            source_system,
            started_at,
            records_loaded,
            metadata_json,
            triggered_by
        FROM `{table}`
        WHERE run_id = @run_id
        ORDER BY created_at DESC
        LIMIT 1
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
        ]
    )

    rows = list(client.query(query, job_config=job_config).result())
    if not rows:
        return {}

    latest_row = rows[0]
    return {
        "source_system": latest_row.source_system,
        "started_at": latest_row.started_at,
        "records_loaded": latest_row.records_loaded,
        "metadata_json": latest_row.metadata_json,
        "triggered_by": latest_row.triggered_by,
    }


def create_etl_run(
    project_id: str,
    dataset_id: str,
    run_id: str,
    source_system: str,
    triggered_by: str = "manual",
    metadata: dict[str, Any] | None = None,
    table_id: str = DEFAULT_ETL_RUNS_TABLE_ID,
) -> None:
    """Append the first row for a new ETL run."""

    client = bigquery.Client(project=project_id)
    now = _utc_now()
    table = _table_ref(project_id, dataset_id, table_id)

    row = {
        "run_id": run_id,
        "source_system": source_system,
        "status": RUNNING_STATUS,
        "started_at": now.isoformat(),
        "completed_at": None,
        "records_loaded": 0,
        "error_message": None,
        "metadata_json": _metadata_to_json(metadata),
        "triggered_by": triggered_by,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    _insert_run_row(client, table, run_id, row, "create")


def update_etl_run(
    project_id: str,
    dataset_id: str,
    run_id: str,
    status: str = SUCCESS_STATUS,
    records_loaded: int | None = None,
    metadata: dict[str, Any] | None = None,
    table_id: str = DEFAULT_ETL_RUNS_TABLE_ID,
) -> None:
    """Append a status row after progress or successful completion."""

    client = bigquery.Client(project=project_id)
    now = _utc_now()
    table = _table_ref(project_id, dataset_id, table_id)
    context = _get_latest_run_context(client, project_id, dataset_id, table_id, run_id)
    metadata_json = _metadata_to_json(metadata) if metadata is not None else context.get("metadata_json")
    loaded_count = records_loaded if records_loaded is not None else context.get("records_loaded", 0)

    row = {
        "run_id": run_id,
        "source_system": context.get("source_system") or UNKNOWN_SOURCE_SYSTEM,
        "status": status,
        "started_at": _timestamp_to_json(context.get("started_at")) or now.isoformat(),
        "completed_at": now.isoformat(),
        "records_loaded": loaded_count,
        "error_message": None,
        "metadata_json": metadata_json,
        "triggered_by": context.get("triggered_by"),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    _insert_run_row(client, table, run_id, row, "append status for")


def fail_etl_run(
    project_id: str,
    dataset_id: str,
    run_id: str,
    error: Exception | str,
    records_loaded: int | None = None,
    table_id: str = DEFAULT_ETL_RUNS_TABLE_ID,
) -> None:
    """Append a failed status row and save the error message."""

    client = bigquery.Client(project=project_id)
    now = _utc_now()
    table = _table_ref(project_id, dataset_id, table_id)
    context = _get_latest_run_context(client, project_id, dataset_id, table_id, run_id)
    loaded_count = records_loaded if records_loaded is not None else context.get("records_loaded", 0)

    row = {
        "run_id": run_id,
        "source_system": context.get("source_system") or UNKNOWN_SOURCE_SYSTEM,
        "status": FAILED_STATUS,
        "started_at": _timestamp_to_json(context.get("started_at")) or now.isoformat(),
        "completed_at": now.isoformat(),
        "records_loaded": loaded_count,
        "error_message": str(error),
        "metadata_json": context.get("metadata_json"),
        "triggered_by": context.get("triggered_by"),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    _insert_run_row(client, table, run_id, row, "append failure for")
