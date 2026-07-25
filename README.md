# Finance Automation Platform - Backend Setup

## Project Brief

This project builds a Finance Automation Platform that connects Zoho Books with GCP, stores finance data in BigQuery, supports document uploads, generates MIS reports, and later runs Gemini-based reconciliation agents.

Business-facing data flow:

Raw Layer -> Enrich Layer -> Consume Layer

Internal dataset names may still follow bronze/silver/gold naming, but business-facing layers are Raw, Enrich, and Consume. Bronze = Raw, Silver = Enrich, and Gold = Consume.

## Current Completed Work

### Week 1 - Zoho Books + GCP Setup

Completed:

- Created Python backend project structure
- Set up virtual environment
- Installed required Python packages
- Created Zoho OAuth integration
- Generated Zoho refresh token using Postman
- Connected Python to Zoho Books API
- Pulled Zoho Chart of Accounts
- Pulled Zoho Contacts
- Pulled Zoho Invoices
- Added Zoho Journals / Transactions extractor
- Created GCS raw bucket
- Created BigQuery Raw layer dataset (`finance_bronze`)
- Created Raw layer tables
- Ran Zoho cloud sync successfully
- Uploaded raw Zoho JSON files to GCS
- Loaded Zoho records into Raw layer tables
- Created BigQuery Enrich layer dataset (`finance_silver`)
- Created Enrich views for clean/deduplicated records
- Created Consume layer views (`finance_gold`) for dashboards, MIS, GST reconciliation, and bank reconciliation

## Current Working Cloud Flow

Zoho Books API
-> Python cloud sync script
-> Google Cloud Storage raw bucket
-> Raw Layer (`finance_bronze`)
-> Enrich Layer (`finance_silver`)
-> Consume Layer (`finance_gold`)

Data moves through Raw, Enrich, and Consume layers.

## GCP Project Details

Project name:

Internal Project Work

Project ID:

internal-project-work-497507

GCS raw bucket:

finance-automation-raw-internal-project-work-497507

BigQuery datasets:

finance_bronze
finance_silver
finance_gold

## Raw Layer Tables

Internal BigQuery dataset: `finance_bronze`

finance_bronze.zoho_accounts_raw
finance_bronze.zoho_contacts_raw
finance_bronze.zoho_invoices_raw
finance_bronze.zoho_transactions_raw
finance_bronze.zoho_customer_payments_raw
finance_bronze.zoho_bills_raw
finance_bronze.zoho_raw

## Enrich Layer Views

Internal BigQuery dataset: `finance_silver`

finance_silver.dim_accounts
finance_silver.dim_contacts
finance_silver.fact_invoices
finance_silver.fact_transactions
finance_silver.fact_bank_transactions
finance_silver.fact_bank_statement_lines
finance_silver.fact_gstr_lines

## Consume Layer Views

Internal BigQuery dataset: `finance_gold`

finance_gold.dashboard_summary
finance_gold.mis_monthly_pl
finance_gold.bank_reconciliation_input
finance_gold.gst_reconciliation_input

Consume layer is where Excel/Reconciliation reads final business-ready data.

## Latest Verified Row Counts

Raw layer:

accounts: 132
contacts: 2
invoices: 2
transactions: 0

Enrich layer:

accounts: 66
contacts: 1
invoices: 1
transactions: 0

Note: the Raw layer contains every sync run, so repeated runs create repeated raw batches. The Enrich layer keeps the latest clean record per source ID.

## Important Files

backend/zoho/auth.py
Handles Zoho OAuth access token generation.

backend/zoho/client.py
Reusable Zoho Books API client.

backend/zoho/extract_zoho.py
Fetches accounts, contacts, invoices, and journals from Zoho Books.

backend/gcp/gcs_loader.py
Uploads raw JSON to Google Cloud Storage.

backend/gcp/bigquery_loader.py
Loads raw Zoho records into BigQuery Raw layer tables.

backend/scripts/run_zoho_cloud_sync.py
Runs the Zoho to GCS to BigQuery sync.

backend/scripts/run_zoho_local_sync.py
Runs local Zoho sync for testing.

sql/ddl/create_bronze_tables.sql
Creates BigQuery Raw layer tables. The file name keeps the internal dataset convention.

sql/ddl/create_silver_views.sql
Creates BigQuery Enrich layer views. The file name keeps the internal dataset convention.

sql/ddl/create_silver_gold_views.sql
Creates BigQuery Enrich and Consume views. The file name keeps the internal dataset convention.

## Local Development Commands

Activate virtual environment:

```powershell
.\.venv\Scripts\activate
```

## Google Cloud Run Deployment

The Streamlit service is deployed with these settings:

- Service: `finance-automation-platform`
- Project: `internal-project-work-497507`
- Region: `asia-south1`
- Container port: the Cloud Run `PORT` value, with `8080` as the local fallback

Build and deploy from the repository root:

```powershell
gcloud run deploy finance-automation-platform `
  --source . `
  --project internal-project-work-497507 `
  --region asia-south1
