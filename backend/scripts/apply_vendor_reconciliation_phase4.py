"""Validate or execute only the three authorized Phase 4 Gold views.

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
MIGRATION_PATH = REPO_ROOT / "sql" / "migrations" / "phase4_vendor_reconciliation.sql"
EXPECTED_VIEWS = (
    "finance_gold.vendor_payment_bank_matches",
    "finance_gold.vendor_bill_reconciliation",
    "finance_gold.vendor_reconciliation_exceptions",
)
VIEW_PATTERN = re.compile(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+`([^`]+)`", re.IGNORECASE)


def read_validated_statements(
    path: Path = MIGRATION_PATH,
) -> list[tuple[str, str]]:
    """Return exactly the three authorized CREATE VIEW statements."""
    sql = path.read_text(encoding="utf-8")
    matches = list(VIEW_PATTERN.finditer(sql))
    statements = [
        sql[match.start() : (matches[index + 1].start() if index + 1 < len(matches) else len(sql))]
        .strip()
        .removesuffix(";")
        for index, match in enumerate(matches)
    ]
    validated = []
    for statement in statements:
        match = VIEW_PATTERN.search(statement)
        if not match:
            raise ValueError("Migration contains a non-CREATE VIEW statement")
        validated.append((match.group(1), statement))
    discovered = tuple(view_name for view_name, _ in validated)
    if discovered != EXPECTED_VIEWS:
        raise ValueError(
            "Migration view scope mismatch: expected "
            + ", ".join(EXPECTED_VIEWS)
            + "; found "
            + ", ".join(discovered)
        )
    return validated


def apply_migration(*, execute: bool = False) -> list[str]:
    """Validate the isolated migration and optionally execute it in order."""
    statements = read_validated_statements()
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
        help="Apply only the three authorized Gold views.",
    )
    args = parser.parse_args()
    apply_migration(execute=args.execute)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
