-- ============================================================
-- Finance Automation Platform
-- Bronze organization metadata migration
-- Purpose: Add non-secret Zoho organization metadata to existing raw rows table
-- ============================================================

ALTER TABLE `finance_bronze.zoho_raw`
ADD COLUMN IF NOT EXISTS source_org_key STRING;

ALTER TABLE `finance_bronze.zoho_raw`
ADD COLUMN IF NOT EXISTS source_org_id STRING;

ALTER TABLE `finance_bronze.zoho_raw`
ADD COLUMN IF NOT EXISTS source_org_name STRING;

ALTER TABLE `finance_bronze.zoho_raw`
ADD COLUMN IF NOT EXISTS source_country STRING;

ALTER TABLE `finance_bronze.zoho_raw`
ADD COLUMN IF NOT EXISTS source_currency STRING;
