-- Phase 4 controlled migration: only the three vendor-reconciliation Gold views.
-- The current vendor-payment source has no repository-confirmed direct bank
-- transaction identifier, so direct_bank_transaction_id is deliberately not
-- inferred from payment references, narration, or vendor names.

CREATE OR REPLACE VIEW `finance_gold.vendor_payment_bank_matches` AS
WITH payment_base AS (
  SELECT
    source_org_id,
    source_org_key,
    payment_id,
    vendor_id,
    vendor_name,
    payment_date,
    payment_number,
    reference_number AS payment_reference,
    UPPER(NULLIF(TRIM(currency_code), '')) AS payment_currency,
    payment_amount,
    paid_through_account_id,
    UPPER(REGEXP_REPLACE(TRIM(COALESCE(reference_number, '')), r'[^A-Z0-9]+', ''))
      AS normalized_payment_reference,
    CASE
      WHEN source_org_id IS NULL OR payment_id IS NULL OR payment_id = ''
        THEN 'Missing organization or payment ID'
      WHEN payment_date IS NULL THEN 'Missing or invalid payment date'
      WHEN paid_through_account_id IS NULL OR paid_through_account_id = ''
        THEN 'Missing paid-through account'
      WHEN currency_code IS NULL OR TRIM(currency_code) = '' THEN 'Missing payment currency'
      WHEN payment_amount IS NULL OR payment_amount <= 0 THEN 'Missing or invalid payment amount'
      ELSE CAST(NULL AS STRING)
    END AS invalid_payment_reason
  FROM `finance_silver.fact_vendor_payments`
),
bank_base AS (
  SELECT
    source_org_id,
    bank_transaction_leg_key,
    transaction_id,
    account_id,
    transaction_date,
    reference_number,
    UPPER(NULLIF(TRIM(original_currency), '')) AS bank_currency,
    transaction_amount,
    debit_or_credit,
    transaction_direction,
    multi_leg_transaction,
    bank_data_quality_status,
    bank_data_quality_reason,
    UPPER(REGEXP_REPLACE(TRIM(COALESCE(reference_number, '')), r'[^A-Z0-9]+', ''))
      AS normalized_bank_reference
  FROM `finance_silver.fact_bank_transactions`
),
review_candidates AS (
  -- Phase 4.2: exact account/currency/amount candidates within three days are
  -- review-only. A direct source bank transaction ID remains unavailable.
  SELECT
    payment.source_org_id,
    payment.payment_id,
    bank.bank_transaction_leg_key,
    bank.transaction_id AS bank_transaction_id,
    bank.account_id AS bank_account_id,
    bank.transaction_date AS bank_transaction_date,
    bank.reference_number AS bank_reference,
    bank.transaction_amount AS bank_amount,
    DATE_DIFF(bank.transaction_date, payment.payment_date, DAY) AS date_difference_days,
    bank.multi_leg_transaction,
    IF(
      bank.multi_leg_transaction,
      'multi_leg_transaction_review',
      'exact_account_amount_date_window_review'
    ) AS match_method,
    IF(
      bank.multi_leg_transaction,
      'Exact organization, currency, debit direction, account, amount, and date window '
        || 'candidate is multi-leg and cannot be verified automatically',
      'Unique single-leg candidate has exact organization, currency, debit direction, '
        || 'account, and amount within 3 days; manual review is required'
    ) AS match_reason
  FROM payment_base payment
  JOIN bank_base bank
    ON bank.source_org_id = payment.source_org_id
   AND bank.bank_currency = payment.payment_currency
   AND bank.debit_or_credit = 'debit'
   AND bank.transaction_direction = 'Outgoing'
   AND bank.account_id = payment.paid_through_account_id
   AND ABS(bank.transaction_amount - payment.payment_amount) <= 0.01
   AND ABS(DATE_DIFF(bank.transaction_date, payment.payment_date, DAY)) <= 3
  WHERE payment.invalid_payment_reason IS NULL
    AND bank.bank_data_quality_status = 'Valid'
),
candidate_summary AS (
  SELECT
    source_org_id,
    payment_id,
    COUNT(*) AS candidate_count,
    ARRAY_AGG(
      STRUCT(
        bank_transaction_leg_key,
        bank_transaction_id,
        bank_account_id,
        bank_transaction_date,
        bank_reference,
        bank_amount,
        date_difference_days,
        multi_leg_transaction,
        match_method,
        match_reason
      )
      ORDER BY bank_transaction_leg_key
      LIMIT 1
    )[OFFSET(0)] AS candidate
  FROM review_candidates
  GROUP BY source_org_id, payment_id
),
bank_leg_claims AS (
  SELECT * EXCEPT(candidate_rank)
  FROM (
    SELECT
      candidate_summary.candidate.bank_transaction_leg_key,
      COUNT(*) OVER (
        PARTITION BY candidate_summary.candidate.bank_transaction_leg_key
      ) AS claiming_payment_count,
      ROW_NUMBER() OVER (
        PARTITION BY candidate_summary.candidate.bank_transaction_leg_key
        ORDER BY candidate_summary.source_org_id, candidate_summary.payment_id
      ) AS candidate_rank
    FROM candidate_summary
    WHERE candidate_count = 1
  )
  WHERE candidate_rank = 1
),
invalid_bank_candidates AS (
  SELECT
    payment.source_org_id,
    payment.payment_id,
    COUNTIF(bank.bank_data_quality_status != 'Valid') AS invalid_bank_candidate_count,
    STRING_AGG(
      DISTINCT IF(
        bank.bank_data_quality_status != 'Valid',
        COALESCE(bank.bank_data_quality_reason, 'Invalid bank data'),
        NULL
      ),
      '; '
    ) AS invalid_bank_reason
  FROM payment_base payment
  JOIN bank_base bank
    ON bank.source_org_id = payment.source_org_id
   AND bank.bank_currency = payment.payment_currency
   AND bank.account_id = payment.paid_through_account_id
   AND bank.transaction_amount IS NOT NULL
   AND ABS(bank.transaction_amount - payment.payment_amount) <= 0.01
   AND ABS(DATE_DIFF(bank.transaction_date, payment.payment_date, DAY)) <= 3
  WHERE payment.invalid_payment_reason IS NULL
    AND (
      (bank.debit_or_credit = 'debit' AND bank.transaction_direction = 'Outgoing')
      OR bank.debit_or_credit IS NULL
      OR bank.transaction_direction = 'Unknown'
    )
  GROUP BY payment.source_org_id, payment.payment_id
)
SELECT
  payment.source_org_id,
  payment.source_org_key,
  payment.payment_id,
  payment.vendor_id,
  payment.vendor_name,
  payment.payment_date,
  payment.payment_number,
  payment.payment_reference,
  payment.payment_currency,
  payment.payment_amount,
  payment.paid_through_account_id,
  IF(candidate_summary.candidate_count = 1, candidate_summary.candidate.bank_transaction_leg_key, NULL)
    AS bank_transaction_leg_key,
  IF(candidate_summary.candidate_count = 1, candidate_summary.candidate.bank_transaction_id, NULL)
    AS bank_transaction_id,
  IF(candidate_summary.candidate_count = 1, candidate_summary.candidate.bank_account_id, NULL)
    AS bank_account_id,
  IF(candidate_summary.candidate_count = 1, candidate_summary.candidate.bank_transaction_date, NULL)
    AS bank_transaction_date,
  IF(candidate_summary.candidate_count = 1, candidate_summary.candidate.bank_reference, NULL)
    AS bank_reference,
  IF(candidate_summary.candidate_count = 1, candidate_summary.candidate.bank_amount, NULL)
    AS bank_amount,
  IF(candidate_summary.candidate_count = 1, candidate_summary.candidate.date_difference_days, NULL)
    AS date_difference_days,
  COALESCE(candidate_summary.candidate_count, 0) AS candidate_count,
  CASE
    WHEN payment.invalid_payment_reason IS NOT NULL THEN 'Invalid Payment Data'
    WHEN candidate_summary.candidate_count > 1 THEN 'Ambiguous Bank Match'
    WHEN candidate_summary.candidate_count = 1
      AND bank_leg_claims.claiming_payment_count > 1 THEN 'Ambiguous Bank Match'
    WHEN candidate_summary.candidate_count = 1 THEN 'Bank Match Pending Review'
    WHEN COALESCE(invalid_bank_candidates.invalid_bank_candidate_count, 0) > 0
      THEN 'Invalid Bank Data'
    ELSE 'Bank Not Found'
  END AS bank_match_status,
  CASE
    WHEN candidate_summary.candidate_count > 1
      OR (
        candidate_summary.candidate_count = 1
        AND bank_leg_claims.claiming_payment_count > 1
      ) THEN 'ambiguous'
    WHEN candidate_summary.candidate_count = 1 THEN candidate_summary.candidate.match_method
    ELSE 'unmatched'
  END AS bank_match_method,
  CASE
    WHEN candidate_summary.candidate_count > 0 THEN CAST(0.0 AS NUMERIC)
    ELSE CAST(NULL AS NUMERIC)
  END AS bank_match_confidence,
  CASE
    WHEN payment.invalid_payment_reason IS NOT NULL THEN payment.invalid_payment_reason
    WHEN candidate_summary.candidate_count > 1
      THEN FORMAT('%d equally ranked bank legs satisfy the safe matching rule', candidate_summary.candidate_count)
    WHEN candidate_summary.candidate_count = 1
      AND bank_leg_claims.claiming_payment_count > 1
      THEN FORMAT(
        'Bank leg is proposed for %d vendor payments and was not assigned',
        bank_leg_claims.claiming_payment_count
      )
    WHEN candidate_summary.candidate_count = 1 THEN candidate_summary.candidate.match_reason
    WHEN COALESCE(invalid_bank_candidates.invalid_bank_candidate_count, 0) > 0
      THEN COALESCE(invalid_bank_candidates.invalid_bank_reason, 'Matching bank data is invalid')
    ELSE 'No safe outgoing debit candidate found'
  END AS bank_match_reason,
  CASE
    WHEN payment.invalid_payment_reason IS NULL
      AND COALESCE(candidate_summary.candidate_count, 0) = 0
      AND COALESCE(invalid_bank_candidates.invalid_bank_candidate_count, 0) = 0 THEN FALSE
    ELSE TRUE
  END AS review_required
