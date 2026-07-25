"""Read-only live validation for canonical Zoho bank transactions.

Only response field names and non-identifying aggregate values are printed.
API payloads are held in memory only and are never persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from backend.config.zoho_organizations import get_zoho_organizations
from backend.zoho.client import zoho_get


@dataclass(frozen=True)
class BankTransactionSummary:
    transaction_count: int
    debit_count: int
    credit_count: int
    unknown_direction_count: int
    earliest_date: date | None
    latest_date: date | None
    total_debit_amount: Decimal
    total_credit_amount: Decimal


def _decimal(value: object) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def summarize_bank_transactions(transactions: list[dict]) -> BankTransactionSummary:
    """Calculate safe counts, date bounds, and direction totals."""
    debit_count = 0
    credit_count = 0
    unknown_direction_count = 0
    total_debit_amount = Decimal("0")
    total_credit_amount = Decimal("0")
    dates = []

    for transaction in transactions:
        parsed_date = _date(transaction.get("date"))
        if parsed_date is not None:
            dates.append(parsed_date)

        direction = str(transaction.get("debit_or_credit") or "").strip().lower()
        amount = abs(_decimal(transaction.get("amount")))
        if direction == "debit":
            debit_count += 1
            total_debit_amount += amount
        elif direction == "credit":
            credit_count += 1
            total_credit_amount += amount
        else:
            unknown_direction_count += 1

    return BankTransactionSummary(
        transaction_count=len(transactions),
        debit_count=debit_count,
        credit_count=credit_count,
        unknown_direction_count=unknown_direction_count,
        earliest_date=min(dates) if dates else None,
        latest_date=max(dates) if dates else None,
        total_debit_amount=total_debit_amount,
        total_credit_amount=total_credit_amount,
    )


def fetch_bank_transactions(organization_id: str) -> tuple[list[dict], set[str]]:
    """Fetch every list page while strictly validating the live collection."""
    page = 1
    records: list[dict] = []
    response_keys: set[str] = set()

    while True:
        response = zoho_get(
            "banktransactions",
            params={"page": page, "per_page": 200},
            organization_id=organization_id,
        )
        response_keys.update(response)
        page_records = response.get("banktransactions")
        if not isinstance(page_records, list):
            raise RuntimeError("Zoho did not return the expected 'banktransactions' collection.")
        records.extend(page_records)
        if not response.get("page_context", {}).get("has_more_page", False):
            break
        page += 1

    return records, response_keys


def main() -> int:
    for organization in get_zoho_organizations():
        transactions, response_keys = fetch_bank_transactions(organization["organization_id"])
        summary = summarize_bank_transactions(transactions)
        transaction_fields = sorted({key for transaction in transactions for key in transaction})
        print(
            f"organization_key={organization['org_key']} | "
            f"collection_key=banktransactions | "
            f"response_keys={sorted(response_keys)} | "
            f"transaction_fields={transaction_fields}"
        )
        print(
            f"organization_key={organization['org_key']} | "
            f"currency={organization['base_currency']} | "
            f"bank_transaction_count={summary.transaction_count} | "
            f"debit_count={summary.debit_count} | "
            f"credit_count={summary.credit_count} | "
            f"unknown_direction_count={summary.unknown_direction_count} | "
            f"earliest_date={summary.earliest_date or ''} | "
            f"latest_date={summary.latest_date or ''} | "
            f"total_debit_amount={summary.total_debit_amount:.2f} | "
            f"total_credit_amount={summary.total_credit_amount:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
