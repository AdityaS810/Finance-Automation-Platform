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

BILLS_VIEW = "finance_silver.fact_bills"
PAYMENTS_VIEW = "finance_silver.fact_vendor_payments"
ALLOCATIONS_VIEW = "finance_silver.bridge_vendor_payment_bill_allocations"
BANK_VIEW = "finance_silver.fact_bank_transactions"
CONTACTS_VIEW = "finance_silver.dim_contacts"
PAYMENT_MATCHES_VIEW = "finance_gold.vendor_payment_bank_matches"
BILL_RECONCILIATION_VIEW = "finance_gold.vendor_bill_reconciliation"
EXCEPTIONS_VIEW = "finance_gold.vendor_reconciliation_exceptions"

WORKBOOK_SHEETS = [
    "Summary",
    "Vendor Master",
    "Vendor Bills",
    "Vendor Payments",
    "Payment Allocations",
    "Bank Transactions",
    "Reconciliation Results",
    "Exceptions - Review",
    "Technical Audit",
]

VENDOR_MASTER_COLUMNS = [
    "Organization",
    "Vendor ID",
    "Vendor Name",
    "Company Name",
    "Vendor Status",
    "Currency",
    "Outstanding Payable",
    "Has Bills",
    "Has Vendor Payments",
    "Bill Count",
    "Payment Count",
    "Activity Status",
]

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
    "Bank Data Quality Status",
    "Used in Vendor Match",
    "Review Required",
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
    "Allocated Amount",
    "Bank-Verified Amount",
    "Bank-Pending Amount",
    "Remaining Reconciliation Amount",
    "Reconciliation Status",
    "Reconciliation Reason",
    "Review Required",
]

EXCEPTION_COLUMNS = [
    "Exception Type",
    "Organization",
    "Vendor ID",
    "Vendor Name",
    "Bill ID",
    "Payment ID",
    "Masked Bank Leg Key",
    "Currency",
    "Amount",
    "Exception Reason",
    "Review Required",
    "Review Status",
    "Reviewer Comment",
]

TECHNICAL_AUDIT_COLUMNS = [
    "Record Type",
    "Source Organization ID",
    "Source Record ID",
    "Bill ID",
    "Payment ID",
    "Allocation Key",
    "Bank Transaction Leg Key",
    "Run ID",
    "Loaded Timestamp",
    "Mapping/Match Method",
    "Data Quality Status",
]

DATASET_COLUMNS = {
    "vendor_master": VENDOR_MASTER_COLUMNS,
    "vendor_bills": VENDOR_BILL_COLUMNS,
    "vendor_payments": VENDOR_PAYMENT_COLUMNS,
    "payment_allocations": PAYMENT_ALLOCATION_COLUMNS,
    "bank_transactions": BANK_TRANSACTION_COLUMNS,
    "reconciliation_results": RECONCILIATION_RESULT_COLUMNS,
    "exceptions": EXCEPTION_COLUMNS,
    "technical_audit": TECHNICAL_AUDIT_COLUMNS,
}

INTERNAL_COLUMNS = {
    "vendor_master": ["_Contact Type", "_Source Organization ID"],
    "vendor_payments": ["_Bank Transaction Leg Key", "_Source Organization ID"],
    "bank_transactions": ["_Bank Transaction Leg Key"],
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
    "Outstanding Balance",
    "Payment Amount",
    "Allocated Amount",
    "Unapplied Amount",
    "Bank-Verified Amount",
    "Bank-Pending Amount",
    "Remaining Reconciliation Amount",
    "Amount Applied",
    "Amount",
    "Signed Amount",
    "Source Outstanding Balance",
    "Bank Amount",
    "Outstanding Payable",
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
    "Source Organization ID",
    "Source Record ID",
    "Bank Transaction Leg Key",
    "Masked Bank Leg Key",
    "Run ID",
    "Payment IDs",
}
WRAP_COLUMNS = {
    "Vendor Name",
    "Company Name",
    "Reconciliation Reason",
    "Bank Match Reason",
    "Exception Reason",
    "Reviewer Comment",
}

