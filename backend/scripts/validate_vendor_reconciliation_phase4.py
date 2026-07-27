"""Read-only validation and masked manual-review output for Phase 4.

This script never prints vendor names, references, narrations, bank account IDs,
tokens, or credentials. It only queries the three Phase 4 Gold views.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from dotenv import load_dotenv
from google.cloud import bigquery


load_dotenv()

TOLERANCE = Decimal("0.01")
PAYMENT_STATUSES = (
    "Bank Verified",
    "Bank Match Pending Review",
    "Bank Not Found",
    "Ambiguous Bank Match",
    "Invalid Payment Data",
    "Invalid Bank Data",
)
BILL_STATUSES = (
    "Fully Reconciled",
    "Partially Reconciled",
    "Payment Recorded - Bank Pending",
    "Bank Evidence Found - Zoho Posting Pending",
    "Unpaid - No Payment Found",
    "Amount Mismatch",
    "Needs Review",
)
EXCEPTION_TYPES = (
    "Payment without bill",
    "Allocation without bill",
    "Payment without bank match",
    "Ambiguous bank match",
    "Over-allocated payment",
    "Duplicate final bank assignment",
)


def normalize_reference(value: Any) -> str:
    """Return the SQL-equivalent conservative reference normalization."""
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").strip().upper())


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass(frozen=True)
class _Candidate:
    leg_key: str
    method: str


def match_vendor_payments(
    payments: Iterable[Mapping[str, Any]],
    bank_legs: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Small deterministic reference implementation used by regression tests.

    The warehouse implementation remains the canonical implementation. This
    helper intentionally models only the strict review-only rule and global
    bank-leg uniqueness; it performs no reference, name, or narration matching.
    """
    payment_rows = [dict(row) for row in payments]
    bank_rows = [dict(row) for row in bank_legs]
    summaries: dict[tuple[str, str], dict[str, Any]] = {}

    for payment in payment_rows:
        key = (str(payment.get("source_org_id") or ""), str(payment.get("payment_id") or ""))
        payment_date = _date(payment.get("payment_date"))
        payment_amount = _decimal(payment.get("payment_amount"))
        currency = str(payment.get("currency") or payment.get("payment_currency") or "").strip().upper()
        account_id = str(payment.get("paid_through_account_id") or "")
        invalid = (
            not key[0]
            or not key[1]
            or payment_date is None
            or payment_amount is None
            or payment_amount <= 0
            or not currency
            or not account_id
        )
        if invalid:
            summaries[key] = {
                "status": "Invalid Payment Data",
                "method": "unmatched",
                "candidate_count": 0,
                "leg_key": None,
            }
            continue

        candidates: list[_Candidate] = []
        invalid_bank = False
        for bank in bank_rows:
            bank_amount = _decimal(bank.get("transaction_amount"))
            bank_date = _date(bank.get("transaction_date"))
            same_base = (
                str(bank.get("source_org_id") or "") == key[0]
                and str(bank.get("currency") or bank.get("bank_currency") or "").strip().upper()
                == currency
                and str(bank.get("account_id") or "") == account_id
                and bank_amount is not None
                and abs(bank_amount - payment_amount) <= TOLERANCE
            )
            if not same_base:
                continue
            direction_ok = (
                str(bank.get("debit_or_credit") or "").lower() == "debit"
                and str(bank.get("transaction_direction") or "") == "Outgoing"
            )
            quality_ok = str(bank.get("bank_data_quality_status") or "Valid") == "Valid"
            within_three_days = (
                bank_date is not None and abs((bank_date - payment_date).days) <= 3
            )
            if (
                within_three_days
                and not quality_ok
                and (
                    direction_ok
                    or not bank.get("debit_or_credit")
                    or str(bank.get("transaction_direction") or "") == "Unknown"
                )
            ):
                invalid_bank = True
            if not within_three_days or not direction_ok or not quality_ok:
                continue
            leg_key = str(bank.get("bank_transaction_leg_key") or "")
            method = (
                "multi_leg_transaction_review"
                if bool(bank.get("multi_leg_transaction"))
                else "exact_account_amount_date_window_review"
            )
            candidates.append(_Candidate(leg_key, method))

        by_leg: dict[str, _Candidate] = {}
        for candidate in candidates:
            by_leg[candidate.leg_key] = candidate
        if by_leg:
            winning = sorted(by_leg.values(), key=lambda candidate: candidate.leg_key)
            summaries[key] = {
                "status": (
                    "Bank Match Pending Review"
                    if len(winning) == 1
                    else "Ambiguous Bank Match"
                ),
                "method": winning[0].method if len(winning) == 1 else "ambiguous",
                "candidate_count": len(winning),
                "leg_key": winning[0].leg_key if len(winning) == 1 else None,
            }
        elif invalid_bank:
            summaries[key] = {
                "status": "Invalid Bank Data",
                "method": "unmatched",
                "candidate_count": 0,
                "leg_key": None,
            }
        else:
            summaries[key] = {
                "status": "Bank Not Found",
                "method": "unmatched",
                "candidate_count": 0,
                "leg_key": None,
            }

    claims: Counter[str] = Counter(
        row["leg_key"]
        for row in summaries.values()
        if row["status"] == "Bank Match Pending Review" and row["leg_key"]
    )
    results = []
    for payment in payment_rows:
        key = (str(payment.get("source_org_id") or ""), str(payment.get("payment_id") or ""))
        result = dict(summaries[key])
        if result["leg_key"] and claims[result["leg_key"]] > 1:
            result["status"] = "Ambiguous Bank Match"
            result["method"] = "ambiguous"
        result["source_org_id"], result["payment_id"] = key
        results.append(result)
    return results


