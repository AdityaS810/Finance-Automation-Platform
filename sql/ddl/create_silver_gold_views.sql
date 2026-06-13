-- ============================================================
-- Finance Automation Platform
-- Silver and Gold Layer Views
-- Purpose: Structure generic bronze Zoho data for reporting and reconciliation
-- ============================================================

CREATE SCHEMA IF NOT EXISTS `finance_silver`;
CREATE SCHEMA IF NOT EXISTS `finance_gold`;


-- ============================================================
-- Silver Layer
-- Bronze is append-only. Silver keeps the latest structured record per
-- source_record_id for each Zoho Books entity.
-- ============================================================

CREATE OR REPLACE VIEW `finance_silver.dim_accounts` AS
SELECT
  run_id,
  source_record_id,
  source_record_id AS account_id,
  JSON_VALUE(raw_json, '$.account_name') AS account_name,
  JSON_VALUE(raw_json, '$.account_code') AS account_code,
  JSON_VALUE(raw_json, '$.account_type') AS account_type,
  JSON_VALUE(raw_json, '$.account_type_formatted') AS account_type_label,
  JSON_VALUE(raw_json, '$.description') AS description,
  SAFE_CAST(JSON_VALUE(raw_json, '$.is_active') AS BOOL) AS is_active,
  JSON_VALUE(raw_json, '$.status') AS status,
  loaded_at
FROM `finance_bronze.zoho_raw`
WHERE entity_name = 'accounts'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY source_record_id
  ORDER BY loaded_at DESC
) = 1;


CREATE OR REPLACE VIEW `finance_silver.dim_contacts` AS
SELECT
  run_id,
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
  JSON_VALUE(raw_json, '$.currency_code') AS currency_code,
  SAFE_CAST(JSON_VALUE(raw_json, '$.outstanding_receivable_amount') AS NUMERIC) AS outstanding_receivable_amount,
  SAFE_CAST(JSON_VALUE(raw_json, '$.outstanding_payable_amount') AS NUMERIC) AS outstanding_payable_amount,
  loaded_at
FROM `finance_bronze.zoho_raw`
WHERE entity_name = 'contacts'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY source_record_id
  ORDER BY loaded_at DESC
) = 1;


-- GST fields are read from common header-level Zoho names. If a company has
-- GST only inside line_items, add a separate line-item silver view later.
CREATE OR REPLACE VIEW `finance_silver.fact_invoices` AS
SELECT
  run_id,
  source_record_id,
  source_record_id AS invoice_id,
  JSON_VALUE(raw_json, '$.invoice_number') AS invoice_number,
  JSON_VALUE(raw_json, '$.customer_id') AS customer_id,
  JSON_VALUE(raw_json, '$.customer_name') AS customer_name,
  SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS invoice_date,
  SAFE_CAST(JSON_VALUE(raw_json, '$.due_date') AS DATE) AS due_date,
  JSON_VALUE(raw_json, '$.status') AS status,
  JSON_VALUE(raw_json, '$.currency_code') AS currency_code,
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
  COALESCE(
    JSON_VALUE(raw_json, '$.gst_no'),
    JSON_VALUE(raw_json, '$.gstin'),
    JSON_VALUE(raw_json, '$.tax_identification_number')
  ) AS gstin,
  JSON_VALUE(raw_json, '$.gst_treatment') AS gst_treatment,
  COALESCE(
    JSON_VALUE(raw_json, '$.place_of_supply'),
    JSON_VALUE(raw_json, '$.place_of_supply_code')
  ) AS place_of_supply,
  loaded_at
FROM `finance_bronze.zoho_raw`
WHERE entity_name = 'invoices'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY source_record_id
  ORDER BY loaded_at DESC
) = 1;


-- Bill tax fields follow the same header-level assumption as invoices.
CREATE OR REPLACE VIEW `finance_silver.fact_bills` AS
SELECT
  run_id,
  source_record_id,
  source_record_id AS bill_id,
  JSON_VALUE(raw_json, '$.bill_number') AS bill_number,
  JSON_VALUE(raw_json, '$.vendor_id') AS vendor_id,
  JSON_VALUE(raw_json, '$.vendor_name') AS vendor_name,
  SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS bill_date,
  SAFE_CAST(JSON_VALUE(raw_json, '$.due_date') AS DATE) AS due_date,
  JSON_VALUE(raw_json, '$.status') AS status,
  JSON_VALUE(raw_json, '$.currency_code') AS currency_code,
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
  COALESCE(
    JSON_VALUE(raw_json, '$.gst_no'),
    JSON_VALUE(raw_json, '$.gstin'),
    JSON_VALUE(raw_json, '$.tax_identification_number')
  ) AS gstin,
  JSON_VALUE(raw_json, '$.gst_treatment') AS gst_treatment,
  COALESCE(
    JSON_VALUE(raw_json, '$.place_of_supply'),
    JSON_VALUE(raw_json, '$.place_of_supply_code')
  ) AS place_of_supply,
  loaded_at
