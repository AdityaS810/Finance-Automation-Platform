"""Read-only live validation for canonical Zoho vendor-payment ingestion.

Only response field names, counts, and aggregate monetary values are printed.
No API payloads are persisted and no identifying record values are logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from backend.config.zoho_organizations import get_zoho_organizations
from backend.zoho.client import zoho_get
from backend.zoho.extract_zoho import fetch_vendor_payments


@dataclass(frozen=True)
class VendorPaymentSummary:
    payment_count: int
    payments_with_bills_array: int
    allocation_count: int
    total_payment_amount: Decimal
    total_allocated_amount: Decimal
    total_unapplied_amount: Decimal
    missing_bills_array_count: int


def _decimal(value: object) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def summarize_vendor_payments(payments: list[dict]) -> VendorPaymentSummary:
    """Calculate non-identifying validation totals from detailed payments."""
    payment_total = Decimal("0")
    allocation_total = Decimal("0")
    allocation_count = 0
    payments_with_bills_array = 0
    missing_bills_array_count = 0

    for payment in payments:
        payment_total += _decimal(payment.get("amount"))
        bills = payment.get("bills")
        if not isinstance(bills, list):
            missing_bills_array_count += 1
            continue

        payments_with_bills_array += 1
        allocation_count += len(bills)
        allocation_total += sum(
            (_decimal(allocation.get("amount_applied")) for allocation in bills if isinstance(allocation, dict)),
            Decimal("0"),
        )

    return VendorPaymentSummary(
        payment_count=len(payments),
        payments_with_bills_array=payments_with_bills_array,
        allocation_count=allocation_count,
        total_payment_amount=payment_total,
        total_allocated_amount=allocation_total,
        total_unapplied_amount=payment_total - allocation_total,
        missing_bills_array_count=missing_bills_array_count,
    )


def _field_names(payments: list[dict], field: str | None = None) -> list[str]:
    objects: list[dict] = payments
    if field is not None:
        objects = [
            item
            for payment in payments
            for item in (payment.get(field) if isinstance(payment.get(field), list) else [])
            if isinstance(item, dict)
        ]
    return sorted({key for item in objects for key in item})


def main() -> int:
    for organization in get_zoho_organizations():
        org_key = organization["org_key"]
        organization_id = organization["organization_id"]
        list_response = zoho_get(
            "vendorpayments",
            params={"page": 1, "per_page": 1},
            organization_id=organization_id,
        )
        list_records = list_response.get("vendorpayments")
        if not isinstance(list_records, list):
            raise RuntimeError(
                f"organization_key={org_key} did not return the expected "
                "'vendorpayments' list collection."
            )

        detail_wrapper_keys: list[str] = []
        if list_records:
            payment_id = list_records[0].get("payment_id")
            if not payment_id:
                raise RuntimeError(
                    f"organization_key={org_key} returned a vendor-payment list record "
                    "without payment_id."
                )
            detail_response = zoho_get(
                f"vendorpayments/{payment_id}",
                organization_id=organization_id,
            )
            detail_wrapper_keys = sorted(detail_response)
            if not isinstance(detail_response.get("vendorpayment"), dict):
                raise RuntimeError(
                    f"organization_key={org_key} did not return the expected "
                    "'vendorpayment' detail object."
                )

        detailed_payments = fetch_vendor_payments(organization_id=organization_id)
        summary = summarize_vendor_payments(detailed_payments)
        currency = organization["base_currency"]
        print(
            f"organization_key={org_key} | "
            f"list_collection_key=vendorpayments | "
            f"detail_collection_key=vendorpayment | "
            f"list_response_keys={sorted(list_response)} | "
            f"detail_response_keys={detail_wrapper_keys} | "
            f"payment_fields={_field_names(detailed_payments)} | "
            f"allocation_fields={_field_names(detailed_payments, 'bills')}"
        )
        print(
            f"organization_key={org_key} | "
            f"currency={currency} | "
            f"vendor_payment_list_count={summary.payment_count} | "
            f"detailed_payments_fetched={summary.payment_count} | "
            f"payments_with_bills_array={summary.payments_with_bills_array} | "
            f"allocation_row_count={summary.allocation_count} | "
            f"total_payment_amount={summary.total_payment_amount:.2f} | "
            f"total_allocated_amount={summary.total_allocated_amount:.2f} | "
            f"total_unapplied_amount={summary.total_unapplied_amount:.2f} | "
            f"missing_bills_array_count={summary.missing_bills_array_count}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
