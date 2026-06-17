"""Deterministic GST reconciliation against accounting-side Gold data."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery

from backend.ai.reconciliation_insights import add_gst_ai_insights


DEFAULT_PROJECT_ID = "internal-project-work-497507"
GSTR_LINES_VIEW = "finance_silver.fact_gstr_lines"
BOOKS_GST_VIEW = "finance_gold.gst_reconciliation_input"
AMOUNT_TOLERANCE = 1.0


load_dotenv()


def _get_project_id(project_id: str | None = None) -> str:
    """Resolve the BigQuery project id without requiring code changes."""
    return project_id or os.getenv("GCP_PROJECT_ID") or DEFAULT_PROJECT_ID


def _table_name(project_id: str, view_name: str) -> str:
    """Build a fully qualified BigQuery view name."""
    return f"`{project_id}.{view_name}`"


def _query_to_dataframe(client: bigquery.Client, query: str) -> pd.DataFrame:
    """Run a BigQuery query and convert rows to pandas without optional extras."""
    result = client.query(query).result()
    columns = [field.name for field in result.schema]
    rows = [dict(row.items()) for row in result]
    return pd.DataFrame(rows, columns=columns)


def fetch_gst_reconciliation_data(project_id: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch uploaded GSTR lines and accounting-side GST records from BigQuery."""
    resolved_project_id = _get_project_id(project_id)
    client = bigquery.Client(project=resolved_project_id)

    gstr_query = f"""
        SELECT
            COALESCE(
                gstr_line_id,
                CONCAT(upload_id, '-', CAST(ROW_NUMBER() OVER (PARTITION BY upload_id ORDER BY created_at, invoice_number) AS STRING))
            ) AS gstr_line_id,
            upload_id,
            supplier_gstin AS gstin,
            invoice_number,
            taxable_value,
            igst_amount,
            cgst_amount,
            sgst_amount,
            total_tax
        FROM {_table_name(resolved_project_id, GSTR_LINES_VIEW)}
        ORDER BY created_at, upload_id, invoice_number
    """
    books_query = f"""
        SELECT
            document_id AS books_record_id,
            document_number AS invoice_number,
            gstin,
            taxable_value AS books_taxable_value,
            igst AS books_igst_amount,
            cgst AS books_cgst_amount,
            sgst AS books_sgst_amount,
            total_tax AS books_tax_amount
        FROM {_table_name(resolved_project_id, BOOKS_GST_VIEW)}
        ORDER BY document_date, document_id
    """

    return _query_to_dataframe(client, gstr_query), _query_to_dataframe(client, books_query)


def _normalise_key(value: Any) -> str:
    """Normalise GSTIN and invoice numbers for matching."""
    text = "" if pd.isna(value) else str(value)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _amount(value: Any) -> float:
    """Convert possible numeric values to float."""
    return float(pd.to_numeric(pd.Series([value]), errors="coerce").fillna(0).iloc[0])


def _amounts_match(left: float, right: float, tolerance: float = AMOUNT_TOLERANCE) -> bool:
    """Return True when two amounts are equal within the allowed tolerance."""
    return abs(left - right) <= tolerance


def _numeric_series(dataframe: pd.DataFrame, column_name: str) -> pd.Series:
    """Return a numeric series with zeros for missing values."""
    if column_name not in dataframe.columns:
        return pd.Series(0, index=dataframe.index, dtype="float64")
    return pd.to_numeric(dataframe[column_name], errors="coerce").fillna(0)


def _text_series(dataframe: pd.DataFrame, column_name: str) -> pd.Series:
    """Return a text series with blanks for missing values."""
    if column_name not in dataframe.columns:
        return pd.Series("", index=dataframe.index, dtype="object")
    return dataframe[column_name].fillna("").astype(str)


def _first_existing_numeric(dataframe: pd.DataFrame, column_names: list[str]) -> pd.Series:
    """Return the first available numeric column from a list of candidates."""
    for column_name in column_names:
        if column_name in dataframe.columns:
            return _numeric_series(dataframe, column_name)
    return pd.Series(0, index=dataframe.index, dtype="float64")


def _first_existing_text(dataframe: pd.DataFrame, column_names: list[str]) -> pd.Series:
    """Return the first available text column from a list of candidates."""
    for column_name in column_names:
        if column_name in dataframe.columns:
            return _text_series(dataframe, column_name)
    return pd.Series("", index=dataframe.index, dtype="object")


