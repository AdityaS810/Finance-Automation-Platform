"""Read-only Phase 3 warehouse validation for one controlled ETL run."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from decimal import Decimal
from typing import Any, Iterable, Mapping

from dotenv import load_dotenv
from google.cloud import bigquery


load_dotenv()


def count_duplicate_ids(records: Iterable[Mapping[str, Any]], id_field: str) -> int:
    """Count repeated non-empty IDs beyond their first occurrence."""
    counts = Counter(str(record.get(id_field)) for record in records if record.get(id_field))
    return sum(count - 1 for count in counts.values() if count > 1)


def validate_allocation_relationships(
    payments: Iterable[Mapping[str, Any]],
    allocations: Iterable[Mapping[str, Any]],
    *,
    tolerance: Decimal = Decimal("0.01"),
) -> dict[str, int]:
    """Validate payment/allocation parent, amount, and organization relationships."""
    payment_index = {
        (str(row.get("source_org_id") or ""), str(row.get("payment_id") or "")): row
        for row in payments
        if row.get("payment_id")
    }
    allocated_totals: Counter[tuple[str, str]] = Counter()
    orphan_count = 0
    organization_mismatch_count = 0
    for allocation in allocations:
        organization_id = str(allocation.get("source_org_id") or "")
        payment_id = str(allocation.get("payment_id") or "")
        key = (organization_id, payment_id)
        amount = allocation.get("amount_applied")
        if key not in payment_index:
            orphan_count += 1
            if any(existing_payment_id == payment_id for _, existing_payment_id in payment_index):
                organization_mismatch_count += 1
            continue
        if amount is not None:
            allocated_totals[key] += Decimal(str(amount))

    exceeds_count = 0
    for key, allocated_amount in allocated_totals.items():
        payment_amount = payment_index[key].get("payment_amount")
        if payment_amount is not None and allocated_amount - Decimal(str(payment_amount)) > tolerance:
            exceeds_count += 1
    return {
        "orphan_allocation_count": orphan_count,
        "organization_mismatch_count": organization_mismatch_count,
        "payments_exceeded_count": exceeds_count,
    }


VALIDATION_QUERIES = {
    "bronze": """
      WITH grid AS (
        SELECT org_key, entity_name
        FROM UNNEST(['india', 'us']) org_key
        CROSS JOIN UNNEST(['vendor_payments', 'bank_transactions']) entity_name
      ),
      current_rows AS (
        SELECT *
        FROM `finance_bronze.zoho_raw`
        WHERE run_id = @run_id
          AND entity_name IN ('vendor_payments', 'bank_transactions')
      ),
      metrics AS (
        SELECT
          source_org_key AS org_key,
          entity_name,
          COUNT(*) AS current_run_rows,
          COUNT(DISTINCT NULLIF(source_record_id, '')) AS distinct_source_record_ids,
          COUNTIF(source_record_id IS NULL OR source_record_id = '') AS missing_source_record_ids,
          MAX(loaded_at) AS latest_loaded_at
        FROM current_rows
        GROUP BY org_key, entity_name
      ),
      duplicates AS (
        SELECT org_key, entity_name, SUM(record_count - 1) AS duplicate_ids_within_run
        FROM (
          SELECT
            source_org_key AS org_key,
            entity_name,
            source_record_id,
            COUNT(*) AS record_count
          FROM current_rows
          WHERE NULLIF(source_record_id, '') IS NOT NULL
          GROUP BY org_key, entity_name, source_record_id
          HAVING COUNT(*) > 1
        )
        GROUP BY org_key, entity_name
      )
      SELECT
        grid.org_key,
        grid.entity_name,
        COALESCE(metrics.current_run_rows, 0) AS current_run_rows,
        COALESCE(metrics.distinct_source_record_ids, 0) AS distinct_source_record_ids,
        COALESCE(metrics.missing_source_record_ids, 0) AS missing_source_record_ids,
        metrics.latest_loaded_at,
        COALESCE(duplicates.duplicate_ids_within_run, 0) AS duplicate_ids_within_run
      FROM grid
      LEFT JOIN metrics USING (org_key, entity_name)
      LEFT JOIN duplicates USING (org_key, entity_name)
      ORDER BY org_key, entity_name
    """,
    "labels": """
      SELECT
        COUNTIF(run_id = @run_id AND entity_name = 'transactions') AS current_run_transactions,
        COUNTIF(entity_name = 'journals') AS historical_canonical_journals,
        COUNTIF(entity_name = 'transactions') AS historical_mislabeled_transactions
      FROM `finance_bronze.zoho_raw`
    """,
    "bank_duplicate_profile": """
      WITH duplicate_groups AS (
        SELECT
          source_org_key AS org_key,
          source_record_id,
          COUNT(*) AS row_count,
          COUNT(DISTINCT TO_HEX(SHA256(raw_json))) AS payload_versions
        FROM `finance_bronze.zoho_raw`
        WHERE run_id = @run_id
          AND entity_name = 'bank_transactions'
          AND NULLIF(source_record_id, '') IS NOT NULL
        GROUP BY org_key, source_record_id
        HAVING COUNT(*) > 1
      ),
      metrics AS (
        SELECT
          org_key,
          COUNT(*) AS duplicate_id_groups,
          SUM(row_count - 1) AS duplicate_excess_rows,
          COUNTIF(payload_versions > 1) AS conflicting_payload_groups,
          MAX(row_count) AS maximum_rows_per_id
        FROM duplicate_groups
        GROUP BY org_key
      ),
      grid AS (SELECT org_key FROM UNNEST(['india', 'us']) org_key)
      SELECT
        grid.org_key,
        COALESCE(metrics.duplicate_id_groups, 0) AS duplicate_id_groups,
        COALESCE(metrics.duplicate_excess_rows, 0) AS duplicate_excess_rows,
        COALESCE(metrics.conflicting_payload_groups, 0) AS conflicting_payload_groups,
        COALESCE(metrics.maximum_rows_per_id, 0) AS maximum_rows_per_id
      FROM grid
      LEFT JOIN metrics USING (org_key)
      ORDER BY org_key
    """,
    "bank_duplicate_field_differences": """
      WITH duplicate_groups AS (
        SELECT
          source_org_key AS org_key,
          source_record_id,
          COUNT(DISTINCT JSON_VALUE(raw_json, '$.account_id')) AS account_versions,
          COUNT(DISTINCT JSON_VALUE(raw_json, '$.amount')) AS amount_versions,
          COUNT(DISTINCT JSON_VALUE(raw_json, '$.debit_or_credit')) AS direction_versions,
          COUNT(DISTINCT JSON_VALUE(raw_json, '$.date')) AS date_versions,
          COUNT(DISTINCT JSON_VALUE(raw_json, '$.transaction_type')) AS type_versions,
          COUNT(DISTINCT JSON_VALUE(raw_json, '$.status')) AS status_versions,
          COUNT(DISTINCT JSON_VALUE(raw_json, '$.reference_number')) AS reference_versions,
          COUNT(DISTINCT JSON_VALUE(raw_json, '$.description')) AS description_versions
        FROM `finance_bronze.zoho_raw`
        WHERE run_id = @run_id
          AND entity_name = 'bank_transactions'
          AND NULLIF(source_record_id, '') IS NOT NULL
        GROUP BY org_key, source_record_id
        HAVING COUNT(*) > 1
      )
      SELECT
        org_key,
        COUNTIF(account_versions > 1) AS groups_with_account_difference,
        COUNTIF(amount_versions > 1) AS groups_with_amount_difference,
        COUNTIF(direction_versions > 1) AS groups_with_direction_difference,
        COUNTIF(date_versions > 1) AS groups_with_date_difference,
        COUNTIF(type_versions > 1) AS groups_with_type_difference,
        COUNTIF(status_versions > 1) AS groups_with_status_difference,
        COUNTIF(reference_versions > 1) AS groups_with_reference_difference,
        COUNTIF(description_versions > 1) AS groups_with_description_difference
      FROM duplicate_groups
      GROUP BY org_key
      ORDER BY org_key
    """,
    "vendor_payments": """
      WITH grid AS (SELECT org_key FROM UNNEST(['india', 'us']) org_key),
      metrics AS (
        SELECT
          source_org_key AS org_key,
          COUNT(*) AS payment_count,
          COUNT(DISTINCT payment_id) AS distinct_payment_ids,
          COUNTIF(allocation_status = 'Fully Allocated') AS fully_allocated,
          COUNTIF(allocation_status = 'Partially Allocated') AS partially_allocated,
          COUNTIF(allocation_status = 'Unallocated') AS unallocated,
          COUNTIF(allocation_status IN ('Needs Review', 'Allocation Exceeds Payment')) AS needs_review,
          SUM(payment_amount) AS payment_total,
          SUM(allocated_amount) AS allocated_total,
          SUM(unapplied_amount) AS unapplied_total,
          STRING_AGG(DISTINCT currency_code ORDER BY currency_code) AS currencies,
          COUNTIF(payment_id IS NULL OR payment_id = '') AS missing_payment_ids
        FROM `finance_silver.fact_vendor_payments`
        GROUP BY org_key
      )
      SELECT
        grid.org_key,
        COALESCE(payment_count, 0) AS payment_count,
        COALESCE(distinct_payment_ids, 0) AS distinct_payment_ids,
        COALESCE(fully_allocated, 0) AS fully_allocated,
        COALESCE(partially_allocated, 0) AS partially_allocated,
        COALESCE(unallocated, 0) AS unallocated,
        COALESCE(needs_review, 0) AS needs_review,
        COALESCE(payment_total, 0) AS payment_total,
        COALESCE(allocated_total, 0) AS allocated_total,
        COALESCE(unapplied_total, 0) AS unapplied_total,
        currencies,
        COALESCE(missing_payment_ids, 0) AS missing_payment_ids,
        COALESCE(payment_count - distinct_payment_ids, 0) AS duplicate_payment_ids
      FROM grid LEFT JOIN metrics USING (org_key)
      ORDER BY org_key
    """,
    "vendor_payment_currency_totals": """
      SELECT
        source_org_key AS org_key,
        currency_code,
        COUNT(*) AS payment_count,
        SUM(payment_amount) AS payment_total,
        SUM(allocated_amount) AS allocated_total,
        SUM(unapplied_amount) AS unapplied_total
      FROM `finance_silver.fact_vendor_payments`
      GROUP BY org_key, currency_code
      ORDER BY org_key, currency_code
    """,
    "allocations": """
      WITH grid AS (SELECT org_key FROM UNNEST(['india', 'us']) org_key),
      metrics AS (
        SELECT
          source_org_key AS org_key,
          COUNT(*) AS allocation_row_count,
          COUNT(DISTINCT payment_id) AS distinct_payment_count,
          COUNT(DISTINCT bill_id) AS distinct_bill_count,
          COUNTIF(bill_id IS NULL OR bill_id = '') AS missing_bill_id,
          COUNTIF(amount_applied IS NULL) AS missing_amount_applied,
          COUNT(*) - COUNT(DISTINCT allocation_key) AS duplicate_allocation_keys
        FROM `finance_silver.bridge_vendor_payment_bill_allocations`
        GROUP BY org_key
      )
      SELECT
        grid.org_key,
        COALESCE(allocation_row_count, 0) AS allocation_row_count,
        COALESCE(distinct_payment_count, 0) AS distinct_payment_count,
        COALESCE(distinct_bill_count, 0) AS distinct_bill_count,
        COALESCE(missing_bill_id, 0) AS missing_bill_id,
        COALESCE(missing_amount_applied, 0) AS missing_amount_applied,
        COALESCE(duplicate_allocation_keys, 0) AS duplicate_allocation_keys
      FROM grid LEFT JOIN metrics USING (org_key)
      ORDER BY org_key
    """,
    "bank_transactions": """
      WITH grid AS (SELECT org_key FROM UNNEST(['india', 'us']) org_key),
      metrics AS (
        SELECT
          source_org_key AS org_key,
          COUNT(*) AS transaction_count,
          COUNT(DISTINCT transaction_id) AS distinct_transaction_ids,
          COUNTIF(debit_or_credit = 'debit') AS debit_count,
          COUNTIF(debit_or_credit = 'credit') AS credit_count,
          COUNTIF(debit_or_credit NOT IN ('debit', 'credit') OR debit_or_credit IS NULL) AS unknown_direction_count,
          SUM(IF(debit_or_credit = 'debit', ABS(transaction_amount), 0)) AS total_debit,
          SUM(IF(debit_or_credit = 'credit', ABS(transaction_amount), 0)) AS total_credit,
          STRING_AGG(DISTINCT original_currency ORDER BY original_currency) AS currencies,
          COUNTIF(transaction_id IS NULL OR transaction_id = '') AS missing_transaction_ids
        FROM `finance_silver.fact_bank_transactions`
        GROUP BY org_key
      )
      SELECT
        grid.org_key,
        COALESCE(transaction_count, 0) AS transaction_count,
        COALESCE(distinct_transaction_ids, 0) AS distinct_transaction_ids,
        COALESCE(debit_count, 0) AS debit_count,
        COALESCE(credit_count, 0) AS credit_count,
        COALESCE(unknown_direction_count, 0) AS unknown_direction_count,
        COALESCE(total_debit, 0) AS total_debit,
        COALESCE(total_credit, 0) AS total_credit,
        currencies,
        COALESCE(missing_transaction_ids, 0) AS missing_transaction_ids,
        COALESCE(transaction_count - distinct_transaction_ids, 0) AS duplicate_transaction_ids
      FROM grid LEFT JOIN metrics USING (org_key)
      ORDER BY org_key
    """,
    "cross_source": """
      WITH payment_allocations AS (
        SELECT source_org_key, source_org_id, payment_id, SUM(COALESCE(amount_applied, 0)) AS allocated
        FROM `finance_silver.bridge_vendor_payment_bill_allocations`
        GROUP BY source_org_key, source_org_id, payment_id
      ),
      payment_metrics AS (
        SELECT
          payment.source_org_key AS org_key,
          COUNTIF(payment.vendor_id IS NULL OR payment.vendor_id = '') AS missing_vendor_ids,
          COUNTIF(COALESCE(allocation.allocated, 0) - payment.payment_amount > 0.01) AS payments_exceeded
        FROM `finance_silver.fact_vendor_payments` payment
        LEFT JOIN payment_allocations allocation
          USING (source_org_key, source_org_id, payment_id)
        GROUP BY org_key
      ),
      allocation_metrics AS (
        SELECT
          allocation.source_org_key AS org_key,
          COUNTIF(payment.payment_id IS NULL) AS orphan_allocation_payments,
          COUNTIF(
            allocation.bill_id IS NOT NULL
            AND allocation.bill_id != ''
            AND bill.bill_id IS NULL
          ) AS allocated_bills_missing_from_fact_bills,
          COUNTIF(
            payment.payment_id IS NULL
            AND payment_any_org.payment_id IS NOT NULL
          ) AS payment_org_mismatches,
          COUNTIF(
            bill.bill_id IS NULL
            AND bill_any_org.bill_id IS NOT NULL
          ) AS bill_org_mismatches
        FROM `finance_silver.bridge_vendor_payment_bill_allocations` allocation
        LEFT JOIN `finance_silver.fact_vendor_payments` payment
          ON payment.source_org_id = allocation.source_org_id
         AND payment.payment_id = allocation.payment_id
        LEFT JOIN (
          SELECT payment_id, ANY_VALUE(source_org_id) AS source_org_id
          FROM `finance_silver.fact_vendor_payments`
          GROUP BY payment_id
        ) payment_any_org
          ON payment_any_org.payment_id = allocation.payment_id
        LEFT JOIN `finance_silver.fact_bills` bill
          ON bill.source_org_id = allocation.source_org_id
         AND bill.bill_id = allocation.bill_id
        LEFT JOIN (
          SELECT bill_id, ANY_VALUE(source_org_id) AS source_org_id
          FROM `finance_silver.fact_bills`
          GROUP BY bill_id
        ) bill_any_org
          ON bill_any_org.bill_id = allocation.bill_id
        GROUP BY org_key
      ),
      org_metrics AS (
        SELECT
          org_key,
          MAX(payment_org_id_count) AS payment_org_id_count,
          MAX(bank_org_id_count) AS bank_org_id_count
        FROM (
          SELECT source_org_key AS org_key, COUNT(DISTINCT source_org_id) AS payment_org_id_count, 0 AS bank_org_id_count
          FROM `finance_silver.fact_vendor_payments`
          GROUP BY org_key
          UNION ALL
          SELECT source_org_key AS org_key, 0, COUNT(DISTINCT source_org_id)
          FROM `finance_silver.fact_bank_transactions`
          GROUP BY org_key
        )
        GROUP BY org_key
      ),
      cross_org_ids AS (
        SELECT
          COUNTIF(org_count > 1) AS payment_ids_in_multiple_orgs
        FROM (
          SELECT payment_id, COUNT(DISTINCT source_org_id) AS org_count
          FROM `finance_silver.fact_vendor_payments`
          GROUP BY payment_id
        )
      ),
      grid AS (SELECT org_key FROM UNNEST(['india', 'us']) org_key)
      SELECT
        grid.org_key,
        COALESCE(payment_metrics.missing_vendor_ids, 0) AS missing_vendor_ids,
        COALESCE(payment_metrics.payments_exceeded, 0) AS payments_exceeded,
        COALESCE(allocation_metrics.orphan_allocation_payments, 0) AS orphan_allocation_payments,
        COALESCE(allocation_metrics.allocated_bills_missing_from_fact_bills, 0) AS allocated_bills_missing_from_fact_bills,
        COALESCE(allocation_metrics.payment_org_mismatches, 0) AS payment_org_mismatches,
        COALESCE(allocation_metrics.bill_org_mismatches, 0) AS bill_org_mismatches,
        COALESCE(org_metrics.payment_org_id_count, 0) AS payment_org_id_count,
        COALESCE(org_metrics.bank_org_id_count, 0) AS bank_org_id_count,
        cross_org_ids.payment_ids_in_multiple_orgs
      FROM grid
      CROSS JOIN cross_org_ids
      LEFT JOIN payment_metrics USING (org_key)
      LEFT JOIN allocation_metrics USING (org_key)
      LEFT JOIN org_metrics USING (org_key)
      ORDER BY org_key
    """,
}


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def run_validation(run_id: str) -> dict[str, list[dict[str, Any]]]:
    """Run aggregate-only BigQuery checks and print safe JSON summaries."""
    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        raise RuntimeError("GCP_PROJECT_ID is required")
    location = os.getenv("BQ_LOCATION") or os.getenv("GCP_LOCATION") or "asia-south1"
    client = bigquery.Client(project=project_id)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    )
    results = {}
    for check_name, query in VALIDATION_QUERIES.items():
        rows = [dict(row.items()) for row in client.query(query, job_config=job_config, location=location).result()]
        results[check_name] = rows
        print(f"{check_name}={json.dumps(rows, default=_json_default, sort_keys=True)}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Controlled ETL run ID to validate.")
    args = parser.parse_args()
    run_validation(args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
