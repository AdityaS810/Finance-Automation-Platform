"""Controlled vendor-reconciliation entity sync.

The command is dry-run/read-only unless ``--execute`` is supplied. It reuses
the production extractors, GCS uploader, canonical Bronze loader, organization
configuration, and ETL run tracker.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from backend.config.zoho_entities import (
    BRONZE_DATASET_ID,
    SOURCE_SYSTEM,
    get_zoho_entity_configs,
)
from backend.config.zoho_organizations import get_zoho_organizations
from backend.etl.run_tracker import create_etl_run, fail_etl_run, update_etl_run
from backend.gcp.gcs_loader import upload_json_to_gcs
from backend.scripts.run_zoho_cloud_sync import build_gcs_path, load_entity_records_to_bronze
from backend.zoho.extract_zoho import fetch_entity_records


load_dotenv()

ALLOWED_ENTITY_NAMES = ("vendor_payments", "bank_transactions")
ENTITY_SELECTIONS = {
    "vendor_payments": ("vendor_payments",),
    "bank_transactions": ("bank_transactions",),
    "both": ALLOWED_ENTITY_NAMES,
}
ORGANIZATION_SELECTIONS = ("india", "us", "all")


def select_organizations(selection: str) -> list[dict]:
    """Return only explicitly selected configured organizations."""
    if selection not in ORGANIZATION_SELECTIONS:
        raise ValueError(f"Unsupported organization selection: {selection}")
    organizations = get_zoho_organizations()
    selected = organizations if selection == "all" else [
        organization for organization in organizations if organization["org_key"] == selection
    ]
    if not selected:
        raise ValueError(f"No configured Zoho organization matched: {selection}")
    return selected


def select_entities(selection: str) -> list[Any]:
    """Return only the two Phase 3 canonical entities."""
    try:
        names = list(ENTITY_SELECTIONS[selection])
    except KeyError as error:
        raise ValueError(f"Unsupported entity selection: {selection}") from error
    return get_zoho_entity_configs(names)


def _require_execute_environment() -> tuple[str, str]:
    project_id = os.getenv("GCP_PROJECT_ID") or ""
    bucket_name = os.getenv("GCS_RAW_BUCKET") or ""
    missing = [
        name
        for name, value in (("GCP_PROJECT_ID", project_id), ("GCS_RAW_BUCKET", bucket_name))
        if not value
    ]
    if missing:
        raise RuntimeError("Missing required execute environment variables: " + ", ".join(missing))
    return project_id, bucket_name


def run_controlled_backfill(
    organization_selection: str,
    entity_selection: str,
    *,
    execute: bool = False,
) -> dict[str, Any]:
    """Fetch selected entities and optionally write canonical Raw data."""
    organizations = select_organizations(organization_selection)
    entities = select_entities(entity_selection)
    now = datetime.now(timezone.utc)
    run_id = f"vendor_recon_{now:%Y%m%d_%H%M%S_%f}"
    project_id = ""
    bucket_name = ""
    run_created = False
    total_loaded = 0
    results: list[dict[str, Any]] = []
    metadata = {
        "mode": "execute" if execute else "dry_run",
        "entities": [entity.name for entity in entities],
        "organizations_synced": [organization["org_key"] for organization in organizations],
        "controlled_vendor_reconciliation_backfill": True,
    }

    if execute:
        project_id, bucket_name = _require_execute_environment()
        create_etl_run(
            project_id=project_id,
            dataset_id=BRONZE_DATASET_ID,
            run_id=run_id,
            source_system=SOURCE_SYSTEM,
            triggered_by="controlled_vendor_reconciliation_backfill",
            metadata=metadata,
        )
        run_created = True

    try:
        for organization in organizations:
            org_key = organization["org_key"]
            for entity in entities:
                print(f"Fetching entity={entity.name} organization_key={org_key}")
                try:
                    records = fetch_entity_records(
                        entity,
                        organization_id=organization["organization_id"],
                    )
                except Exception:
                    raise RuntimeError(
                        f"Zoho fetch failed for entity={entity.name} "
                        f"organization_key={org_key}; no response content was logged."
                    ) from None
                result = {
                    "organization_key": org_key,
                    "entity_name": entity.name,
                    "fetched_records": len(records),
                    "loaded_records": 0,
                    "gcs_uri": None,
                }

                if execute:
                    gcs_path = build_gcs_path(
                        org_key=org_key,
                        entity_name=entity.name,
                        run_id=run_id,
                        run_date=now,
                    )
                    gcs_uri = upload_json_to_gcs(
                        bucket_name=bucket_name,
                        destination_blob_name=gcs_path,
                        data=records,
                    )
                    loaded = load_entity_records_to_bronze(
                        project_id=project_id,
                        entity=entity,
                        records=records,
                        run_id=run_id,
                        gcs_uri=gcs_uri,
                        organization=organization,
                    )
                    result["loaded_records"] = loaded
                    result["gcs_uri"] = gcs_uri
                    total_loaded += loaded

                results.append(result)
                print(
                    f"Completed entity={entity.name} organization_key={org_key} "
                    f"fetched_records={len(records)} loaded_records={result['loaded_records']}"
                )

        if execute:
            counts: dict[str, dict[str, int]] = {}
            for result in results:
                counts.setdefault(result["organization_key"], {})[result["entity_name"]] = int(
                    result["loaded_records"]
                )
            update_etl_run(
                project_id=project_id,
                dataset_id=BRONZE_DATASET_ID,
                run_id=run_id,
                records_loaded=total_loaded,
                metadata={
                    **metadata,
                    "entity_record_counts": counts,
                    "records_loaded": total_loaded,
                },
            )
    except Exception as error:
        if execute and run_created:
            try:
                fail_etl_run(
                    project_id=project_id,
                    dataset_id=BRONZE_DATASET_ID,
                    run_id=run_id,
                    error=error,
                    records_loaded=total_loaded,
                )
            except Exception:
                pass
        raise

    return {
        "run_id": run_id,
        "mode": "execute" if execute else "dry_run",
        "results": results,
        "records_loaded": total_loaded,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organization",
        choices=ORGANIZATION_SELECTIONS,
        required=True,
        help="Configured Zoho organization(s) to fetch.",
    )
    parser.add_argument(
        "--entity",
        choices=tuple(ENTITY_SELECTIONS),
        required=True,
        help="Canonical entity selection.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write GCS, Bronze, and ETL tracking rows. Omit for read-only dry-run.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = run_controlled_backfill(
        args.organization,
        args.entity,
        execute=args.execute,
    )
    print(
        f"run_id={result['run_id']} mode={result['mode']} "
        f"records_loaded={result['records_loaded']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
