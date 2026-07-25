"""Phase 1 tests for vendor-payment ingestion and bill allocations."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from backend.config.zoho_entities import DEFAULT_ZOHO_ENTITY_NAMES, ZOHO_ENTITY_CONFIGS
from backend.scripts import run_zoho_cloud_sync as backend_cloud_sync
from backend.scripts.debug.validate_vendor_payments_live import summarize_vendor_payments
from backend.zoho import extract_zoho


REPO_ROOT = Path(__file__).resolve().parents[2]
SILVER_SQL_PATH = REPO_ROOT / "sql" / "ddl" / "create_silver_gold_views.sql"


def _load_module(module_name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cloud_extractor = _load_module(
    "vendor_payment_cloud_extractor",
    "cloud_function/backend/zoho/extract_zoho.py",
)
cloud_sync = _load_module(
    "vendor_payment_cloud_sync",
    "cloud_function/backend/scripts/run_zoho_cloud_sync.py",
)


def test_vendor_payments_entity_is_canonical_and_enabled():
    entity = ZOHO_ENTITY_CONFIGS["vendor_payments"]

    assert entity.name == "vendor_payments"
    assert entity.endpoint == "vendorpayments"
    assert entity.response_key == "vendorpayments"
    assert entity.id_field == "payment_id"
    assert "vendor_payments" in DEFAULT_ZOHO_ENTITY_NAMES


def test_vendor_payment_list_paginates_with_expected_collection(monkeypatch):
    calls = []

    def fake_get(endpoint, params=None, organization_id=None):
        calls.append((endpoint, params, organization_id))
        page = params["page"]
        return {
            "vendorpayments": [{"payment_id": f"payment-{page}"}],
            "page_context": {"has_more_page": page == 1},
        }

    monkeypatch.setattr(extract_zoho, "zoho_get", fake_get)

    records = extract_zoho.fetch_paginated(
        "vendorpayments",
        "vendorpayments",
        organization_id="india-org",
        require_response_key=True,
    )

    assert records == [{"payment_id": "payment-1"}, {"payment_id": "payment-2"}]
    assert [call[1] for call in calls] == [
        {"page": 1, "per_page": 200},
        {"page": 2, "per_page": 200},
    ]
    assert all(call[2] == "india-org" for call in calls)


def test_vendor_payment_detail_expansion_preserves_bills_and_organization(monkeypatch):
    calls = []

    def fake_get(endpoint, params=None, organization_id=None):
        calls.append((endpoint, organization_id))
        if endpoint == "vendorpayments":
            return {
                "vendorpayments": [{"payment_id": "payment-1"}],
                "page_context": {"has_more_page": False},
            }
        assert endpoint == "vendorpayments/payment-1"
        return {
            "vendorpayment": {
                "payment_id": "payment-1",
                "amount": 100,
                "bills": [
                    {"bill_payment_id": "allocation-1", "bill_id": "bill-1", "amount_applied": 60},
                    {"bill_payment_id": "allocation-2", "bill_id": "bill-2", "amount_applied": 40},
                ],
                "custom_fields": [{"label": "Preserved", "value": "yes"}],
            }
        }

    monkeypatch.setattr(extract_zoho, "zoho_get", fake_get)

    records = extract_zoho.fetch_vendor_payments(organization_id="india-org")

    assert len(records) == 1
    assert len(records[0]["bills"]) == 2
    assert records[0]["custom_fields"] == [{"label": "Preserved", "value": "yes"}]
    assert calls == [
        ("vendorpayments", "india-org"),
        ("vendorpayments/payment-1", "india-org"),
    ]


def test_india_and_us_organizations_handle_records_and_empty_collection(monkeypatch):
    def fake_get(endpoint, params=None, organization_id=None):
        if endpoint == "vendorpayments":
            records = [{"payment_id": "payment-1"}] if organization_id == "india-org" else []
            return {"vendorpayments": records, "page_context": {"has_more_page": False}}
        return {"vendorpayment": {"payment_id": "payment-1", "amount": 25, "bills": []}}

    monkeypatch.setattr(extract_zoho, "zoho_get", fake_get)

    assert len(extract_zoho.fetch_vendor_payments(organization_id="india-org")) == 1
    assert extract_zoho.fetch_vendor_payments(organization_id="us-org") == []


def test_vendor_payment_missing_list_collection_fails_clearly(monkeypatch):
    monkeypatch.setattr(
        extract_zoho,
        "zoho_get",
        lambda *args, **kwargs: {"code": 0, "page_context": {"has_more_page": False}},
    )

    with pytest.raises(RuntimeError, match="expected 'vendorpayments' collection"):
        extract_zoho.fetch_vendor_payments(organization_id="india-org")


def test_vendor_payment_detail_failure_does_not_fall_back_to_list_record(monkeypatch):
    def fake_get(endpoint, params=None, organization_id=None):
        if endpoint == "vendorpayments":
            return {
                "vendorpayments": [{"payment_id": "payment-1", "amount": 100}],
                "page_context": {"has_more_page": False},
            }
        raise RuntimeError("private upstream error")

    monkeypatch.setattr(extract_zoho, "zoho_get", fake_get)

    with pytest.raises(RuntimeError, match="detail request failed at list position 1"):
        extract_zoho.fetch_vendor_payments(organization_id="india-org")


def test_cloud_function_expands_details_and_handles_empty_collection(monkeypatch):
    def fake_get(endpoint, params=None):
        if endpoint == "vendorpayments":
            return {
                "vendorpayments": [{"payment_id": "payment-1"}],
                "page_context": {"has_more_page": False},
            }
        return {
            "vendorpayment": {
                "payment_id": "payment-1",
                "amount": 50,
                "bills": [{"bill_payment_id": "allocation-1", "bill_id": "bill-1", "amount_applied": 50}],
            }
        }

    monkeypatch.setattr(cloud_extractor, "zoho_get", fake_get)
    assert len(cloud_extractor.fetch_vendor_payments()) == 1

    monkeypatch.setattr(
        cloud_extractor,
        "zoho_get",
        lambda *args, **kwargs: {
            "vendorpayments": [],
            "page_context": {"has_more_page": False},
        },
    )
    assert cloud_extractor.fetch_vendor_payments() == []


def test_backend_canonical_loader_uses_entity_name_and_payment_id(monkeypatch):
    captured = {}
    entity = ZOHO_ENTITY_CONFIGS["vendor_payments"]

    def fake_loader(**kwargs):
        captured.update(kwargs)
        return len(kwargs["records"])

    monkeypatch.setattr(backend_cloud_sync, "load_raw_records_to_bigquery", fake_loader)
    loaded = backend_cloud_sync.load_entity_records_to_bronze(
        project_id="project-1",
        entity=entity,
        records=[{"payment_id": "payment-1", "bills": []}],
        run_id="run-1",
        gcs_uri="gs://bucket/vendor-payments.json",
        organization={
            "org_key": "india",
            "organization_id": "india-org",
            "organization_name": "India Organization",
            "country": "India",
            "base_currency": "INR",
        },
    )

    assert loaded == 1
    assert captured["table_id"] == "zoho_raw"
    assert captured["entity_name"] == "vendor_payments"
    assert captured["id_field"] == "payment_id"
    assert captured["source_org_key"] == "india"
    assert captured["gcs_uri"] == "gs://bucket/vendor-payments.json"


def test_cloud_function_routes_vendor_payments_to_canonical_raw(monkeypatch):
    captured = {}
    entities = {entity["name"]: entity for entity in cloud_sync.get_entity_configs()}
    vendor_payment_entity = entities["vendor_payments"]

    def fake_loader(**kwargs):
        captured.update(kwargs)
        return len(kwargs["records"])

    monkeypatch.setattr(cloud_sync, "load_raw_records_to_bigquery", fake_loader)
    loaded = cloud_sync.load_entity_records_to_bronze(
        "project-1",
        vendor_payment_entity,
        [{"payment_id": "payment-1", "bills": []}],
        "run-1",
        "gs://bucket/vendor-payments.json",
        {"source_org_key": "india", "source_org_id": "india-org"},
    )

    assert loaded == 1
    assert vendor_payment_entity["bq_table"] == "zoho_raw"
    assert vendor_payment_entity["id_field"] == "payment_id"
    assert captured["entity_name"] == "vendor_payments"
    assert captured["source_org_key"] == "india"
    assert entities["journals"]["fetch_func"].__name__ == "fetch_journals"
    assert "transactions" not in entities
    assert vendor_payment_entity["fetch_func"].__name__ == "fetch_vendor_payments"


def test_summary_supports_multiple_bills_partial_allocation_and_advance():
    summary = summarize_vendor_payments(
        [
            {
                "amount": 100,
                "bills": [
                    {"bill_payment_id": "allocation-1", "bill_id": "bill-1", "amount_applied": 25},
                    {"bill_payment_id": "allocation-2", "bill_id": "bill-2", "amount_applied": 35},
                ],
            },
            {"amount": 40, "bills": []},
        ]
    )

    assert summary.payment_count == 2
    assert summary.allocation_count == 2
    assert summary.total_payment_amount == Decimal("140")
    assert summary.total_allocated_amount == Decimal("60")
    assert summary.total_unapplied_amount == Decimal("80")


def test_summary_tracks_missing_bills_array_without_creating_allocations():
    summary = summarize_vendor_payments([{"amount": 25}])

    assert summary.payment_count == 1
    assert summary.payments_with_bills_array == 0
    assert summary.allocation_count == 0
    assert summary.missing_bills_array_count == 1
    assert summary.total_unapplied_amount == Decimal("25")


def test_silver_sql_deduplicates_snapshots_and_defines_allocation_quality_fields():
    sql = SILVER_SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE VIEW `finance_silver.fact_vendor_payments`" in sql
    assert "WHERE raw.entity_name = 'vendor_payments'" in sql
    assert "PARTITION BY source_org_id, payment_id" in sql
    assert "ORDER BY loaded_at DESC, run_id DESC, TO_HEX(SHA256(raw_json)) DESC" in sql
    assert "CREATE OR REPLACE VIEW `finance_silver.bridge_vendor_payment_bill_allocations`" in sql
    assert "JSON_VALUE(allocation, '$.bill_payment_id')" in sql
    assert "JSON_VALUE(allocation, '$.amount_applied')" in sql
    assert "Allocation Exceeds Payment" in sql
    assert "Partially Allocated" in sql
    assert "Unallocated" in sql
    assert "Needs Review" in sql
