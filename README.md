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