DISPLAY_COLUMN_MAPS = {
    "vendor_master": {
        "organization": "Organization",
        "vendor_id": "Vendor ID",
        "vendor_name": "Vendor Name",
        "company_name": "Company Name",
        "vendor_status": "Vendor Status",
        "currency": "Currency",
        "outstanding_payable": "Outstanding Payable",
        "has_bills": "Has Bills",
        "has_vendor_payments": "Has Vendor Payments",
        "bill_count": "Bill Count",
        "payment_count": "Payment Count",
        "activity_status": "Activity Status",
        "_contact_type": "_Contact Type",
        "_source_org_id": "_Source Organization ID",
    },
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
        "_bank_transaction_leg_key": "_Bank Transaction Leg Key",
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
        "bank_data_quality_status": "Bank Data Quality Status",
        "used_in_vendor_match": "Used in Vendor Match",
        "review_required": "Review Required",
        "_bank_transaction_leg_key": "_Bank Transaction Leg Key",
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
        "bill_amount": "Bill Amount",
        "source_outstanding_balance": "Source Outstanding Balance",
        "source_bill_status": "Source Bill Status",
        "payment_count": "Payment Count",
        "payment_ids": "Payment IDs",
        "latest_payment_date": "Latest Payment Date",
        "allocated_amount": "Allocated Amount",
        "bank_verified_amount": "Bank-Verified Amount",
        "bank_pending_amount": "Bank-Pending Amount",
        "remaining_reconciliation_amount": "Remaining Reconciliation Amount",
        "reconciliation_status": "Reconciliation Status",
        "reconciliation_reason": "Reconciliation Reason",
        "review_required": "Review Required",
    },
    "exceptions": {
        "exception_type": "Exception Type",
        "organization": "Organization",
        "vendor_id": "Vendor ID",
        "vendor_name": "Vendor Name",
        "bill_id": "Bill ID",
        "payment_id": "Payment ID",
        "masked_bank_leg_key": "Masked Bank Leg Key",
        "currency": "Currency",
        "amount": "Amount",
        "exception_reason": "Exception Reason",
        "review_required": "Review Required",
        "review_status": "Review Status",
        "reviewer_comment": "Reviewer Comment",
    },
    "technical_audit": {
        "record_type": "Record Type",
        "source_org_id": "Source Organization ID",
        "source_record_id": "Source Record ID",
        "bill_id": "Bill ID",
        "payment_id": "Payment ID",
        "allocation_key": "Allocation Key",
        "bank_transaction_leg_key": "Bank Transaction Leg Key",
        "run_id": "Run ID",
        "loaded_timestamp": "Loaded Timestamp",
        "mapping_match_method": "Mapping/Match Method",
        "data_quality_status": "Data Quality Status",
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
) -> pd.DataFrame:
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(query_parameters=parameters or [])
    result = client.query(
        query,
        job_config=job_config,
        location=_bigquery_location(location),
    ).result()
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


