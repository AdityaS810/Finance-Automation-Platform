-- ============================================================
-- Finance Automation Platform
-- Bronze Layer Tables
-- Purpose: Store raw Zoho Books API records in BigQuery
-- ============================================================

CREATE TABLE IF NOT EXISTS `finance-automation-dev-001.finance_bronze.zoho_accounts_raw`
(
  run_id STRING,
  source_system STRING,
  entity_name STRING,
  source_record_id STRING,
  raw_json STRING,
  loaded_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `finance-automation-dev-001.finance_bronze.zoho_contacts_raw`
(
  run_id STRING,
  source_system STRING,
  entity_name STRING,
  source_record_id STRING,
  raw_json STRING,
  loaded_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `finance-automation-dev-001.finance_bronze.zoho_invoices_raw`
(
  run_id STRING,
  source_system STRING,
  entity_name STRING,
  source_record_id STRING,
  raw_json STRING,
  loaded_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `finance-automation-dev-001.finance_bronze.zoho_transactions_raw`
(
  run_id STRING,
  source_system STRING,
  entity_name STRING,
  source_record_id STRING,
  raw_json STRING,
  loaded_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `finance-automation-dev-001.finance_bronze.zoho_customer_payments_raw`
(
  run_id STRING,
  source_system STRING,
  entity_name STRING,
  source_record_id STRING,
  raw_json STRING,
  loaded_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `finance-automation-dev-001.finance_bronze.zoho_bills_raw`
(
  run_id STRING,
  source_system STRING,
  entity_name STRING,
  source_record_id STRING,
  raw_json STRING,
  loaded_at TIMESTAMP
);