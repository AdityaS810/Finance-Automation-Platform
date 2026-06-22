-- ============================================================
-- Finance Automation Platform
-- Silver and Gold Layer Views
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
-- Silver Layer
-- Bronze is append-only. Silver latest views keep the current structured
-- record per organization and source_record_id.
-- ============================================================

CREATE OR REPLACE VIEW `finance_silver.dim_accounts` AS
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
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id
  ORDER BY loaded_at DESC
) = 1;


CREATE OR REPLACE VIEW `finance_silver.dim_contacts` AS
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
)
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
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id
  ORDER BY loaded_at DESC
) = 1;


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
WITH parsed AS (
  SELECT
    run_id,
    COALESCE(source_org_key, 'legacy') AS source_org_key,
    source_org_id,
    source_org_name,
    source_country,
    source_currency,
    source_record_id,
    source_record_id AS bill_id,
    JSON_VALUE(raw_json, '$.bill_number') AS bill_number,
    JSON_VALUE(raw_json, '$.vendor_id') AS vendor_id,
    JSON_VALUE(raw_json, '$.vendor_name') AS vendor_name,
    SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS bill_date,
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
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw_json, '$.payment_made') AS NUMERIC),
      SAFE_CAST(JSON_VALUE(raw_json, '$.amount_paid') AS NUMERIC)
    ) AS amount_paid,
    COALESCE(JSON_VALUE(raw_json, '$.gst_no'), JSON_VALUE(raw_json, '$.gstin'), JSON_VALUE(raw_json, '$.tax_identification_number')) AS gstin,
    JSON_VALUE(raw_json, '$.gst_treatment') AS gst_treatment,
    COALESCE(JSON_VALUE(raw_json, '$.place_of_supply'), JSON_VALUE(raw_json, '$.place_of_supply_code')) AS place_of_supply,
    loaded_at
  FROM `finance_bronze.zoho_raw`
  WHERE entity_name = 'bills'
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
    JSON_VALUE(raw_json, '$.journal_number') AS journal_number,
    SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS journal_date,
    JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
    JSON_VALUE(raw_json, '$.status') AS status,
    JSON_VALUE(raw_json, '$.notes') AS notes,
    COALESCE(JSON_VALUE(raw_json, '$.currency_code'), source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    COALESCE(SAFE_CAST(JSON_VALUE(raw_json, '$.total') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw_json, '$.amount') AS NUMERIC)) AS total_amount,
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
  WHERE entity_name = 'customer_payments'
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


-- ============================================================
-- Silver History Views
-- History views keep every cleaned version and mark the current row.
-- ============================================================

CREATE OR REPLACE VIEW `finance_silver.dim_accounts_history` AS
WITH versioned AS (
  SELECT
    latest.*,
    ROW_NUMBER() OVER (
      PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id
      ORDER BY loaded_at DESC
    ) AS version_number
  FROM (
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
  ) latest
)
SELECT *, version_number = 1 AS is_current
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
versioned AS (
  SELECT
    converted.*,
    ROW_NUMBER() OVER (PARTITION BY COALESCE(source_org_id, 'legacy'), source_record_id ORDER BY loaded_at DESC) AS version_number
  FROM converted
)
SELECT *, version_number = 1 AS is_current
FROM versioned;


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
WITH parsed AS (
  SELECT
    raw.run_id,
    COALESCE(raw.source_org_key, 'legacy') AS source_org_key,
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
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC) AS sub_total_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.taxable_amount') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC)) AS taxable_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_total') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_amount') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total_tax') AS NUMERIC)) AS tax_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.igst') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.igst_amount') AS NUMERIC)) AS igst_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.cgst') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.cgst_amount') AS NUMERIC)) AS cgst_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sgst') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sgst_amount') AS NUMERIC)) AS sgst_amount,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total') AS NUMERIC) AS total_amount,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.balance') AS NUMERIC) AS balance_amount,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.payment_made') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.amount_paid') AS NUMERIC)) AS amount_paid,
    COALESCE(JSON_VALUE(raw.raw_json, '$.gst_no'), JSON_VALUE(raw.raw_json, '$.gstin'), JSON_VALUE(raw.raw_json, '$.tax_identification_number')) AS gstin,
    JSON_VALUE(raw.raw_json, '$.gst_treatment') AS gst_treatment,
    COALESCE(JSON_VALUE(raw.raw_json, '$.place_of_supply'), JSON_VALUE(raw.raw_json, '$.place_of_supply_code')) AS place_of_supply,
    raw.loaded_at
  FROM `finance_bronze.zoho_raw` raw
  WHERE raw.entity_name = 'bills'
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
    JSON_VALUE(raw.raw_json, '$.journal_number') AS journal_number,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.date') AS DATE) AS journal_date,
    JSON_VALUE(raw.raw_json, '$.reference_number') AS reference_number,
    JSON_VALUE(raw.raw_json, '$.status') AS status,
    JSON_VALUE(raw.raw_json, '$.notes') AS notes,
    COALESCE(JSON_VALUE(raw.raw_json, '$.currency_code'), raw.source_currency, 'INR') AS original_currency,
    SAFE_CAST(JSON_VALUE(raw.raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
    COALESCE(SAFE_CAST(JSON_VALUE(raw.raw_json, '$.total') AS NUMERIC), SAFE_CAST(JSON_VALUE(raw.raw_json, '$.amount') AS NUMERIC)) AS total_amount,
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
-- Gold Layer
-- Gold views build on latest Silver views and report in INR.
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
  'journal', 'journal_adjustment', journal_id, journal_number, journal_date, CAST(NULL AS STRING), CAST(NULL AS STRING), reference_number, CAST(NULL AS STRING),
  currency_code, original_currency, original_amount, amount_inr, amount_inr, CAST(NULL AS NUMERIC), status,
  source_org_key, source_org_id, source_org_name, run_id, source_record_id, loaded_at
FROM `finance_silver.fact_journals`;


CREATE OR REPLACE VIEW `finance_gold.gst_reconciliation_input` AS
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
  total_amount AS invoice_value,
  total_amount_inr AS invoice_value_inr,
  status,
  currency_code,
  original_currency,
  source_org_key,
  source_org_id,
  source_org_name,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_invoices`
UNION ALL
SELECT
  'bill', bill_id, bill_number, bill_date, vendor_id, vendor_name, gstin, gst_treatment, place_of_supply,
  taxable_amount, taxable_amount_inr, igst_amount, cgst_amount, sgst_amount, tax_amount, tax_amount_inr, total_amount, total_amount_inr,
  status, currency_code, original_currency, source_org_key, source_org_id, source_org_name, run_id, source_record_id, loaded_at
FROM `finance_silver.fact_bills`;
