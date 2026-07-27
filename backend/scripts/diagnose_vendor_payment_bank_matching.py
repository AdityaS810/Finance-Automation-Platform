"""Read-only Phase 4.1 diagnostics for India vendor-payment bank matching.

Every query returns aggregate counts only. The script never selects or prints
vendor names, account IDs, references, descriptions, payment IDs, bank
transaction IDs, bank-leg keys, tokens, or credentials.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv
from google.cloud import bigquery


load_dotenv()

TOLERANCE = "0.01"

CONDITIONS = (
    (1, "Same organization only", "same_org"),
    (2, "Same organization + currency", "same_org AND same_currency"),
    (3, "Same organization + exact amount", "same_org AND exact_amount"),
    (
        4,
        "Same organization + currency + exact amount",
        "same_org AND same_currency AND exact_amount",
    ),
    (
        5,
        "Same organization + currency + exact amount + same date",
        "same_org AND same_currency AND exact_amount AND same_date",
    ),
    (
        6,
        "Same organization + currency + exact amount + date within 3 days",
        "same_org AND same_currency AND exact_amount AND within_3_days",
    ),
    (
        7,
        "Same organization + currency + exact amount + date within 7 days",
        "same_org AND same_currency AND exact_amount AND within_7_days",
    ),
    (
        8,
        "Same organization + exact paid-through account ID",
        "same_org AND exact_account",
    ),
    (
        9,
        "Same organization + amount + paid-through account ID",
        "same_org AND exact_amount AND exact_account",
    ),
    (
        10,
        "Same organization + amount + date + paid-through account ID",
        "same_org AND exact_amount AND same_date AND exact_account",
    ),
    (
        11,
        "Exact normalized reference matches",
        "same_org AND exact_reference",
    ),
    (
        12,
        "Amount + normalized reference matches",
        "same_org AND exact_amount AND exact_reference",
    ),
    (
        13,
        "Amount + reference + date within 7 days",
        "same_org AND exact_amount AND exact_reference AND within_7_days",
    ),
)

BASE_CTES = f"""
  WITH payments AS (
    SELECT
      source_org_id,
      payment_id,
      payment_date,
      payment_amount,
      NULLIF(TRIM(currency_code), '') AS currency,
      NULLIF(TRIM(paid_through_account_id), '') AS account_id,
      UPPER(
        REGEXP_REPLACE(TRIM(COALESCE(reference_number, '')), r'[^A-Z0-9]+', '')
      ) AS normalized_reference
    FROM `finance_silver.fact_vendor_payments`
    WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
  ),
  bank_debits AS (
    SELECT
      source_org_id,
      bank_transaction_leg_key,
      transaction_date,
      transaction_amount,
      NULLIF(TRIM(original_currency), '') AS currency,
      NULLIF(TRIM(account_id), '') AS account_id,
      UPPER(
        REGEXP_REPLACE(TRIM(COALESCE(reference_number, '')), r'[^A-Z0-9]+', '')
      ) AS normalized_reference
    FROM `finance_silver.fact_bank_transactions`
    WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
      AND debit_or_credit = 'debit'
      AND transaction_direction = 'Outgoing'
  ),
  pairs AS (
    SELECT
      payment.payment_id,
      bank.bank_transaction_leg_key,
      payment.payment_date,
      bank.transaction_date AS bank_date,
      payment.payment_amount,
      bank.transaction_amount AS bank_amount,
      payment.currency AS payment_currency,
      bank.currency AS bank_currency,
      payment.account_id AS payment_account_id,
      bank.account_id AS bank_account_id,
      payment.normalized_reference AS payment_reference,
      bank.normalized_reference AS bank_reference,
      payment.source_org_id = bank.source_org_id AS same_org,
      payment.currency IS NOT NULL
        AND bank.currency IS NOT NULL
        AND UPPER(payment.currency) = UPPER(bank.currency) AS same_currency,
      payment.payment_amount IS NOT NULL
        AND bank.transaction_amount IS NOT NULL
        AND ABS(payment.payment_amount - bank.transaction_amount) <= {TOLERANCE}
        AS exact_amount,
      payment.payment_date IS NOT NULL
        AND bank.transaction_date = payment.payment_date AS same_date,
      payment.payment_date IS NOT NULL
        AND bank.transaction_date IS NOT NULL
        AND ABS(DATE_DIFF(bank.transaction_date, payment.payment_date, DAY)) <= 3
        AS within_3_days,
      payment.payment_date IS NOT NULL
        AND bank.transaction_date IS NOT NULL
        AND ABS(DATE_DIFF(bank.transaction_date, payment.payment_date, DAY)) <= 7
        AS within_7_days,
      payment.account_id IS NOT NULL
        AND bank.account_id IS NOT NULL
        AND payment.account_id = bank.account_id AS exact_account,
      payment.normalized_reference != ''
        AND bank.normalized_reference != ''
        AND payment.normalized_reference = bank.normalized_reference AS exact_reference
    FROM payments payment
    CROSS JOIN bank_debits bank
  )
