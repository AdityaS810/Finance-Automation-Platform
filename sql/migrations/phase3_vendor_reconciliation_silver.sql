-- Phase 3 controlled migration: only the three vendor-reconciliation Silver views.

CREATE OR REPLACE VIEW `finance_silver.fact_vendor_payments` AS
WITH raw_parsed AS (
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_key, 'legacy') AS source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    COALESCE(raw.source_record_id, JSON_VALUE(raw.raw_json, '$.payment_id')) AS payment_id,
    raw.raw_json,
    raw.loaded_at
  FROM `finance_bronze.zoho_raw` raw
  WHERE raw.entity_name = 'vendor_payments'
),
latest AS (
  SELECT *
  FROM raw_parsed
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY source_org_id, payment_id
    ORDER BY loaded_at DESC, run_id DESC, TO_HEX(SHA256(raw_json)) DESC
  ) = 1
),
parsed AS (
  SELECT
    run_id,
    source_org_key,
    source_org_id,
    source_org_name,
    source_country,
    source_currency,
    source_record_id,
    payment_id,
    JSON_VALUE(raw_json, '$.vendor_id') AS vendor_id,
    JSON_VALUE(raw_json, '$.vendor_name') AS vendor_name,
    SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS payment_date,
    JSON_VALUE(raw_json, '$.payment_number') AS payment_number,
    JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
    JSON_VALUE(raw_json, '$.payment_mode') AS payment_mode,
    JSON_VALUE(raw_json, '$.status') AS status,
    COALESCE(JSON_VALUE(raw_json, '$.currency_code'), source_currency, 'INR') AS currency_code,
    SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    SAFE_CAST(JSON_VALUE(raw_json, '$.amount') AS NUMERIC) AS payment_amount,
    JSON_VALUE(raw_json, '$.paid_through_account_id') AS paid_through_account_id,
    JSON_VALUE(raw_json, '$.paid_through_account_name') AS paid_through_account_name,
    JSON_VALUE(raw_json, '$.description') AS description,
    JSON_QUERY(raw_json, '$.bills') IS NULL AS allocations_missing,
    COALESCE(
      (
        SELECT SUM(SAFE_CAST(JSON_VALUE(allocation, '$.amount_applied') AS NUMERIC))
        FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(raw_json, '$.bills'), ARRAY<STRING>[])) AS allocation
      ),
      0
    ) AS allocated_amount,
    (
      SELECT COUNTIF(SAFE_CAST(JSON_VALUE(allocation, '$.amount_applied') AS NUMERIC) IS NULL)
      FROM UNNEST(IFNULL(JSON_QUERY_ARRAY(raw_json, '$.bills'), ARRAY<STRING>[])) AS allocation
    ) AS invalid_allocation_amount_count,
    loaded_at,
    raw_json
  FROM latest
),
calculated AS (
  SELECT parsed.*, payment_amount - allocated_amount AS unapplied_amount
  FROM parsed
),
converted AS (
  SELECT
    calculated.*,
    payment_amount * COALESCE(fx.inr_rate, 1) AS payment_amount_inr,
    allocated_amount * COALESCE(fx.inr_rate, 1) AS allocated_amount_inr,
    unapplied_amount * COALESCE(fx.inr_rate, 1) AS unapplied_amount_inr
  FROM calculated
  LEFT JOIN `finance_silver.fx_rates_demo` fx
    ON fx.currency_code = calculated.currency_code
)
SELECT
  converted.*,
  CASE
    WHEN payment_amount IS NULL
      OR allocations_missing
      OR invalid_allocation_amount_count > 0
      OR payment_amount < -0.01
      OR allocated_amount < -0.01
      THEN 'Needs Review'
    WHEN allocated_amount - payment_amount > 0.01 THEN 'Allocation Exceeds Payment'
    WHEN ABS(payment_amount - allocated_amount) <= 0.01 THEN 'Fully Allocated'
    WHEN allocated_amount <= 0.01 AND payment_amount > 0.01 THEN 'Unallocated'
    WHEN allocated_amount > 0.01 AND payment_amount - allocated_amount > 0.01 THEN 'Partially Allocated'
    ELSE 'Needs Review'
  END AS allocation_status
FROM converted;