FROM `finance_bronze.zoho_raw`
WHERE entity_name = 'bills'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY source_record_id
  ORDER BY loaded_at DESC
) = 1;


-- Journals may carry detailed debit/credit postings inside line_items. This
-- header view keeps the common journal fields and any top-level total amount.
CREATE OR REPLACE VIEW `finance_silver.fact_journals` AS
SELECT
  run_id,
  source_record_id,
  source_record_id AS journal_id,
  JSON_VALUE(raw_json, '$.journal_number') AS journal_number,
  SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS journal_date,
  JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
  JSON_VALUE(raw_json, '$.status') AS status,
  JSON_VALUE(raw_json, '$.notes') AS notes,
  JSON_VALUE(raw_json, '$.currency_code') AS currency_code,
  SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
  COALESCE(
    SAFE_CAST(JSON_VALUE(raw_json, '$.total') AS NUMERIC),
    SAFE_CAST(JSON_VALUE(raw_json, '$.amount') AS NUMERIC)
  ) AS total_amount,
  loaded_at
FROM `finance_bronze.zoho_raw`
WHERE entity_name = 'journals'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY source_record_id
  ORDER BY loaded_at DESC
) = 1;


CREATE OR REPLACE VIEW `finance_silver.fact_customer_payments` AS
SELECT
  run_id,
  source_record_id,
  source_record_id AS payment_id,
  COALESCE(
    JSON_VALUE(raw_json, '$.payment_number'),
    JSON_VALUE(raw_json, '$.payment_no')
  ) AS payment_number,
  JSON_VALUE(raw_json, '$.customer_id') AS customer_id,
  JSON_VALUE(raw_json, '$.customer_name') AS customer_name,
  SAFE_CAST(JSON_VALUE(raw_json, '$.date') AS DATE) AS payment_date,
  JSON_VALUE(raw_json, '$.payment_mode') AS payment_mode,
  JSON_VALUE(raw_json, '$.reference_number') AS reference_number,
  JSON_VALUE(raw_json, '$.status') AS status,
  JSON_VALUE(raw_json, '$.currency_code') AS currency_code,
  SAFE_CAST(JSON_VALUE(raw_json, '$.exchange_rate') AS NUMERIC) AS exchange_rate,
  SAFE_CAST(JSON_VALUE(raw_json, '$.amount') AS NUMERIC) AS amount,
  SAFE_CAST(JSON_VALUE(raw_json, '$.bank_charges') AS NUMERIC) AS bank_charges,
  SAFE_CAST(JSON_VALUE(raw_json, '$.unused_amount') AS NUMERIC) AS unused_amount,
  loaded_at
FROM `finance_bronze.zoho_raw`
WHERE entity_name = 'customer_payments'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY source_record_id
  ORDER BY loaded_at DESC
) = 1;


-- ============================================================
-- Gold Layer
-- Gold views build on silver views and are shaped for dashboards,
-- MIS reporting, and reconciliation workflows.
-- ============================================================

CREATE OR REPLACE VIEW `finance_gold.dashboard_summary` AS
SELECT
  CURRENT_TIMESTAMP() AS generated_at,
  (SELECT COUNT(*) FROM `finance_silver.dim_accounts`) AS account_count,
  (SELECT COUNT(*) FROM `finance_silver.dim_contacts`) AS contact_count,
  (SELECT COUNT(*) FROM `finance_silver.fact_invoices`) AS invoice_count,
  COALESCE((SELECT SUM(total_amount) FROM `finance_silver.fact_invoices`), 0) AS invoice_total_amount,
  COALESCE((SELECT SUM(balance_amount) FROM `finance_silver.fact_invoices`), 0) AS invoice_outstanding_amount,
  (SELECT COUNT(*) FROM `finance_silver.fact_bills`) AS bill_count,
  COALESCE((SELECT SUM(total_amount) FROM `finance_silver.fact_bills`), 0) AS bill_total_amount,
  COALESCE((SELECT SUM(balance_amount) FROM `finance_silver.fact_bills`), 0) AS bill_outstanding_amount,
  (SELECT COUNT(*) FROM `finance_silver.fact_journals`) AS journal_count,
  COALESCE((SELECT SUM(total_amount) FROM `finance_silver.fact_journals`), 0) AS journal_total_amount,
  (SELECT COUNT(*) FROM `finance_silver.fact_customer_payments`) AS customer_payment_count,
  COALESCE((SELECT SUM(amount) FROM `finance_silver.fact_customer_payments`), 0) AS customer_payment_total_amount;


