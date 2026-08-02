"""Build the source-only Jan-Mar vendor data pack from validated BigQuery views.

This module deliberately contains no reconciliation, matching, TDS, exception,
review-status, or summary logic. Workbook authoring is handled separately so
the source-query and validation contract remains independently testable.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from backend.reports.vendor_transactions_report import (
    ALLOCATIONS_VIEW,
    BANK_STATEMENT_VIEW,
    BILLS_VIEW,
    CONTACTS_VIEW,
    PAYMENTS_VIEW,
    UPLOADS_TABLE,
    VendorReportValidationError,
    _bigquery_location,
    _project_id,
    _query_parameters,
    _query_to_dataframe,
    _table_name,
    fetch_bank_statement_upload_options,
)


VENDOR_DATA_PACK_SHEETS = [
    "Vendor Master",
    "Vendor Statement",
    "Zoho Payments",
    "Bank Statement",
]

VENDOR_MASTER_COLUMNS = [
    "Vendor ID",
    "Vendor Name",
    "Vendor Status",
    "Currency",
    "GSTIN",
    "GST Treatment",
    "Place of Contact",
    "Email",
    "Phone",
    "Mobile",
    "Organization",
    "Source System",
]

VENDOR_STATEMENT_COLUMNS = [
    "Vendor Name",
    "Vendor ID",
    "Bill ID",
    "Bill Number",
    "Bill Date",
    "Due Date",
    "Currency",
    "Taxable Amount",
    "GST/Tax Amount",
    "Bill Total",
    "Amount Paid",
    "Outstanding Balance",
    "Zoho Bill Status",
]

ZOHO_PAYMENTS_COLUMNS = [
    "Vendor Name",
    "Vendor ID",
    "Payment ID",
    "Payment Date",
    "Payment Reference",
    "Payment Amount",
    "Bill ID",
    "Bill Number",
    "Amount Applied",
    "Unapplied Amount",
]

BANK_STATEMENT_COLUMNS = [
    "Transaction Date",
    "Value Date",
    "Original Narration/Description",
    "Reference Number",
    "Debit Amount",
    "Credit Amount",
    "Signed Amount",
    "Currency",
    "Account Name",
    "Masked Account Number",
    "Upload ID Suffix",
    "Source Filename",
]

DATA_PACK_COLUMNS = {
    "Vendor Master": VENDOR_MASTER_COLUMNS,
    "Vendor Statement": VENDOR_STATEMENT_COLUMNS,
    "Zoho Payments": ZOHO_PAYMENTS_COLUMNS,
    "Bank Statement": BANK_STATEMENT_COLUMNS,
}

INTERNAL_BANK_ROW_KEY = "_Statement Row Key"
TEXT_COLUMNS = {
    "Vendor ID",
    "Bill ID",
    "Bill Number",
    "Payment ID",
    "Payment Reference",
    "Reference Number",
    "Upload ID Suffix",
}


def build_vendor_data_pack_queries(
    project_id: str,
    start_date: date,
    end_date: date,
    bank_upload_id: str,
) -> dict[str, str]:
    """Return source-only queries for the four requested data-pack sheets."""
    return {
        "Vendor Master": f"""
          SELECT
            CAST(contact.contact_id AS STRING) AS `Vendor ID`,
            COALESCE(
              NULLIF(contact.company_name, ''),
              contact.contact_name
            ) AS `Vendor Name`,
            contact.status AS `Vendor Status`,
            contact.original_currency AS `Currency`,
            contact.gstin AS `GSTIN`,
            contact.gst_treatment AS `GST Treatment`,
            contact.place_of_contact AS `Place of Contact`,
            contact.email AS `Email`,
            contact.phone AS `Phone`,
            contact.mobile AS `Mobile`,
            contact.source_org_name AS `Organization`,
            'Zoho Books' AS `Source System`
          FROM {_table_name(project_id, CONTACTS_VIEW)} contact
          WHERE LOWER(contact.contact_type) = 'vendor'
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY contact.source_org_key, contact.contact_id
            ORDER BY contact.loaded_at DESC
          ) = 1
          ORDER BY `Vendor Name`, `Vendor ID`
        """,
        "Vendor Statement": f"""
          SELECT
            bill.vendor_name AS `Vendor Name`,
            CAST(bill.vendor_id AS STRING) AS `Vendor ID`,
            CAST(bill.bill_id AS STRING) AS `Bill ID`,
            CAST(bill.bill_number AS STRING) AS `Bill Number`,
            bill.bill_date AS `Bill Date`,
            bill.due_date AS `Due Date`,
            bill.original_currency AS `Currency`,
            bill.taxable_amount AS `Taxable Amount`,
            bill.tax_amount AS `GST/Tax Amount`,
            bill.total_amount AS `Bill Total`,
            bill.amount_paid AS `Amount Paid`,
            bill.balance_amount AS `Outstanding Balance`,
            bill.status AS `Zoho Bill Status`
          FROM {_table_name(project_id, BILLS_VIEW)} bill
          WHERE bill.bill_date BETWEEN @start_date AND @end_date
          ORDER BY bill.bill_date, bill.vendor_name, bill.bill_number
        """,
        "Zoho Payments": f"""
          SELECT
            payment.vendor_name AS `Vendor Name`,
            CAST(payment.vendor_id AS STRING) AS `Vendor ID`,
            CAST(payment.payment_id AS STRING) AS `Payment ID`,
            payment.payment_date AS `Payment Date`,
            CAST(payment.reference_number AS STRING) AS `Payment Reference`,
            payment.payment_amount AS `Payment Amount`,
            CAST(allocation.bill_id AS STRING) AS `Bill ID`,
            CAST(allocation.bill_number AS STRING) AS `Bill Number`,
            allocation.amount_applied AS `Amount Applied`,
            payment.unapplied_amount AS `Unapplied Amount`
          FROM {_table_name(project_id, PAYMENTS_VIEW)} payment
          LEFT JOIN {_table_name(project_id, ALLOCATIONS_VIEW)} allocation
            ON allocation.source_org_key = payment.source_org_key
           AND CAST(allocation.payment_id AS STRING)
             = CAST(payment.payment_id AS STRING)
          WHERE payment.payment_date BETWEEN @start_date AND @end_date
          ORDER BY payment.payment_date, payment.payment_id,
                   allocation.bill_number, allocation.bill_id
        """,
        "Bank Statement": f"""
          WITH upload_metadata AS (
            SELECT upload_id, original_file_name
            FROM {_table_name(project_id, UPLOADS_TABLE)}
            WHERE file_type = 'bank_statement'
            QUALIFY ROW_NUMBER() OVER (
              PARTITION BY upload_id ORDER BY uploaded_at DESC
            ) = 1
          )
          SELECT
            bank.transaction_date AS `Transaction Date`,
            bank.value_date AS `Value Date`,
            bank.narration AS `Original Narration/Description`,
            CAST(bank.reference_number AS STRING) AS `Reference Number`,
            bank.debit_amount AS `Debit Amount`,
            bank.credit_amount AS `Credit Amount`,
            COALESCE(bank.credit_amount, 0)
              - COALESCE(bank.debit_amount, 0) AS `Signed Amount`,
            CAST(NULL AS STRING) AS `Currency`,
            bank.bank_name AS `Account Name`,
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
                    LENGTH(REGEXP_REPLACE(
                      bank.account_number_masked, r'[^A-Za-z0-9]', ''
                    )) - 4,
                    4
                  )
                ),
                RIGHT(
                  REGEXP_REPLACE(
                    bank.account_number_masked, r'[^A-Za-z0-9]', ''
                  ),
                  4
                )
              )
            END AS `Masked Account Number`,
            RIGHT(CAST(bank.upload_id AS STRING), 4) AS `Upload ID Suffix`,
            metadata.original_file_name AS `Source Filename`,
            CAST(
              COALESCE(
                bank.bank_line_id,
                CONCAT(bank.upload_id, '-', CAST(bank.raw_row_number AS STRING))
              ) AS STRING
            ) AS `{INTERNAL_BANK_ROW_KEY}`
          FROM {_table_name(project_id, BANK_STATEMENT_VIEW)} bank
          LEFT JOIN upload_metadata metadata USING (upload_id)
          WHERE bank.transaction_date BETWEEN @start_date AND @end_date
            AND CAST(bank.upload_id AS STRING) = @bank_upload_id
          ORDER BY bank.transaction_date, bank.raw_row_number
        """,
    }


def select_upload_ending_in(
    uploads: list[Mapping[str, Any]],
    suffix: str,
) -> Mapping[str, Any]:
    """Select exactly one uploaded statement by case-insensitive ID suffix."""
    normalized_suffix = str(suffix or "").strip().lower()
    matches = [
        upload
        for upload in uploads
        if str(upload.get("upload_id") or "").lower().endswith(normalized_suffix)
    ]
    if len(matches) != 1:
        raise VendorReportValidationError(
            f"Expected exactly one bank upload ending in {suffix}; found {len(matches)}."
        )
    return matches[0]


def _clean_frame(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    cleaned = frame.copy().reindex(columns=columns)
    for column in TEXT_COLUMNS.intersection(cleaned.columns):
        cleaned[column] = cleaned[column].map(
            lambda value: None if pd.isna(value) else str(value)
        )
    return cleaned


def fetch_vendor_data_pack_data(
    start_date: date,
    end_date: date,
    bank_upload_suffix: str = "1ef6",
    project_id: str | None = None,
    location: str | None = None,
    client: Any | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Fetch the live four-sheet data pack from the validated BigQuery sources."""
    from google.cloud import bigquery

    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")
    resolved_project_id = _project_id(project_id)
    resolved_location = _bigquery_location(location)
    query_client = client or bigquery.Client(
        project=resolved_project_id,
        location=resolved_location,
    )
    uploads = fetch_bank_statement_upload_options(
        start_date,
        end_date,
        project_id=resolved_project_id,
        location=resolved_location,
        client=query_client,
    )
    selected_upload = select_upload_ending_in(uploads, bank_upload_suffix)
    if int(selected_upload.get("period_row_count") or 0) == 0:
        raise VendorReportValidationError(
            "The selected bank statement has no rows in the requested period."
        )
    bank_upload_id = str(selected_upload["upload_id"])
    queries = build_vendor_data_pack_queries(
        resolved_project_id,
        start_date,
        end_date,
        bank_upload_id,
    )
    forbidden_sources = (
        "finance_silver.fact_bank_transactions",
        "finance_gold.",
    )
    query_text = "\n".join(queries.values())
    if any(source in query_text for source in forbidden_sources):
        raise VendorReportValidationError(
            "Vendor data pack queries contain a prohibited source."
        )
    parameters = _query_parameters(
        start_date=start_date,
        end_date=end_date,
        bank_upload_id=bank_upload_id,
    )
    datasets: dict[str, pd.DataFrame] = {}
    for sheet_name, query in queries.items():
        query_parameters = [
            parameter
            for parameter in parameters
            if f"@{parameter.name}" in query
        ]
        frame = _query_to_dataframe(
            query_client,
            query,
            query_parameters,
            location=resolved_location,
            stage=f"Vendor data pack query '{sheet_name}'",
        )
        if sheet_name == "Bank Statement":
            public_columns = [*BANK_STATEMENT_COLUMNS, INTERNAL_BANK_ROW_KEY]
        else:
            public_columns = DATA_PACK_COLUMNS[sheet_name]
        datasets[sheet_name] = _clean_frame(frame, public_columns)
    validations = validate_vendor_data_pack(datasets, queries=queries)
    return datasets, {
        "project_id": resolved_project_id,
        "location": resolved_location,
        "start_date": start_date,
        "end_date": end_date,
        "bank_upload": dict(selected_upload),
        "queries": queries,
        "validations": validations,
    }


