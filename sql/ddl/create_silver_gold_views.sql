-- ============================================================
-- Finance Automation Platform
-- Enrich and Consume Layer Views (internal dataset names: finance_silver, finance_gold)
-- Purpose: Multi-organization Zoho Books reporting with INR consolidation
-- ============================================================

CREATE SCHEMA IF NOT EXISTS `finance_silver`;
CREATE SCHEMA IF NOT EXISTS `finance_gold`;


-- Demo exchange rates for controlled INR consolidation.
-- Replace this view with a managed FX table when daily rates are available.
CREATE OR REPLACE VIEW `finance_silver.fx_rates_demo` AS
SELECT 'INR' AS currency_code, CAST(1.00 AS NUMERIC) AS inr_rate
UNION ALL
SELECT 'USD' AS currency_code, CAST(83.00 AS NUMERIC) AS inr_rate;


-- ============================================================
-- Enrich Layer
-- Raw is append-only. Enrich history views keep SCD2 versions, and latest
-- dimension views expose the current structured record per organization and
-- source_record_id.
-- ============================================================

CREATE OR REPLACE VIEW `finance_silver.dim_accounts_history` AS
WITH parsed AS (
  SELECT
    run_id,
    COALESCE(source_org_key, 'legacy') AS source_org_key,
    source_org_id,
    source_org_name,
    source_country,
    source_currency,
    source_record_id,
    source_record_id AS account_id,
    JSON_VALUE(raw_json, '$.account_name') AS account_name,
    JSON_VALUE(raw_json, '$.account_code') AS account_code,
    JSON_VALUE(raw_json, '$.account_type') AS account_type,
    JSON_VALUE(raw_json, '$.account_type_formatted') AS account_type_label,
    JSON_VALUE(raw_json, '$.description') AS description,
    SAFE_CAST(JSON_VALUE(raw_json, '$.is_active') AS BOOL) AS is_active,
    JSON_VALUE(raw_json, '$.status') AS status,
    COALESCE(JSON_VALUE(raw_json, '$.currency_code'), source_currency, 'INR') AS original_currency,
    CAST(NULL AS NUMERIC) AS original_amount,
    CAST(NULL AS NUMERIC) AS amount_inr,
    loaded_at
  FROM `finance_bronze.zoho_raw`
  WHERE entity_name = 'accounts'
),
hashed AS (
  SELECT
    parsed.*,
    TO_HEX(SHA256(TO_JSON_STRING(STRUCT(
      account_name,
      account_code,
      account_type,
      account_type_label,
      description,
      is_active,
      status,
      original_currency
    )))) AS change_hash
  FROM parsed
),
changed AS (
  SELECT
    hashed.*
  FROM hashed
  QUALIFY LAG(change_hash) OVER (
    PARTITION BY COALESCE(source_org_id, 'legacy'), account_id
    ORDER BY loaded_at
  ) IS NULL
    OR LAG(change_hash) OVER (
      PARTITION BY COALESCE(source_org_id, 'legacy'), account_id
      ORDER BY loaded_at
    ) != change_hash
),
versioned AS (
  SELECT
    changed.*,
    loaded_at AS valid_from,
    TIMESTAMP_SUB(
      LEAD(loaded_at) OVER (
        PARTITION BY COALESCE(source_org_id, 'legacy'), account_id
        ORDER BY loaded_at
      ),
      INTERVAL 1 MICROSECOND
    ) AS valid_to,
    ROW_NUMBER() OVER (
      PARTITION BY COALESCE(source_org_id, 'legacy'), account_id
      ORDER BY loaded_at
    ) AS version_number,
    ROW_NUMBER() OVER (
      PARTITION BY COALESCE(source_org_id, 'legacy'), account_id
      ORDER BY loaded_at DESC
    ) = 1 AS is_current
  FROM changed
)
SELECT
  *
FROM versioned;


CREATE OR REPLACE VIEW `finance_silver.dim_contacts_history` AS
WITH parsed AS (
  SELECT
    run_id,
    COALESCE(source_org_key, 'legacy') AS source_org_key,
    source_org_id,
    source_org_name,
    source_country,
    source_currency,
    source_record_id,
    source_record_id AS contact_id,
    JSON_VALUE(raw_json, '$.contact_name') AS contact_name,
    JSON_VALUE(raw_json, '$.company_name') AS company_name,
    JSON_VALUE(raw_json, '$.contact_type') AS contact_type,
    JSON_VALUE(raw_json, '$.email') AS email,
    JSON_VALUE(raw_json, '$.phone') AS phone,
    JSON_VALUE(raw_json, '$.mobile') AS mobile,
    JSON_VALUE(raw_json, '$.status') AS status,
    JSON_VALUE(raw_json, '$.gst_no') AS gstin,
    JSON_VALUE(raw_json, '$.gst_treatment') AS gst_treatment,
    JSON_VALUE(raw_json, '$.place_of_contact') AS place_of_contact,
    COALESCE(JSON_VALUE(raw_json, '$.currency_code'), source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw_json, '$.outstanding_receivable_amount') AS NUMERIC) AS outstanding_receivable_amount,
    SAFE_CAST(JSON_VALUE(raw_json, '$.outstanding_payable_amount') AS NUMERIC) AS outstanding_payable_amount,
    loaded_at
  FROM `finance_bronze.zoho_raw`
  WHERE entity_name = 'contacts'
),
converted AS (
  SELECT
    parsed.*,
    original_currency AS currency_code,
    CAST(NULL AS NUMERIC) AS original_amount,
    CAST(NULL AS NUMERIC) AS amount_inr,
    outstanding_receivable_amount * COALESCE(fx.inr_rate, 1) AS outstanding_receivable_amount_inr,
    outstanding_payable_amount * COALESCE(fx.inr_rate, 1) AS outstanding_payable_amount_inr
  FROM parsed
  LEFT JOIN `finance_silver.fx_rates_demo` fx
    ON fx.currency_code = parsed.original_currency
),
hashed AS (
  SELECT
    converted.*,
    TO_HEX(SHA256(TO_JSON_STRING(STRUCT(
      contact_name,
      company_name,
      contact_type,
      email,
      phone,
      mobile,
      status,
      gstin,
      gst_treatment,
      place_of_contact,
      original_currency
    )))) AS change_hash
  FROM converted
),
changed AS (
  SELECT
    hashed.*
  FROM hashed
  QUALIFY LAG(change_hash) OVER (
    PARTITION BY COALESCE(source_org_id, 'legacy'), contact_id
    ORDER BY loaded_at
  ) IS NULL
    OR LAG(change_hash) OVER (
      PARTITION BY COALESCE(source_org_id, 'legacy'), contact_id
      ORDER BY loaded_at
    ) != change_hash
),
versioned AS (
  SELECT
    changed.*,
    loaded_at AS valid_from,
    TIMESTAMP_SUB(
      LEAD(loaded_at) OVER (
        PARTITION BY COALESCE(source_org_id, 'legacy'), contact_id
        ORDER BY loaded_at
      ),
      INTERVAL 1 MICROSECOND
    ) AS valid_to,
    ROW_NUMBER() OVER (
      PARTITION BY COALESCE(source_org_id, 'legacy'), contact_id
      ORDER BY loaded_at
    ) AS version_number,
    ROW_NUMBER() OVER (
      PARTITION BY COALESCE(source_org_id, 'legacy'), contact_id
      ORDER BY loaded_at DESC
    ) = 1 AS is_current
  FROM changed
)
SELECT
  *
FROM versioned;


CREATE OR REPLACE VIEW `finance_silver.dim_accounts` AS
SELECT
  * EXCEPT(change_hash, valid_from, valid_to, version_number, is_current)
FROM `finance_silver.dim_accounts_history`
WHERE is_current = TRUE;


CREATE OR REPLACE VIEW `finance_silver.dim_contacts` AS
SELECT
  * EXCEPT(change_hash, valid_from, valid_to, version_number, is_current)
FROM `finance_silver.dim_contacts_history`
WHERE is_current = TRUE;


