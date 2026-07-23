"""Regression tests for canonical transaction routing into generic Bronze."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from backend.etl import bronze_loader
from backend.scripts import run_zoho_cloud_sync as local_sync


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cloud_sync = _load_module(
    "cloud_function_run_zoho_cloud_sync",
    "cloud_function/backend/scripts/run_zoho_cloud_sync.py",
)
cloud_loader = _load_module(
    "cloud_function_bigquery_loader",
    "cloud_function/backend/gcp/bigquery_loader.py",
)


def test_local_transactions_use_generic_bronze_loader_with_org_metadata(monkeypatch):
    captured = {}

    def fake_loader(**kwargs):
        captured.update(kwargs)
        return len(kwargs["records"])

    monkeypatch.setattr(local_sync, "load_raw_records_to_bigquery", fake_loader)
    entity = SimpleNamespace(name="transactions", id_field="journal_id")
    organization = {
        "org_key": "india",
        "organization_id": "org-1",
        "organization_name": "India Organization",
        "country": "India",
        "base_currency": "INR",
    }

    rows_loaded = local_sync.load_entity_records_to_bronze(
        project_id="project-1",
        entity=entity,
        records=[{"journal_id": "txn-1"}],
        run_id="run-1",
        gcs_uri="gs://bucket/transactions.json",
        organization=organization,
    )

    assert rows_loaded == 1
    assert captured["table_id"] == "zoho_raw"
    assert captured["entity_name"] == "transactions"
    assert captured["source_org_key"] == "india"
    assert captured["source_org_id"] == "org-1"
    assert captured["source_org_name"] == "India Organization"
    assert captured["source_country"] == "India"
    assert captured["source_currency"] == "INR"


def test_cloud_transactions_route_to_canonical_generic_bronze(monkeypatch):
    captured = {}

    def fake_loader(**kwargs):
        captured.update(kwargs)
        return len(kwargs["records"])

    monkeypatch.setattr(cloud_sync, "load_raw_records_to_bigquery", fake_loader)
    transaction_entity = next(
        entity for entity in cloud_sync.get_entity_configs() if entity["name"] == "transactions"
    )
    metadata = {
        "source_org_key": "india",
        "source_org_id": "org-1",
        "source_org_name": "India Organization",
        "source_country": "India",
        "source_currency": "INR",
    }

    rows_loaded = cloud_sync.load_entity_records_to_bronze(
        project_id="project-1",
        entity=transaction_entity,
        records=[{"journal_id": "txn-1"}],
        run_id="run-1",
        gcs_uri="gs://bucket/transactions.json",
        organization_metadata=metadata,
    )

    assert rows_loaded == 1
    assert transaction_entity["bq_table"] == "zoho_raw"
    assert captured["table_id"] == "zoho_raw"
    assert captured["entity_name"] == "transactions"
    assert captured["gcs_uri"] == "gs://bucket/transactions.json"
    for key, value in metadata.items():
        assert captured[key] == value


def test_generic_loader_preserves_standard_transaction_fields(monkeypatch):
    captured = {}

    class FakeJob:
        def result(self):
            return None

    class FakeClient:
        def load_table_from_dataframe(self, dataframe, table_ref):
            captured["dataframe"] = dataframe.copy()
            captured["table_ref"] = table_ref
            return FakeJob()

    monkeypatch.setattr(bronze_loader.bigquery, "Client", lambda project: FakeClient())

    rows_loaded = bronze_loader.load_raw_records_to_bigquery(
        project_id="project-1",
        dataset_id="finance_bronze",
        table_id="zoho_raw",
        records=[{"journal_id": "txn-1", "amount": 100}],
        run_id="run-1",
        source_system="zoho_books",
        entity_name="transactions",
        id_field="journal_id",
        gcs_uri="gs://bucket/transactions.json",
        source_org_key="india",
        source_org_id="org-1",
        source_org_name="India Organization",
        source_country="India",
        source_currency="INR",
    )

    row = captured["dataframe"].iloc[0].to_dict()
    assert rows_loaded == 1
    assert captured["table_ref"] == "project-1.finance_bronze.zoho_raw"
    assert row["run_id"] == "run-1"
    assert row["source_system"] == "zoho_books"
    assert row["entity_name"] == "transactions"
    assert row["source_record_id"] == "txn-1"
    assert row["source_org_key"] == "india"
    assert row["source_org_id"] == "org-1"
    assert row["source_org_name"] == "India Organization"
    assert row["source_country"] == "India"
    assert row["source_currency"] == "INR"
    assert row["gcs_uri"] == "gs://bucket/transactions.json"
    assert row["raw_json"]
    assert row["loaded_at"] is not None


def test_cloud_generic_loader_writes_nullable_standard_metadata_columns(monkeypatch):
    captured = {}

    class FakeJob:
        def result(self):
            return None

    class FakeClient:
        def load_table_from_dataframe(self, dataframe, table_ref):
            captured["dataframe"] = dataframe.copy()
            return FakeJob()

    monkeypatch.setattr(cloud_loader.bigquery, "Client", lambda project: FakeClient())
    cloud_loader.load_raw_records_to_bigquery(
        project_id="project-1",
        dataset_id="finance_bronze",
        table_id="zoho_raw",
        records=[{"journal_id": "txn-1"}],
        run_id="run-1",
        source_system="zoho_books",
        entity_name="transactions",
        id_field="journal_id",
        source_org_key="configured_zoho_organization",
        source_org_id="org-1",
    )

    row = captured["dataframe"].iloc[0]
    assert row["entity_name"] == "transactions"
    assert row["source_org_key"] == "configured_zoho_organization"
    assert row["source_org_id"] == "org-1"
    assert "source_org_name" in captured["dataframe"].columns
    assert "source_country" in captured["dataframe"].columns
    assert "source_currency" in captured["dataframe"].columns


def test_empty_transactions_do_not_create_bigquery_clients(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("BigQuery client should not be created for empty records")

    monkeypatch.setattr(bronze_loader.bigquery, "Client", fail_if_called)
    monkeypatch.setattr(cloud_loader.bigquery, "Client", fail_if_called)

    local_count = bronze_loader.load_raw_records_to_bigquery(
        "project-1", "finance_bronze", "zoho_raw", [], "run-1", "zoho_books", "transactions", "journal_id"
    )
    cloud_count = cloud_loader.load_raw_records_to_bigquery(
        "project-1", "finance_bronze", "zoho_raw", [], "run-1", "zoho_books", "transactions", "journal_id"
    )

    assert local_count == 0
    assert cloud_count == 0


def test_other_entity_destinations_remain_unchanged(monkeypatch):
    captured_calls = []

    def fake_loader(**kwargs):
        captured_calls.append(kwargs)
        return len(kwargs["records"])

    monkeypatch.setattr(cloud_sync, "load_raw_records_to_bigquery", fake_loader)
    entities = {entity["name"]: entity for entity in cloud_sync.get_entity_configs()}

    cloud_sync.load_entity_records_to_bronze(
        "project-1",
        entities["accounts"],
        [{"account_id": "account-1"}],
        "run-1",
        "gs://bucket/accounts.json",
        {"source_org_key": "india"},
    )
    cloud_sync.load_entity_records_to_bronze(
        "project-1",
        entities["expenses"],
        [{"expense_id": "expense-1"}],
        "run-1",
        "gs://bucket/expenses.json",
        {"source_org_key": "india"},
    )

    assert captured_calls[0]["table_id"] == "zoho_accounts_raw"
    assert captured_calls[0]["entity_name"] == "accounts"
    assert "source_org_key" not in captured_calls[0]
    assert captured_calls[1]["table_id"] == "zoho_raw"
    assert captured_calls[1]["entity_name"] == "expenses"
    assert captured_calls[1]["source_org_key"] == "india"
