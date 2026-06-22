from backend.zoho.client import zoho_get
from backend.config.zoho_entities import ZohoEntityConfig


def fetch_paginated(endpoint: str, response_key: str, organization_id: str | None = None) -> list[dict]:
    """Fetch every page for a Zoho Books endpoint."""

    all_records = []
    page = 1
    per_page = 200

    while True:
        response = zoho_get(
            endpoint,
            params={
                "page": page,
                "per_page": per_page,
            },
            organization_id=organization_id,
        )

        records = response.get(response_key, [])
        all_records.extend(records)

        page_context = response.get("page_context", {})
        has_more_page = page_context.get("has_more_page", False)

        if not has_more_page:
            break

        page += 1

    return all_records


def fetch_entity_records(entity_config: ZohoEntityConfig, organization_id: str | None = None) -> list[dict]:
    """Fetch records for any entity described in the Zoho entity config."""

    return fetch_paginated(entity_config.endpoint, entity_config.response_key, organization_id=organization_id)


def fetch_accounts(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("chartofaccounts", "chartofaccounts", organization_id=organization_id)


def fetch_contacts(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("contacts", "contacts", organization_id=organization_id)


def fetch_invoices(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("invoices", "invoices", organization_id=organization_id)


def fetch_bills(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("bills", "bills", organization_id=organization_id)


def fetch_customer_payments(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("customerpayments", "customer_payments", organization_id=organization_id)


def fetch_journals(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("journals", "journals", organization_id=organization_id)