CREATE OR REPLACE VIEW `finance_silver.fact_invoices` AS
WITH parsed AS (
  SELECT
    run_id,
    COALESCE(source_org_key, 'legacy') AS source_org_key,
    source_org_id,
    source_org_name,
    source_country,
    source_currency,
    source_record_id,
    source_record_id AS invoice_id,
    JSON_VALUE(raw_json, '$.invoice_number') AS invoice_number,
    JSON_VALUE(raw_json, '$.customer_id') AS customer_id,
    JSON_VALUE(raw_json, '$.customer_name') AS customer_name,
    SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS invoice_date,
    SAFE_CAST(JSON_VALUE(raw_json, '$.due_date') AS DATE) AS due_date,
    JSON_VALUE(raw_json, '$.status') AS status,
    COALESCE(JSON_VALUE(raw_json, '$.currency_code'), source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    SAFE_CAST(JSON_VALUE(raw_json, '$.sub_total') AS NUMERIC) AS sub_total_amount,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.taxable_amount') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.sub_total') AS NUMERIC)
    ) AS taxable_amount,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.tax_total') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.tax_amount') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.total_tax') AS NUMERIC)
    ) AS tax_amount,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.igst') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.igst_amount') AS NUMERIC)
    ) AS igst_amount,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.cgst') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.cgst_amount') AS NUMERIC)
    ) AS cgst_amount,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.sgst') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.sgst_amount') AS NUMERIC)
    ) AS sgst_amount,
    SAFE_CAST(JSON_VALUE(raw_json, '$.total') AS NUMERIC) AS total_amount,
    SAFE_CAST(JSON_VALUE(raw_json, '$.balance') AS NUMERIC) AS balance_amount,
    SAFE_CAST(JSON_VALUE(raw_json, '$.amount_paid') AS NUMERIC) AS amount_paid,
    COALESCE(JSON_VALUE(raw_json, '$.gst_no'), JSON_VALUE(raw_json, '$.gstin'), JSON_VALUE(raw_json, '$.tax_identification_number')) AS gstin,
    JSON_VALUE(raw_json, '$.gst_treatment') AS gst_treatment,
    COALESCE(JSON_VALUE(raw_json, '$.place_of_supply'), JSON_VALUE(raw_json, '$.place_of_supply_code')) AS place_of_supply,
    loaded_at
  FROM `finance_bronze.zoho_raw`
  WHERE entity_name = 'invoices'
)
SELECT
  parsed.*,
  original_currency AS currency_code,
  total_amount AS original_amount,
  total_amount * COALESCE(fx.inr_rate, 1) AS amount_inr,
  sub_total_amount * COALESCE(fx.inr_rate, 1) AS sub_total_amount_inr,
  taxable_amount * COALESCE(fx.inr_rate, 1) AS taxable_amount_inr,
  tax_amount * COALESCE(fx.inr_rate, 1) AS tax_amount_inr,
  igst_amount * COALESCE(fx.inr_rate, 1) AS igst_amount_inr,
  cgst_amount * COALESCE(fx.inr_rate, 1) AS cgst_amount_inr,
  sgst_amount * COALESCE(fx.inr_rate, 1) AS sgst_amount_inr,
  total_amount * COALESCE(fx.inr_rate, 1) AS total_amount_inr,
  balance_amount * COALESCE(fx.inr_rate, 1) AS balance_amount_inr,
  amount_paid * COALESCE(fx.inr_rate, 1) AS amount_paid_inr
FROM parsed
LEFT JOIN `finance_silver.fx_rates_demo` fx
  ON fx.currency_code = parsed.original_currency
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id
  ORDER BY loaded_at DESC
) = 1;


CREATE OR REPLACE VIEW `finance_silver.fact_bills` AS
WITH bill_raw AS (
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_key, 'legacy') AS source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    raw.raw_json,
    raw.loaded_at
  FROM `finance_bronze.zoho_raw` raw
  WHERE raw.entity_name = 'bills'
    AND (
      raw.source_org_id IS NOT NULL
      OR NOT EXISTS (
        SELECT 1
        FROM `finance_bronze.zoho_raw` scoped
        WHERE scoped.entity_name = 'bills'
          AND scoped.source_record_id = raw.source_record_id
          AND scoped.source_org_id IS NOT NULL
      )
    )
),
line_amounts AS (
  SELECT
    run_id,
    COALESCE(source_org_id, 'legacy') AS source_org_id_key,
    source_record_id,
    loaded_at,
    SUM(
      COALESCE(
        SAFE_CAST(JSON_VALUE(line_item, '$.item_total') AS NUMERIC),
        SAFE_CAST(JSON_VALUE(line_item, '$.amount') AS NUMERIC),
        SAFE_CAST(JSON_VALUE(line_item, '$.rate') AS NUMERIC)
          * COALESCE(SAFE_CAST(JSON_VALUE(line_item, '$.quantity') AS NUMERIC), 1),
        0
      )
    ) AS line_taxable_amount
  FROM bill_raw
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(raw_json, '$.line_items'), ARRAY<STRING>[])) AS line_item
  GROUP BY run_id, source_org_id_key, source_record_id, loaded_at
),
bill_tax_entries AS (
  SELECT
    run_id,
    COALESCE(source_org_id, 'legacy') AS source_org_id_key,
    source_record_id,
    loaded_at,
    JSON_VALUE(tax, '$.tax_name') AS tax_name,
    SAFE_CAST(JSON_VALUE(tax, '$.tax_amount') AS NUMERIC) AS tax_amount
  FROM bill_raw
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(raw_json, '$.taxes'), ARRAY<STRING>[])) AS tax
  UNION ALL
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_id, 'legacy') AS source_org_id_key,
    raw.source_record_id,
    raw.loaded_at,
    JSON_VALUE(tax, '$.tax_name') AS tax_name,
    SAFE_CAST(JSON_VALUE(tax, '$.tax_amount') AS NUMERIC) AS tax_amount
  FROM bill_raw raw
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(raw.raw_json, '$.line_items'), ARRAY<STRING>[])) AS line_item
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(line_item, '$.line_item_taxes'), ARRAY<STRING>[])) AS tax
  WHERE COALESCE(ARRAY_LENGTH(JSON_QUERY_ARRAY(raw.raw_json, '$.taxes')), 0) = 0
),
tax_breakup AS (
  SELECT
    run_id,
    source_org_id_key,
    source_record_id,
    loaded_at,
    IF(
      COUNTIF(REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|CGST|SGST|INTEGRATED|CENTRAL|STATE)([^A-Z]|$)')) > 0,
      SUM(CASE WHEN REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|INTEGRATED)([^A-Z]|$)') THEN COALESCE(tax_amount, 0) ELSE 0 END),
      NULL
    ) AS igst_amount,
    IF(
      COUNTIF(REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|CGST|SGST|INTEGRATED|CENTRAL|STATE)([^A-Z]|$)')) > 0,
      SUM(CASE WHEN REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(CGST|CENTRAL)([^A-Z]|$)') THEN COALESCE(tax_amount, 0) ELSE 0 END),
      NULL
    ) AS cgst_amount,
    IF(
      COUNTIF(REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|CGST|SGST|INTEGRATED|CENTRAL|STATE)([^A-Z]|$)')) > 0,
      SUM(CASE WHEN REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(SGST|STATE)([^A-Z]|$)') THEN COALESCE(tax_amount, 0) ELSE 0 END),
      NULL
    ) AS sgst_amount,
    SUM(COALESCE(tax_amount, 0)) AS tax_amount
  FROM bill_tax_entries
  GROUP BY run_id, source_org_id_key, source_record_id, loaded_at
),
parsed AS (
  SELECT
    raw.run_id,
    raw.source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    raw.source_record_id AS bill_id,
    JSON_VALUE(raw.raw_json, '$.bill_number') AS bill_number,
    JSON_VALUE(raw.raw_json, '$.vendor_id') AS vendor_id,
    JSON_VALUE(raw.raw_json, '$.vendor_name') AS vendor_name,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.date') AS DATE) AS bill_date,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.due_date') AS DATE) AS due_date,
    JSON_VALUE(raw.raw_json, '$.status') AS status,
    COALESCE(JSON_VALUE(raw.raw_json, '$.currency_code'), raw.source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    COALESCE(
      line_amounts.line_taxable_amount,
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC)
    ) AS sub_total_amount,
    COALESCE(
      line_amounts.line_taxable_amount,
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.taxable_amount') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC)
    ) AS taxable_amount,
    COALESCE(
      tax_breakup.tax_amount,
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_total') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_amount') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total_tax') AS NUMERIC)
    ) AS tax_amount,
    COALESCE(
      tax_breakup.igst_amount,
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.igst') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.igst_amount') AS NUMERIC)
    ) AS igst_amount,
    COALESCE(
      tax_breakup.cgst_amount,
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.cgst') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.cgst_amount') AS NUMERIC)
    ) AS cgst_amount,
    COALESCE(
      tax_breakup.sgst_amount,
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sgst') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sgst_amount') AS NUMERIC)
    ) AS sgst_amount,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total') AS NUMERIC) AS total_amount,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.balance') AS NUMERIC) AS balance_amount,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.payment_made') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.amount_paid') AS NUMERIC)
    ) AS amount_paid,
    COALESCE(JSON_VALUE(raw.raw_json, '$.gst_no'), JSON_VALUE(raw.raw_json, '$.gstin'), JSON_VALUE(raw.raw_json, '$.tax_identification_number')) AS gstin,
    JSON_VALUE(raw.raw_json, '$.gst_treatment') AS gst_treatment,
    COALESCE(JSON_VALUE(raw.raw_json, '$.destination_of_supply'), JSON_VALUE(raw.raw_json, '$.source_of_supply'), JSON_VALUE(raw.raw_json, '$.place_of_supply'), JSON_VALUE(raw.raw_json, '$.place_of_supply_code')) AS place_of_supply,
    raw.loaded_at
  FROM bill_raw raw
  LEFT JOIN line_amounts
    ON line_amounts.run_id = raw.run_id
   AND line_amounts.source_org_id_key = COALESCE(raw.source_org_id, 'legacy')
   AND line_amounts.source_record_id = raw.source_record_id
   AND line_amounts.loaded_at = raw.loaded_at
  LEFT JOIN tax_breakup
    ON tax_breakup.run_id = raw.run_id
   AND tax_breakup.source_org_id_key = COALESCE(raw.source_org_id, 'legacy')
   AND tax_breakup.source_record_id = raw.source_record_id
   AND tax_breakup.loaded_at = raw.loaded_at
)
SELECT
  parsed.*,
  original_currency AS currency_code,
  total_amount AS original_amount,
  total_amount * COALESCE(fx.inr_rate, 1) AS amount_inr,
  sub_total_amount * COALESCE(fx.inr_rate, 1) AS sub_total_amount_inr,
  taxable_amount * COALESCE(fx.inr_rate, 1) AS taxable_amount_inr,
  tax_amount * COALESCE(fx.inr_rate, 1) AS tax_amount_inr,
  igst_amount * COALESCE(fx.inr_rate, 1) AS igst_amount_inr,
  cgst_amount * COALESCE(fx.inr_rate, 1) AS cgst_amount_inr,
  sgst_amount * COALESCE(fx.inr_rate, 1) AS sgst_amount_inr,
  total_amount * COALESCE(fx.inr_rate, 1) AS total_amount_inr,
  balance_amount * COALESCE(fx.inr_rate, 1) AS balance_amount_inr,
  amount_paid * COALESCE(fx.inr_rate, 1) AS amount_paid_inr
