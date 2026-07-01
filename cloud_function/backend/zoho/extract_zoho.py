from backend.zoho.client import zoho_get


def fetch_paginated(endpoint: str, response_key: str) -> list[dict]:
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


def fetch_journals() -> list[dict]:
    return fetch_paginated("journals", "journals")