def classify_bill(
    bill_amount: Any,
    allocated_amount: Any,
    bank_verified_amount: Any,
    bank_pending_amount: Any,
    *,
    source_outstanding_balance: Any = None,
    unsafe_payment_count: int = 0,
    bank_evidence_candidate_count: int = 0,
    valid_source: bool = True,
) -> tuple[str, bool]:
    """Return the Phase 4 bill status and review flag for test fixtures."""
    bill = _decimal(bill_amount)
    allocated = _decimal(allocated_amount) or Decimal("0")
    verified = _decimal(bank_verified_amount) or Decimal("0")
    pending = _decimal(bank_pending_amount) or Decimal("0")
    outstanding = _decimal(source_outstanding_balance)
    if not valid_source or bill is None or bill < 0:
        return "Needs Review", True
    if allocated - bill > TOLERANCE or verified - allocated > TOLERANCE or unsafe_payment_count:
        return "Needs Review", True
    if (
        allocated > 0
        and outstanding is not None
        and abs(bill - allocated - outstanding) > TOLERANCE
    ):
        return "Amount Mismatch", True
    if allocated >= bill - TOLERANCE and verified >= bill - TOLERANCE and pending <= TOLERANCE:
        return "Fully Reconciled", False
    if verified > TOLERANCE and verified < bill - TOLERANCE:
        return "Partially Reconciled", pending > TOLERANCE
    if allocated > TOLERANCE:
        return "Payment Recorded - Bank Pending", True
    if bank_evidence_candidate_count == 1:
        return "Bank Evidence Found - Zoho Posting Pending", True
    if bank_evidence_candidate_count > 1:
        return "Needs Review", True
    if (outstanding if outstanding is not None else bill) > TOLERANCE:
        return "Unpaid - No Payment Found", False
    return "Amount Mismatch", True