FROM parsed
LEFT JOIN `finance_silver.fx_rates_demo` fx
  ON fx.currency_code = parsed.original_currency
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id
  ORDER BY loaded_at DESC
) = 1;


CREATE OR REPLACE VIEW `finance_silver.fact_expenses` AS
WITH expense_raw AS (
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_key, 'legacy') AS source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    raw.raw_json,
    raw.loaded_at
  FROM `finance_bronze.zoho_raw` raw
  WHERE raw.entity_name = 'expenses'
    AND (
      raw.source_org_id IS NOT NULL
      OR NOT EXISTS (
        SELECT 1
        FROM `finance_bronze.zoho_raw` scoped
        WHERE scoped.entity_name = 'expenses'
          AND scoped.source_record_id = raw.source_record_id
          AND scoped.source_org_id IS NOT NULL
      )
    )
),
line_amounts AS (
  SELECT
    run_id,
    COALESCE(source_org_id, 'legacy') AS source_org_id_key,
    source_record_id,
    loaded_at,
    SUM(
      COALESCE(
        SAFE_CAST(JSON_VALUE(line_item, '$.item_total') AS NUMERIC),
        SAFE_CAST(JSON_VALUE(line_item, '$.amount') AS NUMERIC),
        SAFE_CAST(JSON_VALUE(line_item, '$.rate') AS NUMERIC)
          * COALESCE(SAFE_CAST(JSON_VALUE(line_item, '$.quantity') AS NUMERIC), 1),
        0
      )
    ) AS line_taxable_amount
  FROM expense_raw
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(raw_json, '$.line_items'), ARRAY<STRING>[])) AS line_item
  GROUP BY run_id, source_org_id_key, source_record_id, loaded_at
),
expense_tax_entries AS (
  SELECT
    run_id,
    COALESCE(source_org_id, 'legacy') AS source_org_id_key,
    source_record_id,
    loaded_at,
    JSON_VALUE(tax, '$.tax_name') AS tax_name,
    SAFE_CAST(JSON_VALUE(tax, '$.tax_amount') AS NUMERIC) AS tax_amount
  FROM expense_raw
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(raw_json, '$.taxes'), ARRAY<STRING>[])) AS tax
  UNION ALL
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_id, 'legacy') AS source_org_id_key,
    raw.source_record_id,
    raw.loaded_at,
    JSON_VALUE(tax, '$.tax_name') AS tax_name,
    SAFE_CAST(JSON_VALUE(tax, '$.tax_amount') AS NUMERIC) AS tax_amount
  FROM expense_raw raw
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(raw.raw_json, '$.line_items'), ARRAY<STRING>[])) AS line_item
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(line_item, '$.line_item_taxes'), ARRAY<STRING>[])) AS tax
  WHERE COALESCE(ARRAY_LENGTH(JSON_QUERY_ARRAY(raw.raw_json, '$.taxes')), 0) = 0
),
tax_breakup AS (
  SELECT
    run_id,
    source_org_id_key,
    source_record_id,
    loaded_at,
    IF(
      COUNTIF(REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|CGST|SGST|INTEGRATED|CENTRAL|STATE)([^A-Z]|$)')) > 0,
      SUM(CASE WHEN REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|INTEGRATED)([^A-Z]|$)') THEN COALESCE(tax_amount, 0) ELSE 0 END),
      NULL
    ) AS igst_amount,
    IF(
      COUNTIF(REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|CGST|SGST|INTEGRATED|CENTRAL|STATE)([^A-Z]|$)')) > 0,
      SUM(CASE WHEN REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(CGST|CENTRAL)([^A-Z]|$)') THEN COALESCE(tax_amount, 0) ELSE 0 END),
      NULL
    ) AS cgst_amount,
    IF(
      COUNTIF(REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|CGST|SGST|INTEGRATED|CENTRAL|STATE)([^A-Z]|$)')) > 0,
      SUM(CASE WHEN REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(SGST|STATE)([^A-Z]|$)') THEN COALESCE(tax_amount, 0) ELSE 0 END),
      NULL
    ) AS sgst_amount,
    SUM(COALESCE(tax_amount, 0)) AS tax_amount
  FROM expense_tax_entries
  GROUP BY run_id, source_org_id_key, source_record_id, loaded_at
),
parsed AS (
  SELECT
    raw.run_id,
    raw.source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    COALESCE(raw.source_record_id, JSON_VALUE(raw.raw_json, '$.expense_id')) AS expense_id,
    COALESCE(JSON_VALUE(raw.raw_json, '$.reference_number'), JSON_VALUE(raw.raw_json, '$.expense_number'), JSON_VALUE(raw.raw_json, '$.invoice_number')) AS expense_number,
    JSON_VALUE(raw.raw_json, '$.vendor_id') AS vendor_id,
    COALESCE(JSON_VALUE(raw.raw_json, '$.vendor_name'), JSON_VALUE(raw.raw_json, '$.merchant_name'), JSON_VALUE(raw.raw_json, '$.paid_through_account_name'), JSON_VALUE(raw.raw_json, '$.employee_name')) AS vendor_name,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.date') AS DATE) AS expense_date,
    JSON_VALUE(raw.raw_json, '$.status') AS status,
    COALESCE(JSON_VALUE(raw.raw_json, '$.currency_code'), raw.source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC), line_amounts.line_taxable_amount) AS sub_total_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC), line_amounts.line_taxable_amount) AS taxable_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_amount') AS NUMERIC), tax_breakup.tax_amount) AS tax_amount,
    tax_breakup.igst_amount,
    tax_breakup.cgst_amount,
    tax_breakup.sgst_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.amount') AS NUMERIC)) AS total_amount,
    COALESCE(JSON_VALUE(raw.raw_json, '$.gst_no'), JSON_VALUE(raw.raw_json, '$.gstin'), JSON_VALUE(raw.raw_json, '$.tax_identification_number')) AS gstin,
    JSON_VALUE(raw.raw_json, '$.gst_treatment') AS gst_treatment,
    COALESCE(JSON_VALUE(raw.raw_json, '$.destination_of_supply'), JSON_VALUE(raw.raw_json, '$.source_of_supply'), JSON_VALUE(raw.raw_json, '$.place_of_supply'), JSON_VALUE(raw.raw_json, '$.place_of_supply_code')) AS place_of_supply,
    raw.loaded_at
  FROM expense_raw raw
  LEFT JOIN line_amounts
    ON line_amounts.run_id = raw.run_id
   AND line_amounts.source_org_id_key = COALESCE(raw.source_org_id, 'legacy')
   AND line_amounts.source_record_id = raw.source_record_id
   AND line_amounts.loaded_at = raw.loaded_at
  LEFT JOIN tax_breakup
    ON tax_breakup.run_id = raw.run_id
   AND tax_breakup.source_org_id_key = COALESCE(raw.source_org_id, 'legacy')
   AND tax_breakup.source_record_id = raw.source_record_id
   AND tax_breakup.loaded_at = raw.loaded_at
)
SELECT
  parsed.*,
  original_currency AS currency_code,
  total_amount AS original_amount,
  total_amount * COALESCE(fx.inr_rate, 1) AS amount_inr,
  sub_total_amount * COALESCE(fx.inr_rate, 1) AS sub_total_amount_inr,
  taxable_amount * COALESCE(fx.inr_rate, 1) AS taxable_amount_inr,
  tax_amount * COALESCE(fx.inr_rate, 1) AS tax_amount_inr,
  igst_amount * COALESCE(fx.inr_rate, 1) AS igst_amount_inr,
  cgst_amount * COALESCE(fx.inr_rate, 1) AS cgst_amount_inr,
  sgst_amount * COALESCE(fx.inr_rate, 1) AS sgst_amount_inr,
  total_amount * COALESCE(fx.inr_rate, 1) AS total_amount_inr
