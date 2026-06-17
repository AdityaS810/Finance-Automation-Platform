# Finance Automation Platform — Backend Setup

## Project Brief

This project builds a Finance Automation Platform that connects Zoho Books with GCP, stores finance data in BigQuery, supports document uploads, generates MIS reports, and later runs Gemini-based reconciliation agents.

## Current Completed Work

### Week 1 — Zoho Books + GCP Setup

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
- Created BigQuery bronze dataset
- Created BigQuery bronze raw tables
- Ran Zoho cloud sync successfully
- Uploaded raw Zoho JSON files to GCS
- Loaded Zoho records into BigQuery bronze tables
- Created BigQuery silver dataset
- Created silver views for clean/deduplicated records

## Current Working Cloud Flow

Zoho Books API  
→ Python cloud sync script  
→ Google Cloud Storage raw bucket  
→ BigQuery bronze tables  
→ BigQuery silver views

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

## BigQuery Bronze Tables

finance_bronze.zoho_accounts_raw  
finance_bronze.zoho_contacts_raw  
finance_bronze.zoho_invoices_raw  
finance_bronze.zoho_transactions_raw  
finance_bronze.zoho_customer_payments_raw  
finance_bronze.zoho_bills_raw

## BigQuery Silver Views

finance_silver.dim_accounts  
finance_silver.dim_contacts  
finance_silver.fact_invoices  
finance_silver.fact_transactions

## Latest Verified Row Counts

Bronze layer:

accounts: 132  
contacts: 2  
invoices: 2  
transactions: 0

Silver layer:

accounts: 66  
contacts: 1  
invoices: 1  
transactions: 0

Note: Bronze contains every sync run, so repeated runs create repeated raw batches. Silver keeps the latest clean record per source ID.

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
Loads raw Zoho records into BigQuery bronze tables.

backend/scripts/run_zoho_cloud_sync.py  
Runs the Zoho to GCS to BigQuery sync.

backend/scripts/run_zoho_local_sync.py  
Runs local Zoho sync for testing.

sql/ddl/create_bronze_tables.sql  
Creates BigQuery bronze tables.

sql/ddl/create_silver_views.sql  
Creates BigQuery silver views.

## Local Development Commands

Activate virtual environment:

```powershell
.\.venv\Scripts\activate
```

## Data Warehouse Layers

Bronze = raw append-only source data. Zoho Books records land in `finance_bronze.zoho_raw` with the full `raw_json` payload, run metadata, and load timestamps. Bronze stores all raw versions from every sync run.

Silver = cleaned structured records. Latest views in `finance_silver` read from bronze, filter by `entity_name`, extract typed fields, and keep the current record per `source_record_id`. Silver history views keep every typed version with `version_number` and `is_current` for audit and change tracking.

Gold = reporting and reconciliation-ready business views. Views in `finance_gold` build on current Silver views for dashboard totals, monthly MIS profit/loss inputs, bank reconciliation inputs, and GST reconciliation inputs.

MIS report generation now uses real Gold layer BigQuery data from `finance_gold.mis_monthly_pl` and `finance_gold.dashboard_summary`. The Streamlit MIS page writes the workbook to `frontend/outputs/MIS_PL_FY2526_generated.xlsx` with Summary, Monthly P&L, and Dashboard KPIs sheets.

## Reconciliation Logic

Bank reconciliation now uses deterministic backend matching before any AI explanation layer. Uploaded bank lines from `finance_silver.fact_bank_statement_lines` are compared with accounting-side rows from `finance_gold.bank_reconciliation_input` using signed amount matching with a small tolerance, transaction dates within plus/minus 3 days, and narration/customer/vendor text similarity. Results are saved to `frontend/outputs/reconciliation_exports/bank_reconciliation_results.xlsx`.

GST reconciliation compares uploaded GSTR lines from `finance_silver.fact_gstr_lines` with accounting-side GST records from `finance_gold.gst_reconciliation_input`. Matching uses GSTIN, invoice number, taxable value, and IGST/CGST/SGST/total tax checks. Results are saved to `frontend/outputs/reconciliation_exports/gst_reconciliation_results.xlsx`.

Optional Vertex AI Gemini insights can explain reconciliation exceptions for human review. Enable them with Google Cloud Application Default Credentials plus `VERTEX_AI_PROJECT_ID` or `GCP_PROJECT_ID`; `VERTEX_AI_LOCATION` defaults to `asia-south1` and `VERTEX_AI_MODEL` defaults to `gemini-2.0-flash`. Gemini is limited to the first 20 uncertain rows, does not calculate finance values, and never changes rule-based match statuses or confidence scores. If Vertex AI is unavailable, the Streamlit page still shows the rule-based results and downloadable Excel files.

Create or refresh the current Silver and Gold views with:

```powershell
bq query --project_id=internal-project-work-497507 --use_legacy_sql=false < .\sql\ddl\create_silver_gold_views.sql
```