FROM payment_base payment
LEFT JOIN candidate_summary
  USING (source_org_id, payment_id)
LEFT JOIN bank_leg_claims
  ON bank_leg_claims.bank_transaction_leg_key =
    candidate_summary.candidate.bank_transaction_leg_key
LEFT JOIN invalid_bank_candidates
  USING (source_org_id, payment_id);


CREATE OR REPLACE VIEW `finance_gold.vendor_bill_reconciliation` AS
WITH allocation_rows AS (
  SELECT
    allocation.source_org_id,
    allocation.bill_id,
    allocation.payment_id,
    allocation.amount_applied,
    allocation.payment_date,
    payment_match.bank_match_status,
    payment_match.bank_match_method,
    payment_match.bank_transaction_leg_key,
    payment_match.bank_amount,
    payment_match.review_required AS payment_review_required
  FROM `finance_silver.bridge_vendor_payment_bill_allocations` allocation
  LEFT JOIN `finance_gold.vendor_payment_bank_matches` payment_match
    ON payment_match.source_org_id = allocation.source_org_id
   AND payment_match.payment_id = allocation.payment_id
),
allocation_by_bill AS (
  SELECT
    source_org_id,
    bill_id,
    COUNT(DISTINCT payment_id) AS allocated_payment_count,
    SUM(COALESCE(amount_applied, 0)) AS allocated_amount,
    COUNT(DISTINCT IF(bank_match_status = 'Bank Verified', payment_id, NULL))
      AS bank_verified_payment_count,
    SUM(IF(bank_match_status = 'Bank Verified', COALESCE(amount_applied, 0), 0))
      AS bank_verified_amount,
    SUM(IF(bank_match_status != 'Bank Verified' OR bank_match_status IS NULL, COALESCE(amount_applied, 0), 0))
      AS bank_pending_amount,
    COUNTIF(bank_match_status IN (
      'Ambiguous Bank Match',
      'Invalid Payment Data',
      'Invalid Bank Data'
    )) AS unsafe_payment_count,
    MAX(payment_date) AS latest_payment_date,
    ARRAY_TO_STRING(
      ARRAY_AGG(DISTINCT payment_id IGNORE NULLS ORDER BY payment_id),
      ','
    ) AS payment_ids
  FROM allocation_rows
  GROUP BY source_org_id, bill_id
),
bank_evidence_candidates AS (
  SELECT
    bill.source_org_id,
    bill.bill_id,
    bank.bank_transaction_leg_key,
    bank.transaction_id,
    bank.transaction_date,
    bank.transaction_amount,
    bank.reference_number,
    COUNT(*) OVER (PARTITION BY bill.source_org_id, bill.bill_id) AS evidence_candidate_count
  FROM `finance_silver.fact_bills` bill
  JOIN `finance_silver.fact_bank_transactions` bank
    ON bank.source_org_id = bill.source_org_id
   AND UPPER(NULLIF(TRIM(bank.original_currency), '')) =
       UPPER(NULLIF(TRIM(bill.currency_code), ''))
   AND bank.debit_or_credit = 'debit'
   AND bank.transaction_direction = 'Outgoing'
   AND ABS(bank.transaction_amount - bill.total_amount) <= 0.01
   AND (
     UPPER(REGEXP_REPLACE(TRIM(COALESCE(bank.reference_number, '')), r'[^A-Z0-9]+', '')) =
       UPPER(REGEXP_REPLACE(TRIM(COALESCE(bill.bill_number, '')), r'[^A-Z0-9]+', ''))
     OR STRPOS(
       CONCAT(
         ' ',
         TRIM(REGEXP_REPLACE(UPPER(COALESCE(bank.description, '')), r'[^A-Z0-9]+', ' ')),
         ' '
       ),
       CONCAT(
         ' ',
         TRIM(REGEXP_REPLACE(UPPER(COALESCE(bill.bill_number, '')), r'[^A-Z0-9]+', ' ')),
         ' '
       )
     ) > 0
   )
  WHERE bill.bill_id IS NOT NULL
    AND bill.bill_number IS NOT NULL
    AND TRIM(bill.bill_number) != ''
    AND bill.total_amount IS NOT NULL
    AND bill.currency_code IS NOT NULL
    AND bank.bank_data_quality_status = 'Valid'
    AND NOT bank.multi_leg_transaction
),
bank_evidence_by_bill AS (
  SELECT
    source_org_id,
    bill_id,
    COUNT(*) AS bank_evidence_candidate_count,
    IF(
      COUNT(*) = 1,
      ARRAY_AGG(bank_transaction_leg_key ORDER BY bank_transaction_leg_key LIMIT 1)[OFFSET(0)],
      NULL
    ) AS bank_evidence_leg_key,
    IF(COUNT(*) = 1, MAX(transaction_amount), NULL) AS bank_evidence_amount
  FROM bank_evidence_candidates
  GROUP BY source_org_id, bill_id
),
calculated AS (
  SELECT
    bill.source_org_id,
    bill.source_org_key,
    bill.vendor_id,
    bill.vendor_name,
    bill.bill_id,
    bill.bill_number,
    bill.bill_date,
    bill.due_date,
    bill.currency_code AS currency,
    bill.total_amount AS bill_amount,
    bill.balance_amount AS source_outstanding_balance,
    bill.status AS source_bill_status,
    COALESCE(allocation.allocated_payment_count, 0) AS allocated_payment_count,
    COALESCE(allocation.allocated_amount, 0) AS allocated_amount,
    COALESCE(allocation.bank_verified_payment_count, 0) AS bank_verified_payment_count,
    COALESCE(allocation.bank_verified_amount, 0) AS bank_verified_amount,
    COALESCE(allocation.bank_pending_amount, 0) AS bank_pending_amount,
    GREATEST(COALESCE(bill.total_amount, 0) - COALESCE(allocation.bank_verified_amount, 0), 0)
      AS remaining_reconciliation_amount,
    COALESCE(allocation.unsafe_payment_count, 0) AS unsafe_payment_count,
    COALESCE(evidence.bank_evidence_candidate_count, 0) AS bank_evidence_candidate_count,
    evidence.bank_evidence_leg_key,
    evidence.bank_evidence_amount,
    allocation.latest_payment_date,
    allocation.payment_ids,
    CASE
      WHEN bill.source_org_id IS NULL OR bill.bill_id IS NULL OR bill.bill_id = ''
        OR bill.total_amount IS NULL OR bill.total_amount < 0
        OR bill.currency_code IS NULL OR TRIM(bill.currency_code) = ''
        THEN 'Invalid bill source fields'
      WHEN COALESCE(allocation.allocated_amount, 0) - bill.total_amount > 0.01
        THEN 'Allocated amount exceeds bill amount'
      WHEN COALESCE(allocation.bank_verified_amount, 0)
        - COALESCE(allocation.allocated_amount, 0) > 0.01
        THEN 'Bank-verified amount exceeds allocated amount'
      WHEN COALESCE(allocation.unsafe_payment_count, 0) > 0
        THEN 'One or more allocated payments has ambiguous or invalid bank matching'
      WHEN COALESCE(allocation.allocated_amount, 0) > 0
        AND bill.balance_amount IS NOT NULL
        AND ABS(
          bill.total_amount
          - COALESCE(allocation.allocated_amount, 0)
          - bill.balance_amount
        ) > 0.01
        THEN 'Source outstanding balance conflicts with current allocation total'
      ELSE CAST(NULL AS STRING)
    END AS validation_reason
  FROM `finance_silver.fact_bills` bill
  LEFT JOIN allocation_by_bill allocation
    ON allocation.source_org_id = bill.source_org_id
   AND allocation.bill_id = bill.bill_id
  LEFT JOIN bank_evidence_by_bill evidence
    ON evidence.source_org_id = bill.source_org_id
   AND evidence.bill_id = bill.bill_id
)
SELECT
  source_org_id,
  source_org_key,
  vendor_id,
  vendor_name,
  bill_id,
  bill_number,
  bill_date,
  due_date,
  currency,
  bill_amount,
  source_outstanding_balance,
  source_bill_status,
  allocated_payment_count,
  allocated_amount,
  bank_verified_payment_count,
  bank_verified_amount,
  bank_pending_amount,
  remaining_reconciliation_amount,
  CASE
    WHEN validation_reason IS NOT NULL
      AND validation_reason NOT LIKE 'Source outstanding balance conflicts%'
      THEN 'Needs Review'
    WHEN validation_reason LIKE 'Source outstanding balance conflicts%'
      THEN 'Amount Mismatch'
    WHEN allocated_amount >= bill_amount - 0.01
      AND bank_verified_amount >= bill_amount - 0.01
      AND bank_pending_amount <= 0.01
      THEN 'Fully Reconciled'
    WHEN bank_verified_amount > 0.01 AND bank_verified_amount < bill_amount - 0.01
      THEN 'Partially Reconciled'
    WHEN allocated_amount > 0.01
      THEN 'Payment Recorded - Bank Pending'
    WHEN allocated_amount <= 0.01 AND bank_evidence_candidate_count = 1
      THEN 'Bank Evidence Found - Zoho Posting Pending'
    WHEN allocated_amount <= 0.01 AND bank_evidence_candidate_count > 1
      THEN 'Needs Review'
    WHEN allocated_amount <= 0.01 AND COALESCE(source_outstanding_balance, bill_amount, 0) > 0.01
      THEN 'Unpaid - No Payment Found'
    WHEN allocated_amount <= 0.01 AND COALESCE(source_outstanding_balance, bill_amount, 0) <= 0.01
      THEN 'Amount Mismatch'
    ELSE 'Needs Review'
  END AS reconciliation_status,
  CASE
    WHEN validation_reason IS NOT NULL THEN validation_reason
    WHEN allocated_amount >= bill_amount - 0.01
      AND bank_verified_amount >= bill_amount - 0.01
      AND bank_pending_amount <= 0.01
      THEN 'Zoho allocations cover the bill and every allocated amount is bank verified'
    WHEN bank_verified_amount > 0.01 AND bank_verified_amount < bill_amount - 0.01
      THEN 'Some allocated amount is bank verified but the bill is not fully covered'
    WHEN allocated_amount > 0.01
      THEN 'Zoho allocation exists but corresponding bank verification is incomplete'
    WHEN allocated_amount <= 0.01 AND bank_evidence_candidate_count = 1
      THEN 'Unique outgoing bank debit contains the exact normalized bill number and matches amount and currency'
    WHEN allocated_amount <= 0.01 AND bank_evidence_candidate_count > 1
      THEN 'Several bank debits contain the bill number and match amount and currency'
    WHEN allocated_amount <= 0.01 AND COALESCE(source_outstanding_balance, bill_amount, 0) > 0.01
      THEN 'No Zoho allocation or safe bank evidence was found for an outstanding bill'
    ELSE 'Bill source state conflicts with payment and bank evidence'
  END AS reconciliation_reason,
  CASE
    WHEN validation_reason IS NOT NULL THEN TRUE
    WHEN allocated_amount <= 0.01 AND bank_evidence_candidate_count > 0 THEN TRUE
    WHEN allocated_amount > 0.01 AND bank_pending_amount > 0.01 THEN TRUE
    ELSE FALSE
  END AS review_required,
  latest_payment_date,
  payment_ids,
  bank_evidence_leg_key,
  bank_evidence_amount