FROM parsed
LEFT JOIN `finance_silver.fx_rates_demo` fx
  ON fx.currency_code = parsed.original_currency
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id
  ORDER BY loaded_at DESC
) = 1;


CREATE OR REPLACE VIEW `finance_silver.fact_journals` AS
WITH parsed AS (
  SELECT
    run_id,
    COALESCE(source_org_key, 'legacy') AS source_org_key,
    source_org_id,
    source_org_name,
    source_country,
    source_currency,
    source_record_id,
    source_record_id AS journal_id,
    COALESCE(JSON_VALUE(raw_json, '$.journal_number'), JSON_VALUE(raw_json, '$.entry_number')) AS journal_number,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.journal_date') AS DATE),
      SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE),
      SAFE_CAST(SUBSTR(JSON_VALUE(raw_json, '$.last_modified_time'), 1, 10) AS DATE),
      SAFE_CAST(SUBSTR(JSON_VALUE(raw_json, '$.created_time'), 1, 10) AS DATE)
    ) AS journal_date,
    JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
    JSON_VALUE(raw_json, '$.status') AS status,
    JSON_VALUE(raw_json, '$.notes') AS notes,
    COALESCE(JSON_VALUE(raw_json, '$.currency_code'), source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.total') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.amount') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.bcy_total') AS NUMERIC)
    ) AS total_amount,
    loaded_at
  FROM `finance_bronze.zoho_raw`
  WHERE entity_name = 'journals'
)
SELECT
  parsed.*,
  original_currency AS currency_code,
  total_amount AS original_amount,
  total_amount * COALESCE(fx.inr_rate, 1) AS amount_inr,
  total_amount * COALESCE(fx.inr_rate, 1) AS total_amount_inr
FROM parsed
LEFT JOIN `finance_silver.fx_rates_demo` fx
  ON fx.currency_code = parsed.original_currency
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id
  ORDER BY loaded_at DESC
) = 1;


CREATE OR REPLACE VIEW `finance_silver.fact_transactions` AS
WITH parsed AS (
  SELECT
    run_id,
    COALESCE(source_org_key, 'legacy') AS source_org_key,
    source_org_id,
    source_org_name,
    source_country,
    source_currency,
    source_record_id,
    source_record_id AS transaction_id,
    COALESCE(
      JSON_VALUE(raw_json, '$.transaction_number'),
      JSON_VALUE(raw_json, '$.entry_number'),
      JSON_VALUE(raw_json, '$.journal_number')
    ) AS transaction_number,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.transaction_date') AS DATE),
      SAFE_CAST(JSON_VALUE(raw_json, '$.journal_date') AS DATE),
      SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE),
      SAFE_CAST(SUBSTR(JSON_VALUE(raw_json, '$.last_modified_time'), 1, 10) AS DATE),
      SAFE_CAST(SUBSTR(JSON_VALUE(raw_json, '$.created_time'), 1, 10) AS DATE)
    ) AS transaction_date,
    JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
    JSON_VALUE(raw_json, '$.status') AS status,
    JSON_VALUE(raw_json, '$.notes') AS notes,
    COALESCE(JSON_VALUE(raw_json, '$.currency_code'), source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.amount') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.total') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.bcy_total') AS NUMERIC)
    ) AS transaction_amount,
    raw_json,
    loaded_at
  -- Backward compatibility only. Historical Cloud Function runs labelled
  -- journal payloads as "transactions"; this is not a canonical bank source.
  FROM `finance_bronze.zoho_raw`
  WHERE entity_name = 'transactions'
)
SELECT
  parsed.*,
  original_currency AS currency_code,
  transaction_amount AS original_amount,
  transaction_amount * COALESCE(fx.inr_rate, 1) AS amount_inr
FROM parsed
LEFT JOIN `finance_silver.fx_rates_demo` fx
  ON fx.currency_code = parsed.original_currency
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id
  ORDER BY loaded_at DESC
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
    COALESCE(
      NULLIF(raw.source_record_id, ''),
      NULLIF(JSON_VALUE(raw.raw_json, '$.transaction_id'), '')
    ) AS transaction_id,
    raw.raw_json,
    raw.loaded_at
  FROM `finance_bronze.zoho_raw` raw
  WHERE raw.entity_name = 'bank_transactions'
),
latest AS (
  SELECT
    *
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


CREATE OR REPLACE VIEW `finance_silver.fact_customer_payments` AS
WITH parsed AS (
  SELECT
    run_id,
    COALESCE(source_org_key, 'legacy') AS source_org_key,
    source_org_id,
    source_org_name,
    source_country,
    source_currency,
    source_record_id,
    source_record_id AS payment_id,
    COALESCE(JSON_VALUE(raw_json, '$.payment_number'), JSON_VALUE(raw_json, '$.payment_no')) AS payment_number,
    JSON_VALUE(raw_json, '$.customer_id') AS customer_id,
    JSON_VALUE(raw_json, '$.customer_name') AS customer_name,
    SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS payment_date,
    JSON_VALUE(raw_json, '$.payment_mode') AS payment_mode,
    JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
    JSON_VALUE(raw_json, '$.status') AS status,
    COALESCE(JSON_VALUE(raw_json, '$.currency_code'), source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    SAFE_CAST(JSON_VALUE(raw_json, '$.amount') AS NUMERIC) AS amount,
    SAFE_CAST(JSON_VALUE(raw_json, '$.bank_charges') AS NUMERIC) AS bank_charges,
    SAFE_CAST(JSON_VALUE(raw_json, '$.unused_amount') AS NUMERIC) AS unused_amount,
    loaded_at
  FROM `finance_bronze.zoho_raw`
  WHERE entity_name IN ('customer_payments', 'customerpayments')
)
SELECT
  parsed.*,
  original_currency AS currency_code,
  amount AS original_amount,
  amount * COALESCE(fx.inr_rate, 1) AS amount_inr,
  bank_charges * COALESCE(fx.inr_rate, 1) AS bank_charges_inr,
  unused_amount * COALESCE(fx.inr_rate, 1) AS unused_amount_inr
FROM parsed
LEFT JOIN `finance_silver.fx_rates_demo` fx
  ON fx.currency_code = parsed.original_currency
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id
  ORDER BY loaded_at DESC
) = 1;


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
  SELECT
    *
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
  SELECT
    parsed.*,
    payment_amount - allocated_amount AS unapplied_amount
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
SELECT
  * EXCEPT(allocation_offset)
FROM converted
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY
    source_org_id,
    payment_id,
    COALESCE(NULLIF(bill_payment_id, ''), NULLIF(bill_id, ''), CONCAT('missing-', CAST(allocation_offset AS STRING)))
  ORDER BY loaded_at DESC, run_id DESC, allocation_offset DESC
) = 1;