def validate_vendor_data_pack(
    datasets: Mapping[str, pd.DataFrame],
    queries: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run all blocking source and grain validations for the data pack."""
    if list(datasets) != VENDOR_DATA_PACK_SHEETS:
        raise VendorReportValidationError(
            "Vendor data pack must contain exactly the four requested datasets."
        )
    master = datasets["Vendor Master"]
    statement = datasets["Vendor Statement"]
    payments = datasets["Zoho Payments"]
    bank = datasets["Bank Statement"]

    duplicate_vendors = int(
        master.duplicated(["Organization", "Vendor ID"], keep=False).sum()
    )
    duplicate_bill_ids = int(statement.duplicated(["Bill ID"], keep=False).sum())
    duplicate_bank_row_keys = int(
        bank.duplicated([INTERNAL_BANK_ROW_KEY], keep=False).sum()
    )
    allocated_rows = payments[
        payments["Bill ID"].notna()
        | payments["Amount Applied"].notna()
    ]
    allocations_missing_bill_number = int(
        allocated_rows["Bill Number"].isna().sum()
        + allocated_rows["Bill Number"].astype(str).str.strip().eq("").sum()
    )
    unsafe_account_numbers = int(
        bank["Masked Account Number"]
        .dropna()
        .astype(str)
        .map(lambda value: not bool(__import__("re").fullmatch(r"\*+[A-Za-z0-9]{4}", value)))
        .sum()
    )
    upload_suffixes = sorted(
        bank["Upload ID Suffix"].dropna().astype(str).unique().tolist()
    )
    narration_nulls = int(
        bank["Original Narration/Description"].isna().sum()
    )

    if queries:
        query_text = "\n".join(queries.values())
        if "finance_silver.fact_bank_transactions" in query_text:
            raise VendorReportValidationError(
                "fact_bank_transactions is prohibited for the bank sheet."
            )
        if "finance_silver.fact_bank_statement_lines" not in query_text:
            raise VendorReportValidationError(
                "Bank Statement must use fact_bank_statement_lines."
            )
        prohibited_terms = (
            "reconciliation",
            "tds",
            "matching suggestion",
            "review_status",
            "exception",
        )
        lowered = query_text.lower()
        if any(term in lowered for term in prohibited_terms):
            raise VendorReportValidationError(
                "Vendor data pack queries include prohibited analysis content."
            )

    checks = {
        "sheet_count": 4,
        "duplicate_vendors": duplicate_vendors,
        "duplicate_vendor_statement_bill_ids": duplicate_bill_ids,
        "duplicate_bank_statement_row_keys": duplicate_bank_row_keys,
        "payment_allocations_missing_bill_number": allocations_missing_bill_number,
        "unsafe_account_numbers": unsafe_account_numbers,
        "bank_upload_suffixes": upload_suffixes,
        "bank_narration_nulls": narration_nulls,
    }
    blocking = {
        key: value
        for key, value in checks.items()
        if key
        in {
            "duplicate_vendors",
            "duplicate_vendor_statement_bill_ids",
            "duplicate_bank_statement_row_keys",
            "payment_allocations_missing_bill_number",
            "unsafe_account_numbers",
        }
        and value
    }
    if len(upload_suffixes) != 1:
        blocking["bank_upload_count"] = len(upload_suffixes)
    if blocking:
        raise VendorReportValidationError(
            "Vendor data pack validation failed: "
            + ", ".join(f"{key}={value}" for key, value in blocking.items())
        )
    return checks


def _json_value(value: Any) -> Any:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item"):
        return value.item()
    return value


def write_vendor_data_pack_json(
    datasets: Mapping[str, pd.DataFrame],
    metadata: Mapping[str, Any],
    destination: str | Path,
) -> Path:
    """Write an auditable intermediate consumed by the artifact-tool builder."""
    payload = {
        "metadata": {
            "start_date": _json_value(metadata["start_date"]),
            "end_date": _json_value(metadata["end_date"]),
            "bank_upload_suffix": str(
                metadata["bank_upload"]["upload_id"]
            )[-4:],
            "source_filename": _json_value(
                metadata["bank_upload"].get("source_file")
            ),
            "validations": metadata["validations"],
        },
        "sheets": {
            name: {
                "columns": DATA_PACK_COLUMNS[name],
                "rows": [
                    [_json_value(value) for value in row]
                    for row in (
                        frame.reindex(columns=DATA_PACK_COLUMNS[name])
                        .itertuples(index=False, name=None)
                    )
                ],
            }
            for name, frame in datasets.items()
        },
        "bank_row_keys": datasets["Bank Statement"][
            INTERNAL_BANK_ROW_KEY
        ].map(_json_value).tolist(),
    }
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--bank-upload-suffix", default="1ef6")
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    datasets, metadata = fetch_vendor_data_pack_data(
        args.start_date,
        args.end_date,
        bank_upload_suffix=args.bank_upload_suffix,
    )
    write_vendor_data_pack_json(datasets, metadata, args.output_json)
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "row_counts": {
                    name: len(frame) for name, frame in datasets.items()
                },
                "validations": metadata["validations"],
            }
        )
    )


if __name__ == "__main__":
    main()
