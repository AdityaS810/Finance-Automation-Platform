from backend.zoho.client import zoho_get
from backend.config.zoho_entities import ZohoEntityConfig


def fetch_paginated(
    endpoint: str,
    response_key: str,
    organization_id: str | None = None,
    require_response_key: bool = False,
) -> list[dict]:
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

        if require_response_key and (
            not isinstance(response, dict)
            or response_key not in response
            or not isinstance(response.get(response_key), list)
        ):
            raise RuntimeError(
                f"Zoho endpoint '{endpoint}' did not return the expected "
                f"'{response_key}' collection."
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
    if entity_config.name == "bills":
        return fetch_bills(organization_id=organization_id)
    if entity_config.name == "expenses":
        return fetch_expenses(organization_id=organization_id)
    if entity_config.name == "vendor_payments":
        return fetch_vendor_payments(organization_id=organization_id)

    return fetch_paginated(entity_config.endpoint, entity_config.response_key, organization_id=organization_id)


def fetch_accounts(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("chartofaccounts", "chartofaccounts", organization_id=organization_id)


def fetch_contacts(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("contacts", "contacts", organization_id=organization_id)


def fetch_invoices(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("invoices", "invoices", organization_id=organization_id)


def fetch_bills(organization_id: str | None = None) -> list[dict]:
    bills = fetch_paginated("bills", "bills", organization_id=organization_id)
    detailed_bills = []
    for bill in bills:
        bill_id = bill.get("bill_id")
        if not bill_id:
            detailed_bills.append(bill)
            continue

        detail_response = zoho_get(f"bills/{bill_id}", organization_id=organization_id)
        detailed_bills.append(detail_response.get("bill", bill))

    return detailed_bills


def fetch_expenses(organization_id: str | None = None) -> list[dict]:
    expenses = fetch_paginated("expenses", "expenses", organization_id=organization_id)
    detailed_expenses = []
    for expense in expenses:
        expense_id = expense.get("expense_id")
        if not expense_id:
            detailed_expenses.append(expense)
            continue

        detail_response = zoho_get(f"expenses/{expense_id}", organization_id=organization_id)
        detailed_expenses.append(detail_response.get("expense", expense))

    return detailed_expenses


def fetch_customer_payments(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("customerpayments", "customer_payments", organization_id=organization_id)


def fetch_vendor_payments(organization_id: str | None = None) -> list[dict]:
    """Fetch complete vendor payments, including their bill allocations."""
    payments = fetch_paginated(
        "vendorpayments",
        "vendorpayments",
        organization_id=organization_id,
        require_response_key=True,
    )
    detailed_payments = []

    for position, payment in enumerate(payments, start=1):
        payment_id = payment.get("payment_id")
        if not payment_id:
            raise RuntimeError(
                f"Vendor payment at list position {position} has no payment_id; "
                "refusing to load an incomplete record."
            )

        try:
            detail_response = zoho_get(
                f"vendorpayments/{payment_id}",
                organization_id=organization_id,
            )
        except Exception:
            raise RuntimeError(
                f"Vendor payment detail request failed at list position {position}; "
                "refusing to load an incomplete record."
            ) from None

        detailed_payment = detail_response.get("vendorpayment")
        if not isinstance(detailed_payment, dict):
            raise RuntimeError(
                f"Vendor payment detail response at list position {position} did not contain "
                "the expected 'vendorpayment' object; refusing to load an incomplete record."
            )
        if str(detailed_payment.get("payment_id", "")) != str(payment_id):
            raise RuntimeError(
                f"Vendor payment detail response at list position {position} did not preserve "
                "the expected payment_id; refusing to load an incomplete record."
            )

        detailed_payments.append(detailed_payment)

    return detailed_payments


def fetch_journals(organization_id: str | None = None) -> list[dict]:
    return fetch_paginated("journals", "journals", organization_id=organization_id)
