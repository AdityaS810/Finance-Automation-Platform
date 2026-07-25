"""Phase 2 tests for canonical Zoho bank transactions and journal labels."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from backend.config.zoho_entities import DEFAULT_ZOHO_ENTITY_NAMES, ZOHO_ENTITY_CONFIGS
from backend.scripts import run_zoho_cloud_sync as backend_cloud_sync
from backend.scripts.debug.validate_bank_transactions_live import summarize_bank_transactions
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
    "bank_transaction_cloud_extractor",
    "cloud_function/backend/zoho/extract_zoho.py",
)
cloud_sync = _load_module(
    "bank_transaction_cloud_sync",
    "cloud_function/backend/scripts/run_zoho_cloud_sync.py",
)


def test_bank_transactions_entity_is_canonical_and_enabled():
    entity = ZOHO_ENTITY_CONFIGS["bank_transactions"]

    assert entity.name == "bank_transactions"
    assert entity.endpoint == "banktransactions"
    assert entity.response_key == "banktransactions"
    assert entity.id_field == "transaction_id"
    assert "bank_transactions" in DEFAULT_ZOHO_ENTITY_NAMES


def test_bank_transactions_paginate_and_preserve_optional_fields(monkeypatch):
    calls = []

    def fake_get(endpoint, params=None, organization_id=None):
        calls.append((endpoint, params, organization_id))
        page = params["page"]
        record = {"transaction_id": f"transaction-{page}", "amount": 25}
        if page == 1:
            record["debit_or_credit"] = "debit"
        return {
            "banktransactions": [record],
            "page_context": {"has_more_page": page == 1},
        }

    monkeypatch.setattr(extract_zoho, "zoho_get", fake_get)

    records = extract_zoho.fetch_bank_transactions(organization_id="india-org")

    assert records == [
        {"transaction_id": "transaction-1", "amount": 25, "debit_or_credit": "debit"},
        {"transaction_id": "transaction-2", "amount": 25},
    ]
    assert [call[1] for call in calls] == [
        {"page": 1, "per_page": 200},
        {"page": 2, "per_page": 200},
    ]
    assert all(call[2] == "india-org" for call in calls)


def test_india_and_us_handling_including_empty_collection(monkeypatch):
    def fake_get(endpoint, params=None, organization_id=None):
        records = (
            [{"transaction_id": "transaction-1", "amount": 1, "debit_or_credit": "credit"}]
            if organization_id == "india-org"
            else []
        )
        return {"banktransactions": records, "page_context": {"has_more_page": False}}

    monkeypatch.setattr(extract_zoho, "zoho_get", fake_get)

    assert len(extract_zoho.fetch_bank_transactions(organization_id="india-org")) == 1
    assert extract_zoho.fetch_bank_transactions(organization_id="us-org") == []


def test_missing_bank_collection_fails_clearly(monkeypatch):
    monkeypatch.setattr(
        extract_zoho,
        "zoho_get",
        lambda *args, **kwargs: {"code": 0, "page_context": {"has_more_page": False}},
    )

    with pytest.raises(RuntimeError, match="expected 'banktransactions' collection"):
        extract_zoho.fetch_bank_transactions(organization_id="india-org")


def test_missing_transaction_id_is_preserved_for_silver_quality_flag(monkeypatch):
    monkeypatch.setattr(
        extract_zoho,
        "zoho_get",
        lambda *args, **kwargs: {
            "banktransactions": [{"amount": 10, "debit_or_credit": "debit"}],
            "page_context": {"has_more_page": False},
        },
    )

    records = extract_zoho.fetch_bank_transactions(organization_id="india-org")

    assert records == [{"amount": 10, "debit_or_credit": "debit"}]
    assert "transaction_id" not in records[0]


def test_direction_summary_handles_debit_credit_and_unknown():
    summary = summarize_bank_transactions(
        [
            {"date": "2026-01-01", "amount": 100, "debit_or_credit": "debit"},
            {"date": "2026-01-02", "amount": -40, "debit_or_credit": "credit"},
            {"date": "2026-01-03", "amount": 20},
        ]
    )

    assert summary.transaction_count == 3
    assert summary.debit_count == 1
    assert summary.credit_count == 1
    assert summary.unknown_direction_count == 1
    assert summary.total_debit_amount == Decimal("100")
    assert summary.total_credit_amount == Decimal("40")
    assert str(summary.earliest_date) == "2026-01-01"
    assert str(summary.latest_date) == "2026-01-03"


def test_backend_loader_uses_bank_entity_name_and_transaction_id(monkeypatch):
    captured = {}
    entity = ZOHO_ENTITY_CONFIGS["bank_transactions"]

    def fake_loader(**kwargs):
        captured.update(kwargs)
        return len(kwargs["records"])

    monkeypatch.setattr(backend_cloud_sync, "load_raw_records_to_bigquery", fake_loader)
    loaded = backend_cloud_sync.load_entity_records_to_bronze(
        project_id="project-1",
        entity=entity,
        records=[{"transaction_id": "transaction-1", "amount": 10}],
        run_id="run-1",
        gcs_uri="gs://bucket/bank-transactions.json",
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
    assert captured["entity_name"] == "bank_transactions"
    assert captured["id_field"] == "transaction_id"
    assert captured["source_org_key"] == "india"
    assert captured["gcs_uri"] == "gs://bucket/bank-transactions.json"


def test_cloud_routes_bank_and_journals_to_canonical_labels(monkeypatch):
    captured = []
    entities = {entity["name"]: entity for entity in cloud_sync.get_entity_configs()}

    def fake_loader(**kwargs):
        captured.append(kwargs)
        return len(kwargs["records"])

    monkeypatch.setattr(cloud_sync, "load_raw_records_to_bigquery", fake_loader)
    cloud_sync.load_entity_records_to_bronze(
        "project-1",
        entities["bank_transactions"],
        [{"transaction_id": "transaction-1"}],
        "run-1",
        "gs://bucket/bank-transactions.json",
        {"source_org_key": "india", "source_org_id": "india-org"},
    )
    cloud_sync.load_entity_records_to_bronze(
        "project-1",
        entities["journals"],
        [{"journal_id": "journal-1"}],
        "run-1",
        "gs://bucket/journals.json",
        {"source_org_key": "india", "source_org_id": "india-org"},
    )

    assert entities["bank_transactions"]["bq_table"] == "zoho_raw"
    assert entities["bank_transactions"]["id_field"] == "transaction_id"
    assert entities["bank_transactions"]["fetch_func"].__name__ == "fetch_bank_transactions"
    assert entities["journals"]["bq_table"] == "zoho_raw"
    assert entities["journals"]["fetch_func"].__name__ == "fetch_journals"
    assert captured[0]["entity_name"] == "bank_transactions"
    assert captured[1]["entity_name"] == "journals"
    assert all(call["table_id"] == "zoho_raw" for call in captured)
    assert "transactions" not in entities


def test_cloud_extractor_paginates_bank_transactions(monkeypatch):
    calls = []

    def fake_get(endpoint, params=None):
        calls.append((endpoint, params))
        return {
            "banktransactions": [{"transaction_id": "transaction-1"}],
            "page_context": {"has_more_page": False},
        }

    monkeypatch.setattr(cloud_extractor, "zoho_get", fake_get)

    assert cloud_extractor.fetch_bank_transactions() == [{"transaction_id": "transaction-1"}]
    assert calls == [("banktransactions", {"page": 1, "per_page": 200})]


def test_silver_bank_sql_uses_explicit_direction_and_deterministic_deduplication():
    sql = SILVER_SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE VIEW `finance_silver.fact_bank_transactions`" in sql
    assert "WHERE raw.entity_name = 'bank_transactions'" in sql
    assert "PARTITION BY source_org_id, transaction_id" in sql
    assert "ORDER BY loaded_at DESC, run_id DESC, TO_HEX(SHA256(raw_json)) DESC" in sql
    assert "WHEN debit_or_credit = 'debit' THEN -ABS(transaction_amount)" in sql
    assert "WHEN debit_or_credit = 'credit' THEN ABS(transaction_amount)" in sql
    assert "ELSE CAST(NULL AS NUMERIC)" in sql
    assert "THEN 'Outgoing'" in sql
    assert "THEN 'Incoming'" in sql
    assert "ELSE 'Unknown'" in sql
    assert "Missing transaction_id" in sql
    assert "Missing or unsupported debit_or_credit" in sql


def test_legacy_transactions_remain_backward_compatibility_only():
    sql = SILVER_SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE VIEW `finance_silver.fact_transactions`" in sql
    assert 'Historical Cloud Function runs labelled' in sql
    assert "WHERE entity_name = 'transactions'" in sql
