"""Zoho Books entity configuration used by ETL sync jobs."""

from __future__ import annotations

from dataclasses import dataclass


SOURCE_SYSTEM = "zoho_books"
BRONZE_DATASET_ID = "finance_bronze"
RAW_TABLE_ID = "zoho_raw"


@dataclass(frozen=True)
class ZohoEntityConfig:
    """Small description of how to fetch and identify one Zoho entity."""

    name: str
    endpoint: str
    response_key: str
    id_field: str


ZOHO_ENTITY_CONFIGS: dict[str, ZohoEntityConfig] = {
    "accounts": ZohoEntityConfig(
        name="accounts",
        endpoint="chartofaccounts",
        response_key="chartofaccounts",
        id_field="account_id",
    ),
    "contacts": ZohoEntityConfig(
        name="contacts",
        endpoint="contacts",
        response_key="contacts",
        id_field="contact_id",
    ),
    "invoices": ZohoEntityConfig(
        name="invoices",
        endpoint="invoices",
        response_key="invoices",
        id_field="invoice_id",
    ),
    "bills": ZohoEntityConfig(
        name="bills",
        endpoint="bills",
        response_key="bills",
        id_field="bill_id",
    ),
    "expenses": ZohoEntityConfig(
        name="expenses",
        endpoint="expenses",
        response_key="expenses",
        id_field="expense_id",
    ),
    "customer_payments": ZohoEntityConfig(
        name="customer_payments",
        endpoint="customerpayments",
        response_key="customer_payments",
        id_field="payment_id",
    ),
    "vendor_payments": ZohoEntityConfig(
        name="vendor_payments",
        endpoint="vendorpayments",
        response_key="vendorpayments",
        id_field="payment_id",
    ),
    "journals": ZohoEntityConfig(
        name="journals",
        endpoint="journals",
        response_key="journals",
        id_field="journal_id",
    ),
}


DEFAULT_ZOHO_ENTITY_NAMES = [
    "accounts",
    "contacts",
    "invoices",
    "bills",
    "expenses",
    "customer_payments",
    "vendor_payments",
    "journals",
]


def get_zoho_entity_configs(
    entity_names: list[str] | None = None,
) -> list[ZohoEntityConfig]:
    """Return entity configs in a stable order.

    Passing ``entity_names`` lets a script run only a subset without building a
    separate hardcoded entity list.
    """

    names = entity_names or DEFAULT_ZOHO_ENTITY_NAMES
    return [ZOHO_ENTITY_CONFIGS[name] for name in names]
