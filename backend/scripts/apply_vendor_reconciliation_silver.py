"""Validate or execute the isolated Phase 3 Silver migration.

The command is read-only unless ``--execute`` is supplied.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import bigquery


load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "sql" / "migrations" / "phase3_vendor_reconciliation_silver.sql"
EXPECTED_VIEWS = (
    "finance_silver.fact_vendor_payments",
    "finance_silver.bridge_vendor_payment_bill_allocations",
    "finance_silver.fact_bank_transactions",
)
VIEW_PATTERN = re.compile(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+`([^`]+)`", re.IGNORECASE)


def read_validated_statements(path: Path = MIGRATION_PATH) -> list[tuple[str, str]]:
    """Return exactly the three authorized CREATE VIEW statements."""
    sql = path.read_text(encoding="utf-8")
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    discovered = []
    validated = []
    for statement in statements:
        match = VIEW_PATTERN.search(statement)
        if not match:
            raise ValueError("Migration contains a non-CREATE VIEW statement")
        view_name = match.group(1)
        discovered.append(view_name)
        validated.append((view_name, statement))
    if tuple(discovered) != EXPECTED_VIEWS:
        raise ValueError(
            "Migration view scope mismatch: expected "
            + ", ".join(EXPECTED_VIEWS)
            + "; found "
            + ", ".join(discovered)
        )
    return validated


def apply_migration(
    *,
    execute: bool = False,
    selected_view: str | None = None,
) -> list[str]:
    """Validate the migration and optionally apply all or one authorized view."""
    statements = read_validated_statements()
    if selected_view is not None:
        if selected_view not in EXPECTED_VIEWS:
            raise ValueError(f"Unsupported Silver view selection: {selected_view}")
        statements = [
            (view_name, statement)
            for view_name, statement in statements
            if view_name == selected_view
        ]
    view_names = [view_name for view_name, _ in statements]
    if not execute:
        for view_name in view_names:
            print(f"Validated view={view_name}")
        return view_names

    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        raise RuntimeError("GCP_PROJECT_ID is required with --execute")
    location = os.getenv("BQ_LOCATION") or os.getenv("GCP_LOCATION") or "asia-south1"
    client = bigquery.Client(project=project_id)
    for view_name, statement in statements:
        client.query(statement, location=location).result()
        print(f"Applied view={view_name}")
    return view_names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the selected authorized view(s). Omit for read-only validation.",
    )
    parser.add_argument(
        "--view",
        choices=EXPECTED_VIEWS,
        help="Apply or validate only one authorized view.",
    )
    args = parser.parse_args()
    apply_migration(execute=args.execute, selected_view=args.view)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
