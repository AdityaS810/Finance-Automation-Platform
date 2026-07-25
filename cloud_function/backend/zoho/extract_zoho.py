from backend.zoho.client import zoho_get


def fetch_paginated(
    endpoint: str,
    response_key: str,
    require_response_key: bool = False,
) -> list[dict]:
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


def fetch_accounts() -> list[dict]:
    return fetch_paginated("chartofaccounts", "chartofaccounts")


def fetch_contacts() -> list[dict]:
    return fetch_paginated("contacts", "contacts")


def fetch_invoices() -> list[dict]:
    return fetch_paginated("invoices", "invoices")


def fetch_expenses() -> list[dict]:
    expenses = fetch_paginated("expenses", "expenses")
    detailed_expenses = []
    for expense in expenses:
        expense_id = expense.get("expense_id")
        if not expense_id:
            detailed_expenses.append(expense)
            continue

        detail_response = zoho_get(f"expenses/{expense_id}")
        detailed_expenses.append(detail_response.get("expense", expense))

    return detailed_expenses


def fetch_customer_payments() -> list[dict]:
    return fetch_paginated("customerpayments", "customer_payments")


def fetch_vendor_payments() -> list[dict]:
    """Fetch complete vendor payments, including their bill allocations."""
    payments = fetch_paginated(
        "vendorpayments",
        "vendorpayments",
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
            detail_response = zoho_get(f"vendorpayments/{payment_id}")
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


def fetch_bank_transactions() -> list[dict]:
    """Fetch complete bank transaction list records from every page."""
    return fetch_paginated(
        "banktransactions",
        "banktransactions",
        require_response_key=True,
    )


def fetch_journals() -> list[dict]:
    return fetch_paginated("journals", "journals")
