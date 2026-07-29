"""Generate the verified vendor-reconciliation workbook from BigQuery.

Only the verified Silver vendor sources and Phase 4 Gold views are queried.
Workbook generation stops if a blocking grain, direction, allocation, matching,
organization, or currency validation fails.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation


DEFAULT_PROJECT_ID = "internal-project-work-497507"
DEFAULT_BIGQUERY_LOCATION = "asia-south1"
BIGQUERY_QUERY_TIMEOUT_SECONDS = 120
BIGQUERY_QUERY_MAX_ATTEMPTS = 2

BILLS_VIEW = "finance_silver.fact_bills"
PAYMENTS_VIEW = "finance_silver.fact_vendor_payments"
ALLOCATIONS_VIEW = "finance_silver.bridge_vendor_payment_bill_allocations"
BANK_VIEW = "finance_silver.fact_bank_transactions"
BANK_STATEMENT_VIEW = "finance_silver.fact_bank_statement_lines"
UPLOADS_TABLE = "finance_bronze.file_uploads"
CONTACTS_VIEW = "finance_silver.dim_contacts"
PAYMENT_MATCHES_VIEW = "finance_gold.vendor_payment_bank_matches"
BILL_RECONCILIATION_VIEW = "finance_gold.vendor_bill_reconciliation"
EXCEPTIONS_VIEW = "finance_gold.vendor_reconciliation_exceptions"

REPORT_MODE_INPUT = "Reconciliation Input Data"
REPORT_MODE_ANALYSIS = "Automated Reconciliation Analysis"
REPORT_MODES = [REPORT_MODE_INPUT, REPORT_MODE_ANALYSIS]
ANALYSIS_DISCLAIMER = (
    "System-generated analysis. Finance review is required before accepting or "
    "posting reconciliation results."
)

WORKBOOK_SHEETS = [
    "Summary",
    "Vendor Bills",
    "Vendor Payments",
    "Payment Allocations",
    "Bank Transactions",
    "Reconciliation Results",
]

INPUT_WORKBOOK_SHEETS = [
    "Summary",
    "Vendor Bills",
    "Zoho Payment Batch",
    "Bank Statement",
]

INPUT_VENDOR_BILL_COLUMNS = [
    "Organization",
    "Vendor ID",
    "Vendor Name",
    "Bill ID",
    "Bill Number",
    "Bill Date",
    "Due Date",
    "Currency",
    "Taxable Amount",
    "GST Amount",
    "Bill Total",
    "Outstanding Balance",
    "Zoho Bill Status",
    "In Selected Bill Period",
    "Inclusion Reason",
]

INPUT_VENDOR_BILL_SOURCE_COLUMNS = INPUT_VENDOR_BILL_COLUMNS[:-2]

INPUT_VENDOR_PAYMENT_COLUMNS = [
    "Organization",
    "Vendor ID",
    "Vendor Name",
    "Payment ID",
    "Payment Number",
    "Payment Date",
    "Payment Reference",
    "Payment Mode",
    "Paid-Through Account Name",
    "Currency",
    "Total Payment Amount",
    "Allocated Amount",
    "Unapplied Amount",
    "Allocation Status",
]

INPUT_PAYMENT_ALLOCATION_COLUMNS = [
    "Organization",
    "Payment ID",
    "Bill Payment ID",
    "Bill ID",
    "Bill Number",
    "Amount Applied",
    "Allocation Key",
]

ZOHO_PAYMENT_BATCH_COLUMNS = [
    "Organization",
    "Vendor ID",
    "Vendor Name",
    "Payment ID",
    "Payment Date",
    "Payment Reference",
    "Payment Amount",
    "Bill ID",
    "Bill Number",
    "Amount Applied",
    "Unapplied Amount",
]

INPUT_BANK_STATEMENT_SOURCE_COLUMNS = [
    "Organization",
    "Bank Source",
    "Upload ID",
    "Source File",
    "Transaction Date",
    "Value Date",
    "Narration",
    "Reference Number",
    "Debit Amount",
    "Credit Amount",
    "Signed Amount",
    "Direction",
    "Currency",
    "Account Name",
    "Masked Account Number",
    "Statement Row ID",
]

MANUAL_REVIEW_BANK_COLUMNS = [
    "Extracted Invoice Number",
    "Invoice Reference Result",
    "Matching Bill Number",
    "Matching Bill ID",
    "Possible Amount Pattern",
    "Amount Difference",
    "Manual Review Note",
]

INPUT_BANK_STATEMENT_COLUMNS = [
    *INPUT_BANK_STATEMENT_SOURCE_COLUMNS,
    *MANUAL_REVIEW_BANK_COLUMNS,
]

INPUT_DATASET_COLUMNS = {
    "vendor_bills": INPUT_VENDOR_BILL_COLUMNS,
    "vendor_payments": INPUT_VENDOR_PAYMENT_COLUMNS,
    "payment_allocations": INPUT_PAYMENT_ALLOCATION_COLUMNS,
    "bank_statement": INPUT_BANK_STATEMENT_COLUMNS,
}

VENDOR_BILL_COLUMNS = [
    "Organization",
    "Vendor ID",
    "Vendor Name",
    "Bill ID",
    "Bill Number",
    "Bill Date",
    "Due Date",
    "Currency",
    "Bill Amount",
    "Outstanding Balance",
    "Source Bill Status",
    "Allocated Payment Count",
    "Allocated Amount",
    "Bank-Verified Amount",
    "Remaining Reconciliation Amount",
    "Reconciliation Status",
    "Reconciliation Reason",
    "Review Required",
    "Source Record ID",
    "Data Quality Status",
]

VENDOR_PAYMENT_COLUMNS = [
    "Organization",
    "Vendor ID",
    "Vendor Name",
    "Payment ID",
    "Payment Number",
    "Payment Date",
    "Reference Number",
    "Payment Mode",
    "Currency",
    "Payment Amount",
    "Allocated Amount",
    "Unapplied Amount",
    "Allocation Status",
    "Paid-Through Account Name",
    "Bank Match Status",
    "Bank Match Method",
    "Bank Match Reason",
    "Bank Transaction Date",
    "Bank Amount",
    "Review Required",
    "Source Record ID",
    "Bank Transaction Leg Key",
    "Data Quality Status",
]

PAYMENT_ALLOCATION_COLUMNS = [
    "Organization",
    "Vendor ID",
    "Vendor Name",
    "Payment ID",
    "Bill ID",
    "Bill Number",
    "Bill Payment ID",
    "Currency",
    "Amount Applied",
    "Payment Date",
    "Allocation Key",
    "Source Record ID",
    "Data Quality Status",
]

BANK_TRANSACTION_COLUMNS = [
    "Organization",
    "Account Label",
    "Transaction ID",
    "Transaction Date",
    "Transaction Type",
    "Debit/Credit",
    "Direction",
    "Currency",
    "Amount",
    "Signed Amount",
    "Status",
    "Multi-Leg Transaction",
    "Used in Vendor Match",
    "Review Required",
    "Source Record ID",
    "Bank Transaction Leg Key",
    "Match Method",
    "Data Quality Status",
]

RECONCILIATION_RESULT_COLUMNS = [
    "Organization",
    "Vendor ID",
    "Vendor Name",
    "Bill ID",
    "Bill Number",
    "Bill Date",
    "Due Date",
    "Currency",
    "Bill Amount",
    "Source Outstanding Balance",
    "Source Bill Status",
    "Payment Count",
    "Payment IDs",
    "Latest Payment Date",
    "Payment Amount",
    "Allocated Amount",
    "Bank Transaction Date",
    "Bank Amount",
    "Bank Match Status",
    "Bank Match Method",
    "Bank Pending Amount",
    "Remaining Reconciliation Amount",
    "Reconciliation Status",
    "Reconciliation Reason",
    "Exception Type",
    "Review Required",
    "Review Status",
    "Reviewer Comment",
]

DATASET_COLUMNS = {
    "vendor_bills": VENDOR_BILL_COLUMNS,
    "vendor_payments": VENDOR_PAYMENT_COLUMNS,
    "payment_allocations": PAYMENT_ALLOCATION_COLUMNS,
    "bank_transactions": BANK_TRANSACTION_COLUMNS,
    "reconciliation_results": RECONCILIATION_RESULT_COLUMNS,
}

INTERNAL_COLUMNS = {
    "vendor_payments": ["_Source Organization ID"],
}

DATE_COLUMNS = {
    "Bill Date",
    "Due Date",
    "Payment Date",
    "Bank Transaction Date",
    "Transaction Date",
    "Latest Payment Date",
    "Loaded Timestamp",
}
AMOUNT_COLUMNS = {
    "Bill Amount",
    "Taxable Amount",
    "GST Amount",
    "Bill Total",
    "Outstanding Balance",
    "Payment Amount",
    "Total Payment Amount",
    "Allocated Amount",
    "Unapplied Amount",
    "Bank Pending Amount",
    "Remaining Reconciliation Amount",
    "Amount Applied",
    "Amount",
    "Signed Amount",
    "Source Outstanding Balance",
    "Bank Amount",
    "Expected Payment After 2% TDS",
    "Expected Payment After 10% TDS",
    "Amount Difference",
}
TEXT_IDENTIFIER_COLUMNS = {
    "Vendor ID",
    "Bill ID",
    "Bill Number",
    "Payment ID",
    "Payment Number",
    "Bill Payment ID",
    "Allocation Key",
    "Transaction ID",
    "Reference Number",
    "Payment Reference",
    "Source Organization ID",
    "Source Record ID",
    "Bank Transaction Leg Key",
    "Run ID",
    "Payment IDs",
    "Extracted Invoice Number",
    "Upload ID",
    "Statement Row ID",
    "Matching Bill ID",
    "Matching Bill Number",
}
WRAP_COLUMNS = {
    "Vendor Name",
    "Description",
    "Narration",
    "Manual Review Note",
    "Reconciliation Reason",
    "Bank Match Reason",
    "Reviewer Comment",
}

DISPLAY_COLUMN_MAPS = {
    "vendor_bills": {
        "organization": "Organization",
        "vendor_id": "Vendor ID",
        "vendor_name": "Vendor Name",
        "bill_id": "Bill ID",
        "bill_number": "Bill Number",
        "bill_date": "Bill Date",
        "due_date": "Due Date",
        "currency": "Currency",
        "bill_amount": "Bill Amount",
        "outstanding_balance": "Outstanding Balance",
        "source_bill_status": "Source Bill Status",
        "allocated_payment_count": "Allocated Payment Count",
        "allocated_amount": "Allocated Amount",
        "bank_verified_amount": "Bank-Verified Amount",
        "remaining_reconciliation_amount": "Remaining Reconciliation Amount",
        "reconciliation_status": "Reconciliation Status",
        "reconciliation_reason": "Reconciliation Reason",
        "review_required": "Review Required",
        "source_record_id": "Source Record ID",
        "data_quality_status": "Data Quality Status",
    },
    "vendor_payments": {
        "organization": "Organization",
        "vendor_id": "Vendor ID",
        "vendor_name": "Vendor Name",
        "payment_id": "Payment ID",
        "payment_number": "Payment Number",
        "payment_date": "Payment Date",
        "reference_number": "Reference Number",
        "payment_mode": "Payment Mode",
        "currency": "Currency",
        "payment_amount": "Payment Amount",
        "allocated_amount": "Allocated Amount",
        "unapplied_amount": "Unapplied Amount",
        "allocation_status": "Allocation Status",
        "paid_through_account_name": "Paid-Through Account Name",
        "bank_match_status": "Bank Match Status",
        "bank_match_method": "Bank Match Method",
        "bank_match_reason": "Bank Match Reason",
        "bank_transaction_date": "Bank Transaction Date",
        "bank_amount": "Bank Amount",
        "review_required": "Review Required",
        "source_record_id": "Source Record ID",
        "bank_transaction_leg_key": "Bank Transaction Leg Key",
        "data_quality_status": "Data Quality Status",
        "_source_org_id": "_Source Organization ID",
    },
    "payment_allocations": {
        "organization": "Organization",
        "vendor_id": "Vendor ID",
        "vendor_name": "Vendor Name",
        "payment_id": "Payment ID",
        "bill_id": "Bill ID",
        "bill_number": "Bill Number",
        "bill_payment_id": "Bill Payment ID",
        "currency": "Currency",
        "amount_applied": "Amount Applied",
        "payment_date": "Payment Date",
        "allocation_key": "Allocation Key",
        "source_record_id": "Source Record ID",
        "data_quality_status": "Data Quality Status",
    },
    "bank_transactions": {
        "organization": "Organization",
        "account_label": "Account Label",
        "transaction_id": "Transaction ID",
        "transaction_date": "Transaction Date",
        "transaction_type": "Transaction Type",
        "debit_or_credit": "Debit/Credit",
        "direction": "Direction",
        "currency": "Currency",
        "amount": "Amount",
        "signed_amount": "Signed Amount",
        "status": "Status",
        "multi_leg_transaction": "Multi-Leg Transaction",
        "used_in_vendor_match": "Used in Vendor Match",
        "review_required": "Review Required",
        "source_record_id": "Source Record ID",
        "bank_transaction_leg_key": "Bank Transaction Leg Key",
        "match_method": "Match Method",
        "data_quality_status": "Data Quality Status",
    },
    "reconciliation_results": {
        "organization": "Organization",
        "vendor_id": "Vendor ID",
        "vendor_name": "Vendor Name",
        "bill_id": "Bill ID",
        "bill_number": "Bill Number",
        "bill_date": "Bill Date",
        "due_date": "Due Date",
        "currency": "Currency",
        "taxable_amount": "Taxable Amount",
        "gst_amount": "GST Amount",
        "bill_total": "Bill Total",
        "source_outstanding_balance": "Source Outstanding Balance",
        "source_bill_status": "Source Bill Status",
        "payment_count": "Payment Count",
        "payment_ids": "Payment IDs",
        "latest_payment_date": "Latest Payment Date",
        "payment_amount": "Payment Amount",
        "allocated_amount": "Allocated Amount",
        "bank_transaction_date": "Bank Transaction Date",
        "bank_amount": "Bank Amount",
        "bank_match_status": "Bank Match Status",
        "bank_match_method": "Bank Match Method",
        "bank_pending_amount": "Bank Pending Amount",
        "remaining_reconciliation_amount": "Remaining Reconciliation Amount",
        "reconciliation_status": "Reconciliation Status",
        "reconciliation_reason": "Reconciliation Reason",
        "exception_type": "Exception Type",
        "review_required": "Review Required",
        "review_status": "Review Status",
        "reviewer_comment": "Reviewer Comment",
    },
}

INPUT_DISPLAY_COLUMN_MAPS = {
    "vendor_bills": {
        "organization": "Organization",
        "vendor_id": "Vendor ID",
        "vendor_name": "Vendor Name",
        "bill_id": "Bill ID",
        "bill_number": "Bill Number",
        "bill_date": "Bill Date",
        "due_date": "Due Date",
        "currency": "Currency",
        "taxable_amount": "Taxable Amount",
        "gst_amount": "GST Amount",
        "bill_total": "Bill Total",
        "outstanding_balance": "Outstanding Balance",
        "zoho_bill_status": "Zoho Bill Status",
    },
    "vendor_payments": {
        "organization": "Organization",
        "vendor_id": "Vendor ID",
        "vendor_name": "Vendor Name",
        "payment_id": "Payment ID",
        "payment_number": "Payment Number",
        "payment_date": "Payment Date",
        "payment_reference": "Payment Reference",
        "payment_mode": "Payment Mode",
        "paid_through_account_name": "Paid-Through Account Name",
        "currency": "Currency",
        "total_payment_amount": "Total Payment Amount",
        "allocated_amount": "Allocated Amount",
        "unapplied_amount": "Unapplied Amount",
        "allocation_status": "Allocation Status",
    },
    "payment_allocations": {
        "organization": "Organization",
        "payment_id": "Payment ID",
        "bill_payment_id": "Bill Payment ID",
        "bill_id": "Bill ID",
        "bill_number": "Bill Number",
        "amount_applied": "Amount Applied",
        "allocation_key": "Allocation Key",
    },
    "bank_statement": {
        "organization": "Organization",
        "bank_source": "Bank Source",
        "upload_id": "Upload ID",
        "source_file": "Source File",
        "transaction_date": "Transaction Date",
        "value_date": "Value Date",
        "narration": "Narration",
        "reference_number": "Reference Number",
        "debit_amount": "Debit Amount",
        "credit_amount": "Credit Amount",
        "signed_amount": "Signed Amount",
        "direction": "Direction",
        "currency": "Currency",
        "account_name": "Account Name",
        "masked_account_number": "Masked Account Number",
        "statement_row_id": "Statement Row ID",
    },
}

load_dotenv()


class VendorReportValidationError(RuntimeError):
    """Raised when a blocking workbook safety validation fails."""


def _project_id(project_id: str | None = None) -> str:
    return project_id or os.getenv("GCP_PROJECT_ID") or DEFAULT_PROJECT_ID


def _bigquery_location(location: str | None = None) -> str:
    return (
        location
        or os.getenv("BQ_LOCATION")
        or os.getenv("BIGQUERY_LOCATION")
        or DEFAULT_BIGQUERY_LOCATION
    )


def _table_name(project_id: str, view_name: str) -> str:
    return f"`{project_id}.{view_name}`"


def _query_to_dataframe(
    client: Any,
    query: str,
    parameters: list[Any] | None = None,
    location: str | None = None,
    stage: str = "BigQuery query",
) -> pd.DataFrame:
    from google.cloud import bigquery
    from google.api_core import exceptions as google_exceptions

    job_config = bigquery.QueryJobConfig(query_parameters=parameters or [])
    transient_errors = (
        TimeoutError,
        google_exceptions.DeadlineExceeded,
        google_exceptions.InternalServerError,
        google_exceptions.ServiceUnavailable,
        google_exceptions.TooManyRequests,
    )
    result = None
    for attempt in range(1, BIGQUERY_QUERY_MAX_ATTEMPTS + 1):
        try:
            result = client.query(
                query,
                job_config=job_config,
                location=_bigquery_location(location),
                timeout=BIGQUERY_QUERY_TIMEOUT_SECONDS,
            ).result(timeout=BIGQUERY_QUERY_TIMEOUT_SECONDS)
            break
        except transient_errors as error:
            if attempt == BIGQUERY_QUERY_MAX_ATTEMPTS:
                raise RuntimeError(
                    f"{stage} timed out or failed transiently after one retry: "
                    f"{safe_exception_details(error)}"
                ) from error
        except Exception as error:
            error_details = safe_exception_details(error)
            if (
                error.__class__.__name__ == "RefreshError"
                or "reauthentication is needed" in error_details.lower()
            ):
                raise RuntimeError(
                    f"{stage} failed because Google authentication expired. "
                    "Run gcloud auth application-default login and try again."
                ) from error
            raise RuntimeError(f"{stage} failed: {error_details}") from error
    if result is None:
        raise RuntimeError(f"{stage} failed without returning a query result.")
    columns = [field.name for field in result.schema]
    return pd.DataFrame([dict(row.items()) for row in result], columns=columns)


def _query_parameters(
    start_date: date | None,
    end_date: date | None,
    organization: str | None = None,
    vendor: str | None = None,
    currency: str | None = None,
    source_bill_status: str | None = None,
    reconciliation_status: str | None = None,
    bank_match_status: str | None = None,
    review_required: bool | None = None,
    bank_upload_id: str | None = None,
) -> list[Any]:
    from google.cloud import bigquery

    values = {
        "start_date": ("DATE", start_date),
        "end_date": ("DATE", end_date),
        "organization": ("STRING", organization),
        "vendor": ("STRING", vendor),
        "currency": ("STRING", currency),
        "source_bill_status": ("STRING", source_bill_status),
        "reconciliation_status": ("STRING", reconciliation_status),
        "bank_match_status": ("STRING", bank_match_status),
        "review_required": ("BOOL", review_required),
        "bank_upload_id": ("STRING", bank_upload_id),
    }
    return [
        bigquery.ScalarQueryParameter(name, value_type, value)
        for name, (value_type, value) in values.items()
        if value is not None
    ]


def _where(filters: Mapping[str, Any], columns: Mapping[str, str]) -> str:
    predicates = []
    for parameter, column in columns.items():
        if filters.get(parameter) is None:
            continue
        if parameter == "start_date":
            predicates.append(f"{column} >= @start_date")
        elif parameter == "end_date":
            predicates.append(f"{column} <= @end_date")
        else:
            predicates.append(f"{column} = @{parameter}")
    return "WHERE " + " AND ".join(predicates) if predicates else ""


def build_reconciliation_input_queries(
    project_id: str,
    filters: Mapping[str, Any],
) -> dict[str, str]:
    """Build Silver-only queries for the manual-reconciliation input workbook."""
    statement_organization = (
        "@organization"
        if filters.get("organization") is not None
        else "CAST(NULL AS STRING)"
    )
    bills_filter = _where(
        filters,
        {
            "start_date": "bill.bill_date",
            "end_date": "bill.bill_date",
            "organization": "bill.source_org_key",
            "vendor": "bill.vendor_name",
            "currency": "bill.original_currency",
        },
    )
    payments_filter = _where(
        filters,
        {
            "start_date": "payment.payment_date",
            "end_date": "payment.payment_date",
            "organization": "payment.source_org_key",
            "vendor": "payment.vendor_name",
            "currency": "payment.currency_code",
        },
    )
    allocations_filter = _where(
        filters,
        {
            "start_date": "allocation.payment_date",
            "end_date": "allocation.payment_date",
            "organization": "allocation.source_org_key",
            "vendor": "allocation.vendor_name",
            "currency": "allocation.currency_code",
        },
    )
    bank_filter = _where(
        filters,
        {
            "start_date": "bank.transaction_date",
            "end_date": "bank.transaction_date",
            "bank_upload_id": "bank.upload_id",
        },
    )
    return {
        "vendor_bills": f"""
          SELECT
            bill.source_org_key AS organization,
            CAST(bill.vendor_id AS STRING) AS vendor_id,
            bill.vendor_name,
            CAST(bill.bill_id AS STRING) AS bill_id,
            bill.bill_number,
            bill.bill_date,
            bill.due_date,
            bill.original_currency AS currency,
            bill.taxable_amount,
            bill.tax_amount AS gst_amount,
            bill.total_amount AS bill_total,
            bill.balance_amount AS outstanding_balance,
            bill.status AS zoho_bill_status
          FROM {_table_name(project_id, BILLS_VIEW)} bill
          {bills_filter}
          ORDER BY bill.bill_date DESC, bill.vendor_name, bill.bill_number
        """,
        "current_bills": f"""
          SELECT DISTINCT
            bill.source_org_key AS organization,
            CAST(bill.vendor_id AS STRING) AS vendor_id,
            bill.vendor_name,
            CAST(bill.bill_id AS STRING) AS bill_id,
            bill.bill_number,
            bill.bill_date,
            bill.due_date,
            bill.original_currency AS currency,
            bill.taxable_amount,
            bill.tax_amount AS gst_amount,
            bill.total_amount AS bill_total,
            bill.balance_amount AS outstanding_balance,
            bill.status AS zoho_bill_status
          FROM {_table_name(project_id, BILLS_VIEW)} bill
          INNER JOIN {_table_name(project_id, ALLOCATIONS_VIEW)} allocation
            ON allocation.source_org_key = bill.source_org_key
           AND CAST(allocation.bill_id AS STRING) = CAST(bill.bill_id AS STRING)
          {allocations_filter}
        """,
        "vendor_payments": f"""
          SELECT
            payment.source_org_key AS organization,
            CAST(payment.vendor_id AS STRING) AS vendor_id,
            payment.vendor_name,
            CAST(payment.payment_id AS STRING) AS payment_id,
            payment.payment_number,
            payment.payment_date,
            payment.reference_number AS payment_reference,
            payment.payment_mode,
            payment.paid_through_account_name,
            payment.currency_code AS currency,
            payment.payment_amount AS total_payment_amount,
            payment.allocated_amount,
            payment.unapplied_amount,
            payment.allocation_status
          FROM {_table_name(project_id, PAYMENTS_VIEW)} payment
          {payments_filter}
          ORDER BY payment.payment_date DESC, payment.vendor_name, payment.payment_id
        """,
        "payment_allocations": f"""
          SELECT
            allocation.source_org_key AS organization,
            CAST(allocation.payment_id AS STRING) AS payment_id,
            CAST(allocation.bill_payment_id AS STRING) AS bill_payment_id,
            CAST(allocation.bill_id AS STRING) AS bill_id,
            allocation.bill_number,
            allocation.amount_applied,
            CAST(allocation.allocation_key AS STRING) AS allocation_key
          FROM {_table_name(project_id, ALLOCATIONS_VIEW)} allocation
          {allocations_filter}
          ORDER BY allocation.payment_date DESC,
                   allocation.payment_id, allocation.bill_id
        """,
        "bank_statement": f"""
          WITH upload_metadata AS (
            SELECT upload_id, original_file_name
            FROM {_table_name(project_id, UPLOADS_TABLE)}
            WHERE file_type = 'bank_statement'
            QUALIFY ROW_NUMBER() OVER (
              PARTITION BY upload_id ORDER BY uploaded_at DESC
            ) = 1
          )
          SELECT
            {statement_organization} AS organization,
            COALESCE(NULLIF(bank.bank_name, ''), 'Uploaded Bank Statement')
              AS bank_source,
            CAST(bank.upload_id AS STRING) AS upload_id,
            metadata.original_file_name AS source_file,
            bank.transaction_date,
            bank.value_date,
            bank.narration,
            bank.reference_number,
            bank.debit_amount,
            bank.credit_amount,
            COALESCE(bank.credit_amount, 0) - COALESCE(bank.debit_amount, 0)
              AS signed_amount,
            CASE
              WHEN COALESCE(bank.debit_amount, 0) > 0 THEN 'Outgoing'
              WHEN COALESCE(bank.credit_amount, 0) > 0 THEN 'Incoming'
              ELSE 'Other'
            END AS direction,
            CAST(NULL AS STRING) AS currency,
            bank.bank_name AS account_name,
            CASE
              WHEN NULLIF(TRIM(bank.account_number_masked), '') IS NULL
                OR UPPER(TRIM(bank.account_number_masked)) IN (
                  'NOT PROVIDED', 'N/A', 'NA', 'UNKNOWN'
                )
              THEN NULL
              ELSE CONCAT(
                REPEAT(
                  '*',
                  GREATEST(
                    LENGTH(REGEXP_REPLACE(bank.account_number_masked, r'[^A-Za-z0-9]', '')) - 4,
                    0
                  )
                ),
                RIGHT(REGEXP_REPLACE(bank.account_number_masked, r'[^A-Za-z0-9]', ''), 4)
              )
            END AS masked_account_number,
            CAST(
              COALESCE(
                bank.bank_line_id,
                CONCAT(bank.upload_id, '-', CAST(bank.raw_row_number AS STRING))
              ) AS STRING
            ) AS statement_row_id
          FROM {_table_name(project_id, BANK_STATEMENT_VIEW)} bank
          LEFT JOIN upload_metadata metadata USING (upload_id)
          {bank_filter}
          ORDER BY bank.transaction_date, bank.raw_row_number
        """,
    }


def build_vendor_reconciliation_queries(
    project_id: str,
    filters: Mapping[str, Any],
) -> dict[str, str]:
    """Build parameterized, source-scoped queries for every workbook dataset."""
    bills_filter = _where(
        filters,
        {
            "start_date": "reconciliation.bill_date",
            "end_date": "reconciliation.bill_date",
            "organization": "reconciliation.source_org_key",
            "vendor": "reconciliation.vendor_name",
            "currency": "reconciliation.currency",
            "source_bill_status": "reconciliation.source_bill_status",
            "reconciliation_status": "reconciliation.reconciliation_status",
            "review_required": "reconciliation.review_required",
        },
    )
    payments_filter = _where(
        filters,
        {
            "start_date": "payment.payment_date",
            "end_date": "payment.payment_date",
            "organization": "payment.source_org_key",
            "vendor": "payment.vendor_name",
            "currency": "payment.currency_code",
            "bank_match_status": "match.bank_match_status",
            "review_required": "match.review_required",
        },
    )
    allocations_filter = _where(
        filters,
        {
            "start_date": "payment_date",
            "end_date": "payment_date",
            "organization": "source_org_key",
            "vendor": "vendor_name",
            "currency": "currency_code",
        },
    )
    bank_filter = _where(
        filters,
        {
            "start_date": "bank.transaction_date",
            "end_date": "bank.transaction_date",
            "organization": "bank.source_org_key",
            "currency": "bank.original_currency",
            "bank_match_status": "usage.bank_match_status",
            "review_required": "usage.review_required",
        },
    )
    return {
        "vendor_bills": f"""
          SELECT
            reconciliation.source_org_key AS organization,
            CAST(reconciliation.vendor_id AS STRING) AS vendor_id,
            reconciliation.vendor_name,
            CAST(reconciliation.bill_id AS STRING) AS bill_id,
            reconciliation.bill_number,
            reconciliation.bill_date, reconciliation.due_date,
            reconciliation.currency, reconciliation.bill_amount,
            reconciliation.source_outstanding_balance AS outstanding_balance,
            reconciliation.source_bill_status,
            reconciliation.allocated_payment_count, reconciliation.allocated_amount,
            reconciliation.bank_verified_amount,
            reconciliation.remaining_reconciliation_amount,
            reconciliation.reconciliation_status,
            reconciliation.reconciliation_reason, reconciliation.review_required,
            CAST(bill.source_record_id AS STRING) AS source_record_id,
            IF(
              reconciliation.bill_id IS NULL OR reconciliation.bill_amount IS NULL,
              'Needs Review',
              'Valid'
            ) AS data_quality_status
          FROM {_table_name(project_id, BILL_RECONCILIATION_VIEW)} reconciliation
          LEFT JOIN {_table_name(project_id, BILLS_VIEW)} bill
            ON bill.source_org_id = reconciliation.source_org_id
           AND bill.bill_id = reconciliation.bill_id
          {bills_filter}
          ORDER BY reconciliation.bill_date DESC,
                   reconciliation.vendor_name, reconciliation.bill_number
        """,
        "vendor_payments": f"""
          SELECT
            payment.source_org_key AS organization,
            CAST(payment.vendor_id AS STRING) AS vendor_id,
            payment.vendor_name,
            CAST(payment.payment_id AS STRING) AS payment_id,
            payment.payment_number,
            payment.payment_date,
            payment.reference_number,
            payment.payment_mode,
            payment.currency_code AS currency,
            payment.payment_amount,
            payment.allocated_amount,
            payment.unapplied_amount,
            payment.allocation_status,
            payment.paid_through_account_name,
            match.bank_match_status,
            match.bank_match_method,
            match.bank_match_reason,
            match.bank_transaction_date,
            match.bank_amount,
            match.review_required,
            CAST(payment.source_record_id AS STRING) AS source_record_id,
            CAST(match.bank_transaction_leg_key AS STRING) AS bank_transaction_leg_key,
            payment.allocation_status AS data_quality_status,
            CAST(payment.source_org_id AS STRING) AS _source_org_id
          FROM {_table_name(project_id, PAYMENTS_VIEW)} payment
          JOIN {_table_name(project_id, PAYMENT_MATCHES_VIEW)} match
            ON match.source_org_id = payment.source_org_id
           AND match.payment_id = payment.payment_id
          {payments_filter}
          ORDER BY payment.payment_date DESC, payment.vendor_name, payment.payment_id
        """,
        "payment_allocations": f"""
          SELECT
            source_org_key AS organization,
            CAST(vendor_id AS STRING) AS vendor_id,
            vendor_name,
            CAST(payment_id AS STRING) AS payment_id,
            CAST(bill_id AS STRING) AS bill_id,
            bill_number,
            CAST(bill_payment_id AS STRING) AS bill_payment_id,
            currency_code AS currency,
            amount_applied,
            payment_date,
            CAST(allocation_key AS STRING) AS allocation_key,
            CAST(source_record_id AS STRING) AS source_record_id,
            IF(amount_applied IS NULL, 'Needs Review', 'Valid') AS data_quality_status
          FROM {_table_name(project_id, ALLOCATIONS_VIEW)}
          {allocations_filter}
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY source_org_id, allocation_key
            ORDER BY loaded_at DESC, run_id DESC
          ) = 1
          ORDER BY payment_date DESC, payment_id, bill_id
        """,
        "bank_transactions": f"""
          WITH match_usage AS (
            SELECT
              bank_transaction_leg_key,
              LOGICAL_OR(TRUE) AS used_in_vendor_match,
              LOGICAL_OR(review_required) AS review_required,
              STRING_AGG(DISTINCT bank_match_status, ', ' ORDER BY bank_match_status)
                AS bank_match_status
            FROM {_table_name(project_id, PAYMENT_MATCHES_VIEW)}
            WHERE bank_transaction_leg_key IS NOT NULL
            GROUP BY bank_transaction_leg_key
          )
          SELECT
            bank.source_org_key AS organization,
            bank.account_name AS account_label,
            CAST(bank.transaction_id AS STRING) AS transaction_id,
            bank.transaction_date,
            bank.transaction_type,
            bank.debit_or_credit,
            bank.transaction_direction AS direction,
            bank.original_currency AS currency,
            bank.transaction_amount AS amount,
            bank.signed_amount,
            bank.status,
            bank.multi_leg_transaction,
            COALESCE(usage.used_in_vendor_match, FALSE) AS used_in_vendor_match,
            COALESCE(usage.review_required, FALSE) AS review_required,
            CAST(bank.source_record_id AS STRING) AS source_record_id,
            CAST(bank.bank_transaction_leg_key AS STRING) AS bank_transaction_leg_key,
            'bank_transaction_leg' AS match_method,
            bank.bank_data_quality_status AS data_quality_status,
            usage.bank_match_status
          FROM {_table_name(project_id, BANK_VIEW)} bank
          LEFT JOIN match_usage usage
            USING (bank_transaction_leg_key)
          {bank_filter}
          ORDER BY bank.transaction_date DESC, bank.transaction_id,
                   bank.account_id, bank.debit_or_credit
        """,
        "reconciliation_results": f"""
          WITH payment_rows AS (
            SELECT
              allocation.source_org_id,
              allocation.bill_id,
              allocation.payment_id,
              ANY_VALUE(payment.payment_amount) AS payment_amount,
              MAX(match.bank_transaction_date) AS bank_transaction_date,
              ANY_VALUE(match.bank_amount) AS bank_amount,
              ANY_VALUE(match.bank_match_status) AS bank_match_status,
              ANY_VALUE(match.bank_match_method) AS bank_match_method
            FROM {_table_name(project_id, ALLOCATIONS_VIEW)} allocation
            LEFT JOIN {_table_name(project_id, PAYMENTS_VIEW)} payment
              ON payment.source_org_id = allocation.source_org_id
             AND payment.payment_id = allocation.payment_id
            LEFT JOIN {_table_name(project_id, PAYMENT_MATCHES_VIEW)} match
              ON match.source_org_id = allocation.source_org_id
             AND match.payment_id = allocation.payment_id
            GROUP BY
              allocation.source_org_id,
              allocation.bill_id,
              allocation.payment_id
          ),
          payment_details AS (
            SELECT
              source_org_id,
              bill_id,
              SUM(COALESCE(payment_amount, 0)) AS payment_amount,
              MAX(bank_transaction_date) AS bank_transaction_date,
              SUM(COALESCE(bank_amount, 0)) AS bank_amount,
              STRING_AGG(
                DISTINCT bank_match_status,
                ', ' ORDER BY bank_match_status
              ) AS bank_match_status,
              STRING_AGG(
                DISTINCT bank_match_method,
                ', ' ORDER BY bank_match_method
              ) AS bank_match_method
            FROM payment_rows
            GROUP BY source_org_id, bill_id
          ),
          exception_details AS (
            SELECT
              reconciliation.source_org_id,
              reconciliation.bill_id,
              STRING_AGG(
                DISTINCT exception.exception_type,
                ', ' ORDER BY exception.exception_type
              ) AS exception_type,
              LOGICAL_OR(COALESCE(exception.review_required, FALSE))
                AS exception_review_required
            FROM {_table_name(project_id, BILL_RECONCILIATION_VIEW)} reconciliation
            LEFT JOIN {_table_name(project_id, ALLOCATIONS_VIEW)} allocation
              ON allocation.source_org_id = reconciliation.source_org_id
             AND allocation.bill_id = reconciliation.bill_id
            LEFT JOIN {_table_name(project_id, EXCEPTIONS_VIEW)} exception
              ON exception.source_org_id = reconciliation.source_org_id
             AND (
               exception.bill_id = reconciliation.bill_id
               OR (
                 exception.bill_id IS NULL
                 AND exception.payment_id = allocation.payment_id
               )
             )
            GROUP BY reconciliation.source_org_id, reconciliation.bill_id
          )
          SELECT
            reconciliation.source_org_key AS organization,
            CAST(reconciliation.vendor_id AS STRING) AS vendor_id,
            reconciliation.vendor_name,
            CAST(reconciliation.bill_id AS STRING) AS bill_id,
            reconciliation.bill_number,
            reconciliation.bill_date, reconciliation.due_date,
            reconciliation.currency, reconciliation.bill_amount,
            reconciliation.source_outstanding_balance,
            reconciliation.source_bill_status,
            reconciliation.allocated_payment_count AS payment_count,
            CAST(reconciliation.payment_ids AS STRING) AS payment_ids,
            reconciliation.latest_payment_date,
            COALESCE(payment.payment_amount, 0) AS payment_amount,
            reconciliation.allocated_amount,
            payment.bank_transaction_date,
            COALESCE(payment.bank_amount, 0) AS bank_amount,
            payment.bank_match_status,
            payment.bank_match_method,
            reconciliation.bank_pending_amount,
            reconciliation.remaining_reconciliation_amount,
            reconciliation.reconciliation_status,
            reconciliation.reconciliation_reason,
            exception.exception_type,
            reconciliation.review_required
              OR COALESCE(exception.exception_review_required, FALSE)
              AS review_required,
            'Pending' AS review_status,
            CAST(NULL AS STRING) AS reviewer_comment
          FROM {_table_name(project_id, BILL_RECONCILIATION_VIEW)} reconciliation
          LEFT JOIN payment_details payment
            ON payment.source_org_id = reconciliation.source_org_id
           AND payment.bill_id = reconciliation.bill_id
          LEFT JOIN exception_details exception
            ON exception.source_org_id = reconciliation.source_org_id
           AND exception.bill_id = reconciliation.bill_id
          {bills_filter}
          ORDER BY reconciliation.bill_date DESC,
                   reconciliation.vendor_name, reconciliation.bill_number
        """,
    }


def safe_exception_details(error: Exception, max_length: int = 4000) -> str:
    """Return useful diagnostics while redacting common credential patterns."""
    message = str(error).strip() or error.__class__.__name__
    message = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?:(?:Bearer|Basic)\s+)?[^\s,;]+",
        r"\1[redacted]",
        message,
    )
    message = re.sub(
        r"(?i)(access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password)"
        r"(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[redacted]",
        message,
    )
    return message[:max_length]


def user_facing_report_error(error: Exception) -> str:
    """Return a concise, actionable report-generation error for the app."""
    details = safe_exception_details(error)
    lowered = details.lower()
    if "google authentication expired" in lowered or "reauthentication is needed" in lowered:
        return (
            "Google authentication has expired. Run "
            "`gcloud auth application-default login`, then try again."
        )
    if "after one retry" in lowered or "timed out" in lowered:
        return (
            "A BigQuery report query timed out after one retry. "
            "Please try again; technical details identify the failed stage."
        )
    if isinstance(error, ValueError) and "start date" in lowered:
        return "Start date must be on or before end date."
    return "The vendor report could not be generated. Please try again."


def _rename_query_columns(dataframe: pd.DataFrame, dataset_name: str) -> pd.DataFrame:
    mapping = DISPLAY_COLUMN_MAPS[dataset_name]
    return dataframe.rename(columns=mapping).reindex(columns=list(mapping.values()))


def _rename_input_query_columns(
    dataframe: pd.DataFrame,
    dataset_name: str,
) -> pd.DataFrame:
    mapping = INPUT_DISPLAY_COLUMN_MAPS[dataset_name]
    return dataframe.rename(columns=mapping).reindex(columns=list(mapping.values()))


def fetch_bank_statement_upload_options(
    start_date: date,
    end_date: date,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Return safe upload choices with coverage information for the UI."""
    from google.cloud import bigquery

    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")
    resolved_project_id = _project_id(project_id)
    resolved_location = _bigquery_location(location)
    query_client = client or bigquery.Client(
        project=resolved_project_id,
        location=resolved_location,
    )
    query = f"""
      WITH metadata AS (
        SELECT
          upload_id,
          original_file_name,
          uploaded_at
        FROM {_table_name(resolved_project_id, UPLOADS_TABLE)}
        WHERE file_type = 'bank_statement'
          AND COALESCE(parse_status, 'success') = 'success'
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY upload_id ORDER BY uploaded_at DESC
        ) = 1
      )
      SELECT
        CAST(lines.upload_id AS STRING) AS upload_id,
        metadata.original_file_name AS source_file,
        COALESCE(NULLIF(lines.bank_name, ''), 'Uploaded Bank Statement')
          AS bank_source,
        MIN(lines.transaction_date) AS coverage_start_date,
        MAX(lines.transaction_date) AS coverage_end_date,
        COUNT(*) AS row_count,
        COUNTIF(
          lines.transaction_date BETWEEN @start_date AND @end_date
        ) AS period_row_count,
        COUNTIF(
          lines.transaction_date BETWEEN @start_date AND @end_date
          AND COALESCE(lines.debit_amount, 0) > 0
        ) AS period_debit_count,
        MAX(metadata.uploaded_at) AS uploaded_at
      FROM {_table_name(resolved_project_id, BANK_STATEMENT_VIEW)} lines
      LEFT JOIN metadata USING (upload_id)
      GROUP BY lines.upload_id, metadata.original_file_name, bank_source
      ORDER BY uploaded_at DESC, upload_id DESC
    """
    dataframe = _query_to_dataframe(
        query_client,
        query,
        _query_parameters(start_date=start_date, end_date=end_date),
        location=resolved_location,
        stage="Bank statement upload discovery",
    )
    return dataframe.to_dict(orient="records")