def reconcile_gst_data(gstr_lines_df: pd.DataFrame, books_gst_df: pd.DataFrame) -> pd.DataFrame:
    """Match uploaded GSTR records to accounting GST records."""
    if gstr_lines_df.empty:
        raise RuntimeError("No uploaded GSTR data found in finance_silver.fact_gstr_lines.")

    gstr_df = _prepare_gstr_lines(gstr_lines_df)
    books_df = _prepare_books_gst_lines(books_gst_df)
    used_books_indexes: set[int] = set()
    result_rows = []

    for _, gstr_row in gstr_df.iterrows():
        candidate_indexes = books_df[
            (books_df["gstin_key"] == gstr_row["gstin_key"])
            & (books_df["invoice_key"] == gstr_row["invoice_key"])
        ].index

        if len(candidate_indexes) == 0:
            result_rows.append(_gstr_result_row(gstr_row, pd.Series(dtype="object"), "missing_in_books", 0.0, "No accounting GST record matched GSTIN and invoice number."))
            continue

        best_index = candidate_indexes[0]
        books_row = books_df.loc[best_index]
        used_books_indexes.add(best_index)
        amount_checks = [
            _amounts_match(gstr_row["gstr_taxable_value"], books_row["books_taxable_value"]),
            _amounts_match(gstr_row["gstr_tax_amount"], books_row["books_tax_amount"]),
            _amounts_match(gstr_row["gstr_igst_amount"], books_row["books_igst_amount"]),
            _amounts_match(gstr_row["gstr_cgst_amount"], books_row["books_cgst_amount"]),
            _amounts_match(gstr_row["gstr_sgst_amount"], books_row["books_sgst_amount"]),
        ]
        confidence = round(0.4 + (sum(amount_checks) / len(amount_checks)) * 0.6, 2)

        if all(amount_checks):
            status = "matched"
            reason = "GSTIN, invoice number, taxable value, and tax amounts matched."
        else:
            status = "mismatch"
            reason = "GSTIN and invoice number matched, but one or more tax amounts differ."

        result_rows.append(_gstr_result_row(gstr_row, books_row, status, confidence, reason))

    for books_index, books_row in books_df.iterrows():
        if books_index not in used_books_indexes:
            result_rows.append(_books_missing_in_gstr_row(books_row))

    return pd.DataFrame(result_rows)