CREATE OR REPLACE VIEW `finance_silver.fact_invoices_history` AS
WITH parsed AS (
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_key, 'legacy') AS source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    raw.source_record_id AS invoice_id,
    JSON_VALUE(raw.raw_json, '$.invoice_number') AS invoice_number,
    JSON_VALUE(raw.raw_json, '$.customer_id') AS customer_id,
    JSON_VALUE(raw.raw_json, '$.customer_name') AS customer_name,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.date') AS DATE) AS invoice_date,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.due_date') AS DATE) AS due_date,
    JSON_VALUE(raw.raw_json, '$.status') AS status,
    COALESCE(JSON_VALUE(raw.raw_json, '$.currency_code'), raw.source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC) AS sub_total_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.taxable_amount') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC)) AS taxable_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_total') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_amount') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total_tax') AS NUMERIC)) AS tax_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.igst') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.igst_amount') AS NUMERIC)) AS igst_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.cgst') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.cgst_amount') AS NUMERIC)) AS cgst_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sgst') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sgst_amount') AS NUMERIC)) AS sgst_amount,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total') AS NUMERIC) AS total_amount,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.balance') AS NUMERIC) AS balance_amount,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.amount_paid') AS NUMERIC) AS amount_paid,
    COALESCE(JSON_VALUE(raw.raw_json, '$.gst_no'), JSON_VALUE(raw.raw_json, '$.gstin'), JSON_VALUE(raw.raw_json, '$.tax_identification_number')) AS gstin,
    JSON_VALUE(raw.raw_json, '$.gst_treatment') AS gst_treatment,
    COALESCE(JSON_VALUE(raw.raw_json, '$.place_of_supply'), JSON_VALUE(raw.raw_json, '$.place_of_supply_code')) AS place_of_supply,
    raw.loaded_at
  FROM `finance_bronze.zoho_raw` raw
  WHERE raw.entity_name = 'invoices'
),
converted AS (
  SELECT
    parsed.*,
    original_currency AS currency_code,
    total_amount AS original_amount,
    total_amount * COALESCE(fx.inr_rate, 1) AS amount_inr,
    sub_total_amount * COALESCE(fx.inr_rate, 1) AS sub_total_amount_inr,
    taxable_amount * COALESCE(fx.inr_rate, 1) AS taxable_amount_inr,
    tax_amount * COALESCE(fx.inr_rate, 1) AS tax_amount_inr,
    igst_amount * COALESCE(fx.inr_rate, 1) AS igst_amount_inr,
    cgst_amount * COALESCE(fx.inr_rate, 1) AS cgst_amount_inr,
    sgst_amount * COALESCE(fx.inr_rate, 1) AS sgst_amount_inr,
    total_amount * COALESCE(fx.inr_rate, 1) AS total_amount_inr,
    balance_amount * COALESCE(fx.inr_rate, 1) AS balance_amount_inr,
    amount_paid * COALESCE(fx.inr_rate, 1) AS amount_paid_inr
  FROM parsed
  LEFT JOIN `finance_silver.fx_rates_demo` fx
    ON fx.currency_code = parsed.original_currency
),
versioned AS (
  SELECT
    converted.*,
    ROW_NUMBER() OVER (PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id ORDER BY loaded_at DESC) AS version_number
  FROM converted
)
SELECT *, version_number = 1 AS is_current
FROM versioned;


CREATE OR REPLACE VIEW `finance_silver.fact_bills_history` AS
WITH bill_raw AS (
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_key, 'legacy') AS source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    raw.raw_json,
    raw.loaded_at
  FROM `finance_bronze.zoho_raw` raw
  WHERE raw.entity_name = 'bills'
    AND (
      raw.source_org_id IS NOT NULL
      OR NOT EXISTS (
        SELECT 1
        FROM `finance_bronze.zoho_raw` scoped
        WHERE scoped.entity_name = 'bills'
          AND scoped.source_record_id = raw.source_record_id
          AND scoped.source_org_id IS NOT NULL
      )
    )
),
line_amounts AS (
  SELECT
    run_id,
    COALESCE(source_org_id, 'legacy') AS source_org_id_key,
    source_record_id,
    loaded_at,
    SUM(
      COALESCE(
        SAFE_CAST(JSON_VALUE(line_item, '$.item_total') AS NUMERIC),
        SAFE_CAST(JSON_VALUE(line_item, '$.amount') AS NUMERIC),
        SAFE_CAST(JSON_VALUE(line_item, '$.rate') AS NUMERIC)
          * COALESCE(SAFE_CAST(JSON_VALUE(line_item, '$.quantity') AS NUMERIC), 1),
        0
      )
    ) AS line_taxable_amount
  FROM bill_raw
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(raw_json, '$.line_items'), ARRAY<STRING>[])) AS line_item
  GROUP BY run_id, source_org_id_key, source_record_id, loaded_at
),
bill_tax_entries AS (
  SELECT
    run_id,
    COALESCE(source_org_id, 'legacy') AS source_org_id_key,
    source_record_id,
    loaded_at,
    JSON_VALUE(tax, '$.tax_name') AS tax_name,
    SAFE_CAST(JSON_VALUE(tax, '$.tax_amount') AS NUMERIC) AS tax_amount
  FROM bill_raw
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(raw_json, '$.taxes'), ARRAY<STRING>[])) AS tax
  UNION ALL
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_id, 'legacy') AS source_org_id_key,
    raw.source_record_id,
    raw.loaded_at,
    JSON_VALUE(tax, '$.tax_name') AS tax_name,
    SAFE_CAST(JSON_VALUE(tax, '$.tax_amount') AS NUMERIC) AS tax_amount
  FROM bill_raw raw
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(raw.raw_json, '$.line_items'), ARRAY<STRING>[])) AS line_item
  CROSS JOIN UNNEST(IFNULL(JSON_QUERY_ARRAY(line_item, '$.line_item_taxes'), ARRAY<STRING>[])) AS tax
  WHERE COALESCE(ARRAY_LENGTH(JSON_QUERY_ARRAY(raw.raw_json, '$.taxes')), 0) = 0
),
tax_breakup AS (
  SELECT
    run_id,
    source_org_id_key,
    source_record_id,
    loaded_at,
    IF(
      COUNTIF(REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|CGST|SGST|INTEGRATED|CENTRAL|STATE)([^A-Z]|$)')) > 0,
      SUM(CASE WHEN REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|INTEGRATED)([^A-Z]|$)') THEN COALESCE(tax_amount, 0) ELSE 0 END),
      NULL
    ) AS igst_amount,
    IF(
      COUNTIF(REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|CGST|SGST|INTEGRATED|CENTRAL|STATE)([^A-Z]|$)')) > 0,
      SUM(CASE WHEN REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(CGST|CENTRAL)([^A-Z]|$)') THEN COALESCE(tax_amount, 0) ELSE 0 END),
      NULL
    ) AS cgst_amount,
    IF(
      COUNTIF(REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(IGST|CGST|SGST|INTEGRATED|CENTRAL|STATE)([^A-Z]|$)')) > 0,
      SUM(CASE WHEN REGEXP_CONTAINS(UPPER(COALESCE(tax_name, '')), r'(^|[^A-Z])(SGST|STATE)([^A-Z]|$)') THEN COALESCE(tax_amount, 0) ELSE 0 END),
      NULL
    ) AS sgst_amount,
    SUM(COALESCE(tax_amount, 0)) AS tax_amount
  FROM bill_tax_entries
  GROUP BY run_id, source_org_id_key, source_record_id, loaded_at
),
parsed AS (
  SELECT
    raw.run_id,
    raw.source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    raw.source_record_id AS bill_id,
    JSON_VALUE(raw.raw_json, '$.bill_number') AS bill_number,
    JSON_VALUE(raw.raw_json, '$.vendor_id') AS vendor_id,
    JSON_VALUE(raw.raw_json, '$.vendor_name') AS vendor_name,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.date') AS DATE) AS bill_date,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.due_date') AS DATE) AS due_date,
    JSON_VALUE(raw.raw_json, '$.status') AS status,
    COALESCE(JSON_VALUE(raw.raw_json, '$.currency_code'), raw.source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    COALESCE(line_amounts.line_taxable_amount, SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC)) AS sub_total_amount,
    COALESCE(line_amounts.line_taxable_amount, SAFE_CAST(JSON_VALUE(raw.raw_json, '$.taxable_amount') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC)) AS taxable_amount,
    COALESCE(tax_breakup.tax_amount, SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_total') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_amount') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total_tax') AS NUMERIC)) AS tax_amount,
    COALESCE(tax_breakup.igst_amount, SAFE_CAST(JSON_VALUE(raw.raw_json, '$.igst') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.igst_amount') AS NUMERIC)) AS igst_amount,
    COALESCE(tax_breakup.cgst_amount, SAFE_CAST(JSON_VALUE(raw.raw_json, '$.cgst') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.cgst_amount') AS NUMERIC)) AS cgst_amount,
    COALESCE(tax_breakup.sgst_amount, SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sgst') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sgst_amount') AS NUMERIC)) AS sgst_amount,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total') AS NUMERIC) AS total_amount,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.balance') AS NUMERIC) AS balance_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.payment_made') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.amount_paid') AS NUMERIC)) AS amount_paid,
    COALESCE(JSON_VALUE(raw.raw_json, '$.gst_no'), JSON_VALUE(raw.raw_json, '$.gstin'), JSON_VALUE(raw.raw_json, '$.tax_identification_number')) AS gstin,
    JSON_VALUE(raw.raw_json, '$.gst_treatment') AS gst_treatment,
    COALESCE(JSON_VALUE(raw.raw_json, '$.destination_of_supply'), JSON_VALUE(raw.raw_json, '$.source_of_supply'), JSON_VALUE(raw.raw_json, '$.place_of_supply'), JSON_VALUE(raw.raw_json, '$.place_of_supply_code')) AS place_of_supply,
    raw.loaded_at
  FROM bill_raw raw
  LEFT JOIN line_amounts
    ON line_amounts.run_id = raw.run_id
   AND line_amounts.source_org_id_key = COALESCE(raw.source_org_id, 'legacy')
   AND line_amounts.source_record_id = raw.source_record_id
   AND line_amounts.loaded_at = raw.loaded_at
  LEFT JOIN tax_breakup
    ON tax_breakup.run_id = raw.run_id
   AND tax_breakup.source_org_id_key = COALESCE(raw.source_org_id, 'legacy')
   AND tax_breakup.source_record_id = raw.source_record_id
   AND tax_breakup.loaded_at = raw.loaded_at
),
converted AS (
  SELECT
    parsed.*,
    original_currency AS currency_code,
    total_amount AS original_amount,
    total_amount * COALESCE(fx.inr_rate, 1) AS amount_inr,
    sub_total_amount * COALESCE(fx.inr_rate, 1) AS sub_total_amount_inr,
    taxable_amount * COALESCE(fx.inr_rate, 1) AS taxable_amount_inr,
    tax_amount * COALESCE(fx.inr_rate, 1) AS tax_amount_inr,
    igst_amount * COALESCE(fx.inr_rate, 1) AS igst_amount_inr,
    cgst_amount * COALESCE(fx.inr_rate, 1) AS cgst_amount_inr,
    sgst_amount * COALESCE(fx.inr_rate, 1) AS sgst_amount_inr,
    total_amount * COALESCE(fx.inr_rate, 1) AS total_amount_inr,
    balance_amount * COALESCE(fx.inr_rate, 1) AS balance_amount_inr,
    amount_paid * COALESCE(fx.inr_rate, 1) AS amount_paid_inr
  FROM parsed
  LEFT JOIN `finance_silver.fx_rates_demo` fx
    ON fx.currency_code = parsed.original_currency
),
versioned AS (
  SELECT
    converted.*,
    ROW_NUMBER() OVER (PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id ORDER BY loaded_at DESC) AS version_number
  FROM converted
)
SELECT *, version_number = 1 AS is_current
FROM versioned;


