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
FROM `finance_silver.fact_bills`
UNION ALL
SELECT
  'expense' AS source_type,
  expense_id AS document_id,
  expense_number AS document_number,
  expense_date AS document_date,
  vendor_id AS party_id,
  vendor_name AS party_name,
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
FROM `finance_silver.fact_expenses`
WHERE
  (gstin IS NOT NULL AND TRIM(gstin) != '')
  OR COALESCE(tax_amount, 0) != 0
  OR COALESCE(igst_amount, 0) != 0
  OR COALESCE(cgst_amount, 0) != 0
  OR COALESCE(sgst_amount, 0) != 0
  OR LOWER(COALESCE(gst_treatment, '')) NOT IN ('', 'out_of_scope', 'non_gst', 'non-gst');
