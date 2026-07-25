"""Phase 3 controlled backfill, migration, and validation tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.config.zoho_entities import ZOHO_ENTITY_CONFIGS
from backend.scripts import apply_vendor_reconciliation_silver as silver_migration
from backend.scripts import run_vendor_reconciliation_backfill as backfill
from backend.scripts.validate_vendor_reconciliation_warehouse import (
    VALIDATION_QUERIES,
    count_duplicate_ids,
    validate_allocation_relationships,
)


ORGANIZATIONS = [
    {"org_key": "india", "organization_id": "india-id"},
    {"org_key": "us", "organization_id": "us-id"},
]


def test_dry_run_performs_no_writes_and_fetches_only_selected_entities(monkeypatch):
    calls = []
    monkeypatch.setattr(backfill, "get_zoho_organizations", lambda: ORGANIZATIONS)
    monkeypatch.setattr(
        backfill,
        "fetch_entity_records",
        lambda entity, organization_id: calls.append((entity.name, organization_id)) or [],
    )
    for write_name in (
        "create_etl_run",
        "upload_json_to_gcs",
        "load_entity_records_to_bronze",
        "update_etl_run",
        "fail_etl_run",
    ):
        monkeypatch.setattr(
            backfill,
            write_name,
            lambda *args, _name=write_name, **kwargs: pytest.fail(f"{_name} must not be called"),
        )

    result = backfill.run_controlled_backfill("all", "both")

    assert result["mode"] == "dry_run"
    assert result["records_loaded"] == 0
    assert calls == [
        ("vendor_payments", "india-id"),
        ("bank_transactions", "india-id"),
        ("vendor_payments", "us-id"),
        ("bank_transactions", "us-id"),
    ]
    assert all(name not in {"contacts", "bills", "invoices", "journals", "expenses", "customer_payments"} for name, _ in calls)


@pytest.mark.parametrize(
    ("selection", "expected"),
    [("india", ["india"]), ("us", ["us"]), ("all", ["india", "us"])],
)
def test_organization_selection(monkeypatch, selection, expected):
    monkeypatch.setattr(backfill, "get_zoho_organizations", lambda: ORGANIZATIONS)
    assert [row["org_key"] for row in backfill.select_organizations(selection)] == expected


def test_entity_selection_uses_only_canonical_names():
    assert [entity.name for entity in backfill.select_entities("both")] == [
        "vendor_payments",
        "bank_transactions",
    ]
    assert ZOHO_ENTITY_CONFIGS["vendor_payments"].id_field == "payment_id"
    assert ZOHO_ENTITY_CONFIGS["bank_transactions"].id_field == "transaction_id"


def test_execute_preserves_zero_record_us_vendor_payment_and_is_rerunnable(monkeypatch):
    writes = []
    monkeypatch.setattr(backfill, "get_zoho_organizations", lambda: ORGANIZATIONS)
    monkeypatch.setattr(backfill, "_require_execute_environment", lambda: ("project", "bucket"))
    monkeypatch.setattr(backfill, "create_etl_run", lambda **kwargs: writes.append(("run", kwargs["run_id"])))
    monkeypatch.setattr(
        backfill,
        "fetch_entity_records",
        lambda entity, organization_id: (
            [] if entity.name == "vendor_payments" and organization_id == "us-id"
            else [{entity.id_field: f"{organization_id}-{entity.name}"}]
        ),
    )
    monkeypatch.setattr(backfill, "upload_json_to_gcs", lambda **kwargs: "gs://bucket/safe.json")
    monkeypatch.setattr(
        backfill,
        "load_entity_records_to_bronze",
        lambda **kwargs: writes.append((kwargs["entity"].name, len(kwargs["records"]))) or len(kwargs["records"]),
    )
    monkeypatch.setattr(backfill, "update_etl_run", lambda **kwargs: writes.append(("complete", kwargs["records_loaded"])))

    first = backfill.run_controlled_backfill("all", "both", execute=True)
    second = backfill.run_controlled_backfill("all", "both", execute=True)

    assert first["records_loaded"] == 3
    assert second["records_loaded"] == 3
    assert first["run_id"] != second["run_id"]
    us_vendor = [
        row for row in first["results"]
        if row["organization_key"] == "us" and row["entity_name"] == "vendor_payments"
    ]
    assert us_vendor == [{
        "organization_key": "us",
        "entity_name": "vendor_payments",
        "fetched_records": 0,
        "loaded_records": 0,
        "gcs_uri": "gs://bucket/safe.json",
    }]


def test_current_run_duplicate_detection():
    records = [
        {"payment_id": "one"},
        {"payment_id": "one"},
        {"payment_id": "two"},
        {"payment_id": ""},
        {},
    ]
    assert count_duplicate_ids(records, "payment_id") == 1
    assert "WHERE run_id = @run_id" in VALIDATION_QUERIES["bronze"]


def test_silver_migration_is_scoped_and_rerun_safe():
    statements = silver_migration.read_validated_statements()
    assert [name for name, _ in statements] == list(silver_migration.EXPECTED_VIEWS)
    assert len(statements) == 3
    assert all("CREATE OR REPLACE VIEW" in sql for _, sql in statements)
    assert "ROW_NUMBER() OVER" in statements[0][1]
    assert "PARTITION BY source_org_id, payment_id" in statements[0][1]
    assert "PARTITION BY bank_transaction_leg_key" in statements[2][1]


def test_phase_35_can_select_only_bank_view_without_writing():
    selected = silver_migration.apply_migration(
        selected_view="finance_silver.fact_bank_transactions",
    )

    assert selected == ["finance_silver.fact_bank_transactions"]


def test_allocation_cross_source_validation():
    payments = [
        {"source_org_id": "india", "payment_id": "p1", "payment_amount": Decimal("100")},
        {"source_org_id": "us", "payment_id": "p2", "payment_amount": Decimal("10")},
    ]
    allocations = [
        {"source_org_id": "india", "payment_id": "p1", "amount_applied": Decimal("60")},
        {"source_org_id": "india", "payment_id": "p1", "amount_applied": Decimal("41")},
        {"source_org_id": "india", "payment_id": "p2", "amount_applied": Decimal("5")},
        {"source_org_id": "india", "payment_id": "missing", "amount_applied": Decimal("1")},
    ]

    result = validate_allocation_relationships(payments, allocations)

    assert result == {
        "orphan_allocation_count": 2,
        "organization_mismatch_count": 1,
        "payments_exceeded_count": 1,
    }
    assert "fact_bills" in VALIDATION_QUERIES["cross_source"]