-- Revenue uses invoice totals, expenses use bill totals, and journal
-- adjustments use the top-level journal total if Zoho provides it. If journal
-- totals are not signed, expand journals to line items before using this for
-- final statutory reporting.
CREATE OR REPLACE VIEW `finance_gold.mis_monthly_pl` AS
WITH invoice_monthly AS (
  SELECT
    DATE_TRUNC(invoice_date, MONTH) AS report_month,
    COUNT(*) AS invoice_count,
    SUM(COALESCE(total_amount, 0)) AS revenue_amount
  FROM `finance_silver.fact_invoices`
  WHERE invoice_date IS NOT NULL
  GROUP BY report_month
),
bill_monthly AS (
  SELECT
    DATE_TRUNC(bill_date, MONTH) AS report_month,
    COUNT(*) AS bill_count,
    SUM(COALESCE(total_amount, 0)) AS expense_amount
  FROM `finance_silver.fact_bills`
  WHERE bill_date IS NOT NULL
  GROUP BY report_month
),
journal_monthly AS (
  SELECT
    DATE_TRUNC(journal_date, MONTH) AS report_month,
    COUNT(*) AS journal_count,
    SUM(COALESCE(total_amount, 0)) AS journal_adjustment_amount
  FROM `finance_silver.fact_journals`
  WHERE journal_date IS NOT NULL
  GROUP BY report_month
),
months AS (
  SELECT report_month FROM invoice_monthly
  UNION DISTINCT
  SELECT report_month FROM bill_monthly
  UNION DISTINCT
  SELECT report_month FROM journal_monthly
)
SELECT
  months.report_month,
  COALESCE(invoice_monthly.invoice_count, 0) AS invoice_count,
  COALESCE(bill_monthly.bill_count, 0) AS bill_count,
  COALESCE(journal_monthly.journal_count, 0) AS journal_count,
  COALESCE(invoice_monthly.revenue_amount, 0) AS revenue_amount,
  COALESCE(bill_monthly.expense_amount, 0) AS expense_amount,
  COALESCE(journal_monthly.journal_adjustment_amount, 0) AS journal_adjustment_amount,
  COALESCE(invoice_monthly.revenue_amount, 0)
    - COALESCE(bill_monthly.expense_amount, 0)
    + COALESCE(journal_monthly.journal_adjustment_amount, 0) AS profit_amount
FROM months
LEFT JOIN invoice_monthly USING (report_month)
LEFT JOIN bill_monthly USING (report_month)
LEFT JOIN journal_monthly USING (report_month);


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
  total_amount AS transaction_amount,
  balance_amount AS outstanding_amount,
  status,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_invoices`

UNION ALL

SELECT
  'bill' AS source_type,
  'bill_payable' AS transaction_type,
  bill_id AS transaction_id,
  bill_number AS transaction_number,
  bill_date AS transaction_date,
  vendor_id AS counterparty_id,
  vendor_name AS counterparty_name,
  bill_number AS reference_number,
  CAST(NULL AS STRING) AS payment_mode,
  currency_code,
  -COALESCE(total_amount, 0) AS transaction_amount,
  balance_amount AS outstanding_amount,
  status,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_bills`

UNION ALL

SELECT
  'customer_payment' AS source_type,
  'customer_payment' AS transaction_type,
  payment_id AS transaction_id,
  payment_number AS transaction_number,
  payment_date AS transaction_date,
  customer_id AS counterparty_id,
  customer_name AS counterparty_name,
  reference_number,
  payment_mode,
  currency_code,
  amount AS transaction_amount,
  CAST(0 AS NUMERIC) AS outstanding_amount,
  status,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_customer_payments`

UNION ALL

SELECT
  'journal' AS source_type,
  'journal_adjustment' AS transaction_type,
  journal_id AS transaction_id,
  journal_number AS transaction_number,
  journal_date AS transaction_date,
  CAST(NULL AS STRING) AS counterparty_id,
  CAST(NULL AS STRING) AS counterparty_name,
  reference_number,
  CAST(NULL AS STRING) AS payment_mode,
  currency_code,
  total_amount AS transaction_amount,
  CAST(NULL AS NUMERIC) AS outstanding_amount,
  status,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_journals`;


-- GST reconciliation is based on header-level GST fields where present. For
-- exact line-level GST matching, add a future line-item view from raw_json.
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
  igst_amount AS igst,
  cgst_amount AS cgst,
  sgst_amount AS sgst,
  tax_amount AS total_tax,
  total_amount AS invoice_value,
  status,
  currency_code,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_invoices`

UNION ALL

SELECT
  'bill' AS source_type,
  bill_id AS document_id,
  bill_number AS document_number,
  bill_date AS document_date,
  vendor_id AS party_id,
  vendor_name AS party_name,
  gstin,
  gst_treatment,
  place_of_supply,
  taxable_amount AS taxable_value,
  igst_amount AS igst,
  cgst_amount AS cgst,
  sgst_amount AS sgst,
  tax_amount AS total_tax,
  total_amount AS invoice_value,
  status,
  currency_code,
  run_id,
  source_record_id,
  loaded_at
FROM `finance_silver.fact_bills`;
