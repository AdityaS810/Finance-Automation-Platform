"""Phase 4 vendor reconciliation matching and SQL regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.scripts.validate_vendor_reconciliation_phase4 import (
    BILL_STATUSES,
    PAYMENT_STATUSES,
    VALIDATION_QUERIES,
    classify_bill,
    match_vendor_payments,
)
from backend.scripts import apply_vendor_reconciliation_phase4 as phase4_migration


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "sql" / "migrations" / "phase4_vendor_reconciliation.sql"
MONOLITHIC_PATH = REPO_ROOT / "sql" / "ddl" / "create_silver_gold_views.sql"


def payment(**overrides):
    row = {
        "source_org_id": "india",
        "payment_id": "payment-1",
        "payment_date": "2026-07-10",
        "payment_amount": 100,
        "currency": "INR",
        "paid_through_account_id": "bank-account-1",
        "reference_number": "PAY-100",
    }
    row.update(overrides)
    return row


def bank(**overrides):
    row = {
        "source_org_id": "india",
        "bank_transaction_leg_key": "bank-leg-1",
        "transaction_date": "2026-07-10",
        "transaction_amount": 100,
        "currency": "INR",
        "account_id": "bank-account-1",
        "reference_number": "PAY-100",
        "debit_or_credit": "debit",
        "transaction_direction": "Outgoing",
        "bank_data_quality_status": "Valid",
        "multi_leg_transaction": False,
    }
    row.update(overrides)
    return row


def test_same_day_single_leg_candidate_is_pending_review():
    result = match_vendor_payments([payment()], [bank()])[0]
    assert result["status"] == "Bank Match Pending Review"
    assert result["method"] == "exact_account_amount_date_window_review"
    assert result["candidate_count"] == 1


def test_unique_single_leg_exact_account_amount_within_three_days_is_pending_review():
    result = match_vendor_payments(
        [payment(reference_number="payment-reference")],
        [bank(transaction_date="2026-07-13", reference_number="different-reference")],
    )[0]
    assert result["status"] == "Bank Match Pending Review"
    assert result["method"] == "exact_account_amount_date_window_review"


def test_four_day_difference_remains_bank_not_found_even_with_exact_reference():
    result = match_vendor_payments(
        [payment(reference_number="same-reference")],
        [
            bank(
                transaction_date="2026-07-14",
                reference_number="same-reference",
                description="vendor payment narration",
            )
        ],
    )[0]
    assert result["status"] == "Bank Not Found"
    assert result["method"] == "unmatched"


@pytest.mark.parametrize(
    "bank_overrides",
    [
        {"debit_or_credit": "credit", "transaction_direction": "Incoming"},
        {"account_id": "different-account"},
        {"source_org_id": "us"},
        {"currency": "USD"},
        {"transaction_amount": 101},
    ],
)
def test_unsafe_bank_rows_are_rejected(bank_overrides):
    result = match_vendor_payments([payment()], [bank(**bank_overrides)])[0]
    assert result["status"] == "Bank Not Found"
    assert result["method"] == "unmatched"


def test_multiple_bank_candidates_remain_ambiguous():
    result = match_vendor_payments(
        [payment()],
        [bank(), bank(bank_transaction_leg_key="bank-leg-2")],
    )[0]
    assert result["status"] == "Ambiguous Bank Match"
    assert result["method"] == "ambiguous"
    assert result["candidate_count"] == 2


def test_multi_leg_transaction_does_not_auto_match_without_direct_id():
    result = match_vendor_payments(
        [payment()],
        [bank(multi_leg_transaction=True)],
    )[0]
    assert result["status"] == "Bank Match Pending Review"
    assert result["method"] == "multi_leg_transaction_review"
    assert result["status"] != "Bank Verified"


def test_bank_leg_cannot_match_two_payments():
    results = match_vendor_payments(
        [payment(payment_id="payment-1"), payment(payment_id="payment-2")],
        [bank()],
    )
    assert [row["status"] for row in results] == [
        "Ambiguous Bank Match",
        "Ambiguous Bank Match",
    ]
    assert not any(row["status"] == "Bank Verified" for row in results)


def test_invalid_payment_and_bank_data_are_separate():
    invalid_payment = match_vendor_payments(
        [payment(paid_through_account_id=None)],
        [bank()],
    )[0]
    invalid_bank = match_vendor_payments(
        [payment()],
        [bank(bank_data_quality_status="Needs Review")],
    )[0]
    assert invalid_payment["status"] == "Invalid Payment Data"
    assert invalid_bank["status"] == "Invalid Bank Data"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((100, 100, 100, 0), ("Fully Reconciled", False)),
        ((100, 50, 50, 0), ("Partially Reconciled", False)),
        ((100, 100, 0, 100), ("Payment Recorded - Bank Pending", True)),
        ((100, 0, 0, 0), ("Unpaid - No Payment Found", False)),
    ],
)
def test_core_bill_reconciliation_statuses(values, expected):
    assert classify_bill(*values) == expected


def test_bank_evidence_candidate_always_requires_review():
    assert classify_bill(
        100,
        0,
        0,
        0,
        bank_evidence_candidate_count=1,
    ) == ("Bank Evidence Found - Zoho Posting Pending", True)


def test_bill_amount_mismatch_and_overallocation():
    assert classify_bill(
        100,
        40,
        40,
        0,
        source_outstanding_balance=30,
    ) == ("Amount Mismatch", True)
    assert classify_bill(100, 101, 100, 1) == ("Needs Review", True)


def test_phase4_sql_uses_preaggregations_for_many_to_many_allocations():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "allocation_by_payment AS" in sql
    assert "allocation_by_bill AS" in sql
    assert "COUNT(DISTINCT payment_id)" in sql
    assert "SUM(COALESCE(amount_applied, 0))" in sql
    assert "payment_match.source_org_id = allocation.source_org_id" in sql


def test_payment_and_allocation_exceptions_are_present():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "'Payment without bill'" in sql
    assert "'Allocation without bill'" in sql
    assert "'Payment without bank match'" in sql
    assert "'Over-allocated payment'" in sql
    assert "'Duplicate final bank assignment'" in sql
    assert "'Bank leg proposed for more than one payment'" in sql


def test_duplicate_prevention_is_enforced_before_final_status():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "bank_leg_claims AS" in sql
    assert "claiming_payment_count > 1" in sql
    assert "candidate_summary.candidate_count = 1 THEN 'Bank Match Pending Review'" in sql
    assert "bank_leg_claims.claiming_payment_count > 1" in sql
    assert "duplicate_final_assignments" in sql


def test_phase42_sql_rule_is_review_only_and_has_no_reference_fallback():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    payment_view_sql = sql.split(
        "CREATE OR REPLACE VIEW `finance_gold.vendor_bill_reconciliation`"
    )[0]
    assert "exact_account_amount_date_window_review" in payment_view_sql
    assert "ABS(DATE_DIFF(bank.transaction_date, payment.payment_date, DAY)) <= 3" in payment_view_sql
    assert "WHEN candidate_summary.candidate_count = 1 THEN 'Bank Match Pending Review'" in payment_view_sql
    assert "THEN 'Bank Verified'" not in payment_view_sql
    assert "exact_reference_account_amount" not in payment_view_sql
    assert "normalized_bank_reference = payment.normalized_payment_reference" not in payment_view_sql
    assert "<= 7" not in payment_view_sql


def test_us_zero_payment_handling():
    assert match_vendor_payments([], [bank(source_org_id="us", currency="USD")]) == []


def test_phase4_never_uses_legacy_fact_transactions_for_reconciliation():
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    monolithic = MONOLITHIC_PATH.read_text(encoding="utf-8")
    phase4_monolithic = monolithic[monolithic.index("-- Phase 4 controlled migration:") :]
    assert "finance_silver.fact_transactions" not in migration
    assert "finance_silver.fact_transactions" not in phase4_monolithic
    assert "finance_silver.fact_bank_transactions" in migration
    assert "finance_silver.fact_bank_transactions" in phase4_monolithic


def test_monolithic_phase4_definitions_are_synchronized():
    migration = MIGRATION_PATH.read_text(encoding="utf-8").strip()
    monolithic = MONOLITHIC_PATH.read_text(encoding="utf-8")
    assert migration in monolithic


def test_phase4_migration_runner_is_gold_only_and_rerunnable():
    statements = phase4_migration.read_validated_statements()
    assert [name for name, _ in statements] == list(phase4_migration.EXPECTED_VIEWS)
    assert all(name.startswith("finance_gold.") for name, _ in statements)
    assert phase4_migration.apply_migration() == list(phase4_migration.EXPECTED_VIEWS)


def test_validation_queries_cover_every_required_status_and_mask_ids():
    queries = "\n".join(VALIDATION_QUERIES.values())
    assert set(PAYMENT_STATUSES) <= set(queries.split("'"))
    assert set(BILL_STATUSES) <= set(queries.split("'"))
    assert "RIGHT(COALESCE(bill.bill_id, ''), 4)" in queries
    assert "RIGHT(COALESCE(allocation.payment_id, ''), 4)" in queries
    assert "vendor_name" not in VALIDATION_QUERIES["masked_manual_sample"]
    review_query = VALIDATION_QUERIES["masked_payment_review_candidates"]
    assert "payment_id_last4" in review_query
    assert "bank_leg_key_last4" in review_query
    assert "single_or_multi_leg" in review_query
    assert "account_id AS" not in review_query