CREATE OR REPLACE VIEW `finance_silver.fact_journals_history` AS
WITH parsed AS (
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_key, 'legacy') AS source_org_key,
    raw.source_org_id,
    raw.source_org_name,
    raw.source_country,
    raw.source_currency,
    raw.source_record_id,
    raw.source_record_id AS journal_id,
    COALESCE(JSON_VALUE(raw.raw_json, '$.journal_number'), JSON_VALUE(raw.raw_json, '$.entry_number')) AS journal_number,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.journal_date') AS DATE),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.date') AS DATE),
      SAFE_CAST(SUBSTR(JSON_VALUE(raw.raw_json, '$.last_modified_time'), 1, 10) AS DATE),
      SAFE_CAST(SUBSTR(JSON_VALUE(raw.raw_json, '$.created_time'), 1, 10) AS DATE)
    ) AS journal_date,
    JSON_VALUE(raw.raw_json, '$.reference_number') AS reference_number,
    JSON_VALUE(raw.raw_json, '$.status') AS status,
    JSON_VALUE(raw.raw_json, '$.notes') AS notes,
    COALESCE(JSON_VALUE(raw.raw_json, '$.currency_code'), raw.source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.amount') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.bcy_total') AS NUMERIC)
    ) AS total_amount,
    raw.loaded_at
  FROM `finance_bronze.zoho_raw` raw
  WHERE raw.entity_name = 'journals'
),
converted AS (
  SELECT
    parsed.*,
    original_currency AS currency_code,
    total_amount AS original_amount,
    total_amount * COALESCE(fx.inr_rate, 1) AS amount_inr,
    total_amount * COALESCE(fx.inr_rate, 1) AS total_amount_inr
  FROM parsed
  LEFT JOIN `finance_silver.fx_rates_demo` fx
    ON fx.currency_code = parsed.original_currency
),
versioned AS (
  SELECT
    converted.*,
    ROW_NUMBER() OVER (PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id ORDER BY loaded_at DESC) AS version_number
  FROM converted
)
SELECT *, version_number = 1 AS is_current
FROM versioned;


-- ============================================================
-- Consume Layer
-- Consume views build on latest Enrich views and report in INR.
-- ============================================================

CREATE OR REPLACE VIEW `finance_gold.dashboard_summary` AS
WITH orgs AS (
  SELECT DISTINCT source_org_key, source_org_name, source_country, source_currency FROM `finance_silver.dim_accounts`
  UNION DISTINCT SELECT DISTINCT source_org_key, source_org_name, source_country, source_currency FROM `finance_silver.dim_contacts`
  UNION DISTINCT SELECT DISTINCT source_org_key, source_org_name, source_country, source_currency FROM `finance_silver.fact_invoices`
  UNION DISTINCT SELECT DISTINCT source_org_key, source_org_name, source_country, source_currency FROM `finance_silver.fact_bills`
  UNION DISTINCT SELECT DISTINCT source_org_key, source_org_name, source_country, source_currency FROM `finance_silver.fact_journals`
  UNION DISTINCT SELECT DISTINCT source_org_key, source_org_name, source_country, source_currency FROM `finance_silver.fact_customer_payments`
),
accounts AS (
  SELECT source_org_key, COUNT(*) AS account_count FROM `finance_silver.dim_accounts` GROUP BY source_org_key
),
contacts AS (
  SELECT source_org_key, COUNT(*) AS contact_count FROM `finance_silver.dim_contacts` GROUP BY source_org_key
),
invoices AS (
  SELECT source_org_key, COUNT(*) AS invoice_count, SUM(COALESCE(amount_inr, 0)) AS invoice_total_amount, SUM(COALESCE(balance_amount_inr, 0)) AS invoice_outstanding_amount
  FROM `finance_silver.fact_invoices` GROUP BY source_org_key
),
bills AS (
  SELECT source_org_key, COUNT(*) AS bill_count, SUM(COALESCE(amount_inr, 0)) AS bill_total_amount, SUM(COALESCE(balance_amount_inr, 0)) AS bill_outstanding_amount
  FROM `finance_silver.fact_bills` GROUP BY source_org_key
),
journals AS (
  SELECT source_org_key, COUNT(*) AS journal_count, SUM(COALESCE(amount_inr, 0)) AS journal_total_amount
  FROM `finance_silver.fact_journals` GROUP BY source_org_key
),
payments AS (
  SELECT source_org_key, COUNT(*) AS customer_payment_count, SUM(COALESCE(amount_inr, 0)) AS customer_payment_total_amount
  FROM `finance_silver.fact_customer_payments` GROUP BY source_org_key
),
org_summary AS (
  SELECT
    CURRENT_TIMESTAMP() AS generated_at,
    orgs.source_org_key,
    orgs.source_org_name,
    orgs.source_country,
    orgs.source_currency,
    'INR' AS reporting_currency,
    COALESCE(accounts.account_count, 0) AS account_count,
    COALESCE(contacts.contact_count, 0) AS contact_count,
    COALESCE(invoices.invoice_count, 0) AS invoice_count,
    COALESCE(invoices.invoice_total_amount, 0) AS invoice_total_amount,
    COALESCE(invoices.invoice_outstanding_amount, 0) AS invoice_outstanding_amount,
    COALESCE(bills.bill_count, 0) AS bill_count,
    COALESCE(bills.bill_total_amount, 0) AS bill_total_amount,
    COALESCE(bills.bill_outstanding_amount, 0) AS bill_outstanding_amount,
    COALESCE(journals.journal_count, 0) AS journal_count,
    COALESCE(journals.journal_total_amount, 0) AS journal_total_amount,
    COALESCE(payments.customer_payment_count, 0) AS customer_payment_count,
    COALESCE(payments.customer_payment_total_amount, 0) AS customer_payment_total_amount
  FROM orgs
  LEFT JOIN accounts USING (source_org_key)
  LEFT JOIN contacts USING (source_org_key)
  LEFT JOIN invoices USING (source_org_key)
  LEFT JOIN bills USING (source_org_key)
  LEFT JOIN journals USING (source_org_key)
  LEFT JOIN payments USING (source_org_key)
),
all_summary AS (
  SELECT
    CURRENT_TIMESTAMP() AS generated_at,
    'all' AS source_org_key,
    'All Organizations' AS source_org_name,
    'Consolidated' AS source_country,
    'Mixed' AS source_currency,
    'INR' AS reporting_currency,
    SUM(account_count) AS account_count,
    SUM(contact_count) AS contact_count,
    SUM(invoice_count) AS invoice_count,
    SUM(invoice_total_amount) AS invoice_total_amount,
    SUM(invoice_outstanding_amount) AS invoice_outstanding_amount,
    SUM(bill_count) AS bill_count,
    SUM(bill_total_amount) AS bill_total_amount,
    SUM(bill_outstanding_amount) AS bill_outstanding_amount,
    SUM(journal_count) AS journal_count,
    SUM(journal_total_amount) AS journal_total_amount,
    SUM(customer_payment_count) AS customer_payment_count,
    SUM(customer_payment_total_amount) AS customer_payment_total_amount
  FROM org_summary
)
SELECT * FROM all_summary
UNION ALL
SELECT * FROM org_summary;