"""


def _progressive_query() -> str:
    selects = []
    for condition_number, condition_name, predicate in CONDITIONS:
        escaped_name = condition_name.replace("'", "''")
        selects.append(
            f"""
            SELECT
              {condition_number} AS condition_number,
              '{escaped_name}' AS condition_name,
              COUNTIF({predicate}) AS candidate_pair_count,
              COUNT(DISTINCT IF({predicate}, payment_id, NULL))
                AS payments_with_candidates,
              COUNT(DISTINCT IF(candidate_count = 1, payment_id, NULL))
                AS payments_with_exactly_one_candidate,
              COUNT(DISTINCT IF(candidate_count > 1, payment_id, NULL))
                AS payments_with_multiple_candidates
            FROM pairs
            LEFT JOIN (
              SELECT
                payment_id AS counted_payment_id,
                COUNTIF({predicate}) AS candidate_count
              FROM pairs
              GROUP BY payment_id
            )
              ON counted_payment_id = payment_id
            """
        )
    return BASE_CTES + "\n" + "\nUNION ALL\n".join(selects) + "\nORDER BY condition_number"


DIAGNOSTIC_QUERIES = {
    "source_population": """
      WITH payments AS (
        SELECT paid_through_account_id, currency_code
        FROM `finance_silver.fact_vendor_payments`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
      ),
      bank_debits AS (
        SELECT account_id, original_currency
        FROM `finance_silver.fact_bank_transactions`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
          AND debit_or_credit = 'debit'
          AND transaction_direction = 'Outgoing'
      ),
      payment_profile AS (
        SELECT
          COUNT(*) AS vendor_payment_count,
          COUNTIF(NULLIF(TRIM(paid_through_account_id), '') IS NOT NULL)
            AS populated_paid_through_account_count,
          COUNT(DISTINCT NULLIF(TRIM(paid_through_account_id), ''))
            AS distinct_paid_through_account_count,
          COUNTIF(NULLIF(TRIM(currency_code), '') IS NOT NULL)
            AS populated_payment_currency_count,
          COUNTIF(NULLIF(TRIM(currency_code), '') IS NULL)
            AS missing_payment_currency_count
        FROM payments
      ),
      bank_profile AS (
        SELECT
          COUNT(*) AS outgoing_bank_debit_count,
          COUNT(DISTINCT NULLIF(TRIM(account_id), '')) AS distinct_bank_account_count,
          COUNTIF(NULLIF(TRIM(original_currency), '') IS NOT NULL)
            AS populated_bank_currency_count,
          COUNTIF(NULLIF(TRIM(original_currency), '') IS NULL)
            AS missing_bank_currency_count
        FROM bank_debits
      ),
      overlap_profile AS (
        SELECT COUNT(*) AS distinct_exact_account_id_overlaps
        FROM (
          SELECT DISTINCT NULLIF(TRIM(paid_through_account_id), '') AS account_id
          FROM payments
        )
        JOIN (
          SELECT DISTINCT NULLIF(TRIM(account_id), '') AS account_id
          FROM bank_debits
        )
        USING (account_id)
        WHERE account_id IS NOT NULL
      )
      SELECT * FROM payment_profile
      CROSS JOIN bank_profile
      CROSS JOIN overlap_profile
    """,
    "progressive_matching_conditions": _progressive_query(),
    "amount_candidate_profile": BASE_CTES
    + """
      , per_payment AS (
        SELECT
          payment_id,
          COUNTIF(same_org AND exact_amount) AS amount_candidate_count,
          COUNTIF(same_org AND exact_amount AND same_date) AS amount_date_candidate_count
        FROM pairs
        GROUP BY payment_id
      )
      SELECT
        COUNTIF(amount_candidate_count > 0) AS payments_whose_amount_appears_in_bank_debits,
        COUNTIF(amount_candidate_count = 0) AS payments_with_no_amount_candidate,
        COUNTIF(amount_candidate_count = 1) AS payments_with_one_amount_candidate,
        COUNTIF(amount_candidate_count > 1) AS payments_with_multiple_amount_candidates,
        COUNTIF(amount_date_candidate_count = 1) AS payments_with_exactly_one_amount_date_candidate,
        COUNTIF(amount_date_candidate_count > 1) AS payments_with_multiple_amount_date_candidates
      FROM per_payment
    """,
    "date_difference_distribution": BASE_CTES
    + """
      SELECT
        DATE_DIFF(bank_date, payment_date, DAY) AS date_difference_days,
        COUNT(*) AS exact_amount_candidate_pairs,
        COUNT(DISTINCT payment_id) AS payments_with_candidate
      FROM pairs
      WHERE same_org
        AND exact_amount
        AND payment_date IS NOT NULL
        AND bank_date IS NOT NULL
      GROUP BY date_difference_days
      ORDER BY date_difference_days
    """,
    "currency_population": """
      WITH currency_rows AS (
        SELECT
          'vendor_payments' AS source,
          COALESCE(NULLIF(UPPER(TRIM(currency_code)), ''), '<missing>') AS currency,
          COUNT(*) AS row_count
        FROM `finance_silver.fact_vendor_payments`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
        GROUP BY currency
        UNION ALL
        SELECT
          'outgoing_bank_debits',
          COALESCE(NULLIF(UPPER(TRIM(original_currency)), ''), '<missing>'),
          COUNT(*)
        FROM `finance_silver.fact_bank_transactions`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
          AND debit_or_credit = 'debit'
          AND transaction_direction = 'Outgoing'
        GROUP BY COALESCE(NULLIF(UPPER(TRIM(original_currency)), ''), '<missing>')
      )
      SELECT source, currency, row_count
      FROM currency_rows
      ORDER BY source, currency
    """,
    "currency_match_for_amount_candidates": BASE_CTES
    + """
      , per_payment AS (
        SELECT
          payment_id,
          COUNTIF(same_org AND exact_amount) AS amount_candidates,
          COUNTIF(same_org AND exact_amount AND same_currency) AS matching_currency_candidates,
          COUNTIF(same_org AND exact_amount AND NOT same_currency) AS mismatching_currency_candidates
        FROM pairs
        GROUP BY payment_id
      )
      SELECT
        SUM(amount_candidates) AS exact_amount_candidate_pairs,
        SUM(matching_currency_candidates) AS matching_currency_candidate_pairs,
        SUM(mismatching_currency_candidates) AS mismatching_currency_candidate_pairs,
        COUNTIF(amount_candidates > 0 AND matching_currency_candidates = 0)
          AS payments_with_amount_candidates_but_no_currency_match
      FROM per_payment
    """,
    "strict_candidate_exclusions": f"""
      WITH payments AS (
        SELECT
          source_org_id,
          payment_id,
          payment_date,
          payment_amount,
          UPPER(NULLIF(TRIM(currency_code), '')) AS currency,
          NULLIF(TRIM(paid_through_account_id), '') AS account_id
        FROM `finance_silver.fact_vendor_payments`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
      ),
      bank_debits AS (
        SELECT
          source_org_id,
          transaction_date,
          transaction_amount,
          UPPER(NULLIF(TRIM(original_currency), '')) AS currency,
          NULLIF(TRIM(account_id), '') AS account_id,
          multi_leg_transaction,
          bank_data_quality_status
        FROM `finance_silver.fact_bank_transactions`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
          AND debit_or_credit = 'debit'
          AND transaction_direction = 'Outgoing'
      ),
      strict_candidates AS (
        SELECT
          payment.payment_id,
          bank.multi_leg_transaction,
          bank.bank_data_quality_status
        FROM payments payment
        JOIN bank_debits bank
          ON bank.source_org_id = payment.source_org_id
         AND bank.currency = payment.currency
         AND bank.account_id = payment.account_id
         AND ABS(bank.transaction_amount - payment.payment_amount) <= {TOLERANCE}
         AND bank.transaction_date = payment.payment_date
      )
      SELECT
        COUNT(*) AS strict_candidate_pairs_before_safety_gates,
        COUNT(DISTINCT payment_id) AS strict_candidate_payments_before_safety_gates,
        COUNTIF(
          bank_data_quality_status = 'Valid' AND NOT multi_leg_transaction
        ) AS valid_single_leg_candidate_pairs,
        COUNTIF(
          bank_data_quality_status = 'Valid' AND multi_leg_transaction
        ) AS valid_multi_leg_candidate_pairs,
        COUNTIF(bank_data_quality_status != 'Valid') AS invalid_quality_candidate_pairs
      FROM strict_candidates
    """,
    "account_amount_date_window_safety": f"""
      WITH payments AS (
        SELECT
          source_org_id,
          payment_id,
          payment_date,
          payment_amount,
          UPPER(NULLIF(TRIM(currency_code), '')) AS currency,
          NULLIF(TRIM(paid_through_account_id), '') AS account_id
        FROM `finance_silver.fact_vendor_payments`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
      ),
      bank_debits AS (
        SELECT
          source_org_id,
          transaction_date,
          transaction_amount,
          UPPER(NULLIF(TRIM(original_currency), '')) AS currency,
          NULLIF(TRIM(account_id), '') AS account_id,
          multi_leg_transaction,
          bank_data_quality_status
        FROM `finance_silver.fact_bank_transactions`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
          AND debit_or_credit = 'debit'
          AND transaction_direction = 'Outgoing'
      ),
      candidate_rows AS (
        SELECT
          window_days,
          payment.payment_id,
          bank.bank_data_quality_status = 'Valid'
            AND NOT bank.multi_leg_transaction AS valid_single_leg,
          bank.bank_data_quality_status = 'Valid'
            AND bank.multi_leg_transaction AS valid_multi_leg
        FROM payments payment
        JOIN bank_debits bank
          ON bank.source_org_id = payment.source_org_id
         AND bank.currency = payment.currency
         AND bank.account_id = payment.account_id
         AND ABS(bank.transaction_amount - payment.payment_amount) <= {TOLERANCE}
        CROSS JOIN UNNEST([0, 3, 7]) AS window_days
        WHERE payment.payment_date IS NOT NULL
          AND bank.transaction_date IS NOT NULL
          AND ABS(DATE_DIFF(bank.transaction_date, payment.payment_date, DAY)) <= window_days
      ),
      by_payment AS (
        SELECT
          window_days,
          payment_id,
          COUNT(*) AS all_candidate_count,
          COUNTIF(valid_single_leg) AS valid_single_leg_count,
          COUNTIF(valid_multi_leg) AS valid_multi_leg_count
        FROM candidate_rows
        GROUP BY window_days, payment_id
      )
      SELECT
        window_days,
        SUM(all_candidate_count) AS candidate_pair_count,
        COUNT(*) AS payments_with_candidates,
        COUNTIF(valid_single_leg_count = 1) AS payments_with_one_valid_single_leg_candidate,
        COUNTIF(valid_single_leg_count > 1) AS payments_with_multiple_valid_single_leg_candidates,
        COUNTIF(valid_single_leg_count = 0 AND valid_multi_leg_count > 0)
          AS payments_with_only_valid_multi_leg_candidates
      FROM by_payment
      GROUP BY window_days
      ORDER BY window_days
    """,
    "outgoing_debit_safety_profile": """
      SELECT
        bank_data_quality_status,
        multi_leg_transaction,
        COUNT(*) AS outgoing_debit_count
      FROM `finance_silver.fact_bank_transactions`
      WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
        AND debit_or_credit = 'debit'
        AND transaction_direction = 'Outgoing'
      GROUP BY bank_data_quality_status, multi_leg_transaction
      ORDER BY bank_data_quality_status, multi_leg_transaction
    """,
    "current_gold_status_profile": """
      SELECT
        bank_match_status,
        bank_match_method,
        COUNT(*) AS vendor_payment_count
      FROM `finance_gold.vendor_payment_bank_matches`
      WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
      GROUP BY bank_match_status, bank_match_method
      ORDER BY bank_match_status, bank_match_method
    """,
    "grouped_and_split_evidence": f"""
      WITH payments AS (
        SELECT
          source_org_id,
          payment_id,
          payment_date,
          payment_amount,
          UPPER(NULLIF(TRIM(currency_code), '')) AS currency
        FROM `finance_silver.fact_vendor_payments`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
          AND payment_amount IS NOT NULL
          AND payment_date IS NOT NULL
      ),
      bank_debits AS (
        SELECT
          source_org_id,
          bank_transaction_leg_key,
          transaction_date,
          transaction_amount,
          UPPER(NULLIF(TRIM(original_currency), '')) AS currency
        FROM `finance_silver.fact_bank_transactions`
        WHERE LOWER(COALESCE(source_org_key, '')) = 'india'
          AND debit_or_credit = 'debit'
          AND transaction_direction = 'Outgoing'
          AND transaction_amount IS NOT NULL
          AND transaction_date IS NOT NULL
      ),
      grouped_candidates AS (
        SELECT
          bank.bank_transaction_leg_key,
          COUNT(*) AS candidate_combinations
        FROM payments first_payment
        JOIN payments second_payment
          ON second_payment.source_org_id = first_payment.source_org_id
         AND second_payment.currency = first_payment.currency
         AND second_payment.payment_id > first_payment.payment_id
        JOIN bank_debits bank
          ON bank.source_org_id = first_payment.source_org_id
         AND bank.currency = first_payment.currency
         AND ABS(DATE_DIFF(bank.transaction_date, first_payment.payment_date, DAY)) <= 7
         AND ABS(DATE_DIFF(bank.transaction_date, second_payment.payment_date, DAY)) <= 7
         AND ABS(
           first_payment.payment_amount
           + second_payment.payment_amount
           - bank.transaction_amount
         ) <= {TOLERANCE}
        GROUP BY bank.bank_transaction_leg_key
      ),
      split_candidates AS (
        SELECT
          payment.payment_id,
          COUNT(*) AS candidate_combinations
        FROM bank_debits first_bank
        JOIN bank_debits second_bank
          ON second_bank.source_org_id = first_bank.source_org_id
         AND second_bank.currency = first_bank.currency
         AND second_bank.bank_transaction_leg_key > first_bank.bank_transaction_leg_key
        JOIN payments payment
          ON payment.source_org_id = first_bank.source_org_id
         AND payment.currency = first_bank.currency
         AND ABS(DATE_DIFF(first_bank.transaction_date, payment.payment_date, DAY)) <= 7
         AND ABS(DATE_DIFF(second_bank.transaction_date, payment.payment_date, DAY)) <= 7
         AND ABS(
           first_bank.transaction_amount
           + second_bank.transaction_amount
           - payment.payment_amount
         ) <= {TOLERANCE}
        GROUP BY payment.payment_id
      )
      SELECT
        (SELECT COUNT(*) FROM grouped_candidates)
          AS bank_debits_equal_to_two_vendor_payments,
        (SELECT COALESCE(SUM(candidate_combinations), 0) FROM grouped_candidates)
          AS grouped_payment_candidate_combinations,
        (SELECT COUNT(*) FROM split_candidates)
          AS vendor_payments_equal_to_two_bank_debits,
        (SELECT COALESCE(SUM(candidate_combinations), 0) FROM split_candidates)
          AS split_bank_candidate_combinations
    """,
}

FORBIDDEN_OUTPUT_FIELDS = (
    "vendor_name",
    "paid_through_account_id",
    "account_id",
    "reference_number",
    "description",
    "payment_id",
    "transaction_id",
    "bank_transaction_leg_key",
)


def _safe_value(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if value is None:
        return "null"
    return str(value)


def _validate_read_only_queries() -> None:
    forbidden_statements = (
        "CREATE ",
        "REPLACE ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "MERGE ",
        "DROP ",
        "ALTER ",
        "TRUNCATE ",
    )
    for section, query in DIAGNOSTIC_QUERIES.items():
        uppercase_query = query.upper()
        if any(token in uppercase_query for token in forbidden_statements):
            raise RuntimeError(f"Diagnostic section is not read-only: {section}")


def run_diagnostic() -> dict[str, list[dict[str, Any]]]:
    """Run the aggregate-only diagnostic queries."""
    _validate_read_only_queries()
    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        raise RuntimeError("GCP_PROJECT_ID is required")
    location = os.getenv("BQ_LOCATION") or os.getenv("GCP_LOCATION") or "asia-south1"
    client = bigquery.Client(project=project_id)
    output: dict[str, list[dict[str, Any]]] = {}
    for section, query in DIAGNOSTIC_QUERIES.items():
        rows = [dict(row.items()) for row in client.query(query, location=location).result()]
        for row in rows:
            exposed = {column.lower() for column in row}
            forbidden = exposed.intersection(FORBIDDEN_OUTPUT_FIELDS)
            if forbidden:
                raise RuntimeError(
                    f"Unsafe diagnostic output columns in {section}: {sorted(forbidden)}"
                )
        output[section] = rows
    return output


def main() -> int:
    result = run_diagnostic()
    for section, rows in result.items():
        print(f"[{section}]")
        if not rows:
            print("no_rows")
            continue
        for row in rows:
            print(" | ".join(f"{key}={_safe_value(value)}" for key, value in row.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