VALIDATION_QUERIES = {
    "payment_status_counts": """
      WITH statuses AS (
        SELECT status FROM UNNEST([
          'Total vendor payments',
          'Bank Verified', 'Bank Match Pending Review', 'Bank Not Found',
          'Ambiguous Bank Match', 'Invalid Payment Data', 'Invalid Bank Data'
        ]) status
      ),
      counts AS (
        SELECT bank_match_status AS status, COUNT(*) AS row_count
        FROM `finance_gold.vendor_payment_bank_matches`
        GROUP BY status
        UNION ALL
        SELECT 'Total vendor payments', COUNT(*)
        FROM `finance_gold.vendor_payment_bank_matches`
      )
      SELECT statuses.status, COALESCE(counts.row_count, 0) AS row_count
      FROM statuses LEFT JOIN counts USING (status)
      ORDER BY status
    """,
    "bill_status_counts": """
      WITH statuses AS (
        SELECT status FROM UNNEST([
          'Total bills',
          'Fully Reconciled', 'Partially Reconciled',
          'Payment Recorded - Bank Pending',
          'Bank Evidence Found - Zoho Posting Pending',
          'Unpaid - No Payment Found', 'Amount Mismatch', 'Needs Review'
        ]) status
      ),
      counts AS (
        SELECT reconciliation_status AS status, COUNT(*) AS row_count
        FROM `finance_gold.vendor_bill_reconciliation`
        GROUP BY status
        UNION ALL
        SELECT 'Total bills', COUNT(*)
        FROM `finance_gold.vendor_bill_reconciliation`
      )
      SELECT statuses.status, COALESCE(counts.row_count, 0) AS row_count
      FROM statuses LEFT JOIN counts USING (status)
      ORDER BY status
    """,
    "financial_totals": """
      SELECT
        source_org_key,
        currency,
        SUM(COALESCE(bill_amount, 0)) AS bill_total,
        SUM(COALESCE(allocated_amount, 0)) AS allocated_total,
        SUM(COALESCE(bank_verified_amount, 0)) AS bank_verified_total,
        SUM(COALESCE(source_outstanding_balance, 0)) AS outstanding_total,
        SUM(COALESCE(remaining_reconciliation_amount, 0)) AS remaining_reconciliation_total
      FROM `finance_gold.vendor_bill_reconciliation`
      GROUP BY source_org_key, currency
      ORDER BY source_org_key, currency
    """,
    "exception_counts": """
      SELECT exception_type, COUNT(*) AS row_count
      FROM `finance_gold.vendor_reconciliation_exceptions`
      GROUP BY exception_type
      ORDER BY exception_type
    """,
    "duplicate_final_bank_assignments": """
      SELECT COUNT(*) AS duplicate_bank_assignment_count
      FROM (
        SELECT bank_transaction_leg_key
        FROM `finance_gold.vendor_payment_bank_matches`
        WHERE bank_match_status = 'Bank Verified'
          AND bank_transaction_leg_key IS NOT NULL
        GROUP BY bank_transaction_leg_key
        HAVING COUNT(*) > 1
      )
    """,
    "masked_manual_sample": """
      WITH ranked_bills AS (
        SELECT
          bill.*,
          ROW_NUMBER() OVER (
            PARTITION BY reconciliation_status
            ORDER BY bill_date DESC, bill_id
          ) AS status_rank
        FROM `finance_gold.vendor_bill_reconciliation` bill
        WHERE reconciliation_status IN (
          'Fully Reconciled',
          'Payment Recorded - Bank Pending',
          'Unpaid - No Payment Found',
          'Amount Mismatch'
        )
      ),
      sampled_bills AS (
        SELECT *
        FROM ranked_bills
        WHERE (reconciliation_status = 'Fully Reconciled' AND status_rank <= 5)
           OR (reconciliation_status = 'Payment Recorded - Bank Pending' AND status_rank <= 3)
           OR (reconciliation_status = 'Unpaid - No Payment Found' AND status_rank <= 3)
           OR reconciliation_status = 'Amount Mismatch'
      ),
      bill_rows AS (
        SELECT
          RIGHT(COALESCE(bill.bill_id, ''), 4) AS bill_id_last4,
          RIGHT(COALESCE(allocation.payment_id, ''), 4) AS payment_id_last4,
          RIGHT(
            COALESCE(payment.bank_transaction_leg_key, bill.bank_evidence_leg_key, ''),
            4
          ) AS bank_leg_key_last4,
          bill.bill_date,
          payment.payment_date,
          payment.bank_transaction_date,
          bill.currency,
          bill.bill_amount,
          bill.allocated_amount,
          COALESCE(payment.bank_amount, bill.bank_evidence_amount) AS bank_amount,
          bill.reconciliation_status AS status,
          COALESCE(payment.bank_match_method, 'unmatched') AS match_method,
          bill.reconciliation_reason AS reason,
          ROW_NUMBER() OVER (
            PARTITION BY bill.source_org_id, bill.bill_id
            ORDER BY allocation.payment_date DESC, allocation.payment_id
          ) AS payment_rank
        FROM sampled_bills bill
        LEFT JOIN `finance_silver.bridge_vendor_payment_bill_allocations` allocation
          ON allocation.source_org_id = bill.source_org_id
         AND allocation.bill_id = bill.bill_id
        LEFT JOIN `finance_gold.vendor_payment_bank_matches` payment
          ON payment.source_org_id = allocation.source_org_id
         AND payment.payment_id = allocation.payment_id
      )
      SELECT * EXCEPT(payment_rank) FROM bill_rows WHERE payment_rank = 1
      ORDER BY status, bill_id_last4, payment_id_last4
    """,
    "masked_payment_review_candidates": """
      WITH candidate_pairs AS (
        SELECT
          payment.payment_id,
          bank.bank_transaction_leg_key,
          payment.payment_date,
          bank.transaction_date AS bank_date,
          DATE_DIFF(bank.transaction_date, payment.payment_date, DAY)
            AS date_difference_days,
          payment.payment_currency AS currency,
          payment.payment_amount,
          bank.transaction_amount AS bank_amount,
          bank.multi_leg_transaction,
          payment.bank_match_status,
          payment.bank_match_method,
          payment.bank_match_reason
        FROM `finance_gold.vendor_payment_bank_matches` payment
        JOIN `finance_silver.fact_bank_transactions` bank
          ON bank.source_org_id = payment.source_org_id
         AND UPPER(NULLIF(TRIM(bank.original_currency), '')) = payment.payment_currency
         AND bank.account_id = payment.paid_through_account_id
         AND ABS(bank.transaction_amount - payment.payment_amount) <= 0.01
         AND ABS(DATE_DIFF(bank.transaction_date, payment.payment_date, DAY)) <= 3
         AND bank.debit_or_credit = 'debit'
         AND bank.transaction_direction = 'Outgoing'
         AND bank.bank_data_quality_status = 'Valid'
      ),
      leg_claims AS (
        SELECT bank_transaction_leg_key
        FROM candidate_pairs
        GROUP BY bank_transaction_leg_key
        HAVING COUNT(DISTINCT payment_id) > 1
      )
      SELECT DISTINCT
        RIGHT(COALESCE(candidate.payment_id, ''), 4) AS payment_id_last4,
        RIGHT(COALESCE(candidate.bank_transaction_leg_key, ''), 4)
          AS bank_leg_key_last4,
        candidate.payment_date,
        candidate.bank_date,
        candidate.date_difference_days,
        candidate.currency,
        candidate.payment_amount,
        candidate.bank_amount,
        IF(candidate.multi_leg_transaction, 'multi-leg', 'single-leg')
          AS single_or_multi_leg,
        candidate.bank_match_method AS match_method,
        candidate.bank_match_reason AS reason
      FROM candidate_pairs candidate
      LEFT JOIN leg_claims
        USING (bank_transaction_leg_key)
      WHERE candidate.bank_match_status IN (
        'Bank Match Pending Review',
        'Ambiguous Bank Match'
      )
        OR leg_claims.bank_transaction_leg_key IS NOT NULL
      ORDER BY payment_id_last4, bank_leg_key_last4
    """,
}


def _serialize(value: Any) -> Any:
    if isinstance(value, (date, Decimal)):
        return str(value)
    return value


def run_validation() -> dict[str, Any]:
    """Run every read-only aggregate and masked-sample query."""
    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        raise RuntimeError("GCP_PROJECT_ID is required")
    location = os.getenv("BQ_LOCATION") or os.getenv("GCP_LOCATION") or "asia-south1"
    client = bigquery.Client(project=project_id)
    output: dict[str, Any] = {}
    for name, query in VALIDATION_QUERIES.items():
        output[name] = [
            {key: _serialize(value) for key, value in row.items()}
            for row in client.query(query, location=location).result()
        ]
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the safe aggregate and masked output as JSON.",
    )
    args = parser.parse_args()
    result = run_validation()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for section, rows in result.items():
            print(f"[{section}]")
            for row in rows:
                print(" | ".join(f"{key}={value}" for key, value in row.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
