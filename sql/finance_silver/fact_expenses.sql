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
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC),
      line_amounts.line_taxable_amount
    ) AS sub_total_amount,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.sub_total') AS NUMERIC),
      line_amounts.line_taxable_amount
    ) AS taxable_amount,
    COALESCE(
      SAFE_CAST(JSON_VALUE(raw.raw_json, '$.tax_amount') AS NUMERIC),
      tax_breakup.tax_amount
    ) AS tax_amount,
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