```

Required non-secret environment variables:

- `GCP_PROJECT_ID=internal-project-work-497507`
- `GCS_RAW_BUCKET=finance-automation-raw-internal-project-work-497507`
- `ZOHO_ACCOUNTS_BASE_URL`
- `ZOHO_BOOKS_BASE_URL`

Optional non-secret settings include `BIGQUERY_LOCATION` (defaults to `asia-south1`), `FINANCE_OUTPUT_ROOT` (defaults to a `/tmp`-backed directory on Cloud Run), `VERTEX_AI_PROJECT_ID`, `VERTEX_AI_LOCATION`, `VERTEX_AI_MODEL`, `GEMINI_MODEL`, `DEFAULT_BANK_NAME`, `DEFAULT_BANK_ACCOUNT_MASKED`, `DEFAULT_GSTR_TYPE`, and the legacy `ZOHO_ORGANIZATION_ID`.

Configure these credentials as Secret Manager-backed Cloud Run environment variables:

- `ZOHO_CLIENT_ID`
- `ZOHO_CLIENT_SECRET`
- `ZOHO_REFRESH_TOKEN`
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) when API-key Gemini features are enabled

Do not pass or package `.env`. Grant the Cloud Run service account access to BigQuery, Cloud Storage, Vertex AI (when used), and only the required Secret Manager secrets.

## Data Warehouse Layers

Data moves through Raw, Enrich, and Consume layers.

Raw Layer = source/raw ingested data. Zoho Books records land in `finance_bronze.zoho_raw` with the full `raw_json` payload, run metadata, and load timestamps. Raw stores all raw versions from every sync run.

`finance_bronze.zoho_raw` is the canonical Bronze source for Zoho entities and production Enrich/Consume views. New bank transactions write to `zoho_raw` with `entity_name = 'bank_transactions'`, and new journals use `entity_name = 'journals'`. The older `finance_bronze.zoho_transactions_raw` table and `finance_silver.fact_transactions` view remain for audit compatibility because historical Cloud Function runs labelled journal payloads as `transactions`; they are not canonical bank sources.

Enrich Layer = cleaned/transformed/standardized data. Latest views in `finance_silver` read from Raw, filter by `entity_name`, extract typed fields, and keep the current record per `source_record_id`. Enrich history views keep every typed version with `version_number` and `is_current` for audit and change tracking.

Consume Layer = final business-ready data used by MIS, GST Reconciliation, Bank Reconciliation, and dashboards. Views in `finance_gold` build on current Enrich views for dashboard totals, monthly MIS profit/loss inputs, bank reconciliation inputs, and GST reconciliation inputs.

Internal dataset names may still follow bronze/silver/gold naming, but business-facing layers are Raw, Enrich, and Consume. Bronze = Raw, Silver = Enrich, and Gold = Consume.

MIS reports are generated from the Consume layer. MIS report generation uses Consume layer BigQuery data from `finance_gold.mis_monthly_pl` and `finance_gold.dashboard_summary`. The Streamlit MIS page writes the workbook to `frontend/outputs/MIS_PL_FY2526_generated.xlsx` with Summary, Monthly P&L, and Dashboard KPIs sheets.

The Downloads page can also generate a **Vendor Payments & Transactions** Excel report from `finance_silver.dim_contacts`, `finance_silver.fact_bills`, and `finance_silver.fact_transactions`. The workbook contains Summary, Vendor Bills, Vendor Transactions, and Data Availability sheets. It keeps bill balances separate from transaction amounts and clearly notes that dedicated vendor-payment records are not currently available in the warehouse.

## Multi-Organization Zoho Sync

The Zoho sync supports two non-secret organization configs:

- `us` = Midoffice Data International, Inc, org ID `916007477`, base currency USD
- `india` = Midoffice Data Solutions Private Limited, org ID `880373191`, base currency INR

Each sync fetches the same configured Zoho Books entities for both organizations and writes raw files under `raw/zoho_books/{org_key}/{entity}/...`. Raw rows include `source_org_key`, `source_org_id`, `source_org_name`, `source_country`, and `source_currency` so Enrich and Consume can preserve source lineage.

Consume reporting is INR-based. The current demo FX view uses USD to INR = `83.00` and INR to INR = `1.00`; replace `finance_silver.fx_rates_demo` with managed FX rates before final statutory reporting. The MIS page can generate consolidated INR reporting for all organizations or filter to India/US.

Apply the Raw layer metadata migration before the next multi-org sync:

```powershell
bq query --project_id=internal-project-work-497507 --use_legacy_sql=false < .\sql\ddl\alter_bronze_add_org_metadata.sql
```

## Reconciliation Logic

Reconciliation uses final business-ready data from the Consume layer. Consume layer is where Excel/Reconciliation reads final business-ready data.

Bank reconciliation now uses deterministic backend matching before any AI explanation layer. Uploaded bank lines from the Enrich layer table `finance_silver.fact_bank_statement_lines` are compared with accounting-side Consume rows from `finance_gold.bank_reconciliation_input` using signed amount matching with a small tolerance, transaction dates within plus/minus 3 days, and narration/customer/vendor text similarity. Results are saved to `frontend/outputs/reconciliation_exports/bank_reconciliation_results.xlsx`.

GST reconciliation compares uploaded GSTR lines from the Enrich layer table `finance_silver.fact_gstr_lines` with accounting-side Consume GST records from `finance_gold.gst_reconciliation_input`. Matching uses GSTIN, invoice number, taxable value, and IGST/CGST/SGST/total tax checks. Results are saved to `frontend/outputs/reconciliation_exports/gst_reconciliation_results.xlsx`.

Optional Vertex AI Gemini insights can explain reconciliation exceptions for human review. Enable them with Google Cloud Application Default Credentials plus `VERTEX_AI_PROJECT_ID` or `GCP_PROJECT_ID`; `VERTEX_AI_LOCATION` defaults to `asia-south1` and `VERTEX_AI_MODEL` defaults to `gemini-2.0-flash`. Gemini is limited to the first 20 uncertain rows, does not calculate finance values, and never changes rule-based match statuses or confidence scores. If Vertex AI is unavailable, the Streamlit page still shows the rule-based results and downloadable Excel files.

Create or refresh the current Enrich and Consume views with:

```powershell
bq query --project_id=internal-project-work-497507 --use_legacy_sql=false < .\sql\ddl\create_silver_gold_views.sql
```