def _prepare_gstr_lines(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Normalise uploaded GSTR columns before matching."""
    working_df = dataframe.copy().reset_index(drop=True)
    if "gstr_line_id" not in working_df.columns:
        upload = working_df.get("upload_id", pd.Series("upload", index=working_df.index)).fillna("upload").astype(str)
        row_number = pd.Series(range(1, len(working_df.index) + 1), index=working_df.index).astype(str)
        gstr_line_id = upload + "-" + row_number
    else:
        gstr_line_id = _text_series(working_df, "gstr_line_id")

    gstin = _first_existing_text(working_df, ["gstin", "supplier_gstin"])
    invoice_number = _text_series(working_df, "invoice_number")
    taxable_value = _first_existing_numeric(working_df, ["gstr_taxable_value", "taxable_value"])
    igst_amount = _first_existing_numeric(working_df, ["gstr_igst_amount", "igst_amount"])
    cgst_amount = _first_existing_numeric(working_df, ["gstr_cgst_amount", "cgst_amount"])
    sgst_amount = _first_existing_numeric(working_df, ["gstr_sgst_amount", "sgst_amount"])
    tax_amount = _first_existing_numeric(working_df, ["gstr_tax_amount", "total_tax"])
    if "gstr_tax_amount" not in working_df.columns and "total_tax" not in working_df.columns:
        tax_amount = igst_amount + cgst_amount + sgst_amount

    return pd.DataFrame(
        {
            "gstr_line_id": gstr_line_id,
            "gstin": gstin,
            "invoice_number": invoice_number,
            "gstr_taxable_value": taxable_value,
            "gstr_igst_amount": igst_amount,
            "gstr_cgst_amount": cgst_amount,
            "gstr_sgst_amount": sgst_amount,
            "gstr_tax_amount": tax_amount,
            "gstin_key": gstin.map(_normalise_key),
            "invoice_key": invoice_number.map(_normalise_key),
        }
    )


def _prepare_books_gst_lines(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Normalise accounting-side GST columns before matching."""
    working_df = dataframe.copy().reset_index(drop=True)
    if working_df.empty:
        return pd.DataFrame(
            columns=[
                "books_record_id",
                "gstin",
                "invoice_number",
                "books_taxable_value",
                "books_tax_amount",
                "books_igst_amount",
                "books_cgst_amount",
                "books_sgst_amount",
                "gstin_key",
                "invoice_key",
            ]
        )

    books_record_id = _first_existing_text(working_df, ["books_record_id", "document_id"])
    invoice_number = _first_existing_text(working_df, ["invoice_number", "document_number"])
    gstin = _text_series(working_df, "gstin")
    taxable_value = _first_existing_numeric(working_df, ["books_taxable_value", "taxable_value"])
    igst_amount = _first_existing_numeric(working_df, ["books_igst_amount", "igst"])
    cgst_amount = _first_existing_numeric(working_df, ["books_cgst_amount", "cgst"])
    sgst_amount = _first_existing_numeric(working_df, ["books_sgst_amount", "sgst"])
    tax_amount = _first_existing_numeric(working_df, ["books_tax_amount", "total_tax"])
    if "books_tax_amount" not in working_df.columns and "total_tax" not in working_df.columns:
        tax_amount = igst_amount + cgst_amount + sgst_amount

    return pd.DataFrame(
        {
            "books_record_id": books_record_id,
            "gstin": gstin,
            "invoice_number": invoice_number,
            "books_taxable_value": taxable_value,
            "books_igst_amount": igst_amount,
            "books_cgst_amount": cgst_amount,
            "books_sgst_amount": sgst_amount,
            "books_tax_amount": tax_amount,
            "gstin_key": gstin.map(_normalise_key),
            "invoice_key": invoice_number.map(_normalise_key),
        }
    )


def _gstr_result_row(gstr_row: pd.Series, books_row: pd.Series, status: str, confidence: float, reason: str) -> dict:
    """Build one GST reconciliation output row."""
    return {
        "gstr_line_id": gstr_row.get("gstr_line_id", ""),
        "gstin": gstr_row.get("gstin", ""),
        "invoice_number": gstr_row.get("invoice_number", ""),
        "gstr_taxable_value": gstr_row.get("gstr_taxable_value", 0),
        "books_taxable_value": books_row.get("books_taxable_value", None),
        "gstr_tax_amount": gstr_row.get("gstr_tax_amount", 0),
        "books_tax_amount": books_row.get("books_tax_amount", None),
        "books_record_id": books_row.get("books_record_id", ""),
        "match_status": status,
        "confidence_score": confidence,
        "match_reason": reason,
    }


def _books_missing_in_gstr_row(books_row: pd.Series) -> dict:
    """Build a books-side row that has no uploaded GSTR match."""
    return {
        "gstr_line_id": "",
        "gstin": books_row.get("gstin", ""),
        "invoice_number": books_row.get("invoice_number", ""),
        "gstr_taxable_value": None,
        "books_taxable_value": books_row.get("books_taxable_value", 0),
        "gstr_tax_amount": None,
        "books_tax_amount": books_row.get("books_tax_amount", 0),
        "books_record_id": books_row.get("books_record_id", ""),
        "match_status": "missing_in_gstr",
        "confidence_score": 0.0,
        "match_reason": "Accounting GST record was not found in uploaded GSTR data.",
    }


def _summary(results: pd.DataFrame) -> dict:
    """Build Streamlit KPI counts from GST reconciliation results."""
    return {
        "total_records": int(len(results.index)),
        "matched": int((results["match_status"] == "matched").sum()),
        "mismatch": int((results["match_status"] == "mismatch").sum()),
        "missing_in_books": int((results["match_status"] == "missing_in_books").sum()),
        "missing_in_gstr": int((results["match_status"] == "missing_in_gstr").sum()),
        "exact_matches": int((results["match_status"] == "matched").sum()),
        "mismatches": int((results["match_status"] == "mismatch").sum()),
    }


def run_gst_reconciliation(
    output_dir: str | Path,
    project_id: str | None = None,
    gstr_lines_df: pd.DataFrame | None = None,
    books_gst_df: pd.DataFrame | None = None,
) -> dict:
    """Run deterministic GST reconciliation and write an Excel export."""
    if gstr_lines_df is None or books_gst_df is None:
        gstr_lines_df, books_gst_df = fetch_gst_reconciliation_data(project_id)

    results = reconcile_gst_data(gstr_lines_df, books_gst_df)
    results, ai_metadata = add_gst_ai_insights(results)
    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)
    export_path = destination_folder / "gst_reconciliation_results.xlsx"

    with pd.ExcelWriter(export_path, engine="openpyxl") as writer:
        results.to_excel(writer, sheet_name="GST Reconciliation", index=False)

    return {
        "summary": _summary(results),
        "results": results,
        "export_path": export_path,
        "is_placeholder": False,
        "message": "GST reconciliation completed using deterministic BigQuery-backed matching.",
        **ai_metadata,
    }