FROM calculated;


CREATE OR REPLACE VIEW `finance_gold.vendor_reconciliation_exceptions` AS
WITH allocation_by_payment AS (
  SELECT
    source_org_id,
    payment_id,
    SUM(COALESCE(amount_applied, 0)) AS allocated_amount,
    COUNT(DISTINCT bill_id) AS allocated_bill_count
  FROM `finance_silver.bridge_vendor_payment_bill_allocations`
  GROUP BY source_org_id, payment_id
),
duplicate_final_assignments AS (
  SELECT bank_transaction_leg_key
  FROM `finance_gold.vendor_payment_bank_matches`
  WHERE bank_match_status = 'Bank Verified'
    AND bank_transaction_leg_key IS NOT NULL
  GROUP BY bank_transaction_leg_key
  HAVING COUNT(*) > 1
),
proposed_multiple_assignments AS (
  SELECT bank_transaction_leg_key
  FROM `finance_gold.vendor_payment_bank_matches`
  WHERE bank_transaction_leg_key IS NOT NULL
  GROUP BY bank_transaction_leg_key
  HAVING COUNT(*) > 1
),
exceptions AS (
  SELECT
    'Payment without bill' AS exception_type,
    payment.source_org_id,
    payment.vendor_id,
    payment.vendor_name,
    CAST(NULL AS STRING) AS bill_id,
    payment.payment_id,
    payment.bank_transaction_leg_key,
    payment.payment_amount AS amount,
    payment.payment_currency AS currency,
    'Vendor payment has no bill allocation' AS exception_reason,
    TRUE AS review_required
  FROM `finance_gold.vendor_payment_bank_matches` payment
  LEFT JOIN allocation_by_payment allocation
    USING (source_org_id, payment_id)
  WHERE COALESCE(allocation.allocated_bill_count, 0) = 0

  UNION ALL

  SELECT
    'Payment without bank match',
    source_org_id,
    vendor_id,
    vendor_name,
    CAST(NULL AS STRING),
    payment_id,
    bank_transaction_leg_key,
    payment_amount,
    payment_currency,
    bank_match_reason,
    TRUE
  FROM `finance_gold.vendor_payment_bank_matches`
  WHERE bank_match_status != 'Bank Verified'

  UNION ALL

  SELECT
    'Ambiguous bank match',
    source_org_id,
    vendor_id,
    vendor_name,
    CAST(NULL AS STRING),
    payment_id,
    bank_transaction_leg_key,
    payment_amount,
    payment_currency,
    bank_match_reason,
    TRUE
  FROM `finance_gold.vendor_payment_bank_matches`
  WHERE bank_match_status = 'Ambiguous Bank Match'

  UNION ALL

  SELECT
    'Over-allocated payment',
    payment.source_org_id,
    payment.vendor_id,
    payment.vendor_name,
    CAST(NULL AS STRING),
    payment.payment_id,
    payment.bank_transaction_leg_key,
    allocation.allocated_amount,
    payment.payment_currency,
    'Bill allocation total exceeds vendor-payment amount',
    TRUE
  FROM `finance_gold.vendor_payment_bank_matches` payment
  JOIN allocation_by_payment allocation
    USING (source_org_id, payment_id)
  WHERE allocation.allocated_amount - payment.payment_amount > 0.01

  UNION ALL

  SELECT
    'Allocation without bill',
    allocation.source_org_id,
    allocation.vendor_id,
    allocation.vendor_name,
    allocation.bill_id,
    allocation.payment_id,
    CAST(NULL AS STRING),
    allocation.amount_applied,
    allocation.currency_code,
    'Allocation references a bill absent from the current bill fact',
    TRUE
  FROM `finance_silver.bridge_vendor_payment_bill_allocations` allocation
  LEFT JOIN `finance_silver.fact_bills` bill
    ON bill.source_org_id = allocation.source_org_id
   AND bill.bill_id = allocation.bill_id
  WHERE bill.bill_id IS NULL

  UNION ALL

  SELECT
    'Bill with bank evidence but no Zoho allocation',
    source_org_id,
    vendor_id,
    vendor_name,
    bill_id,
    CAST(NULL AS STRING),
    bank_evidence_leg_key,
    bank_evidence_amount,
    currency,
    reconciliation_reason,
    TRUE
  FROM `finance_gold.vendor_bill_reconciliation`
  WHERE reconciliation_status = 'Bank Evidence Found - Zoho Posting Pending'

  UNION ALL

  SELECT
    'Invalid payment source fields',
    source_org_id,
    vendor_id,
    vendor_name,
    CAST(NULL AS STRING),
    payment_id,
    bank_transaction_leg_key,
    payment_amount,
    payment_currency,
    bank_match_reason,
    TRUE
  FROM `finance_gold.vendor_payment_bank_matches`
  WHERE bank_match_status = 'Invalid Payment Data'

  UNION ALL

  SELECT
    'Invalid bank source fields',
    bank.source_org_id,
    CAST(NULL AS STRING),
    CAST(NULL AS STRING),
    CAST(NULL AS STRING),
    CAST(NULL AS STRING),
    bank.bank_transaction_leg_key,
    bank.transaction_amount,
    bank.original_currency,
    COALESCE(bank.bank_data_quality_reason, 'Invalid bank source fields'),
    TRUE
  FROM `finance_silver.fact_bank_transactions` bank
  WHERE bank.bank_data_quality_status != 'Valid'

  UNION ALL

  SELECT
    'Invalid bill source fields',
    source_org_id,
    vendor_id,
    vendor_name,
    bill_id,
    CAST(NULL AS STRING),
    bank_evidence_leg_key,
    bill_amount,
    currency,
    reconciliation_reason,
    TRUE
  FROM `finance_gold.vendor_bill_reconciliation`
  WHERE reconciliation_reason = 'Invalid bill source fields'

  UNION ALL

  SELECT
    'Duplicate final bank assignment',
    payment.source_org_id,
    payment.vendor_id,
    payment.vendor_name,
    CAST(NULL AS STRING),
    payment.payment_id,
    payment.bank_transaction_leg_key,
    payment.payment_amount,
    payment.payment_currency,
    'Bank leg is finally assigned to more than one vendor payment',
    TRUE
  FROM `finance_gold.vendor_payment_bank_matches` payment
  JOIN duplicate_final_assignments duplicate
    USING (bank_transaction_leg_key)

  UNION ALL

  SELECT
    'Bank leg proposed for more than one payment',
    payment.source_org_id,
    payment.vendor_id,
    payment.vendor_name,
    CAST(NULL AS STRING),
    payment.payment_id,
    payment.bank_transaction_leg_key,
    payment.payment_amount,
    payment.payment_currency,
    'Potential duplicate bank assignment requires manual review',
    TRUE
  FROM `finance_gold.vendor_payment_bank_matches` payment
  JOIN proposed_multiple_assignments proposed
    USING (bank_transaction_leg_key)
)
SELECT
  exception_type,
  source_org_id,
  vendor_id,
  vendor_name,
  bill_id,
  payment_id,
  bank_transaction_leg_key,
  amount,
  currency,
  exception_reason,
  review_required
FROM exceptions;