def recommend_bank_statement_upload(
    uploads: list[Mapping[str, Any]],
    start_date: date,
    end_date: date,
) -> str | None:
    """Recommend one full-period upload; never merge or fall back to latest."""
    covering = [
        upload
        for upload in uploads
        if upload.get("coverage_start_date") is not None
        and upload.get("coverage_end_date") is not None
        and upload["coverage_start_date"] <= start_date
        and upload["coverage_end_date"] >= end_date
        and int(upload.get("period_row_count") or 0) > 0
    ]
    if not covering:
        return None
    return str(covering[0]["upload_id"])


def fetch_reconciliation_input_data(
    start_date: date,
    end_date: date,
    organization: str | None = None,
    vendor: str | None = None,
    currency: str | None = None,
    bank_upload_id: str | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Fetch the Silver inputs required for finance-led reconciliation."""
    from google.cloud import bigquery

    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")
    if not str(bank_upload_id or "").strip():
        raise VendorReportValidationError(
            "Select one uploaded bank statement before generating the workbook."
        )
    resolved_project_id = _project_id(project_id)
    resolved_location = _bigquery_location(location)
    query_client = client or bigquery.Client(
        project=resolved_project_id,
        location=resolved_location,
    )
    filters = {
        "start_date": start_date,
        "end_date": end_date,
        "organization": organization,
        "vendor": vendor,
        "currency": currency,
        "bank_upload_id": str(bank_upload_id).strip(),
    }
    upload_options = fetch_bank_statement_upload_options(
        start_date,
        end_date,
        project_id=resolved_project_id,
        location=resolved_location,
        client=query_client,
    )
    selected_upload = next(
        (
            option
            for option in upload_options
            if str(option.get("upload_id")) == filters["bank_upload_id"]
        ),
        None,
    )
    if selected_upload is None:
        raise VendorReportValidationError(
            "The selected bank statement upload no longer exists."
        )
    if int(selected_upload.get("period_row_count") or 0) == 0:
        raise VendorReportValidationError(
            "The selected bank statement has no rows in the requested report period."
        )
    parameters = _query_parameters(**filters)
    queries = build_reconciliation_input_queries(resolved_project_id, filters)
    if any("finance_silver.fact_transactions" in query for query in queries.values()):
        raise VendorReportValidationError("Legacy fact_transactions query is prohibited.")
    datasets = {}
    for name, query in queries.items():
        dataset_name = "vendor_bills" if name == "current_bills" else name
        datasets[name] = _rename_input_query_columns(
            _query_to_dataframe(
                query_client,
                query,
                [
                    parameter
                    for parameter in parameters
                    if f"@{parameter.name}" in query
                ],
                location=resolved_location,
                stage=f"Reconciliation input query '{name}'",
            ),
            dataset_name,
        )
    return datasets, {
        "project_id": resolved_project_id,
        "location": resolved_location,
        "queries": queries,
        "filters": filters,
        "bank_upload": selected_upload,
    }


def fetch_vendor_report_data(
    start_date: date,
    end_date: date,
    organization: str | None = None,
    vendor: str | None = None,
    currency: str | None = None,
    source_bill_status: str | None = None,
    reconciliation_status: str | None = None,
    bank_match_status: str | None = None,
    review_required: bool | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Fetch all workbook datasets with filters pushed into BigQuery."""
    from google.cloud import bigquery

    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")
    resolved_project_id = _project_id(project_id)
    resolved_location = _bigquery_location(location)
    query_client = client or bigquery.Client(
        project=resolved_project_id,
        location=resolved_location,
    )
    filters = {
        "start_date": start_date,
        "end_date": end_date,
        "organization": organization,
        "vendor": vendor,
        "currency": currency,
        "source_bill_status": source_bill_status,
        "reconciliation_status": reconciliation_status,
        "bank_match_status": bank_match_status,
        "review_required": review_required,
    }
    parameters = _query_parameters(**filters)
    queries = build_vendor_reconciliation_queries(resolved_project_id, filters)
    if any("finance_silver.fact_transactions" in query for query in queries.values()):
        raise VendorReportValidationError("Legacy fact_transactions query is prohibited.")
    datasets = {
        name: _rename_query_columns(
            _query_to_dataframe(
                query_client,
                query,
                [
                    parameter
                    for parameter in parameters
                    if f"@{parameter.name}" in query
                ],
                location=resolved_location,
                stage=f"Automated analysis query '{name}'",
            ),
            name,
        )
        for name, query in queries.items()
    }
    return datasets, {
        "project_id": resolved_project_id,
        "location": resolved_location,
        "queries": queries,
        "filters": filters,
    }


def fetch_vendor_filter_options(
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> dict[str, list[Any]]:
    """Return filter values from verified Gold/Silver sources."""
    from google.cloud import bigquery

    resolved_project_id = _project_id(project_id)
    resolved_location = _bigquery_location(location)
    query_client = client or bigquery.Client(
        project=resolved_project_id,
        location=resolved_location,
    )
    query = f"""
      SELECT 'organization' AS option_type, source_org_key AS option_value
      FROM {_table_name(resolved_project_id, CONTACTS_VIEW)}
      WHERE LOWER(contact_type) = 'vendor'
      UNION DISTINCT
      SELECT
        'vendor',
        COALESCE(NULLIF(contact_name, ''), NULLIF(company_name, ''), contact_id)
      FROM {_table_name(resolved_project_id, CONTACTS_VIEW)}
      WHERE LOWER(contact_type) = 'vendor'
      UNION DISTINCT
      SELECT 'currency', currency_code
      FROM {_table_name(resolved_project_id, CONTACTS_VIEW)}
      WHERE LOWER(contact_type) = 'vendor'
      UNION DISTINCT
      SELECT 'source_bill_status', source_bill_status
      FROM {_table_name(resolved_project_id, BILL_RECONCILIATION_VIEW)}
      UNION DISTINCT
      SELECT 'reconciliation_status', reconciliation_status
      FROM {_table_name(resolved_project_id, BILL_RECONCILIATION_VIEW)}
      UNION DISTINCT
      SELECT 'bank_match_status', bank_match_status
      FROM {_table_name(resolved_project_id, PAYMENT_MATCHES_VIEW)}
    """
    options_df = _query_to_dataframe(
        query_client,
        query,
        location=resolved_location,
        stage="Vendor filter-options query",
    )
    output: dict[str, list[Any]] = {
        "organizations": [],
        "vendors": [],
        "currencies": [],
        "source_bill_statuses": [],
        "reconciliation_statuses": [],
        "bank_match_statuses": [],
    }
    key_map = {
        "organization": "organizations",
        "vendor": "vendors",
        "currency": "currencies",
        "source_bill_status": "source_bill_statuses",
        "reconciliation_status": "reconciliation_statuses",
        "bank_match_status": "bank_match_statuses",
    }
    for option_type, group in options_df.dropna(subset=["option_value"]).groupby("option_type"):
        key = key_map.get(str(option_type))
        if key:
            output[key] = sorted(
                {str(value).strip() for value in group["option_value"] if str(value).strip()}
            )
    return output


def _clean_dataframe(dataframe: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    if dataframe is None:
        return pd.DataFrame(columns=columns)
    cleaned = dataframe.copy().reindex(columns=columns)
    for column_name in TEXT_IDENTIFIER_COLUMNS.intersection(cleaned.columns):
        cleaned = cleaned.assign(
            **{
                column_name: cleaned[column_name].map(
                    lambda value: None if pd.isna(value) else str(value)
                )
            }
        )
    return cleaned


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)


def _duplicate_count(frame: pd.DataFrame, columns: list[str]) -> int:
    if frame.empty or not set(columns).issubset(frame.columns):
        return 0
    return int(frame.duplicated(columns, keep=False).sum())


def combine_zoho_payment_batch(
    payments: pd.DataFrame,
    allocations: pd.DataFrame,
) -> pd.DataFrame:
    """Left-join allocation detail to payment headers without losing payments."""
    payment_headers = _clean_dataframe(payments, INPUT_VENDOR_PAYMENT_COLUMNS)
    allocation_rows = _clean_dataframe(
        allocations,
        INPUT_PAYMENT_ALLOCATION_COLUMNS,
    )
    allocation_detail_columns = [
        "Organization",
        "Payment ID",
        "Bill Payment ID",
        "Bill ID",
        "Bill Number",
        "Amount Applied",
    ]
    batch = payment_headers.merge(
        allocation_rows.reindex(columns=allocation_detail_columns),
        on=["Organization", "Payment ID"],
        how="left",
        validate="one_to_many",
    )
    batch["Payment Amount"] = batch["Total Payment Amount"]
    return batch.reindex(columns=ZOHO_PAYMENT_BATCH_COLUMNS)


def _select_reconciliation_input_bills(
    datasets: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Add allocation-linked current bills without duplicating period bills."""
    supplied_bills = datasets.get("vendor_bills")
    if (
        isinstance(supplied_bills, pd.DataFrame)
        and "In Selected Bill Period" in supplied_bills.columns
        and supplied_bills["In Selected Bill Period"].notna().any()
    ):
        return _clean_dataframe(supplied_bills, INPUT_VENDOR_BILL_COLUMNS)
    period_bills = _clean_dataframe(
        supplied_bills,
        INPUT_VENDOR_BILL_SOURCE_COLUMNS,
    ).assign(
        **{
            "In Selected Bill Period": "Yes",
            "Inclusion Reason": "Bill Date in Selected Period",
        }
    )
    current_bills = _clean_dataframe(
        datasets.get("current_bills", datasets.get("vendor_bills")),
        INPUT_VENDOR_BILL_SOURCE_COLUMNS,
    )
    allocations = _clean_dataframe(
        datasets.get("payment_allocations"),
        INPUT_PAYMENT_ALLOCATION_COLUMNS,
    )
    valid_allocation_keys = allocations[
        allocations["Bill ID"].notna()
        & allocations["Bill ID"].astype(str).ne("")
    ][["Organization", "Bill ID"]].drop_duplicates()
    linked_bills = current_bills.merge(
        valid_allocation_keys,
        on=["Organization", "Bill ID"],
        how="inner",
    )
    period_keys = period_bills[["Organization", "Bill ID"]].drop_duplicates().assign(
        _in_period=True
    )
    linked_bills = linked_bills.merge(
        period_keys,
        on=["Organization", "Bill ID"],
        how="left",
    )
    linked_bills = (
        linked_bills[linked_bills["_in_period"].isna()]
        .drop(columns="_in_period")
        .drop_duplicates(["Organization", "Bill ID"])
        .assign(
            **{
                "In Selected Bill Period": "No",
                "Inclusion Reason": "Linked to Payment in Selected Period",
            }
        )
    )
    return pd.concat(
        [period_bills, linked_bills],
        ignore_index=True,
    ).reindex(columns=INPUT_VENDOR_BILL_COLUMNS)


def _older_linked_bills_message(count: int) -> str:
    noun = "bill was" if count == 1 else "bills were"
    pronoun = "it" if count == 1 else "them"
    return (
        f"{count} older {noun} included because payments allocated to "
        f"{pronoun} fall within the selected report period."
    )


def _invoice_reference_pattern(value: Any) -> re.Pattern[str] | None:
    """Return a bounded, punctuation-tolerant pattern for a known bill number."""
    if value is None or pd.isna(value):
        return None
    tokens = re.findall(r"[A-Z0-9]+", str(value).upper())
    if not tokens or sum(len(token) for token in tokens) < 2:
        return None
    expression = r"[\s./_#-]*".join(re.escape(token) for token in tokens)
    return re.compile(rf"(?<![A-Z0-9]){expression}(?![A-Z0-9])", re.IGNORECASE)


def _money_value(value: Any) -> float | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(numeric) else float(numeric)


def _is_high_specificity_bill_number(value: Any) -> bool:
    normalized = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    if not normalized or normalized.isdigit() and len(normalized) < 5:
        return False
    if normalized.isdigit() and len(normalized) == 4 and 1900 <= int(normalized) <= 2100:
        return False
    month_names = (
        "JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|"
        "JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|SEP(?:T(?:EMBER)?)?|"
        "OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?"
    )
    if re.fullmatch(rf"(?:{month_names})\d{{2,4}}", normalized):
        return False
    if re.fullmatch(rf"\d{{2,4}}(?:{month_names})", normalized):
        return False
    return len(normalized) >= 5


def _normalized_reference(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def prepare_manual_review_bank_statement(
    bills: pd.DataFrame,
    bank_statement: pd.DataFrame,
) -> pd.DataFrame:
    """Add conservative, non-final invoice and amount review suggestions."""
    bill_rows = _clean_dataframe(bills, INPUT_VENDOR_BILL_COLUMNS)
    statement = _clean_dataframe(
        bank_statement,
        INPUT_BANK_STATEMENT_SOURCE_COLUMNS,
    ).reset_index(drop=True)
    for column in MANUAL_REVIEW_BANK_COLUMNS:
        statement[column] = None
    if statement.empty:
        return statement.reindex(columns=INPUT_BANK_STATEMENT_COLUMNS)

    candidates: list[dict[str, Any]] = []
    for _, bill in bill_rows.iterrows():
        pattern = _invoice_reference_pattern(bill["Bill Number"])
        if pattern is None:
            continue
        candidates.append(
            {
                "pattern": pattern,
                "normalized": _normalized_reference(bill["Bill Number"]),
                "high_specificity": _is_high_specificity_bill_number(
                    bill["Bill Number"]
                ),
                "organization": str(bill["Organization"] or ""),
                "bill_id": str(bill["Bill ID"] or ""),
                "bill_number": str(bill["Bill Number"]),
                "currency": str(bill["Currency"] or ""),
                "taxable_amount": _money_value(bill["Taxable Amount"]),
                "bill_total": _money_value(bill["Bill Total"]),
            }
        )

    unique_matches: dict[Any, dict[str, Any]] = {}
    for index, bank in statement.iterrows():
        debit_amount = _money_value(bank["Debit Amount"])
        signed_amount = _money_value(bank["Signed Amount"])
        if not (
            (debit_amount is not None and debit_amount > 0)
            or (signed_amount is not None and signed_amount < 0)
        ):
            statement.at[index, "Invoice Reference Result"] = "No Invoice Reference"
            statement.at[index, "Possible Amount Pattern"] = (
                "No Unique Invoice Reference"
            )
            statement.at[index, "Manual Review Note"] = (
                "Invoice helpers are evaluated only for outgoing debit rows."
            )
            continue
        search_text = " ".join(
            str(value)
            for value in (bank["Narration"], bank["Reference Number"])
            if value is not None and not pd.isna(value)
        )
        normalized_text = _normalized_reference(search_text)
        matched = []
        for item in candidates:
            exact_token = bool(item["pattern"].search(search_text))
            embedded = (
                len(item["normalized"]) >= 6
                and item["normalized"] in normalized_text
            )
            if exact_token or embedded:
                matched.append(item)
        matched = list(
            {
                (item["bill_id"], item["bill_number"]): item
                for item in matched
            }.values()
        )
        if not matched:
            statement.at[index, "Invoice Reference Result"] = "No Invoice Reference"
            statement.at[index, "Possible Amount Pattern"] = (
                "No Unique Invoice Reference"
            )
            statement.at[index, "Manual Review Note"] = (
                "No conservative invoice reference found."
            )
            continue

        high_confidence = [item for item in matched if item["high_specificity"]]
        invoice_numbers = sorted({item["bill_number"] for item in matched})
        statement.at[index, "Extracted Invoice Number"] = "; ".join(invoice_numbers)
        if len(high_confidence) > 1:
            statement.at[index, "Invoice Reference Result"] = "Ambiguous Reference"
            statement.at[index, "Possible Amount Pattern"] = (
                "No Unique Invoice Reference"
            )
            statement.at[index, "Manual Review Note"] = (
                "Multiple high-confidence bill references found; review manually."
            )
            continue
        if not high_confidence:
            statement.at[index, "Invoice Reference Result"] = (
                "Low-Specificity Candidate"
            )
            statement.at[index, "Possible Amount Pattern"] = (
                "No Unique Invoice Reference"
            )
            statement.at[index, "Manual Review Note"] = (
                "Only short, date-like, or month-period bill references were found."
            )
            continue

        bill = high_confidence[0]
        statement.at[index, "Invoice Reference Result"] = (
            "Unique High-Confidence Reference"
        )
        statement.at[index, "Matching Bill Number"] = bill["bill_number"]
        statement.at[index, "Matching Bill ID"] = bill["bill_id"]
        unique_matches[index] = bill
        taxable_amount = bill["taxable_amount"]
        bill_total = bill["bill_total"]
        if taxable_amount is None or bill_total is None or debit_amount is None:
            statement.at[index, "Possible Amount Pattern"] = (
                "Amount Difference - Review Required"
            )
            statement.at[index, "Manual Review Note"] = (
                "Invoice reference found; bill or bank amount is incomplete."
            )
            continue

        expected_2 = round(bill_total - taxable_amount * 0.02, 2)
        expected_10 = round(bill_total - taxable_amount * 0.10, 2)
        comparisons = {
            "bill total": debit_amount - bill_total,
            "2% TDS": debit_amount - expected_2,
            "10% TDS": debit_amount - expected_10,
        }
        closest = min(comparisons, key=lambda name: abs(comparisons[name]))
        statement.at[index, "Amount Difference"] = round(comparisons[closest], 2)
        if abs(comparisons["bill total"]) <= 0.01:
            statement.at[index, "Possible Amount Pattern"] = (
                "Exact Invoice Total Candidate"
            )
            statement.at[index, "Manual Review Note"] = (
                "Exact invoice-total candidate; finance review required."
            )
        elif abs(comparisons["2% TDS"]) <= 0.01:
            statement.at[index, "Possible Amount Pattern"] = (
                "Possible 2% TDS Candidate"
            )
            statement.at[index, "Manual Review Note"] = (
                "Possible 2% TDS candidate; finance review required."
            )
        elif abs(comparisons["10% TDS"]) <= 0.01:
            statement.at[index, "Possible Amount Pattern"] = (
                "Possible 10% TDS Candidate"
            )
            statement.at[index, "Manual Review Note"] = (
                "Possible 10% TDS candidate; finance review required."
            )
        else:
            statement.at[index, "Possible Amount Pattern"] = (
                "Amount Difference - Review Required"
            )
            statement.at[index, "Manual Review Note"] = (
                "Unique invoice reference found; amount difference requires review."
            )

    split_groups: dict[str, list[Any]] = {}
    for index, bill in unique_matches.items():
        split_groups.setdefault(bill["bill_id"], []).append(index)
    for indexes in split_groups.values():
        if len(indexes) < 2:
            continue
        bill = unique_matches[indexes[0]]
        taxable_amount = bill["taxable_amount"]
        bill_total = bill["bill_total"]
        debit_total = sum(
            _money_value(statement.at[index, "Debit Amount"]) or 0
            for index in indexes
        )
        if taxable_amount is None or bill_total is None:
            continue
        expected = {
            "bill total": bill_total,
            "2% TDS": round(bill_total - taxable_amount * 0.02, 2),
            "10% TDS": round(bill_total - taxable_amount * 0.10, 2),
        }
        split_match = next(
            (
                label
                for label, amount in expected.items()
                if abs(debit_total - amount) <= 0.01
            ),
            None,
        )
        if split_match is None:
            continue
        for index in indexes:
            statement.at[index, "Possible Amount Pattern"] = (
                "Possible Split Payment - Review Required"
            )
            statement.at[index, "Manual Review Note"] = (
                f"Possible split-payment case; combined debits match {split_match}."
            )

    return statement.reindex(columns=INPUT_BANK_STATEMENT_COLUMNS)


def validate_reconciliation_input_data(
    datasets: Mapping[str, pd.DataFrame],
    queries: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Block input workbooks that would hand finance incomplete or unsafe data."""
    bills = _clean_dataframe(
        datasets.get("vendor_bills"),
        INPUT_VENDOR_BILL_COLUMNS,
    )
    current_bills = _clean_dataframe(
        datasets.get("current_bills", datasets.get("vendor_bills")),
        INPUT_VENDOR_BILL_SOURCE_COLUMNS,
    )
    payments = _clean_dataframe(
        datasets.get("vendor_payments"),
        INPUT_VENDOR_PAYMENT_COLUMNS,
    )
    allocations = _clean_dataframe(
        datasets.get("payment_allocations"),
        INPUT_PAYMENT_ALLOCATION_COLUMNS,
    )
    statement = _clean_dataframe(
        datasets.get("bank_statement"),
        INPUT_BANK_STATEMENT_COLUMNS,
    )

    allocation_exceeds_payment = 0
    missing_allocation_payment_ids = 0
    if not allocations.empty:
        allocation_totals = (
            allocations.assign(_allocated=_numeric(allocations["Amount Applied"]))
            .groupby(["Organization", "Payment ID"], dropna=False)["_allocated"]
            .sum()
            .reset_index()
        )
        payment_amounts = payments[
            ["Organization", "Payment ID", "Total Payment Amount"]
        ].assign(
            _payment_amount=lambda frame: _numeric(frame["Total Payment Amount"]),
            _payment_exists=True,
        )
        comparison = allocation_totals.merge(
            payment_amounts[
                [
                    "Organization",
                    "Payment ID",
                    "_payment_amount",
                    "_payment_exists",
                ]
            ],
            on=["Organization", "Payment ID"],
            how="left",
        )
        missing_allocation_payment_ids = int(
            comparison["_payment_exists"].isna().sum()
        )
        allocation_exceeds_payment = int(
            (
                comparison["_payment_exists"].notna()
                & ((comparison["_allocated"] - comparison["_payment_amount"]) > 0.01)
            ).sum()
        )

    missing_allocation_bill_ids = 0
    if not allocations.empty:
        checkable_allocations = allocations[
            allocations["Bill ID"].notna()
            & allocations["Bill ID"].astype(str).ne("")
        ]
        if not checkable_allocations.empty:
            bill_keys = current_bills[
                ["Organization", "Bill ID"]
            ].drop_duplicates().assign(_bill_exists=True)
            bill_comparison = checkable_allocations.merge(
                bill_keys,
                on=["Organization", "Bill ID"],
                how="left",
            )
            missing_allocation_bill_ids = int(
                bill_comparison["_bill_exists"].isna().sum()
            )

    query_text = "\n".join((queries or {}).values()).lower()
    legacy_query_count = query_text.count("finance_silver.fact_transactions")
    journal_query_count = sum(
        prohibited in query_text
        for prohibited in (
            "finance_silver.fact_journals",
            "finance_silver.fact_journal",
        )
    )
    selected_upload_count = int(statement["Upload ID"].dropna().astype(str).nunique())
    matching_ids = statement[
        statement["Matching Bill ID"].notna()
        & statement["Matching Bill ID"].astype(str).ne("")
    ]
    missing_matching_bill_ids = int(
        (~matching_ids["Matching Bill ID"].isin(bills["Bill ID"])).sum()
    )
    non_unique_with_bill_id = int(
        (
            statement["Invoice Reference Result"].ne(
                "Unique High-Confidence Reference"
            )
            & statement["Matching Bill ID"].notna()
            & statement["Matching Bill ID"].astype(str).ne("")
        ).sum()
    )
    no_unique_reference = statement["Invoice Reference Result"].ne(
        "Unique High-Confidence Reference"
    )
    amount_helpers_without_unique_reference = int(
        (
            no_unique_reference
            & (
                statement["Amount Difference"].notna()
                | statement["Possible Amount Pattern"].isin(
                    [
                        "Exact Invoice Total Candidate",
                        "Possible 2% TDS Candidate",
                        "Possible 10% TDS Candidate",
                        "Possible Split Payment - Review Required",
                        "Amount Difference - Review Required",
                    ]
                )
            )
        ).sum()
    )
    unmasked_account_numbers = int(
        statement["Masked Account Number"]
        .fillna("")
        .astype(str)
        .map(
            lambda value: bool(
                len(re.sub(r"[^A-Za-z0-9]", "", value)) > 4
                and "*" not in value
            )
        )
        .sum()
    )
    checks = {
        "duplicate_bill_ids": _duplicate_count(
            bills,
            ["Organization", "Bill ID"],
        ),
        "duplicate_payment_ids": _duplicate_count(
            payments,
            ["Organization", "Payment ID"],
        ),
        "duplicate_allocation_keys": _duplicate_count(
            allocations,
            ["Organization", "Allocation Key"],
        ),
        "duplicate_statement_row_keys": _duplicate_count(
            statement,
            ["Statement Row ID"],
        ),
        "selected_bank_upload_count": selected_upload_count,
        "statement_rows_present": not statement.empty,
        "matching_bill_ids_missing_from_exported_bills": missing_matching_bill_ids,
        "non_unique_references_with_matching_bill_id": non_unique_with_bill_id,
        "amount_helpers_without_unique_reference": (
            amount_helpers_without_unique_reference
        ),
        "unmasked_account_numbers": unmasked_account_numbers,
        "input_query_uses_uploaded_statement": (
            not queries or BANK_STATEMENT_VIEW.lower() in query_text
        ),
        "input_query_mislabeled_zoho_bank_legs": (
            bool(not queries or BANK_VIEW.lower() not in query_text)
        ),
        "allocations_exceeding_payment": allocation_exceeds_payment,
        "allocation_payment_ids_missing_from_vendor_payments": (
            missing_allocation_payment_ids
        ),
        "allocation_bill_ids_missing_from_bills": missing_allocation_bill_ids,
        "older_linked_bills_included": int(
            bills["In Selected Bill Period"].eq("No").sum()
        ),
        "legacy_fact_transactions_queries": legacy_query_count,
        "journal_queries_used_as_vendor_payments": journal_query_count,
        "organizations_kept_separate": True,
        "currency_totals_separated": True,
    }
    checks["informational_warnings"] = (
        [
            _older_linked_bills_message(
                checks["older_linked_bills_included"]
            )
        ]
        if checks["older_linked_bills_included"]
        else []
    )
    non_blocking_checks = {
        "older_linked_bills_included",
        "informational_warnings",
    }
    checks["selected_bank_upload_count"] = (
        0 if selected_upload_count == 1 else selected_upload_count or -1
    )
    failures = {
        name: value
        for name, value in checks.items()
        if name not in non_blocking_checks
        if (isinstance(value, bool) and not value)
        or (not isinstance(value, bool) and value != 0)
    }
    if failures:
        details = ", ".join(f"{name}={value}" for name, value in failures.items())
        if missing_allocation_bill_ids:
            raise VendorReportValidationError(
                "Workbook generation stopped because one or more payment "
                "allocations refer to bills that are missing from the current "
                f"bills dataset. {details}"
            )
        raise VendorReportValidationError(
            "Reconciliation input workbook blocked by validation failure: "
            f"{details}"
        )
    return checks


def validate_vendor_report_data(
    datasets: Mapping[str, pd.DataFrame],
    queries: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run blocking grain and financial validations before workbook creation."""
    bills = datasets.get("vendor_bills", pd.DataFrame())
    payments = datasets.get("vendor_payments", pd.DataFrame())
    allocations = datasets.get("payment_allocations", pd.DataFrame())
    banks = datasets.get("bank_transactions", pd.DataFrame())
    reconciliation = datasets.get("reconciliation_results", pd.DataFrame())

    duplicate_bill_ids = _duplicate_count(bills, ["Organization", "Bill ID"])
    duplicate_payment_ids = _duplicate_count(payments, ["Organization", "Payment ID"])
    duplicate_allocation_keys = _duplicate_count(
        allocations,
        ["Organization", "Allocation Key"],
    )
    duplicate_bank_legs = _duplicate_count(banks, ["Bank Transaction Leg Key"])

    credit_used_count = 0
    if not banks.empty and {"Used in Vendor Match", "Debit/Credit"}.issubset(banks.columns):
        credit_used_count = int(
            (
                banks["Used in Vendor Match"].fillna(False).astype(bool)
                & banks["Debit/Credit"].fillna("").astype(str).str.lower().ne("debit")
            ).sum()
        )

    duplicate_final_bank_assignments = 0
    if not payments.empty and {
        "Bank Match Status",
        "Bank Transaction Leg Key",
    }.issubset(payments.columns):
        final = payments[
            payments["Bank Match Status"].eq("Bank Verified")
            & payments["Bank Transaction Leg Key"].notna()
        ]
        duplicate_final_bank_assignments = _duplicate_count(
            final,
            ["Bank Transaction Leg Key"],
        )

    allocation_exceeds_payment = 0
    allocation_org_mismatch = 0
    if not allocations.empty and not payments.empty:
        allocation_totals = (
            allocations.assign(_amount=_numeric(allocations["Amount Applied"]))
            .groupby(["Organization", "Payment ID"], dropna=False)["_amount"]
            .sum()
            .reset_index()
        )
        payment_amounts = payments.loc[
            :,
            ["Organization", "Payment ID", "Payment Amount"],
        ].assign(
            _payment_amount=lambda frame: _numeric(frame["Payment Amount"]),
            _payment_exists=True,
        )
        comparison = allocation_totals.merge(
            payment_amounts[
                [
                    "Organization",
                    "Payment ID",
                    "_payment_amount",
                    "_payment_exists",
                ]
            ],
            on=["Organization", "Payment ID"],
            how="left",
        )
        allocation_exceeds_payment = int(
            (
                comparison["_payment_amount"].notna()
                & ((comparison["_amount"] - comparison["_payment_amount"]) > 0.01)
            ).sum()
        )
        allocation_org_mismatch = int(comparison["_payment_exists"].isna().sum())

    matched_amount_exceeds_bill = 0
    if not reconciliation.empty:
        matched_amount_exceeds_bill = int(
            (
                (
                    _numeric(reconciliation["Allocated Amount"])
                    - _numeric(reconciliation["Bank Pending Amount"])
                )
                - _numeric(reconciliation["Bill Amount"])
                > 0.01
            ).sum()
        )

    pending_status_violations = 0
    if not payments.empty:
        review_methods = payments["Bank Match Method"].isin(
            [
                "exact_account_amount_date_window_review",
                "multi_leg_transaction_review",
            ]
        )
        pending_status_violations = int(
            (
                review_methods
                & ~payments["Bank Match Status"].isin(
                    ["Bank Match Pending Review", "Ambiguous Bank Match"]
                )
            ).sum()
        )

    legacy_query_count = sum(
        "finance_silver.fact_transactions" in query
        for query in (queries or {}).values()
    )
    checks = {
        "duplicate_bill_ids": duplicate_bill_ids,
        "duplicate_payment_ids": duplicate_payment_ids,
        "duplicate_allocation_keys": duplicate_allocation_keys,
        "duplicate_bank_transaction_leg_keys": duplicate_bank_legs,
        "credit_legs_used_as_vendor_payments": credit_used_count,
        "duplicate_final_bank_assignments": duplicate_final_bank_assignments,
        "allocations_exceeding_payment": allocation_exceeds_payment,
        "allocation_organization_mismatches": allocation_org_mismatch,
        "matched_amount_exceeding_bill": matched_amount_exceeds_bill,
        "pending_review_status_violations": pending_status_violations,
        "legacy_fact_transactions_queries": legacy_query_count,
        "currency_totals_separated": True,
    }
    failures = {
        name: value
        for name, value in checks.items()
        if (isinstance(value, bool) and not value)
        or (not isinstance(value, bool) and value != 0)
    }
    if failures:
        details = ", ".join(f"{name}={value}" for name, value in failures.items())
        raise VendorReportValidationError(
            f"Vendor reconciliation workbook blocked by validation failure: {details}"
        )
    return checks


def _excel_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime().replace(tzinfo=None)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _style_title(worksheet: Any, title: str, width: int) -> None:
    worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(width, 2))
    cell = worksheet.cell(1, 1, title)
    cell.fill = PatternFill("solid", fgColor="17365D")
    cell.font = Font(name="Aptos Display", color="FFFFFF", bold=True, size=15)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    worksheet.row_dimensions[1].height = 28


def _status_fill(value: Any) -> PatternFill | None:
    text = str(value or "").lower()
    if any(term in text for term in ("fully reconciled", "bank verified", "valid")):
        return PatternFill("solid", fgColor="E2F0D9")
    if any(term in text for term in ("pending", "review", "ambiguous")):
        return PatternFill("solid", fgColor="FFF2CC")
    if any(term in text for term in ("not found", "unpaid", "mismatch", "invalid")):
        return PatternFill("solid", fgColor="FCE4D6")
    return None


def _write_detail_sheet(
    worksheet: Any,
    title: str,
    dataframe: pd.DataFrame,
) -> None:
    _style_title(worksheet, title, len(dataframe.columns))
    header_row = 3
    thin_border = Border(bottom=Side(style="thin", color="A6A6A6"))
    for column_index, column_name in enumerate(dataframe.columns, start=1):
        cell = worksheet.cell(header_row, column_index, column_name)
        cell.fill = PatternFill("solid", fgColor="4472C4")
        cell.font = Font(name="Aptos", color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
    worksheet.row_dimensions[header_row].height = 32

    for row_index, row in enumerate(dataframe.itertuples(index=False, name=None), start=4):
        for column_index, value in enumerate(row, start=1):
            column_name = dataframe.columns[column_index - 1]
            cell = worksheet.cell(row_index, column_index, _excel_value(value))
            cell.font = Font(name="Aptos", size=10)
            cell.alignment = Alignment(horizontal="left", vertical="top")
            if column_name in DATE_COLUMNS and cell.value is not None:
                cell.number_format = "yyyy-mm-dd"
            elif column_name in AMOUNT_COLUMNS:
                cell.number_format = '#,##0.00;[Red](#,##0.00);-'
                cell.alignment = Alignment(horizontal="right", vertical="top")
            elif column_name in TEXT_IDENTIFIER_COLUMNS:
                cell.number_format = "@"
            if column_name in WRAP_COLUMNS:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if "Status" in column_name or column_name == "Review Required":
                fill = _status_fill(cell.value)
                if fill:
                    cell.fill = fill
            elif row_index % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F3F6FA")

    last_row = max(3, len(dataframe) + 3)
    last_column = get_column_letter(max(1, len(dataframe.columns)))
    worksheet.freeze_panes = "A4"
    worksheet.auto_filter.ref = f"A3:{last_column}{last_row}"
    worksheet.sheet_view.showGridLines = False
    for column_index, column_name in enumerate(dataframe.columns, start=1):
        max_length = len(column_name)
        for value in dataframe.iloc[:, column_index - 1].head(250):
            if not pd.isna(value):
                max_length = max(max_length, len(str(value)))
        cap = 55 if column_name in WRAP_COLUMNS else 32
        worksheet.column_dimensions[get_column_letter(column_index)].width = min(
            max(max_length + 2, 12),
            cap,
        )
    for column_name in WRAP_COLUMNS.intersection(dataframe.columns):
        column_index = dataframe.columns.get_loc(column_name) + 1
        worksheet.column_dimensions[get_column_letter(column_index)].width = 48


def summarize_reconciliation_input(
    datasets: Mapping[str, pd.DataFrame],
    filters: Mapping[str, Any] | None = None,
    bank_upload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize source records without producing reconciliation conclusions."""
    bills = datasets.get("vendor_bills", pd.DataFrame())
    payments = datasets.get("vendor_payments", pd.DataFrame())
    allocations = datasets.get("payment_allocations", pd.DataFrame())
    statement = datasets.get("bank_statement", pd.DataFrame())
    selected_filters = filters or {}
    organizations = sorted(
        {
            str(value)
            for frame in (bills, payments)
            if "Organization" in frame
            for value in frame["Organization"].dropna()
        }
    )
    currencies = sorted(
        {
            str(value)
            for frame in (bills, payments, statement)
            if "Currency" in frame
            for value in frame["Currency"].dropna()
        }
    )
    currency_rows = []
    for currency in currencies:
        bill_rows = bills[bills["Currency"].astype(str).eq(currency)]
        payment_rows = payments[payments["Currency"].astype(str).eq(currency)]
        payment_keys = payment_rows[["Organization", "Payment ID"]].drop_duplicates()
        allocation_rows = allocations.merge(
            payment_keys,
            on=["Organization", "Payment ID"],
            how="inner",
        )
        bank_rows = statement[
            statement.get(
                "Currency",
                pd.Series(index=statement.index, dtype="object"),
            ).astype(str).eq(currency)
        ]
        currency_rows.append(
            {
                "currency": currency,
                "bill_count": len(bill_rows),
                "bill_total": float(_numeric(bill_rows["Bill Total"]).sum()),
                "payment_count": len(payment_rows),
                "payment_total": float(
                    _numeric(payment_rows["Total Payment Amount"]).sum()
                ),
                "allocation_count": len(allocation_rows),
                "allocation_total": float(
                    _numeric(allocation_rows["Amount Applied"]).sum()
                ),
                "bank_leg_count": len(bank_rows),
                "bank_total": float(_numeric(bank_rows["Debit Amount"]).sum()),
            }
        )
    older_linked_bill_count = int(
        bills.get(
            "In Selected Bill Period",
            pd.Series(dtype="object"),
        ).eq("No").sum()
    )
    debit_rows = statement[
        _numeric(
            statement.get(
                "Debit Amount",
                pd.Series(index=statement.index, dtype="object"),
            )
        ).gt(0)
    ]
    reference_results = debit_rows.get(
        "Invoice Reference Result",
        pd.Series(index=debit_rows.index, dtype="object"),
    )
    amount_patterns = debit_rows.get(
        "Possible Amount Pattern",
        pd.Series(index=debit_rows.index, dtype="object"),
    )
    upload_metadata = dict(bank_upload or {})
    if not upload_metadata and not statement.empty:
        def first_nonblank(column: str) -> Any:
            values = statement[column].dropna().astype(str)
            values = values[values.str.strip().ne("")]
            return values.iloc[0] if not values.empty else None

        upload_metadata = {
            "upload_id": first_nonblank("Upload ID"),
            "source_file": first_nonblank("Source File"),
            "bank_source": first_nonblank("Bank Source"),
            "coverage_start_date": statement["Transaction Date"].min(),
            "coverage_end_date": statement["Transaction Date"].max(),
        }
    return {
        "report_start_date": selected_filters.get("start_date"),
        "report_end_date": selected_filters.get("end_date"),
        "organizations": organizations,
        "bills_in_selected_period": int(
            bills.get(
                "In Selected Bill Period",
                pd.Series("Yes", index=bills.index, dtype="object"),
            ).eq("Yes").sum()
        ),
        "older_linked_bills_included": older_linked_bill_count,
        "total_bill_rows_exported": len(bills),
        "bill_record_count": len(bills),
        "vendor_payment_count": len(
            payments[["Organization", "Payment ID"]].drop_duplicates()
        ),
        "payment_allocation_count": len(allocations),
        "selected_bank_source": upload_metadata.get("bank_source"),
        "selected_source_file": upload_metadata.get("source_file"),
        "selected_upload_id_suffix": str(
            upload_metadata.get("upload_id") or ""
        )[-4:],
        "statement_coverage_start_date": upload_metadata.get(
            "coverage_start_date"
        ),
        "statement_coverage_end_date": upload_metadata.get("coverage_end_date"),
        "statement_rows_exported": len(statement),
        "bank_transaction_leg_count": len(statement),
        "bank_debit_count": len(debit_rows),
        "descriptions_with_invoice_references": int(
            reference_results.eq("Unique High-Confidence Reference").sum()
        ),
        "ambiguous_invoice_references": int(
            reference_results.eq("Ambiguous Reference").sum()
        ),
        "low_specificity_candidates": int(
            reference_results.eq("Low-Specificity Candidate").sum()
        ),
        "exact_invoice_total_candidates": int(
            amount_patterns.eq("Exact Invoice Total Candidate").sum()
        ),
        "possible_2_percent_tds_candidates": int(
            amount_patterns.eq("Possible 2% TDS Candidate").sum()
        ),
        "possible_10_percent_tds_candidates": int(
            amount_patterns.eq("Possible 10% TDS Candidate").sum()
        ),
        "possible_split_payment_cases": int(
            debit_rows.loc[
                amount_patterns.eq(
                    "Possible Split Payment - Review Required"
                ),
                "Matching Bill ID",
            ]
            .dropna()
            .astype(str)
            .nunique()
        ),
        "records_with_no_invoice_reference": int(
            (~reference_results.eq("Unique High-Confidence Reference")).sum()
        ),
        "currency_totals": currency_rows,
        "generated_timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
        "informational_warnings": (
            [_older_linked_bills_message(older_linked_bill_count)]
            if older_linked_bill_count
            else []
        ),
    }


def _write_input_summary(
    worksheet: Any,
    summary: Mapping[str, Any],
) -> None:
    _style_title(worksheet, REPORT_MODE_INPUT, 9)
    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "A3"
    widths = [34, 24, 15, 18, 21, 18, 21, 16, 18]
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width

    metadata = [
        ("Report start date", summary.get("report_start_date")),
        ("Report end date", summary.get("report_end_date")),
        ("Organizations", ", ".join(summary.get("organizations", [])) or "None"),
        ("Selected bank source", summary.get("selected_bank_source")),
        ("Selected source filename", summary.get("selected_source_file")),
        ("Selected upload ID suffix", summary.get("selected_upload_id_suffix")),
        (
            "Statement coverage start date",
            summary.get("statement_coverage_start_date"),
        ),
        (
            "Statement coverage end date",
            summary.get("statement_coverage_end_date"),
        ),
        ("Statement rows exported", summary["statement_rows_exported"]),
        ("Outgoing/debit statement rows", summary["bank_debit_count"]),
        (
            "High-confidence invoice references",
            summary["descriptions_with_invoice_references"],
        ),
        ("Ambiguous references", summary["ambiguous_invoice_references"]),
        ("Low-specificity candidates", summary["low_specificity_candidates"]),
        (
            "No high-confidence reference",
            summary["records_with_no_invoice_reference"],
        ),
        (
            "Exact invoice-total candidates",
            summary["exact_invoice_total_candidates"],
        ),
        (
            "Possible 2% TDS candidates",
            summary["possible_2_percent_tds_candidates"],
        ),
        (
            "Possible 10% TDS candidates",
            summary["possible_10_percent_tds_candidates"],
        ),
        (
            "Possible split-payment candidates",
            summary["possible_split_payment_cases"],
        ),
        (
            "Bills dated within selected period",
            summary["bills_in_selected_period"],
        ),
        (
            "Older linked bills included",
            summary["older_linked_bills_included"],
        ),
        ("Total bill rows exported", summary["total_bill_rows_exported"]),
        ("Distinct vendor-payment count", summary["vendor_payment_count"]),
        ("Payment-allocation count", summary["payment_allocation_count"]),
        ("Generated timestamp", summary["generated_timestamp"]),
    ]
    row = 3
    for label, value in metadata:
        worksheet.cell(row, 1, label).font = Font(bold=True, color="17365D")
        worksheet.cell(row, 2, _excel_value(value))
        if isinstance(value, datetime):
            worksheet.cell(row, 2).number_format = "yyyy-mm-dd hh:mm"
        elif isinstance(value, date):
            worksheet.cell(row, 2).number_format = "yyyy-mm-dd"
        elif isinstance(value, int):
            worksheet.cell(row, 2).number_format = "#,##0"
        row += 1

    notices = [
        "Invoice-reference and amount indicators are system-generated review "
        "helpers. Finance review is required.",
        *summary.get("informational_warnings", []),
    ]
    for warning in notices:
        worksheet.cell(row, 1, "Information").font = Font(
            bold=True,
            color="17365D",
        )
        worksheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=9)
        message_cell = worksheet.cell(row, 2, warning)
        message_cell.fill = PatternFill("solid", fgColor="D9EAF7")
        message_cell.alignment = Alignment(wrap_text=True, vertical="top")
        worksheet.row_dimensions[row].height = 32
        row += 1

    row += 1
    headers = [
        "Currency",
        "Bill Count",
        "Bill Total",
        "Vendor-Payment Count",
        "Vendor-Payment Total",
        "Allocation Count",
        "Allocation Total",
        "Statement Row Count",
        "Statement Debit Total",
    ]
    for column, label in enumerate(headers, start=1):
        cell = worksheet.cell(row, column, label)
        cell.fill = PatternFill("solid", fgColor="4472C4")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center")
    currency_header_row = row
    for totals in summary["currency_totals"]:
        row += 1
        values = [
            totals["currency"],
            totals["bill_count"],
            totals["bill_total"],
            totals["payment_count"],
            totals["payment_total"],
            totals["allocation_count"],
            totals["allocation_total"],
            totals["bank_leg_count"],
            totals["bank_total"],
        ]
        for column, value in enumerate(values, start=1):
            cell = worksheet.cell(row, column, value)
            if column in {2, 4, 6, 8}:
                cell.number_format = "#,##0"
            elif column > 1:
                cell.number_format = '#,##0.00;[Red](#,##0.00);-'
    worksheet.auto_filter.ref = (
        f"A{currency_header_row}:I{max(currency_header_row, row)}"
    )


def create_reconciliation_input_workbook(
    datasets: Mapping[str, pd.DataFrame] | None,
    filters: Mapping[str, Any] | None = None,
    validation_results: Mapping[str, Any] | None = None,
    bank_upload: Mapping[str, Any] | None = None,
) -> Workbook:
    """Create the four-sheet, finance-led reconciliation input workbook."""
    source = datasets or {}
    cleaned = {
        name: _clean_dataframe(source.get(name), columns)
        for name, columns in INPUT_DATASET_COLUMNS.items()
    }
    cleaned["vendor_bills"] = _select_reconciliation_input_bills(source)
    cleaned["bank_statement"] = prepare_manual_review_bank_statement(
        cleaned["vendor_bills"],
        source.get("bank_statement"),
    )
    validation_source = dict(source)
    validation_source["vendor_bills"] = cleaned["vendor_bills"]
    validation_source["bank_statement"] = cleaned["bank_statement"]
    validations = dict(
        validation_results
        or validate_reconciliation_input_data(validation_source)
    )
    payment_batch = combine_zoho_payment_batch(
        cleaned["vendor_payments"],
        cleaned["payment_allocations"],
    )
    summary = summarize_reconciliation_input(cleaned, filters, bank_upload)

    workbook = Workbook()
    workbook.remove(workbook.active)
    sheets = {
        name: workbook.create_sheet(name)
        for name in INPUT_WORKBOOK_SHEETS
    }
    _write_input_summary(sheets["Summary"], summary)
    _write_detail_sheet(
        sheets["Vendor Bills"],
        "Vendor Bills",
        cleaned["vendor_bills"],
    )
    _write_detail_sheet(
        sheets["Zoho Payment Batch"],
        "Zoho Payment Batch",
        payment_batch,
    )
    _write_detail_sheet(
        sheets["Bank Statement"],
        "Bank Statement",
        cleaned["bank_statement"],
    )
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    return workbook


def summarize_vendor_report(
    datasets: Mapping[str, pd.DataFrame],
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate KPI and currency-separated totals for Summary and UI cards."""
    bills = datasets.get("vendor_bills", pd.DataFrame())
    payments = datasets.get("vendor_payments", pd.DataFrame())
    allocations = datasets.get("payment_allocations", pd.DataFrame())
    banks = datasets.get("bank_transactions", pd.DataFrame())
    reconciliation = datasets.get("reconciliation_results", pd.DataFrame())
    selected_filters = filters or {}

    organizations = sorted(
        {
            str(value)
            for frame in (bills, payments, banks)
            if "Organization" in frame
            for value in frame["Organization"].dropna()
        }
    )
    active_vendor_keys = {
        (str(row["Organization"]), str(row["Vendor ID"]))
        for frame in (bills, payments)
        if {"Organization", "Vendor ID"}.issubset(frame.columns)
        for _, row in frame[["Organization", "Vendor ID"]].dropna().iterrows()
    }
    reconciliation_counts = (
        reconciliation.get("Reconciliation Status", pd.Series(dtype="object"))
        .value_counts()
        .to_dict()
    )
    bank_counts = (
        payments.get("Bank Match Status", pd.Series(dtype="object"))
        .value_counts()
        .to_dict()
    )
    currency_totals = []
    if not reconciliation.empty:
        grouped = reconciliation.assign(
            _bill=_numeric(reconciliation["Bill Amount"]),
            _allocated=_numeric(reconciliation["Allocated Amount"]),
            _verified=(
                _numeric(reconciliation["Allocated Amount"])
                - _numeric(reconciliation["Bank Pending Amount"])
            ),
            _outstanding=_numeric(reconciliation["Source Outstanding Balance"]),
        ).groupby("Currency", dropna=False)
        for currency, group in grouped:
            currency_totals.append(
                {
                    "currency": currency or "<missing>",
                    "bill_total": float(group["_bill"].sum()),
                    "allocated_total": float(group["_allocated"].sum()),
                    "bank_verified_total": float(group["_verified"].sum()),
                    "outstanding_total": float(group["_outstanding"].sum()),
                }
            )
    return {
        "report_start_date": selected_filters.get("start_date"),
        "report_end_date": selected_filters.get("end_date"),
        "organizations": organizations,
        "total_vendors": len(active_vendor_keys),
        "total_bills": len(bills),
        "total_vendor_payments": len(payments),
        "total_payment_allocations": len(allocations),
        "total_bank_transaction_legs": len(banks),
        "reconciliation_counts": reconciliation_counts,
        "bank_match_counts": bank_counts,
        "currency_totals": currency_totals,
    }


def _write_summary(
    worksheet: Any,
    summary: Mapping[str, Any],
    validations: Mapping[str, Any],
) -> None:
    _style_title(worksheet, REPORT_MODE_ANALYSIS, 6)
    worksheet.merge_cells("A2:F2")
    disclaimer = worksheet["A2"]
    disclaimer.value = ANALYSIS_DISCLAIMER
    disclaimer.font = Font(bold=True, color="9C5700")
    disclaimer.fill = PatternFill("solid", fgColor="FFF2CC")
    disclaimer.alignment = Alignment(wrap_text=True, vertical="center")
    worksheet.row_dimensions[2].height = 32
    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "A3"
    worksheet.column_dimensions["A"].width = 42
    worksheet.column_dimensions["B"].width = 24
    worksheet.column_dimensions["C"].width = 18
    worksheet.column_dimensions["D"].width = 18
    worksheet.column_dimensions["E"].width = 18
    worksheet.column_dimensions["F"].width = 28

    metadata = [
        ("Generated At (UTC)", datetime.now(timezone.utc).replace(tzinfo=None)),
        ("Report Start Date", summary.get("report_start_date")),
        ("Report End Date", summary.get("report_end_date")),
        ("Organizations Included", ", ".join(summary.get("organizations", [])) or "None"),
    ]
    row = 3
    for label, value in metadata:
        worksheet.cell(row, 1, label).font = Font(bold=True, color="17365D")
        worksheet.cell(row, 2, _excel_value(value))
        if isinstance(value, datetime):
            worksheet.cell(row, 2).number_format = "yyyy-mm-dd hh:mm"
        elif isinstance(value, date):
            worksheet.cell(row, 2).number_format = "yyyy-mm-dd"
        row += 1

    row += 1
    worksheet.cell(row, 1, "KPI").fill = PatternFill("solid", fgColor="4472C4")
    worksheet.cell(row, 2, "Count").fill = PatternFill("solid", fgColor="4472C4")
    for cell in worksheet[row][:2]:
        cell.font = Font(color="FFFFFF", bold=True)
    kpis = [
        ("Total Vendors", summary["total_vendors"]),
        ("Total Bills", summary["total_bills"]),
        ("Total Vendor Payments", summary["total_vendor_payments"]),
        ("Total Payment Allocations", summary["total_payment_allocations"]),
        ("Total Bank Transaction Legs", summary["total_bank_transaction_legs"]),
    ]
    for status in [
        "Fully Reconciled",
        "Partially Reconciled",
        "Payment Recorded - Bank Pending",
        "Bank Evidence Found - Zoho Posting Pending",
        "Unpaid - No Payment Found",
        "Amount Mismatch",
        "Needs Review",
    ]:
        kpis.append((status, int(summary["reconciliation_counts"].get(status, 0))))
    for status in ["Bank Match Pending Review", "Bank Not Found"]:
        kpis.append((status, int(summary["bank_match_counts"].get(status, 0))))
    for label, value in kpis:
        row += 1
        worksheet.cell(row, 1, label)
        worksheet.cell(row, 2, value).number_format = "#,##0"
        fill = _status_fill(label)
        if fill:
            worksheet.cell(row, 1).fill = fill
            worksheet.cell(row, 2).fill = fill

    row += 2
    currency_header = row
    currency_headers = [
        "Currency",
        "Bill Total",
        "Allocated Total",
        "Bank-Verified Total",
        "Outstanding Total",
    ]
    for column, label in enumerate(currency_headers, start=1):
        cell = worksheet.cell(row, column, label)
        cell.fill = PatternFill("solid", fgColor="4472C4")
        cell.font = Font(color="FFFFFF", bold=True)
    for totals in summary["currency_totals"]:
        row += 1
        values = [
            totals["currency"],
            totals["bill_total"],
            totals["allocated_total"],
            totals["bank_verified_total"],
            totals["outstanding_total"],
        ]
        for column, value in enumerate(values, start=1):
            cell = worksheet.cell(row, column, value)
            if column > 1:
                cell.number_format = '#,##0.00;[Red](#,##0.00);-'
    worksheet.auto_filter.ref = f"A{currency_header}:E{max(currency_header, row)}"

    row += 2
    worksheet.cell(row, 1, "Validation Check").fill = PatternFill("solid", fgColor="17365D")
    worksheet.cell(row, 2, "Result").fill = PatternFill("solid", fgColor="17365D")
    for cell in worksheet[row][:2]:
        cell.font = Font(color="FFFFFF", bold=True)
    for name, value in validations.items():
        row += 1
        worksheet.cell(row, 1, name.replace("_", " ").title())
        worksheet.cell(row, 2, "PASS" if value is True or value == 0 else f"FAIL ({value})")
        worksheet.cell(row, 2).fill = PatternFill(
            "solid",
            fgColor="E2F0D9" if value is True or value == 0 else "F4CCCC",
        )


def create_vendor_report_workbook(
    datasets: Mapping[str, pd.DataFrame] | None,
    filters: Mapping[str, Any] | None = None,
    validation_results: Mapping[str, Any] | None = None,
) -> Workbook:
    """Create the six-sheet reconciliation workbook, including empty datasets."""
    source = datasets or {}
    validations = dict(
        validation_results
        or validate_vendor_report_data(source)
    )
    cleaned = {
        name: _clean_dataframe(source.get(name), columns)
        for name, columns in DATASET_COLUMNS.items()
    }
    summary_values = summarize_vendor_report(cleaned, filters)

    workbook = Workbook()
    workbook.remove(workbook.active)
    sheets = {name: workbook.create_sheet(name) for name in WORKBOOK_SHEETS}
    _write_summary(sheets["Summary"], summary_values, validations)
    _write_detail_sheet(sheets["Vendor Bills"], "Vendor Bills", cleaned["vendor_bills"])
    _write_detail_sheet(sheets["Vendor Payments"], "Vendor Payments", cleaned["vendor_payments"])
    _write_detail_sheet(
        sheets["Payment Allocations"],
        "Payment Allocations",
        cleaned["payment_allocations"],
    )
    _write_detail_sheet(
        sheets["Bank Transactions"],
        "Bank Transactions",
        cleaned["bank_transactions"],
    )
    _write_detail_sheet(
        sheets["Reconciliation Results"],
        "Reconciliation Results",
        cleaned["reconciliation_results"],
    )
    reconciliation_sheet = sheets["Reconciliation Results"]
    if len(cleaned["reconciliation_results"]) > 0:
        status_column = RECONCILIATION_RESULT_COLUMNS.index("Review Status") + 1
        comment_column = RECONCILIATION_RESULT_COLUMNS.index("Reviewer Comment") + 1
        last_row = len(cleaned["reconciliation_results"]) + 3
        validation = DataValidation(
            type="list",
            formula1='"Pending,In Review,Resolved"',
            allow_blank=True,
        )
        reconciliation_sheet.add_data_validation(validation)
        validation.add(
            f"{get_column_letter(status_column)}4:"
            f"{get_column_letter(status_column)}{last_row}"
        )
        for column in (status_column, comment_column):
            for row in range(4, last_row + 1):
                reconciliation_sheet.cell(row, column).font = Font(color="0000FF")

    for sheet_name in ("Vendor Bills", "Vendor Payments", "Reconciliation Results"):
        sheet = sheets[sheet_name]
        headers = [cell.value for cell in sheet[3]]
        for status_header in (
            "Reconciliation Status",
            "Bank Match Status",
        ):
            if status_header not in headers:
                continue
            column = get_column_letter(headers.index(status_header) + 1)
            last_row = max(4, sheet.max_row)
            sheet.conditional_formatting.add(
                f"{column}4:{column}{last_row}",
                FormulaRule(
                    formula=[f'ISNUMBER(SEARCH("Pending",{column}4))'],
                    fill=PatternFill("solid", fgColor="FFF2CC"),
                ),
            )

    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    return workbook


def generate_vendor_transactions_report(
    start_date: date,
    end_date: date,
    organization: str | None = None,
    vendor: str | None = None,
    currency: str | None = None,
    source_bill_status: str | None = None,
    reconciliation_status: str | None = None,
    bank_match_status: str | None = None,
    review_required: bool | None = None,
    bank_upload_id: str | None = None,
    report_mode: str = REPORT_MODE_INPUT,
    destination_folder: str | Path | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Fetch, validate, and build the selected reconciliation workbook."""
    if report_mode not in REPORT_MODES:
        raise ValueError(f"Unsupported vendor report mode: {report_mode}")

    generated_at = datetime.now(timezone.utc)
    timestamp_suffix = generated_at.strftime("%Y%m%dT%H%M%S%fZ")

    if report_mode == REPORT_MODE_INPUT:
        datasets, metadata = fetch_reconciliation_input_data(
            start_date=start_date,
            end_date=end_date,
            organization=organization,
            vendor=vendor,
            currency=currency,
            bank_upload_id=bank_upload_id,
            project_id=project_id,
            location=location,
            client=client,
        )
        datasets["vendor_bills"] = _select_reconciliation_input_bills(datasets)
        datasets["bank_statement"] = prepare_manual_review_bank_statement(
            datasets["vendor_bills"],
            datasets["bank_statement"],
        )
        validations = validate_reconciliation_input_data(
            datasets,
            metadata["queries"],
        )
        workbook = create_reconciliation_input_workbook(
            datasets,
            filters=metadata["filters"],
            validation_results=validations,
            bank_upload=metadata["bank_upload"],
        )
        report_name = (
            f"Vendor_Reconciliation_Input_{start_date:%Y%m%d}_"
            f"{end_date:%Y%m%d}_{timestamp_suffix}.xlsx"
        )
        payment_batch = combine_zoho_payment_batch(
            datasets["vendor_payments"],
            datasets["payment_allocations"],
        )
        row_counts = {
            "Summary": workbook["Summary"].max_row,
            "Vendor Bills": len(datasets["vendor_bills"]),
            "Zoho Payment Batch": len(payment_batch),
            "Bank Statement": len(datasets["bank_statement"]),
        }
        summary = summarize_reconciliation_input(
            datasets,
            metadata["filters"],
            metadata["bank_upload"],
        )
        message = "Reconciliation Input Data generated successfully."
    else:
        datasets, metadata = fetch_vendor_report_data(
            start_date=start_date,
            end_date=end_date,
            organization=organization,
            vendor=vendor,
            currency=currency,
            source_bill_status=source_bill_status,
            reconciliation_status=reconciliation_status,
            bank_match_status=bank_match_status,
            review_required=review_required,
            project_id=project_id,
            location=location,
            client=client,
        )
        validations = validate_vendor_report_data(datasets, metadata["queries"])
        workbook = create_vendor_report_workbook(
            datasets,
            filters=metadata["filters"],
            validation_results=validations,
        )
        report_name = (
            f"Vendor_Reconciliation_Analysis_{start_date:%Y%m%d}_"
            f"{end_date:%Y%m%d}_{timestamp_suffix}.xlsx"
        )
        row_counts = {
            sheet_name: (
                workbook[sheet_name].max_row
                if sheet_name == "Summary"
                else len(datasets[dataset_name])
            )
            for sheet_name, dataset_name in [
                ("Summary", "vendor_bills"),
                ("Vendor Bills", "vendor_bills"),
                ("Vendor Payments", "vendor_payments"),
                ("Payment Allocations", "payment_allocations"),
                ("Bank Transactions", "bank_transactions"),
                ("Reconciliation Results", "reconciliation_results"),
            ]
        }
        summary = summarize_vendor_report(datasets, metadata["filters"])
        message = "Automated Reconciliation Analysis generated successfully."

    report_buffer = BytesIO()
    workbook.save(report_buffer)
    report_bytes = report_buffer.getvalue()
    report_path = None
    if destination_folder is not None:
        output_folder = Path(destination_folder)
        try:
            output_folder.mkdir(parents=True, exist_ok=True)
            report_path = output_folder / report_name
            report_path.write_bytes(report_bytes)
        except OSError as error:
            raise RuntimeError(
                "Workbook output could not be written. Close any open copy, "
                "verify the destination is writable, and try again: "
                f"{safe_exception_details(error)}"
            ) from error

    return {
        "report_mode": report_mode,
        "report_path": report_path,
        "report_name": report_name,
        "report_bytes": report_bytes,
        "row_counts": row_counts,
        "summary": summary,
        "validations": validations,
        "bill_rows": len(datasets.get("vendor_bills", [])),
        "payment_rows": len(datasets.get("vendor_payments", [])),
        "total_rows": sum(
            len(frame)
            for name, frame in datasets.items()
            if name in DATASET_COLUMNS or name in INPUT_DATASET_COLUMNS
        ),
        "message": message,
        "informational_warnings": validations.get(
            "informational_warnings",
            [],
        ),
    }
