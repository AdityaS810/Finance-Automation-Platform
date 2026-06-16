-- ============================================================
-- Finance Automation Platform
-- Generic ETL Framework Tables
-- Purpose: Track ETL runs and store raw Zoho Books payloads
-- ============================================================

-- Append-only status history for ETL executions. A normal run writes one
-- "running" row and then one terminal "success" or "failed" row with the
-- same run_id. This avoids BigQuery streaming-buffer DML limitations.
CREATE TABLE IF NOT EXISTS `finance_bronze.etl_runs`
(
  run_id STRING NOT NULL,
  source_system STRING NOT NULL,
  status STRING NOT NULL,
  started_at TIMESTAMP NOT NULL,
  completed_at TIMESTAMP,
  records_loaded INT64,
  error_message STRING,
  metadata_json STRING,
  triggered_by STRING,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(started_at)
CLUSTER BY run_id, source_system, status;

-- Generic raw table for Zoho Books. Each source record is stored as
-- raw_json so new entities can be added without creating a new table.
CREATE TABLE IF NOT EXISTS `finance_bronze.zoho_raw`
(
  run_id STRING NOT NULL,
  source_system STRING NOT NULL,
  entity_name STRING NOT NULL,
  source_record_id STRING,
  raw_json STRING NOT NULL,
  gcs_uri STRING,
  loaded_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(loaded_at)
CLUSTER BY entity_name, source_record_id, run_id;
