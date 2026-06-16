from backend.zoho.client import zoho_get
from backend.config.zoho_entities import ZohoEntityConfig


def fetch_paginated(endpoint: str, response_key: str) -> list[dict]:
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
        )

        records = response.get(response_key, [])
        all_records.extend(records)

        page_context = response.get("page_context", {})
        has_more_page = page_context.get("has_more_page", False)

        if not has_more_page:
            break

        page += 1

    return all_records


def fetch_entity_records(entity_config: ZohoEntityConfig) -> list[dict]:
    """Fetch records for any entity described in the Zoho entity config."""

    return fetch_paginated(entity_config.endpoint, entity_config.response_key)


def fetch_accounts() -> list[dict]:
    return fetch_paginated("chartofaccounts", "chartofaccounts")


def fetch_contacts() -> list[dict]:
    return fetch_paginated("contacts", "contacts")


def fetch_invoices() -> list[dict]:
    return fetch_paginated("invoices", "invoices")


def fetch_bills() -> list[dict]:
    return fetch_paginated("bills", "bills")


def fetch_customer_payments() -> list[dict]:
    return fetch_paginated("customerpayments", "customer_payments")


def fetch_journals() -> list[dict]:
    return fetch_paginated("journals", "journals")