def build_vendor_reconciliation_queries(
    project_id: str,
    filters: Mapping[str, Any],
) -> dict[str, str]:
    """Build parameterized, source-scoped queries for every workbook dataset."""
    vendor_master_filter = _where(
        filters,
        {
            "organization": "contact.source_org_key",
            "vendor": (
                "COALESCE(NULLIF(contact.contact_name, ''), "
                "NULLIF(contact.company_name, ''), contact.contact_id)"
            ),
            "currency": "contact.currency_code",
        },
    )
    bill_activity_filter = _where(
        filters,
        {
            "start_date": "bill.bill_date",
            "end_date": "bill.bill_date",
            "organization": "bill.source_org_key",
            "currency": "bill.original_currency",
        },
    )
    payment_activity_filter = _where(
        filters,
        {
            "start_date": "payment.payment_date",
            "end_date": "payment.payment_date",
            "organization": "payment.source_org_key",
            "currency": "payment.currency_code",
        },
    )
    bills_filter = _where(
        filters,
        {
            "start_date": "bill_date",
            "end_date": "bill_date",
            "organization": "source_org_key",
            "vendor": "vendor_name",
            "currency": "currency",
            "source_bill_status": "source_bill_status",
            "reconciliation_status": "reconciliation_status",
            "review_required": "review_required",
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
    exceptions_filter = _where(
        filters,
        {
            "start_date": "record_date",
            "end_date": "record_date",
            "organization": "organization",
            "vendor": "vendor_name",
            "currency": "currency",
            "bank_match_status": "bank_match_status",
            "review_required": "review_required",
        },
    )
    audit_filter = _where(
        filters,
        {
            "start_date": "audit_date",
            "end_date": "audit_date",
            "organization": "source_org_key",
        },
    )

    return {
        "vendor_master": f"""
          WITH bill_activity AS (
            SELECT
              source_org_id,
              CAST(vendor_id AS STRING) AS vendor_id,
              COUNT(DISTINCT CAST(bill_id AS STRING)) AS bill_count
            FROM {_table_name(project_id, BILLS_VIEW)} bill
            {bill_activity_filter}
            GROUP BY source_org_id, vendor_id
          ),
          payment_activity AS (
            SELECT
              source_org_id,
              CAST(vendor_id AS STRING) AS vendor_id,
              COUNT(DISTINCT CAST(payment_id AS STRING)) AS payment_count
            FROM {_table_name(project_id, PAYMENTS_VIEW)} payment
            {payment_activity_filter}
            GROUP BY source_org_id, vendor_id
          )
          SELECT
            contact.source_org_key AS organization,
            CAST(contact.contact_id AS STRING) AS vendor_id,
            COALESCE(
              NULLIF(contact.contact_name, ''),
              NULLIF(contact.company_name, ''),
              CAST(contact.contact_id AS STRING)
            ) AS vendor_name,
            contact.company_name,
            contact.status AS vendor_status,
            contact.currency_code AS currency,
            contact.outstanding_payable_amount AS outstanding_payable,
            COALESCE(bill.bill_count, 0) > 0 AS has_bills,
            COALESCE(payment.payment_count, 0) > 0 AS has_vendor_payments,
            COALESCE(bill.bill_count, 0) AS bill_count,
            COALESCE(payment.payment_count, 0) AS payment_count,
            IF(
              COALESCE(bill.bill_count, 0) > 0
                OR COALESCE(payment.payment_count, 0) > 0,
              'Reconciliation Activity',
              'No Activity'
            ) AS activity_status,
            contact.contact_type AS _contact_type,
            CAST(contact.source_org_id AS STRING) AS _source_org_id
          FROM {_table_name(project_id, CONTACTS_VIEW)} contact
          LEFT JOIN bill_activity bill
            ON bill.source_org_id = contact.source_org_id
           AND bill.vendor_id = CAST(contact.contact_id AS STRING)
          LEFT JOIN payment_activity payment
            ON payment.source_org_id = contact.source_org_id
           AND payment.vendor_id = CAST(contact.contact_id AS STRING)
          {vendor_master_filter}
          {"AND" if vendor_master_filter else "WHERE"} LOWER(contact.contact_type) = 'vendor'
            AND (
              contact.source_org_id IS NOT NULL
              OR NOT EXISTS (
                SELECT 1
                FROM {_table_name(project_id, CONTACTS_VIEW)} scoped_contact
                WHERE scoped_contact.contact_id = contact.contact_id
                  AND scoped_contact.source_org_id IS NOT NULL
              )
            )
          ORDER BY contact.source_org_key, vendor_name, vendor_id
        """,
        "vendor_bills": f"""
          SELECT
            source_org_key AS organization,
            CAST(vendor_id AS STRING) AS vendor_id,
            vendor_name,
            CAST(bill_id AS STRING) AS bill_id,
            bill_number,
            bill_date, due_date, currency, bill_amount,
            source_outstanding_balance AS outstanding_balance, source_bill_status,
            allocated_payment_count, allocated_amount, bank_verified_amount,
            remaining_reconciliation_amount, reconciliation_status,
            reconciliation_reason, review_required
          FROM {_table_name(project_id, BILL_RECONCILIATION_VIEW)}
          {bills_filter}
          ORDER BY bill_date DESC, vendor_name, bill_number
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
            CAST(match.bank_transaction_leg_key AS STRING) AS _bank_transaction_leg_key,
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
            CAST(allocation_key AS STRING) AS allocation_key
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
            bank.bank_data_quality_status,
            COALESCE(usage.used_in_vendor_match, FALSE) AS used_in_vendor_match,
            COALESCE(usage.review_required, FALSE) AS review_required,
            CAST(bank.bank_transaction_leg_key AS STRING) AS _bank_transaction_leg_key,
            usage.bank_match_status
          FROM {_table_name(project_id, BANK_VIEW)} bank
          LEFT JOIN match_usage usage
            USING (bank_transaction_leg_key)
          {bank_filter}
          ORDER BY bank.transaction_date DESC, bank.transaction_id,
                   bank.account_id, bank.debit_or_credit
        """,
        "reconciliation_results": f"""
          SELECT
            source_org_key AS organization,
            CAST(vendor_id AS STRING) AS vendor_id,
            vendor_name,
            CAST(bill_id AS STRING) AS bill_id,
            bill_number,
            bill_date, due_date, currency, bill_amount, source_outstanding_balance,
            source_bill_status,
            allocated_payment_count AS payment_count,
            CAST(payment_ids AS STRING) AS payment_ids,
            latest_payment_date, allocated_amount, bank_verified_amount,
            bank_pending_amount, remaining_reconciliation_amount,
            reconciliation_status, reconciliation_reason, review_required
          FROM {_table_name(project_id, BILL_RECONCILIATION_VIEW)}
          {bills_filter}
          ORDER BY bill_date DESC, vendor_name, bill_number
        """,
        "exceptions": f"""
          WITH exception_rows AS (
            SELECT
              exception.exception_type,
              COALESCE(bill.source_org_key, payment.source_org_key, exception.source_org_id)
                AS organization,
              CAST(exception.vendor_id AS STRING) AS vendor_id,
              exception.vendor_name,
              CAST(exception.bill_id AS STRING) AS bill_id,
              CAST(exception.payment_id AS STRING) AS payment_id,
              IF(
                exception.bank_transaction_leg_key IS NULL,
                NULL,
                CONCAT('...', RIGHT(exception.bank_transaction_leg_key, 4))
              ) AS masked_bank_leg_key,
              exception.currency,
              exception.amount,
              exception.exception_reason,
              exception.review_required,
              CAST(NULL AS STRING) AS review_status,
              CAST(NULL AS STRING) AS reviewer_comment,
              COALESCE(bill.bill_date, payment.payment_date) AS record_date,
              payment.bank_match_status
            FROM {_table_name(project_id, EXCEPTIONS_VIEW)} exception
            LEFT JOIN {_table_name(project_id, BILL_RECONCILIATION_VIEW)} bill
              ON bill.source_org_id = exception.source_org_id
             AND bill.bill_id = exception.bill_id
            LEFT JOIN {_table_name(project_id, PAYMENT_MATCHES_VIEW)} payment
              ON payment.source_org_id = exception.source_org_id
             AND payment.payment_id = exception.payment_id
          )
          SELECT * EXCEPT(record_date, bank_match_status)
          FROM exception_rows
          {exceptions_filter}
          ORDER BY exception_type, organization, bill_id, payment_id
        """,
        "technical_audit": f"""
          WITH audit_rows AS (
            SELECT
              'Bill' AS record_type, source_org_key, source_org_id, source_record_id,
              bill_id, CAST(NULL AS STRING) AS payment_id,
              CAST(NULL AS STRING) AS allocation_key,
              CAST(NULL AS STRING) AS bank_transaction_leg_key,
              run_id, loaded_at AS loaded_timestamp,
              'source_bill' AS mapping_match_method,
              IF(bill_id IS NULL OR total_amount IS NULL, 'Needs Review', 'Valid')
                AS data_quality_status,
              bill_date AS audit_date
            FROM {_table_name(project_id, BILLS_VIEW)}
            UNION ALL
            SELECT
              'Vendor Payment', payment.source_org_key, payment.source_org_id,
              payment.source_record_id,
              CAST(NULL AS STRING), payment.payment_id, CAST(NULL AS STRING),
              IF(
                match.bank_transaction_leg_key IS NULL,
                NULL,
                CONCAT('...', RIGHT(match.bank_transaction_leg_key, 4))
              ),
              payment.run_id, payment.loaded_at, match.bank_match_method,
              payment.allocation_status, payment.payment_date
            FROM {_table_name(project_id, PAYMENTS_VIEW)} payment
            LEFT JOIN {_table_name(project_id, PAYMENT_MATCHES_VIEW)} match
              ON match.source_org_id = payment.source_org_id
             AND match.payment_id = payment.payment_id
            UNION ALL
            SELECT
              'Payment Allocation', source_org_key, source_org_id, source_record_id,
              bill_id, payment_id, allocation_key, CAST(NULL AS STRING),
              run_id, loaded_at, 'payment_to_bill_allocation',
              IF(amount_applied IS NULL, 'Needs Review', 'Valid'), payment_date
            FROM {_table_name(project_id, ALLOCATIONS_VIEW)}
            UNION ALL
            SELECT
              'Bank Transaction', source_org_key, source_org_id,
              IF(
                source_record_id IS NULL,
                NULL,
                CONCAT('...', RIGHT(source_record_id, 4))
              ),
              CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING),
              CONCAT('...', RIGHT(bank_transaction_leg_key, 4)),
              run_id, loaded_at, 'bank_transaction_leg',
              bank_data_quality_status, transaction_date
            FROM {_table_name(project_id, BANK_VIEW)}
          )
          SELECT
            record_type,
            CAST(source_org_id AS STRING) AS source_org_id,
            CAST(source_record_id AS STRING) AS source_record_id,
            CAST(bill_id AS STRING) AS bill_id,
            CAST(payment_id AS STRING) AS payment_id,
            CAST(allocation_key AS STRING) AS allocation_key,
            CAST(bank_transaction_leg_key AS STRING) AS bank_transaction_leg_key,
            CAST(run_id AS STRING) AS run_id,
            loaded_timestamp,
            mapping_match_method, data_quality_status
          FROM audit_rows
          {audit_filter}
          ORDER BY record_type, loaded_timestamp DESC
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


def _rename_query_columns(dataframe: pd.DataFrame, dataset_name: str) -> pd.DataFrame:
    mapping = DISPLAY_COLUMN_MAPS[dataset_name]
    return dataframe.rename(columns=mapping).reindex(columns=list(mapping.values()))


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


def validate_vendor_report_data(
    datasets: Mapping[str, pd.DataFrame],
    queries: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run blocking grain and financial validations before workbook creation."""
    vendor_master = datasets.get("vendor_master", pd.DataFrame())
    bills = datasets.get("vendor_bills", pd.DataFrame())
    payments = datasets.get("vendor_payments", pd.DataFrame())
    allocations = datasets.get("payment_allocations", pd.DataFrame())
    banks = datasets.get("bank_transactions", pd.DataFrame())
    reconciliation = datasets.get("reconciliation_results", pd.DataFrame())

    def duplicate_count(frame: pd.DataFrame, columns: list[str]) -> int:
        if frame.empty or not set(columns).issubset(frame.columns):
            return 0
        return int(frame.duplicated(columns, keep=False).sum())

    duplicate_bill_ids = duplicate_count(bills, ["Organization", "Bill ID"])
    duplicate_vendor_master_ids = duplicate_count(
        vendor_master,
        ["Organization", "Vendor ID"],
    )
    duplicate_payment_ids = duplicate_count(payments, ["Organization", "Payment ID"])
    duplicate_allocation_keys = duplicate_count(
        allocations,
        ["Organization", "Allocation Key"],
    )
    duplicate_bank_legs = duplicate_count(banks, ["_Bank Transaction Leg Key"])

    non_vendor_master_contacts = 0
    vendor_master_activity_mismatches = 0
    if not vendor_master.empty:
        if "_Contact Type" not in vendor_master.columns:
            non_vendor_master_contacts = len(vendor_master)
        else:
            non_vendor_master_contacts = int(
                vendor_master["_Contact Type"]
                .fillna("")
                .astype(str)
                .str.lower()
                .ne("vendor")
                .sum()
            )
        required_activity_columns = {
            "Has Bills",
            "Has Vendor Payments",
            "Bill Count",
            "Payment Count",
            "Activity Status",
        }
        if required_activity_columns.issubset(vendor_master.columns):
            expected_has_bills = _numeric(vendor_master["Bill Count"]).gt(0)
            expected_has_payments = _numeric(vendor_master["Payment Count"]).gt(0)
            actual_has_bills = vendor_master["Has Bills"].fillna(False).astype(bool)
            actual_has_payments = (
                vendor_master["Has Vendor Payments"].fillna(False).astype(bool)
            )
            expected_status = (expected_has_bills | expected_has_payments).map(
                {
                    True: "Reconciliation Activity",
                    False: "No Activity",
                }
            )
            vendor_master_activity_mismatches = int(
                (
                    actual_has_bills.ne(expected_has_bills)
                    | actual_has_payments.ne(expected_has_payments)
                    | vendor_master["Activity Status"].ne(expected_status)
                ).sum()
            )
        else:
            vendor_master_activity_mismatches = len(vendor_master)

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
        "_Bank Transaction Leg Key",
    }.issubset(payments.columns):
        final = payments[
            payments["Bank Match Status"].eq("Bank Verified")
            & payments["_Bank Transaction Leg Key"].notna()
        ]
        duplicate_final_bank_assignments = duplicate_count(
            final,
            ["_Bank Transaction Leg Key"],
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
                _numeric(reconciliation["Bank-Verified Amount"])
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
        "duplicate_vendor_master_ids": duplicate_vendor_master_ids,
        "non_vendor_master_contacts": non_vendor_master_contacts,
        "vendor_master_activity_mismatches": vendor_master_activity_mismatches,
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


def summarize_vendor_report(
    datasets: Mapping[str, pd.DataFrame],
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate KPI and currency-separated totals for Summary and UI cards."""
    vendor_master = datasets.get("vendor_master", pd.DataFrame())
    bills = datasets.get("vendor_bills", pd.DataFrame())
    payments = datasets.get("vendor_payments", pd.DataFrame())
    allocations = datasets.get("payment_allocations", pd.DataFrame())
    banks = datasets.get("bank_transactions", pd.DataFrame())
    reconciliation = datasets.get("reconciliation_results", pd.DataFrame())
    selected_filters = filters or {}

    organizations = sorted(
        {
            str(value)
            for frame in (vendor_master, bills, payments, banks)
            if "Organization" in frame
            for value in frame["Organization"].dropna()
        }
    )
    activity_counts = (
        vendor_master.get("Activity Status", pd.Series(dtype="object"))
        .value_counts()
        .to_dict()
    )
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
            _verified=_numeric(reconciliation["Bank-Verified Amount"]),
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
        "total_master_vendors": len(vendor_master),
        "vendors_with_reconciliation_activity": int(
            activity_counts.get("Reconciliation Activity", 0)
        ),
        "vendors_with_no_activity": int(activity_counts.get("No Activity", 0)),
        "total_vendors": len(vendor_master),
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
    _style_title(worksheet, "Vendor Reconciliation Report", 6)
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
        ("Total Master Vendors", summary["total_master_vendors"]),
        (
            "Vendors With Reconciliation Activity",
            summary["vendors_with_reconciliation_activity"],
        ),
        ("Vendors With No Activity", summary["vendors_with_no_activity"]),
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
    """Create the eight-sheet reconciliation workbook, including empty datasets."""
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
    _write_detail_sheet(
        sheets["Vendor Master"],
        "Vendor Master",
        cleaned["vendor_master"],
    )
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
    _write_detail_sheet(
        sheets["Exceptions - Review"],
        "Exceptions - Review",
        cleaned["exceptions"],
    )
    _write_detail_sheet(
        sheets["Technical Audit"],
        "Technical Audit",
        cleaned["technical_audit"],
    )

    exceptions_sheet = sheets["Exceptions - Review"]
    if len(cleaned["exceptions"]) > 0:
        status_column = EXCEPTION_COLUMNS.index("Review Status") + 1
        comment_column = EXCEPTION_COLUMNS.index("Reviewer Comment") + 1
        last_row = len(cleaned["exceptions"]) + 3
        validation = DataValidation(
            type="list",
            formula1='"Open,In Review,Resolved"',
            allow_blank=True,
        )
        exceptions_sheet.add_data_validation(validation)
        validation.add(
            f"{get_column_letter(status_column)}4:"
            f"{get_column_letter(status_column)}{last_row}"
        )
        for column in (status_column, comment_column):
            for row in range(4, last_row + 1):
                exceptions_sheet.cell(row, column).font = Font(color="0000FF")

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
    destination_folder: str | Path | None = None,
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Fetch verified data, validate it, and create an in-memory workbook."""
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
    report_name = f"Vendor_Reconciliation_{start_date:%Y%m%d}_{end_date:%Y%m%d}.xlsx"
    report_buffer = BytesIO()
    workbook.save(report_buffer)
    report_bytes = report_buffer.getvalue()
    report_path = None
    if destination_folder is not None:
        output_folder = Path(destination_folder)
        output_folder.mkdir(parents=True, exist_ok=True)
        report_path = output_folder / report_name
        report_path.write_bytes(report_bytes)

    row_counts = {
        sheet_name: (
            workbook[sheet_name].max_row
            if sheet_name == "Summary"
            else len(datasets[dataset_name])
        )
        for sheet_name, dataset_name in [
            ("Summary", "vendor_bills"),
            ("Vendor Master", "vendor_master"),
            ("Vendor Bills", "vendor_bills"),
            ("Vendor Payments", "vendor_payments"),
            ("Payment Allocations", "payment_allocations"),
            ("Bank Transactions", "bank_transactions"),
            ("Reconciliation Results", "reconciliation_results"),
            ("Exceptions - Review", "exceptions"),
            ("Technical Audit", "technical_audit"),
        ]
    }
    summary = summarize_vendor_report(datasets, metadata["filters"])
    return {
        "report_path": report_path,
        "report_name": report_name,
        "report_bytes": report_bytes,
        "row_counts": row_counts,
        "summary": summary,
        "validations": validations,
        "bill_rows": len(datasets["vendor_bills"]),
        "payment_rows": len(datasets["vendor_payments"]),
        "total_rows": sum(
            len(frame)
            for name, frame in datasets.items()
            if name != "technical_audit"
        ),
        "message": "Vendor Reconciliation Report generated successfully.",
    }
