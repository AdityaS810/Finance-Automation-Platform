-- ============================================================
-- Finance Automation Platform
-- Silver Layer Views
-- Purpose: Clean and deduplicate Zoho Books bronze data
-- ============================================================


-- ============================================================
-- 1. Accounts Dimension
-- ============================================================

CREATE OR REPLACE VIEW `internal-project-work-497507.finance_silver.dim_accounts` AS
WITH parsed AS (
  SELECT
    run_id,
    loaded_at,
    source_record_id AS account_id,
    JSON_VALUE(raw_json, '$.account_name') AS account_name,
    JSON_VALUE(raw_json, '$.account_code') AS account_code,
    JSON_VALUE(raw_json, '$.account_type') AS account_type,
    JSON_VALUE(raw_json, '$.description') AS description,
    JSON_VALUE(raw_json, '$.is_active') AS is_active,
    raw_json
  FROM `internal-project-work-497507.finance_bronze.zoho_accounts_raw`
),

deduped AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY account_id
      ORDER BY loaded_at DESC
    ) AS row_num
  FROM parsed
)

SELECT
  account_id,
  account_name,
  account_code,
  account_type,
  description,
  is_active,
  run_id,
  loaded_at,
  raw_json
FROM deduped
WHERE row_num = 1;


-- ============================================================
-- 2. Contacts Dimension
-- ============================================================

CREATE OR REPLACE VIEW `internal-project-work-497507.finance_silver.dim_contacts` AS
WITH parsed AS (
  SELECT
    run_id,
    loaded_at,
    source_record_id AS contact_id,
    JSON_VALUE(raw_json, '$.contact_name') AS contact_name,
    JSON_VALUE(raw_json, '$.company_name') AS company_name,
    JSON_VALUE(raw_json, '$.contact_type') AS contact_type,
    JSON_VALUE(raw_json, '$.email') AS email,
    JSON_VALUE(raw_json, '$.phone') AS phone,
    JSON_VALUE(raw_json, '$.status') AS status,
    raw_json
  FROM `internal-project-work-497507.finance_bronze.zoho_contacts_raw`
),

deduped AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY contact_id
      ORDER BY loaded_at DESC
    ) AS row_num
  FROM parsed
)

SELECT
  contact_id,
  contact_name,
  company_name,
  contact_type,
  email,
  phone,
  status,
  run_id,
  loaded_at,
  raw_json
FROM deduped
WHERE row_num = 1;


-- ============================================================
-- 3. Invoices Fact
-- ============================================================

CREATE OR REPLACE VIEW `internal-project-work-497507.finance_silver.fact_invoices` AS
WITH parsed AS (
  SELECT
    run_id,
    loaded_at,
    source_record_id AS invoice_id,
    JSON_VALUE(raw_json, '$.invoice_number') AS invoice_number,
    JSON_VALUE(raw_json, '$.customer_id') AS customer_id,
    JSON_VALUE(raw_json, '$.customer_name') AS customer_name,
    JSON_VALUE(raw_json, '$.date') AS invoice_date,
    JSON_VALUE(raw_json, '$.due_date') AS due_date,
    JSON_VALUE(raw_json, '$.status') AS status,
    SAFE_CAST(JSON_VALUE(raw_json, '$.total') AS NUMERIC) AS total_amount,
    SAFE_CAST(JSON_VALUE(raw_json, '$.balance') AS NUMERIC) AS balance_amount,
    JSON_VALUE(raw_json, '$.currency_code') AS currency_code,
    raw_json
  FROM `internal-project-work-497507.finance_bronze.zoho_invoices_raw`
),

deduped AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY invoice_id
      ORDER BY loaded_at DESC
    ) AS row_num
  FROM parsed
)

SELECT
  invoice_id,
  invoice_number,
  customer_id,
  customer_name,
  invoice_date,
  due_date,
  status,
  total_amount,
  balance_amount,
  currency_code,
  run_id,
  loaded_at,
  raw_json
FROM deduped
WHERE row_num = 1;


-- ============================================================
-- 4. Transactions / Journals Fact
-- ============================================================

CREATE OR REPLACE VIEW `internal-project-work-497507.finance_silver.fact_transactions` AS
WITH parsed AS (
  SELECT
    run_id,
    loaded_at,
    source_record_id AS journal_id,
    JSON_VALUE(raw_json, '$.journal_number') AS journal_number,
    JSON_VALUE(raw_json, '$.date') AS transaction_date,
    JSON_VALUE(raw_json, '$.status') AS status,
    JSON_VALUE(raw_json, '$.notes') AS notes,
    raw_json
  FROM `internal-project-work-497507.finance_bronze.zoho_transactions_raw`
),

deduped AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY journal_id
      ORDER BY loaded_at DESC
    ) AS row_num
  FROM parsed
)

SELECT
  journal_id,
  journal_number,
  transaction_date,
  status,
  notes,
  run_id,
  loaded_at,
  raw_json
FROM deduped
WHERE row_num = 1;