CREATE OR REPLACE VIEW `finance_silver.bridge_vendor_payment_bill_allocations` AS
WITH allocations AS (
  SELECT
    payment.run_id,
    payment.source_org_key,
    payment.source_org_id,
    payment.source_org_name,
    payment.source_country,
    payment.source_currency,
    payment.source_record_id,
    payment.payment_id,
    payment.vendor_id,
    payment.vendor_name,
    payment.payment_date,
    JSON_VALUE(allocation, '$.bill_payment_id') AS bill_payment_id,
    JSON_VALUE(allocation, '$.bill_id') AS bill_id,
    JSON_VALUE(allocation, '$.bill_number') AS bill_number,
    SAFE_CAST(JSON_VALUE(allocation, '$.amount_applied') AS NUMERIC) AS amount_applied,
    payment.currency_code,
    payment.loaded_at,
    allocation_offset
  FROM `finance_silver.fact_vendor_payments` payment
  CROSS JOIN UNNEST(
    IFNULL(JSON_QUERY_ARRAY(payment.raw_json, '$.bills'), ARRAY<STRING>[])
  ) AS allocation WITH OFFSET AS allocation_offset
),
converted AS (
  SELECT
    allocations.* EXCEPT(allocation_offset),
    CONCAT(
      COALESCE(source_org_id, 'legacy'),
      ':',
      payment_id,
      ':',
      COALESCE(
        NULLIF(bill_payment_id, ''),
        NULLIF(bill_id, ''),
        CONCAT('missing-', CAST(allocation_offset AS STRING))
      )
    ) AS allocation_key,
    amount_applied * COALESCE(fx.inr_rate, 1) AS amount_applied_inr,
    allocation_offset
  FROM allocations
  LEFT JOIN `finance_silver.fx_rates_demo` fx
    ON fx.currency_code = allocations.currency_code
)
SELECT * EXCEPT(allocation_offset)
FROM converted
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY
    source_org_id,
    payment_id,
    COALESCE(
      NULLIF(bill_payment_id, ''),
      NULLIF(bill_id, ''),
      CONCAT('missing-', CAST(allocation_offset AS STRING))
    )
  ORDER BY loaded_at DESC, run_id DESC, allocation_offset DESC
) = 1;

CREATE OR REPLACE VIEW `finance_silver.fact_bank_transactions` AS
WITH raw_parsed AS (
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_key, 'legacy') AS source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    COALESCE(NULLIF(raw.source_record_id, ''), NULLIF(JSON_VALUE(raw.raw_json, '$.transaction_id'), '')) AS transaction_id,
    raw.raw_json,
    raw.loaded_at
  FROM `finance_bronze.zoho_raw` raw
  WHERE raw.entity_name = 'bank_transactions'
),
latest AS (
  SELECT *
  FROM raw_parsed
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY source_org_id, transaction_id
    ORDER BY loaded_at DESC, run_id DESC, TO_HEX(SHA256(raw_json)) DESC
  ) = 1
),
parsed AS (
  SELECT
    run_id,
    source_org_key,
    source_org_id,
    source_org_name,
    source_country,
    source_currency,
    source_record_id,
    transaction_id,
    JSON_VALUE(raw_json, '$.account_id') AS account_id,
    JSON_VALUE(raw_json, '$.account_name') AS account_name,
    SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS transaction_date,
    JSON_VALUE(raw_json, '$.transaction_type') AS transaction_type,
    JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
    JSON_VALUE(raw_json, '$.description') AS description,
    JSON_VALUE(raw_json, '$.status') AS status,
    LOWER(JSON_VALUE(raw_json, '$.debit_or_credit')) AS debit_or_credit,
    COALESCE(JSON_VALUE(raw_json, '$.currency_code'), source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw_json, '$.amount') AS NUMERIC) AS transaction_amount,
    loaded_at,
    raw_json
  FROM latest
),
quality AS (
  SELECT
    parsed.*,
    CASE
      WHEN debit_or_credit = 'debit' THEN -ABS(transaction_amount)
      WHEN debit_or_credit = 'credit' THEN ABS(transaction_amount)
      ELSE CAST(NULL AS NUMERIC)
    END AS signed_amount,
    CASE
      WHEN debit_or_credit = 'debit' THEN 'Outgoing'
      WHEN debit_or_credit = 'credit' THEN 'Incoming'
      ELSE 'Unknown'
    END AS transaction_direction,
    CASE
      WHEN transaction_id IS NULL THEN 'Invalid'
      WHEN account_id IS NULL OR account_id = '' THEN 'Needs Review'
      WHEN transaction_date IS NULL THEN 'Needs Review'
      WHEN transaction_amount IS NULL THEN 'Needs Review'
      WHEN debit_or_credit NOT IN ('debit', 'credit') OR debit_or_credit IS NULL THEN 'Needs Review'
      ELSE 'Valid'
    END AS bank_data_quality_status,
    CASE
      WHEN transaction_id IS NULL THEN 'Missing transaction_id'
      WHEN account_id IS NULL OR account_id = '' THEN 'Missing account_id'
      WHEN transaction_date IS NULL THEN 'Missing or invalid transaction date'
      WHEN transaction_amount IS NULL THEN 'Missing or invalid amount'
      WHEN debit_or_credit NOT IN ('debit', 'credit') OR debit_or_credit IS NULL
        THEN 'Missing or unsupported debit_or_credit'
      ELSE CAST(NULL AS STRING)
    END AS bank_data_quality_reason
  FROM parsed
)
SELECT
  quality.*,
  transaction_amount * COALESCE(fx.inr_rate, 1) AS transaction_amount_inr,
  signed_amount * COALESCE(fx.inr_rate, 1) AS signed_amount_inr
FROM quality
LEFT JOIN `finance_silver.fx_rates_demo` fx
  ON fx.currency_code = quality.original_currency;
