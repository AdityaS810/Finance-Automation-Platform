-- ============================================================
-- Finance Automation Platform
-- Upload Tables for Bank Statements and GSTR Files
-- Purpose: Store parsed upload data in BigQuery
-- ============================================================


-- ============================================================
-- 1. Upload tracking table
-- One row per uploaded file
-- ============================================================

CREATE TABLE IF NOT EXISTS `internal-project-work-497507.finance_bronze.file_uploads`
(
  upload_id STRING,
  file_type STRING,
  original_file_name STRING,
  gcs_raw_path STRING,
  uploaded_by STRING,
  uploaded_at TIMESTAMP,
  parse_status STRING,
  records_parsed INT64,
  error_message STRING
);


-- ============================================================
-- 2. Parsed bank statement lines
-- From uploaded CSV/PDF bank statements
-- ============================================================

CREATE TABLE IF NOT EXISTS `internal-project-work-497507.finance_silver.fact_bank_statement_lines`
(
  bank_line_id STRING,
  upload_id STRING,
  bank_name STRING,
  account_number_masked STRING,
  transaction_date DATE,
  value_date DATE,
  narration STRING,
  debit_amount NUMERIC,
  credit_amount NUMERIC,
  balance_amount NUMERIC,
  reference_number STRING,
  raw_row_number INT64,
  created_at TIMESTAMP
);


-- ============================================================
-- 3. Parsed GSTR lines
-- From uploaded GSTR-2A / GSTR-2B JSON or Excel files
-- ============================================================

CREATE TABLE IF NOT EXISTS `internal-project-work-497507.finance_silver.fact_gstr_lines`
(
  gstr_line_id STRING,
  upload_id STRING,
  gstr_type STRING,
  period STRING,
  supplier_gstin STRING,
  supplier_name STRING,
  invoice_number STRING,
  invoice_date DATE,
  taxable_value NUMERIC,
  igst_amount NUMERIC,
  cgst_amount NUMERIC,
  sgst_amount NUMERIC,
  total_tax NUMERIC,
  invoice_value NUMERIC,
  created_at TIMESTAMP
);