CREATE OR REPLACE VIEW `finance_gold.mis_monthly_pl` AS
WITH invoice_monthly AS (
  SELECT source_org_key, source_org_name, DATE_TRUNC(invoice_date, MONTH) AS report_month, COUNT(*) AS invoice_count, SUM(COALESCE(amount_inr, 0)) AS revenue_amount
  FROM `finance_silver.fact_invoices`
  WHERE invoice_date IS NOT NULL
  GROUP BY source_org_key, source_org_name, report_month
),
bill_monthly AS (
  SELECT source_org_key, source_org_name, DATE_TRUNC(bill_date, MONTH) AS report_month, COUNT(*) AS bill_count, SUM(COALESCE(amount_inr, 0)) AS expense_amount
  FROM `finance_silver.fact_bills`
  WHERE bill_date IS NOT NULL
  GROUP BY source_org_key, source_org_name, report_month
),
journal_monthly AS (
  SELECT source_org_key, source_org_name, DATE_TRUNC(journal_date, MONTH) AS report_month, COUNT(*) AS journal_count, SUM(COALESCE(amount_inr, 0)) AS journal_adjustment_amount
  FROM `finance_silver.fact_journals`
  WHERE journal_date IS NOT NULL
  GROUP BY source_org_key, source_org_name, report_month
),
months AS (
  SELECT source_org_key, source_org_name, report_month FROM invoice_monthly
  UNION DISTINCT SELECT source_org_key, source_org_name, report_month FROM bill_monthly
  UNION DISTINCT SELECT source_org_key, source_org_name, report_month FROM journal_monthly
),
org_monthly AS (
  SELECT
    months.source_org_key,
    months.source_org_name,
    'INR' AS reporting_currency,
    months.report_month,
    COALESCE(invoice_monthly.invoice_count, 0) AS invoice_count,
    COALESCE(bill_monthly.bill_count, 0) AS bill_count,
    COALESCE(journal_monthly.journal_count, 0) AS journal_count,
    COALESCE(invoice_monthly.revenue_amount, 0) AS revenue_amount,
    COALESCE(bill_monthly.expense_amount, 0) AS expense_amount,
    COALESCE(journal_monthly.journal_adjustment_amount, 0) AS journal_adjustment_amount,
    COALESCE(invoice_monthly.revenue_amount, 0) - COALESCE(bill_monthly.expense_amount, 0) + COALESCE(journal_monthly.journal_adjustment_amount, 0) AS profit_amount
  FROM months
  LEFT JOIN invoice_monthly USING (source_org_key, source_org_name, report_month)
  LEFT JOIN bill_monthly USING (source_org_key, source_org_name, report_month)
  LEFT JOIN journal_monthly USING (source_org_key, source_org_name, report_month)
),
all_monthly AS (
  SELECT
    'all' AS source_org_key,
    'All Organizations' AS source_org_name,
    'INR' AS reporting_currency,
    report_month,
    SUM(invoice_count) AS invoice_count,
    SUM(bill_count) AS bill_count,
    SUM(journal_count) AS journal_count,
    SUM(revenue_amount) AS revenue_amount,
    SUM(expense_amount) AS expense_amount,
    SUM(journal_adjustment_amount) AS journal_adjustment_amount,
    SUM(profit_amount) AS profit_amount
  FROM org_monthly
  GROUP BY report_month
)
SELECT * FROM all_monthly
UNION ALL
SELECT * FROM org_monthly;


CREATE OR REPLACE VIEW `finance_gold.bank_reconciliation_input` AS
SELECT
  'invoice' AS source_type,
  'invoice_receivable' AS transaction_type,
  invoice_id AS transaction_id,
  invoice_number AS transaction_number,
  invoice_date AS transaction_date,
  customer_id AS counterparty_id,
  customer_name AS counterparty_name,
  invoice_number AS reference_number,
  CAST(NULL AS STRING) AS payment_mode,
  currency_code,
  original_currency,
  original_amount,
  amount_inr AS transaction_amount,
  amount_inr AS transaction_amount_inr,
  balance_amount_inr AS outstanding_amount,
  status,
  source_org_key,
  source_org_id,
  source_org_name,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_invoices`
UNION ALL
SELECT
  'bill', 'bill_payable', bill_id, bill_number, bill_date, vendor_id, vendor_name, bill_number, CAST(NULL AS STRING),
  currency_code, original_currency, original_amount, -COALESCE(amount_inr, 0), -COALESCE(amount_inr, 0), balance_amount_inr, status,
  source_org_key, source_org_id, source_org_name, run_id, source_record_id, loaded_at
FROM `finance_silver.fact_bills`
UNION ALL
SELECT
  'customer_payment', 'customer_payment', payment_id, payment_number, payment_date, customer_id, customer_name, reference_number, payment_mode,
  currency_code, original_currency, original_amount, amount_inr, amount_inr, CAST(0 AS NUMERIC), status,
  source_org_key, source_org_id, source_org_name, run_id, source_record_id, loaded_at
FROM `finance_silver.fact_customer_payments`
UNION ALL
SELECT
  'journal', 'journal_adjustment', journal_id, journal_number, journal_date, CAST(NULL AS STRING), notes, COALESCE(reference_number, journal_number, notes), CAST(NULL AS STRING),
  currency_code, original_currency, original_amount, amount_inr, amount_inr, CAST(NULL AS NUMERIC), status,
  source_org_key, source_org_id, source_org_name, run_id, source_record_id, loaded_at
FROM `finance_silver.fact_journals`
WHERE journal_date IS NOT NULL
  AND amount_inr IS NOT NULL
  AND amount_inr != 0
  AND REGEXP_CONTAINS(
    UPPER(CONCAT(
      COALESCE(notes, ''),
      ' ',
      COALESCE(reference_number, ''),
      ' ',
      COALESCE(journal_number, '')
    )),
    r'(^|[^A-Z0-9])(BANK|HSBC|CASH|PAYMENT|PAID|RECEIPT|RECEIVED|TRANSFER|NEFT|RTGS|IMPS|UPI|ACH|SALARY|TDS|GST|TAX|CHARGES|DC|CR)([^A-Z0-9]|$)'
  )
UNION ALL
SELECT
  'expense',
  'expense_payment',
  expense_id,
  expense_number,
  expense_date,
  vendor_id,
  vendor_name,
  expense_number,
  CAST(NULL AS STRING),
  currency_code,
  original_currency,
  original_amount,
  -ABS(amount_inr),
  -ABS(amount_inr),
  CAST(0 AS NUMERIC),
  status,
  source_org_key,
  source_org_id,
  source_org_name,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_expenses`
UNION ALL
SELECT
  'transaction',
  'bank_transaction_or_journal',
  transaction_id,
  transaction_number,
  transaction_date,
  CAST(NULL AS STRING),
  CAST(NULL AS STRING),
  reference_number,
  CAST(NULL AS STRING),
  currency_code,
  original_currency,
  original_amount,
  transaction_amount,
  transaction_amount,
  CAST(NULL AS NUMERIC),
  status,
  source_org_key,
  source_org_id,
  source_org_name,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_transactions`;


CREATE OR REPLACE VIEW `finance_gold.gst_reconciliation_input` AS
WITH invoice_source AS (
  SELECT
    *,
    taxable_amount + COALESCE(NULLIF(COALESCE(igst_amount, 0) + COALESCE(cgst_amount, 0) + COALESCE(sgst_amount, 0), 0), tax_amount, 0) AS calculated_invoice_value,
    taxable_amount_inr + COALESCE(NULLIF(COALESCE(igst_amount_inr, 0) + COALESCE(cgst_amount_inr, 0) + COALESCE(sgst_amount_inr, 0), 0), tax_amount_inr, 0) AS calculated_invoice_value_inr
  FROM `finance_silver.fact_invoices`
),
bill_source AS (
  SELECT
    *,
    taxable_amount + COALESCE(NULLIF(COALESCE(igst_amount, 0) + COALESCE(cgst_amount, 0) + COALESCE(sgst_amount, 0), 0), tax_amount, 0) AS calculated_invoice_value,
    taxable_amount_inr + COALESCE(NULLIF(COALESCE(igst_amount_inr, 0) + COALESCE(cgst_amount_inr, 0) + COALESCE(sgst_amount_inr, 0), 0), tax_amount_inr, 0) AS calculated_invoice_value_inr
  FROM `finance_silver.fact_bills`
),
expense_source AS (
  SELECT
    *,
    taxable_amount + COALESCE(NULLIF(COALESCE(igst_amount, 0) + COALESCE(cgst_amount, 0) + COALESCE(sgst_amount, 0), 0), tax_amount, 0) AS calculated_invoice_value,
    taxable_amount_inr + COALESCE(NULLIF(COALESCE(igst_amount_inr, 0) + COALESCE(cgst_amount_inr, 0) + COALESCE(sgst_amount_inr, 0), 0), tax_amount_inr, 0) AS calculated_invoice_value_inr
  FROM `finance_silver.fact_expenses`
)
SELECT
  'invoice' AS source_type,
  invoice_id AS document_id,
  invoice_number AS document_number,
  invoice_date AS document_date,
  customer_id AS party_id,
  customer_name AS party_name,
  gstin,
  gst_treatment,
  place_of_supply,
  taxable_amount AS taxable_value,
  taxable_amount_inr AS taxable_value_inr,
  igst_amount AS igst,
  cgst_amount AS cgst,
  sgst_amount AS sgst,
  tax_amount AS total_tax,
  tax_amount_inr AS total_tax_inr,
  total_amount AS raw_invoice_value,
  calculated_invoice_value,
  CASE
    WHEN taxable_amount IS NOT NULL
      AND (igst_amount IS NOT NULL OR cgst_amount IS NOT NULL OR sgst_amount IS NOT NULL OR tax_amount IS NOT NULL)
      AND calculated_invoice_value IS NOT NULL
      AND (total_amount IS NULL OR ABS(total_amount - calculated_invoice_value) > 1)
    THEN calculated_invoice_value
    ELSE total_amount
  END AS invoice_value,
  CASE
    WHEN taxable_amount_inr IS NOT NULL
      AND (igst_amount IS NOT NULL OR cgst_amount IS NOT NULL OR sgst_amount IS NOT NULL OR tax_amount_inr IS NOT NULL)
      AND calculated_invoice_value_inr IS NOT NULL
      AND (total_amount_inr IS NULL OR ABS(total_amount_inr - calculated_invoice_value_inr) > 1)
    THEN calculated_invoice_value_inr
    ELSE total_amount_inr
  END AS invoice_value_inr,
  CASE
    WHEN taxable_amount IS NOT NULL
      AND (igst_amount IS NOT NULL OR cgst_amount IS NOT NULL OR sgst_amount IS NOT NULL OR tax_amount IS NOT NULL)
      AND calculated_invoice_value IS NOT NULL
      AND (total_amount IS NULL OR ABS(total_amount - calculated_invoice_value) > 1)
    THEN 'calculated_from_tax_components'
    ELSE 'zoho_total_amount'
  END AS invoice_value_source,
  status,
  currency_code,
  original_currency,
  source_org_key,
  source_org_id,
  source_org_name,
  run_id,
  source_record_id,
  loaded_at
FROM invoice_source
UNION ALL
SELECT
  'bill', bill_id, bill_number, bill_date, vendor_id, vendor_name, gstin, gst_treatment, place_of_supply,
  taxable_amount, taxable_amount_inr, igst_amount, cgst_amount, sgst_amount, tax_amount, tax_amount_inr,
  total_amount,
  calculated_invoice_value,
  CASE
    WHEN taxable_amount IS NOT NULL
      AND (igst_amount IS NOT NULL OR cgst_amount IS NOT NULL OR sgst_amount IS NOT NULL OR tax_amount IS NOT NULL)
      AND calculated_invoice_value IS NOT NULL
      AND (total_amount IS NULL OR ABS(total_amount - calculated_invoice_value) > 1)
    THEN calculated_invoice_value
    ELSE total_amount
  END,
  CASE
    WHEN taxable_amount_inr IS NOT NULL
      AND (igst_amount IS NOT NULL OR cgst_amount IS NOT NULL OR sgst_amount IS NOT NULL OR tax_amount_inr IS NOT NULL)
      AND calculated_invoice_value_inr IS NOT NULL
      AND (total_amount_inr IS NULL OR ABS(total_amount_inr - calculated_invoice_value_inr) > 1)
    THEN calculated_invoice_value_inr
    ELSE total_amount_inr
  END,
  CASE
    WHEN taxable_amount IS NOT NULL
      AND (igst_amount IS NOT NULL OR cgst_amount IS NOT NULL OR sgst_amount IS NOT NULL OR tax_amount IS NOT NULL)
      AND calculated_invoice_value IS NOT NULL
      AND (total_amount IS NULL OR ABS(total_amount - calculated_invoice_value) > 1)
    THEN 'calculated_from_tax_components'
    ELSE 'zoho_total_amount'
  END,
  status, currency_code, original_currency, source_org_key, source_org_id, source_org_name, run_id, source_record_id, loaded_at
FROM bill_source
UNION ALL
SELECT
  'expense', expense_id, expense_number, expense_date, vendor_id, vendor_name, gstin, gst_treatment, place_of_supply,
  taxable_amount, taxable_amount_inr, igst_amount, cgst_amount, sgst_amount, tax_amount, tax_amount_inr,
  total_amount,
  calculated_invoice_value,
  CASE
    WHEN taxable_amount IS NOT NULL
      AND (igst_amount IS NOT NULL OR cgst_amount IS NOT NULL OR sgst_amount IS NOT NULL OR tax_amount IS NOT NULL)
      AND calculated_invoice_value IS NOT NULL
      AND (total_amount IS NULL OR ABS(total_amount - calculated_invoice_value) > 1)
    THEN calculated_invoice_value
    ELSE total_amount
  END,
  CASE
    WHEN taxable_amount_inr IS NOT NULL
      AND (igst_amount IS NOT NULL OR cgst_amount IS NOT NULL OR sgst_amount IS NOT NULL OR tax_amount_inr IS NOT NULL)
      AND calculated_invoice_value_inr IS NOT NULL
      AND (total_amount_inr IS NULL OR ABS(total_amount_inr - calculated_invoice_value_inr) > 1)
    THEN calculated_invoice_value_inr
    ELSE total_amount_inr
  END,
  CASE
    WHEN taxable_amount IS NOT NULL
      AND (igst_amount IS NOT NULL OR cgst_amount IS NOT NULL OR sgst_amount IS NOT NULL OR tax_amount IS NOT NULL)
      AND calculated_invoice_value IS NOT NULL
      AND (total_amount IS NULL OR ABS(total_amount - calculated_invoice_value) > 1)
    THEN 'calculated_from_tax_components'
    ELSE 'zoho_total_amount'
  END,
  status, currency_code, original_currency, source_org_key, source_org_id, source_org_name, run_id, source_record_id, loaded_at
FROM expense_source
WHERE
  (gstin IS NOT NULL AND TRIM(gstin) != '')
  OR COALESCE(tax_amount, 0) != 0
  OR COALESCE(igst_amount, 0) != 0
  OR COALESCE(cgst_amount, 0) != 0
  OR COALESCE(sgst_amount, 0) != 0
  OR LOWER(COALESCE(gst_treatment, '')) NOT IN ('', 'out_of_scope', 'non_gst', 'non-gst');
\n-- Phase 4 controlled migration: only the three vendor-reconciliation Gold views.
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
