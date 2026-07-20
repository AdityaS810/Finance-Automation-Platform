"""Deterministic GST reconciliation against accounting-side Consume data."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from google.cloud import bigquery

from backend.ai.reconciliation_insights import add_gst_ai_insights
from backend.reconciliation.upload_registry import (
    get_project_id,
    get_upload_metadata,
    query_to_dataframe,
    table_name,
)


GSTR_LINES_VIEW = "finance_silver.fact_gstr_lines"
BOOKS_GST_VIEW = "finance_gold.gst_reconciliation_input"
AMOUNT_TOLERANCE = 1.0
GST_AI_MAX_ROWS = 10
FALLBACK_DATE_TOLERANCE_DAYS = 7
SUPPLIER_NAME_SIMILARITY = 0.82
STRICT_FORMAT_DATE_TOLERANCE_DAYS = 0
GSTR_PERIOD_ERROR = "Could not determine GSTR period from uploaded invoice dates."
AI_COLUMNS = ["ai_summary", "ai_recommendation", "ai_risk_level"]
STATUS_SORT_ORDER = {
    "amount_mismatch": 0,
    "mismatch": 0,
    "tax_component_mismatch": 1,
    "possible_match": 2,
    "missing_in_books": 3,
    "missing_in_gstr": 4,
    "matched": 5,
}
STATUS_LABELS = {
    "matched": "Matched",
    "amount_mismatch": "Amount Mismatch",
    "tax_component_mismatch": "Tax Type Mismatch",
    "possible_match": "Possible Match",
    "missing_in_books": "Missing in Books",
    "missing_in_gstr": "Missing in GSTR",
}
ACTION_REQUIRED = {
    "matched": "No action required.",
    "amount_mismatch": "Review taxable value and GST amounts in Zoho/books and GSTR before filing.",
    "tax_component_mismatch": "Check place of supply and GST tax treatment in Zoho/books.",
    "possible_match": "Confirm manually before marking as matched.",
    "missing_in_books": "Check whether the purchase/sales document exists in Zoho/books or needs to be recorded.",
    "missing_in_gstr": "Verify filing period, supplier GSTIN, and invoice number in the uploaded GSTR report.",
}
POSSIBLE_MATCH_COLUMNS = [
    "zoho_supplier_name",
    "gstr_supplier_name",
    "zoho_gstin",
    "gstr_gstin",
    "zoho_invoice_number",
    "gstr_invoice_number",
    "zoho_invoice_date",
    "gstr_invoice_date",
    "zoho_taxable_value",
    "gstr_taxable_value",
    "zoho_invoice_value",
    "gstr_invoice_value",
    "difference_amount",
    "match_reason",
    "action_required",
]
FINANCE_COLUMNS = [
    "status_label",
    "books_source_type",
    "zoho_supplier_name",
    "gstr_supplier_name",
    "zoho_gstin",
    "gstr_gstin",
    "zoho_invoice_number",
    "gstr_invoice_number",
    "zoho_invoice_date",
    "gstr_invoice_date",
    "zoho_taxable_value",
    "gstr_taxable_value",
    "zoho_invoice_value",
    "gstr_invoice_value",
    "difference_amount",
    "supplier_name",
    "party_name",
    "gstin",
    "invoice_number",
    "invoice_date",
    "return_period",
    "taxable_value_gstr",
    "taxable_value_books",
    "igst_gstr",
    "igst_books",
    "cgst_gstr",
    "cgst_books",
    "sgst_gstr",
    "sgst_books",
    "invoice_value_gstr",
    "invoice_value_books",
    "raw_invoice_value",
    "calculated_invoice_value",
    "invoice_value_source",
    "amount_difference",
    "match_reason",
    "action_required",
    "ai_summary",
    "ai_recommendation",
    "ai_risk_level",
    "selected_upload_id",
    "selected_file_name",
    "reconciliation_timestamp",
]
TECHNICAL_AUDIT_COLUMNS = FINANCE_COLUMNS + [
    "match_status",
    "match_level",
    "confidence_score",
    "source_side",
    "selected_upload_min_date",
    "selected_upload_max_date",
    "books_rows_before_period_filter",
    "books_rows_after_period_filter",
    "gstr_line_id",
    "books_record_id",
    "gstr_upload_id",
    "normalized_gstin",
    "normalized_invoice_number_gstr",
    "normalized_invoice_number_books",
    "cleaned_invoice_number_gstr",
    "cleaned_invoice_number_books",
    "tax_amount_gstr",
    "tax_amount_books",
    "gstr_duplicate_count",
    "books_duplicate_count",
    "gstr_raw_ids",
    "books_raw_ids",
    "gstr_created_at",
    "books_party_id",
    "books_status",
    "source_org_key",
    "source_org_id",
    "source_org_name",
    "run_id",
    "source_record_id",
    "loaded_at",
    "gstr_raw_rows",
    "books_raw_rows",
]


def fetch_gst_reconciliation_data(
    project_id: str | None = None,
    upload_id: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Fetch selected uploaded GSTR rows and latest accounting GST records."""
    resolved_project_id = get_project_id(project_id)
    selected_upload = get_upload_metadata("gstr", upload_id, resolved_project_id)
    if not selected_upload:
        raise RuntimeError("Please upload a GSTR file first.")

    client = bigquery.Client(project=resolved_project_id)

    gstr_query = f"""
        SELECT
            COALESCE(
                gstr_line_id,
                CONCAT(upload_id, '-', CAST(ROW_NUMBER() OVER (PARTITION BY upload_id ORDER BY created_at, invoice_number) AS STRING))
            ) AS gstr_line_id,
            upload_id,
            supplier_gstin AS gstin,
            supplier_name,
            invoice_number,
            invoice_date,
            period AS return_period,
            taxable_value,
            igst_amount,
            cgst_amount,
            sgst_amount,
            total_tax,
            invoice_value,
            created_at
        FROM {table_name(resolved_project_id, GSTR_LINES_VIEW)}
        WHERE upload_id = @upload_id
        ORDER BY invoice_date, invoice_number, gstr_line_id
    """
    books_count_query = f"""
        SELECT COUNT(1) AS books_rows_before_period_filter
        FROM {table_name(resolved_project_id, BOOKS_GST_VIEW)}
    """
    books_query = f"""
        SELECT
            document_id AS books_record_id,
            source_type AS books_source_type,
            party_id AS books_party_id,
            document_number AS invoice_number,
            document_date AS books_invoice_date,
            party_name,
            gstin,
            taxable_value AS books_taxable_value,
            igst AS books_igst_amount,
            cgst AS books_cgst_amount,
            sgst AS books_sgst_amount,
            total_tax AS books_tax_amount,
            invoice_value AS books_invoice_value,
            status AS books_status,
            source_org_key,
            source_org_id,
            source_org_name,
            run_id,
            source_record_id,
            loaded_at
        FROM {table_name(resolved_project_id, BOOKS_GST_VIEW)}
        WHERE document_date BETWEEN @period_start AND @period_end
        ORDER BY document_date, document_number, document_id
    """

    gstr_df = query_to_dataframe(
        client,
        gstr_query,
        [bigquery.ScalarQueryParameter("upload_id", "STRING", selected_upload["upload_id"])],
    )
    period_start, period_end = _selected_gstr_period(gstr_df)
    books_before_df = query_to_dataframe(client, books_count_query)
    books_rows_before = int(books_before_df["books_rows_before_period_filter"].iloc[0]) if not books_before_df.empty else 0
    books_df = query_to_dataframe(
        client,
        books_query,
        [
            bigquery.ScalarQueryParameter("period_start", "DATE", period_start),
            bigquery.ScalarQueryParameter("period_end", "DATE", period_end),
        ],
    )
    period_metadata = _period_filter_metadata(period_start, period_end, books_rows_before, len(books_df.index))
    return gstr_df, books_df, period_metadata


def reconcile_gst_data(
    gstr_lines_df: pd.DataFrame,
    books_gst_df: pd.DataFrame,
    amount_tolerance: float = AMOUNT_TOLERANCE,
    selected_upload_id: str | None = None,
    fallback_date_tolerance_days: int = FALLBACK_DATE_TOLERANCE_DAYS,
) -> pd.DataFrame:
    """Match uploaded GSTR rows to accounting GST records using deterministic rules."""
    selected_gstr_df = _filter_selected_gstr_upload(gstr_lines_df, selected_upload_id)
    if selected_gstr_df.empty:
        raise RuntimeError("No rows found for the selected GSTR upload. Please upload a GSTR file first.")

    gstr_df = _prepare_gstr_lines(selected_gstr_df)
    books_df = _prepare_books_gst_lines(books_gst_df)
    used_books_indexes: set[int] = set()
    result_rows = []

    for _, gstr_row in gstr_df.iterrows():
        available_books_df = books_df.loc[[index for index in books_df.index if index not in used_books_indexes]]
        match = _priority_ladder_books_match(
            gstr_row,
            available_books_df,
            amount_tolerance=amount_tolerance,
            fallback_date_tolerance_days=fallback_date_tolerance_days,
        )

        if match:
            used_books_indexes.add(match["books_index"])
            result_rows.append(
                _gstr_result_row(
                    gstr_row,
                    match["books_row"],
                    match["match_status"],
                    match["confidence_score"],
                    match["match_reason"],
                    match["amount_difference"],
                    match["match_level"],
                )
            )
            continue

        result_rows.append(
            _gstr_result_row(
                gstr_row,
                pd.Series({"missing_in_books_action_required": _missing_in_books_action_required(books_df)}),
                "missing_in_books",
                0.0,
                "Invoice exists in uploaded GSTR but was not found in Zoho/books.",
                None,
                "P5_MISSING_IN_BOOKS",
            )
        )

    for books_index, books_row in books_df.iterrows():
        if books_index not in used_books_indexes:
            safeguard_match = _missing_in_gstr_safeguard_candidate(
                books_row,
                gstr_df,
                amount_tolerance=amount_tolerance,
                fallback_date_tolerance_days=fallback_date_tolerance_days,
            )
            if safeguard_match is not None:
                result_rows.append(
                    _gstr_result_row(
                        safeguard_match["gstr_row"],
                        books_row,
                        safeguard_match.get("match_status", "possible_match"),
                        safeguard_match["confidence_score"],
                        safeguard_match["match_reason"],
                        safeguard_match["amount_difference"],
                        safeguard_match["match_level"],
                    )
                )
            else:
                result_rows.append(_books_missing_in_gstr_row(books_row))

    return _sort_gst_results(pd.DataFrame(result_rows))


def _priority_ladder_books_match(
    gstr_row: pd.Series,
    candidate_df: pd.DataFrame,
    amount_tolerance: float,
    fallback_date_tolerance_days: int,
) -> dict[str, Any] | None:
    """Try GST matching priorities P1-P5 and return the first accepted candidate."""
    if candidate_df.empty:
        return None

    p1_candidates = candidate_df[
        (candidate_df["gstin_key"] == gstr_row["gstin_key"])
        & (candidate_df["invoice_key"] == gstr_row["invoice_key"])
        & (candidate_df["gstin_key"] != "")
        & (candidate_df["invoice_key"] != "")
    ]
    p1_candidates = _nearby_date_candidates(
        gstr_row,
        p1_candidates,
        fallback_date_tolerance_days,
    )
    if not p1_candidates.empty:
        books_index = _best_books_gst_candidate(gstr_row, p1_candidates, amount_tolerance)
        books_row = candidate_df.loc[books_index]
        amounts_match, amount_difference = _gst_amounts_match(gstr_row, books_row, amount_tolerance)
        if amounts_match:
            return _match_result(
                books_index,
                books_row,
                "matched",
                "P1_EXACT",
                1.0,
                _matched_amount_reason(books_row)
                or "Invoice found in Zoho/books and selected GSTR with same GSTIN, invoice number, nearby date, and values within tolerance.",
                amount_difference,
            )
        if _has_missing_gst_amount_value(gstr_row, books_row):
            reason = (
                "GSTIN, invoice number, and invoice date matched, but one or more "
                "taxable/tax/invoice values are missing in GSTR or books."
            )
        if _tax_component_type_differs(gstr_row, books_row, amount_tolerance):
            return _match_result(
                books_index,
                books_row,
                "tax_component_mismatch",
                "P1_EXACT",
                0.86,
                _tax_component_type_differs_reason(gstr_row, books_row),
                amount_difference,
            )
        return _match_result(
            books_index,
            books_row,
            "amount_mismatch",
            "P1_EXACT",
            0.85,
            reason
            if _has_missing_gst_amount_value(gstr_row, books_row)
            else (
                "GSTIN, invoice number, and invoice date matched, but "
                f"taxable/tax/invoice values differ by INR {amount_difference:.2f}."
            ),
            amount_difference,
        )

    p2_candidates = candidate_df[
        (candidate_df["gstin_key"] == gstr_row["gstin_key"])
        & (candidate_df["invoice_key"] == gstr_row["invoice_key"])
        & (candidate_df["gstin_key"] != "")
        & (candidate_df["invoice_key"] != "")
    ]
    p2_match = _best_invoice_candidate(gstr_row, p2_candidates, amount_tolerance)
    if p2_match is not None:
        books_index, books_row, amounts_match, amount_difference = p2_match
        if amounts_match:
            return _match_result(
                books_index,
                books_row,
                "matched",
                "P2_STRONG",
                0.92,
                _matched_amount_reason(books_row)
                or "GSTIN and invoice number matched in Zoho/books and selected GSTR; values are within tolerance.",
                amount_difference,
            )
        if _tax_component_type_differs(gstr_row, books_row, amount_tolerance):
            return _match_result(
                books_index,
                books_row,
                "tax_component_mismatch",
                "P2_STRONG",
                0.83,
                _tax_component_type_differs_reason(gstr_row, books_row),
                amount_difference,
            )
        return _match_result(
            books_index,
            books_row,
            "amount_mismatch",
            "P2_STRONG",
            0.82,
            f"GSTIN and invoice number matched, but taxable/tax/invoice values differ by INR {amount_difference:.2f}.",
            amount_difference,
        )

    p3_candidates = candidate_df[
        (candidate_df["clean_invoice_key"] == gstr_row["clean_invoice_key"])
        & (candidate_df["clean_invoice_key"] != "")
    ]
    p3_candidates = _same_or_missing_gstin_candidates(gstr_row, p3_candidates)
    p3_candidates = _nearby_date_candidates(
        gstr_row,
        p3_candidates,
        fallback_date_tolerance_days,
    )
    p3_match = _best_invoice_candidate(gstr_row, p3_candidates, amount_tolerance)
    if p3_match is not None:
        books_index, books_row, amounts_match, amount_difference = p3_match
        p3_reason = _invoice_format_match_reason(gstr_row, books_row)
        if amounts_match:
            return _match_result(
                books_index,
                books_row,
                "matched",
                "P3_FORMAT",
                0.88,
                _matched_amount_reason(books_row)
                or (
                    p3_reason
                    if p3_reason == "Invoice exists in GSTR with different invoice number format"
                    else "Invoice number matched after removing spaces and separators; date and values are within tolerance."
                ),
                amount_difference,
            )
        if _tax_component_type_differs(gstr_row, books_row, amount_tolerance):
            return _match_result(
                books_index,
                books_row,
                "tax_component_mismatch",
                "P3_FORMAT",
                0.79,
                _tax_component_type_differs_reason(gstr_row, books_row),
                amount_difference,
            )
        return _match_result(
            books_index,
            books_row,
            "amount_mismatch",
            "P3_FORMAT",
            0.78,
            f"Invoice number matched after formatting cleanup, but taxable/tax/invoice values differ by INR {amount_difference:.2f}.",
            amount_difference,
        )

    p4_candidates = candidate_df[
        (candidate_df["clean_invoice_key"] == gstr_row["clean_invoice_key"])
        & (candidate_df["clean_invoice_key"] != "")
    ]
    p4_match = _best_invoice_candidate(gstr_row, p4_candidates, amount_tolerance)
    if p4_match is not None:
        books_index, books_row, _amounts_match, amount_difference = p4_match
        return _match_result(
            books_index,
            books_row,
            "possible_match",
            "P4_WEAK_INVOICE",
            0.62,
            "Invoice exists in selected GSTR but GSTIN/date/value differs or is missing.",
            amount_difference,
        )

    p4_token_candidates = _invoice_token_candidates(gstr_row, candidate_df)
    p4_token_candidates = _nearby_date_candidates(
        gstr_row,
        p4_token_candidates,
        fallback_date_tolerance_days,
    )
    p4_token_match = _best_invoice_candidate(gstr_row, p4_token_candidates, amount_tolerance)
    if p4_token_match is not None:
        books_index, books_row, amounts_match, amount_difference = p4_token_match
        if _safe_format_only_match(gstr_row, books_row, amount_tolerance):
            status = "matched"
            confidence = 0.90
            reason = "Matched using invoice format normalization; GSTIN, date, taxable value, and invoice value match."
        else:
            status = "possible_match"
            confidence = 0.60
            reason = _invoice_format_match_reason(gstr_row, books_row)
        return _match_result(
            books_index,
            books_row,
            status,
            "P4_INVOICE_TOKEN",
            confidence,
            reason,
            amount_difference,
        )

    p5_match = _best_amount_date_supplier_candidate(
        gstr_row,
        candidate_df,
        amount_tolerance=amount_tolerance,
        fallback_date_tolerance_days=fallback_date_tolerance_days,
    )
    if p5_match is not None:
        books_index, books_row, amount_difference = p5_match
        reason = (
            "Possible match; GSTIN differs, possible manual typo."
            if _gstin_possible_typo(gstr_row, books_row)
            else "Possible match based on invoice value, nearby date, and supplier name similarity; manual review required."
        )
        return _match_result(
            books_index,
            books_row,
            "possible_match",
            "P5_AMOUNT_DATE_CANDIDATE",
            0.50,
            reason,
            amount_difference,
        )

    return None


def _match_result(
    books_index: int,
    books_row: pd.Series,
    status: str,
    match_level: str,
    confidence_score: float,
    reason: str,
    amount_difference: float,
) -> dict[str, Any]:
    """Build an internal matched candidate payload."""
    return {
        "books_index": books_index,
        "books_row": books_row,
        "match_status": status,
        "match_level": match_level,
        "confidence_score": confidence_score,
        "match_reason": reason,
        "amount_difference": amount_difference,
    }


def _filter_selected_gstr_upload(dataframe: pd.DataFrame, selected_upload_id: str | None) -> pd.DataFrame:
    """Return only the selected GSTR upload rows when upload IDs are available."""
    if dataframe.empty or not selected_upload_id or "upload_id" not in dataframe.columns:
        return dataframe.copy()

    upload_ids = dataframe["upload_id"].fillna("").astype(str)
    return dataframe[upload_ids == str(selected_upload_id)].copy()


def _selected_gstr_period(dataframe: pd.DataFrame) -> tuple[Any, Any]:
    """Return selected upload min/max invoice dates as date objects."""
    if dataframe.empty or "invoice_date" not in dataframe.columns:
        raise RuntimeError(GSTR_PERIOD_ERROR)

    parsed_dates = pd.to_datetime(dataframe["invoice_date"], errors="coerce").dropna()
    if parsed_dates.empty:
        raise RuntimeError(GSTR_PERIOD_ERROR)

    return parsed_dates.min().date(), parsed_dates.max().date()


def _filter_books_to_gstr_period(
    books_gst_df: pd.DataFrame,
    period_start: Any,
    period_end: Any,
) -> tuple[pd.DataFrame, int, int]:
    """Filter books rows to the selected uploaded GSTR invoice-date period."""
    books_rows_before = len(books_gst_df.index)
    if books_gst_df.empty:
        return books_gst_df.copy(), books_rows_before, 0

    date_source = _first_existing_text(books_gst_df, ["books_invoice_date", "document_date"])
    parsed_dates = pd.to_datetime(date_source, errors="coerce")
    period_start_ts = pd.to_datetime(period_start)
    period_end_ts = pd.to_datetime(period_end)
    period_mask = parsed_dates.between(period_start_ts, period_end_ts, inclusive="both")
    filtered_df = books_gst_df[period_mask.fillna(False)].copy()
    return filtered_df, books_rows_before, len(filtered_df.index)


def _period_filter_metadata(
    period_start: Any,
    period_end: Any,
    books_rows_before: int,
    books_rows_after: int,
) -> dict[str, Any]:
    """Build stable period filter metadata for UI, Excel, and audit columns."""
    return {
        "selected_upload_min_date": str(period_start),
        "selected_upload_max_date": str(period_end),
        "books_rows_before_period_filter": int(books_rows_before),
        "books_rows_after_period_filter": int(books_rows_after),
    }


def _log_period_filter(upload_metadata: dict[str, Any], period_metadata: dict[str, Any]) -> None:
    """Print required period-filter diagnostics for debugging."""
    print(f"[GST Reconciliation] selected upload_id: {upload_metadata.get('upload_id', '')}")
    print(f"[GST Reconciliation] selected file name: {upload_metadata.get('file_name', '')}")
    print(f"[GST Reconciliation] selected upload min date: {period_metadata.get('selected_upload_min_date', '')}")
    print(f"[GST Reconciliation] selected upload max date: {period_metadata.get('selected_upload_max_date', '')}")
    print(
        "[GST Reconciliation] books rows before period filter: "
        f"{period_metadata.get('books_rows_before_period_filter', 0)}"
    )
    print(
        "[GST Reconciliation] books rows after period filter: "
        f"{period_metadata.get('books_rows_after_period_filter', 0)}"
    )


def _prepare_gstr_lines(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Normalize and deduplicate selected uploaded GSTR rows before matching."""
    working_df = dataframe.copy().reset_index(drop=True)
    if "gstr_line_id" not in working_df.columns:
        upload = working_df.get("upload_id", pd.Series("upload", index=working_df.index)).fillna("upload").astype(str)
        row_number = pd.Series(range(1, len(working_df.index) + 1), index=working_df.index).astype(str)
        gstr_line_id = upload + "-" + row_number
    else:
        gstr_line_id = _text_series(working_df, "gstr_line_id")
        blank_id_mask = gstr_line_id.str.strip() == ""
        gstr_line_id.loc[blank_id_mask] = "gstr-row-" + (working_df.index[blank_id_mask] + 1).astype(str)

    gstin = _first_existing_text(working_df, ["gstin", "supplier_gstin"]).map(_display_gstin)
    invoice_number = _first_existing_text(working_df, ["invoice_number"]).map(_display_invoice_number)
    invoice_date = _date_series(working_df, ["invoice_date"])
    return_period = _first_existing_text(working_df, ["return_period", "period"])
    supplier_name = _first_existing_text(working_df, ["supplier_name", "party_name"])
    taxable_value = _money_series(working_df, ["gstr_taxable_value", "taxable_value"])
    igst_amount = _money_series(working_df, ["gstr_igst_amount", "igst_amount", "igst"])
    cgst_amount = _money_series(working_df, ["gstr_cgst_amount", "cgst_amount", "cgst"])
    sgst_amount = _money_series(working_df, ["gstr_sgst_amount", "sgst_amount", "sgst"])
    tax_amount = _money_series(working_df, ["gstr_tax_amount", "total_tax"])
    tax_amount = _fill_missing_or_zero_money(tax_amount, _sum_money_series([igst_amount, cgst_amount, sgst_amount]))
    invoice_value = _money_series(working_df, ["gstr_invoice_value", "invoice_value"])
    invoice_value = _fill_missing_or_zero_money(
        invoice_value,
        _sum_money_series([taxable_value, tax_amount], require_all=True),
    )

    prepared_df = pd.DataFrame(
        {
            "gstr_line_id": gstr_line_id,
            "upload_id": _first_existing_text(working_df, ["upload_id"]),
            "supplier_name": supplier_name,
            "gstin": gstin,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "return_period": return_period,
            "gstr_taxable_value": taxable_value,
            "gstr_igst_amount": igst_amount,
            "gstr_cgst_amount": cgst_amount,
            "gstr_sgst_amount": sgst_amount,
            "gstr_tax_amount": tax_amount,
            "gstr_invoice_value": invoice_value,
            "gstr_created_at": _first_existing_text(working_df, ["created_at"]),
            "gstr_raw_row_json": _raw_json_series(working_df),
            "gstin_key": gstin.map(_normalise_gstin_key),
            "invoice_key": invoice_number.map(_normalise_invoice_key),
            "clean_invoice_key": invoice_number.map(_clean_invoice_key),
            "stripped_hash_invoice_key": invoice_number.map(strip_hash_invoice).map(_normalise_invoice_key),
            "numeric_invoice_token": invoice_number.map(numeric_invoice_token),
            "zero_tolerant_invoice_token": invoice_number.map(_zero_tolerant_invoice_token),
            "supplier_name_key": supplier_name.map(_normalise_name_key),
        }
    )
    return _deduplicate_gst_records(prepared_df, "gstr")


def _prepare_books_gst_lines(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Normalize and deduplicate accounting-side GST records before matching."""
    working_df = dataframe.copy().reset_index(drop=True)
    if working_df.empty:
        return pd.DataFrame(
            columns=[
                "books_record_id",
                "books_source_type",
                "books_party_id",
                "party_name",
                "gstin",
                "invoice_number",
                "books_invoice_date",
                "books_taxable_value",
                "books_igst_amount",
                "books_cgst_amount",
                "books_sgst_amount",
                "books_tax_amount",
                "books_invoice_value",
                "books_raw_invoice_value",
                "books_calculated_invoice_value",
                "books_invoice_value_source",
                "books_status",
                "source_org_key",
                "source_org_id",
                "source_org_name",
                "run_id",
                "source_record_id",
                "loaded_at",
                "books_raw_ids",
                "books_duplicate_count",
                "books_raw_rows",
                "gstin_key",
                "invoice_key",
                "clean_invoice_key",
                "stripped_hash_invoice_key",
                "numeric_invoice_token",
                "zero_tolerant_invoice_token",
                "party_name_key",
            ]
        )

    books_record_id = _first_existing_text(working_df, ["books_record_id", "document_id"])
    invoice_number = _first_existing_text(working_df, ["invoice_number", "document_number"]).map(_display_invoice_number)
    gstin = _first_existing_text(working_df, ["gstin"]).map(_display_gstin)
    taxable_value = _money_series(working_df, ["books_taxable_value", "taxable_value"])
    igst_amount = _money_series(working_df, ["books_igst_amount", "igst"])
    cgst_amount = _money_series(working_df, ["books_cgst_amount", "cgst"])
    sgst_amount = _money_series(working_df, ["books_sgst_amount", "sgst"])
    tax_amount = _money_series(working_df, ["books_tax_amount", "total_tax"])
    tax_amount = _fill_missing_or_zero_money(tax_amount, _sum_money_series([igst_amount, cgst_amount, sgst_amount]))
    raw_invoice_value = _money_series(working_df, ["raw_invoice_value", "books_raw_invoice_value", "books_invoice_value", "invoice_value"])
    invoice_value, calculated_invoice_value, invoice_value_source = _books_invoice_value_for_gst(
        taxable_value,
        igst_amount,
        cgst_amount,
        sgst_amount,
        tax_amount,
        raw_invoice_value,
        AMOUNT_TOLERANCE,
    )

    prepared_df = pd.DataFrame(
        {
            "books_record_id": books_record_id,
            "books_source_type": _first_existing_text(working_df, ["books_source_type", "source_type"]),
            "books_party_id": _first_existing_text(working_df, ["books_party_id", "party_id"]),
            "party_name": _first_existing_text(working_df, ["party_name", "supplier_name", "customer_name", "vendor_name"]),
            "gstin": gstin,
            "invoice_number": invoice_number,
            "books_invoice_date": _date_series(working_df, ["books_invoice_date", "document_date", "invoice_date"]),
            "books_taxable_value": taxable_value,
            "books_igst_amount": igst_amount,
            "books_cgst_amount": cgst_amount,
            "books_sgst_amount": sgst_amount,
            "books_tax_amount": tax_amount,
            "books_invoice_value": invoice_value,
            "books_raw_invoice_value": raw_invoice_value,
            "books_calculated_invoice_value": calculated_invoice_value,
            "books_invoice_value_source": invoice_value_source,
            "books_status": _first_existing_text(working_df, ["books_status", "status"]),
            "source_org_key": _first_existing_text(working_df, ["source_org_key"]),
            "source_org_id": _first_existing_text(working_df, ["source_org_id"]),
            "source_org_name": _first_existing_text(working_df, ["source_org_name"]),
            "run_id": _first_existing_text(working_df, ["run_id"]),
            "source_record_id": _first_existing_text(working_df, ["source_record_id"]),
            "loaded_at": _first_existing_text(working_df, ["loaded_at"]),
            "books_raw_row_json": _raw_json_series(working_df),
            "gstin_key": gstin.map(_normalise_gstin_key),
            "invoice_key": invoice_number.map(_normalise_invoice_key),
            "clean_invoice_key": invoice_number.map(_clean_invoice_key),
            "stripped_hash_invoice_key": invoice_number.map(strip_hash_invoice).map(_normalise_invoice_key),
            "numeric_invoice_token": invoice_number.map(numeric_invoice_token),
            "zero_tolerant_invoice_token": invoice_number.map(_zero_tolerant_invoice_token),
            "party_name_key": _first_existing_text(
                working_df,
                ["party_name", "supplier_name", "customer_name", "vendor_name"],
            ).map(_normalise_name_key),
        }
    )
    return _deduplicate_gst_records(prepared_df, "books")


def _deduplicate_gst_records(dataframe: pd.DataFrame, source: str) -> pd.DataFrame:
    """Keep one row per normalized GSTIN, invoice, date, taxable, IGST, CGST, and SGST."""
    if dataframe.empty:
        return dataframe

    working_df = dataframe.copy()
    if source == "gstr":
        amount_columns = ["gstr_taxable_value", "gstr_igst_amount", "gstr_cgst_amount", "gstr_sgst_amount"]
        dedupe_columns = ["gstin_key", "invoice_key", "invoice_date"] + [f"{column}_key" for column in amount_columns]
        id_column = "gstr_line_id"
    else:
        amount_columns = ["books_taxable_value", "books_igst_amount", "books_cgst_amount", "books_sgst_amount"]
        dedupe_columns = ["gstin_key", "invoice_key", "books_invoice_date"] + [f"{column}_key" for column in amount_columns]
        id_column = "books_record_id"

    amount_key_columns = {
        f"{column_name}_key": pd.to_numeric(working_df[column_name], errors="coerce").round(2)
        for column_name in amount_columns
    }
    working_df = working_df.assign(**amount_key_columns)

    if id_column in working_df.columns:
        working_df = working_df.drop_duplicates(subset=[id_column], keep="first")

    raw_row_column = f"{source}_raw_row_json"
    raw_ids_column = f"{source}_raw_ids"
    duplicate_count_column = f"{source}_duplicate_count"
    raw_rows_column = f"{source}_raw_rows"
    aggregated_rows = []
    for _, group_df in working_df.groupby(dedupe_columns, dropna=False, sort=False):
        first_row = group_df.iloc[0].copy()
        first_row[duplicate_count_column] = int(len(group_df.index))
        first_row[raw_ids_column] = _join_unique_text(group_df.get(id_column, pd.Series("", index=group_df.index)))
        first_row[raw_rows_column] = "\n".join(group_df.get(raw_row_column, pd.Series("", index=group_df.index)).astype(str))
        aggregated_rows.append(first_row)

    working_df = pd.DataFrame(aggregated_rows)
    key_columns = [
        column_name
        for column_name in working_df.columns
        if column_name.endswith("_key")
        and column_name
        not in {
            "gstin_key",
            "invoice_key",
            "clean_invoice_key",
            "stripped_hash_invoice_key",
            "numeric_invoice_token",
            "zero_tolerant_invoice_token",
            "supplier_name_key",
            "party_name_key",
        }
    ]
    drop_columns = key_columns + [raw_row_column]
    return working_df.drop(columns=[column for column in drop_columns if column in working_df.columns]).reset_index(drop=True)


def _best_books_gst_candidate(
    gstr_row: pd.Series,
    candidate_df: pd.DataFrame,
    amount_tolerance: float = AMOUNT_TOLERANCE,
) -> int:
    """Choose the closest books candidate when duplicate invoice keys exist."""
    best_index = candidate_df.index[0]
    best_difference = float("inf")
    for books_index, books_row in candidate_df.iterrows():
        _, difference = _gst_amounts_match(gstr_row, books_row, amount_tolerance)
        if difference < best_difference:
            best_difference = difference
            best_index = books_index
    return best_index


def _best_amount_matching_candidate(
    gstr_row: pd.Series,
    candidate_df: pd.DataFrame,
    amount_tolerance: float,
) -> tuple[int, pd.Series, float] | None:
    """Return the best candidate whose taxable/tax/invoice values match within tolerance."""
    if candidate_df.empty:
        return None

    best_index = None
    best_row = None
    best_date_difference = float("inf")
    best_amount_difference = float("inf")
    for books_index, books_row in candidate_df.iterrows():
        amounts_match, amount_difference = _gst_amounts_match(gstr_row, books_row, amount_tolerance)
        if not amounts_match:
            continue

        date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
        date_sort_value = float("inf") if date_difference is None else date_difference
        if date_sort_value < best_date_difference or (
            date_sort_value == best_date_difference and amount_difference < best_amount_difference
        ):
            best_index = books_index
            best_row = books_row
            best_date_difference = date_sort_value
            best_amount_difference = amount_difference

    if best_index is None or best_row is None:
        return None
    return best_index, best_row, round(best_amount_difference, 2)


def _best_invoice_candidate(
    gstr_row: pd.Series,
    candidate_df: pd.DataFrame,
    amount_tolerance: float,
) -> tuple[int, pd.Series, bool, float] | None:
    """Return the closest invoice candidate whether values match or differ."""
    if candidate_df.empty:
        return None

    best_index = None
    best_row = None
    best_amounts_match = False
    best_date_difference = float("inf")
    best_amount_difference = float("inf")
    for books_index, books_row in candidate_df.iterrows():
        amounts_match, amount_difference = _gst_amounts_match(gstr_row, books_row, amount_tolerance)
        date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
        date_sort_value = float("inf") if date_difference is None else date_difference
        if best_index is None or date_sort_value < best_date_difference or (
            date_sort_value == best_date_difference and amount_difference < best_amount_difference
        ):
            best_index = books_index
            best_row = books_row
            best_amounts_match = amounts_match
            best_date_difference = date_sort_value
            best_amount_difference = amount_difference

    if best_index is None or best_row is None:
        return None
    return best_index, best_row, best_amounts_match, round(best_amount_difference, 2)


def _nearby_date_candidates(
    gstr_row: pd.Series,
    candidate_df: pd.DataFrame,
    fallback_date_tolerance_days: int,
) -> pd.DataFrame:
    """Return candidates with exact, close, or missing dates."""
    if candidate_df.empty:
        return candidate_df

    keep_indexes = []
    for books_index, books_row in candidate_df.iterrows():
        date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
        if date_difference is None or date_difference <= fallback_date_tolerance_days:
            keep_indexes.append(books_index)
    return candidate_df.loc[keep_indexes]


def _same_or_missing_gstin_candidates(gstr_row: pd.Series, candidate_df: pd.DataFrame) -> pd.DataFrame:
    """Keep format matches only when GSTIN agrees or one side is blank."""
    if candidate_df.empty:
        return candidate_df

    gstr_gstin = str(gstr_row.get("gstin_key", "") or "")
    if not gstr_gstin:
        return candidate_df
    return candidate_df[
        (candidate_df["gstin_key"] == gstr_gstin)
        | (candidate_df["gstin_key"] == "")
    ]


def _invoice_token_candidates(gstr_row: pd.Series, candidate_df: pd.DataFrame) -> pd.DataFrame:
    """Return same-GSTIN candidates with invoice token, suffix, or zero-tolerant evidence."""
    if candidate_df.empty:
        return candidate_df

    gstr_gstin = str(gstr_row.get("gstin_key", "") or "")
    if not gstr_gstin:
        return candidate_df.iloc[0:0]

    keep_indexes = []
    for books_index, books_row in candidate_df.iterrows():
        if str(books_row.get("gstin_key", "") or "") != gstr_gstin:
            continue
        if _invoice_format_evidence_matches(gstr_row, books_row):
            keep_indexes.append(books_index)
    return candidate_df.loc[keep_indexes]


def _invoice_format_evidence_matches(gstr_row: pd.Series, books_row: pd.Series) -> bool:
    """Return True when invoice numbers differ only by known format patterns."""
    gstr_invoice = gstr_row.get("invoice_number", "")
    books_invoice = books_row.get("invoice_number", "")

    gstr_compact = str(gstr_row.get("clean_invoice_key", "") or "")
    books_compact = str(books_row.get("clean_invoice_key", "") or "")
    if gstr_compact and books_compact and (gstr_compact.endswith(books_compact) or books_compact.endswith(gstr_compact)):
        return _invoice_token_context_matches(gstr_invoice, books_invoice)

    gstr_token = str(gstr_row.get("numeric_invoice_token", "") or "")
    books_token = str(books_row.get("numeric_invoice_token", "") or "")
    if gstr_token and books_token and _zero_tolerant_numeric_equal(gstr_token, books_token):
        return _invoice_token_context_matches(gstr_invoice, books_invoice)

    return False


def _invoice_token_context_matches(left_invoice: Any, right_invoice: Any) -> bool:
    """Avoid treating unrelated invoice prefixes as equivalent just because digits match."""
    left_compact = compact_invoice_number(left_invoice)
    right_compact = compact_invoice_number(right_invoice)
    left_token = numeric_invoice_token(left_invoice)
    right_token = numeric_invoice_token(right_invoice)
    if not left_token or not right_token:
        return False

    if left_compact == left_token or right_compact == right_token:
        return True

    left_skeleton = _invoice_alpha_skeleton(left_invoice)
    right_skeleton = _invoice_alpha_skeleton(right_invoice)
    return bool(left_skeleton and left_skeleton == right_skeleton)


def _safe_format_only_match(gstr_row: pd.Series, books_row: pd.Series, amount_tolerance: float) -> bool:
    """Return True when invoice format is the only meaningful difference."""
    if not _strong_invoice_format_evidence(gstr_row, books_row):
        return False

    same_gstin = str(gstr_row.get("gstin_key", "") or "") == str(books_row.get("gstin_key", "") or "")
    if not same_gstin:
        return False

    date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
    if date_difference is None or date_difference > STRICT_FORMAT_DATE_TOLERANCE_DAYS:
        return False

    compared_pairs = [
        (gstr_row.get("gstr_taxable_value"), books_row.get("books_taxable_value")),
        (gstr_row.get("gstr_tax_amount"), books_row.get("books_tax_amount")),
        (gstr_row.get("gstr_invoice_value"), books_row.get("books_invoice_value")),
    ]
    if any(_is_missing_money_value(left) or _is_missing_money_value(right) for left, right in compared_pairs):
        return False
    return all(abs(_amount(left) - _amount(right)) <= amount_tolerance for left, right in compared_pairs)


def _strong_invoice_format_evidence(gstr_row: pd.Series, books_row: pd.Series) -> bool:
    """Return True for suffix/token evidence strong enough to auto-match."""
    gstr_invoice = gstr_row.get("invoice_number", "")
    books_invoice = books_row.get("invoice_number", "")
    gstr_token = str(gstr_row.get("numeric_invoice_token", "") or numeric_invoice_token(gstr_invoice))
    books_token = str(books_row.get("numeric_invoice_token", "") or numeric_invoice_token(books_invoice))
    if not gstr_token or not books_token or not _zero_tolerant_numeric_equal(gstr_token, books_token):
        return False
    if min(len(gstr_token), len(books_token)) < 3:
        return False
    alpha_skeleton = _invoice_alpha_skeleton(gstr_invoice) or _invoice_alpha_skeleton(books_invoice)
    return len(alpha_skeleton) >= 2


def _best_amount_date_candidate(
    gstr_row: pd.Series,
    candidate_df: pd.DataFrame,
    amount_tolerance: float,
    fallback_date_tolerance_days: int,
) -> tuple[int, pd.Series, float] | None:
    """Return the best GSTIN + taxable/invoice value + nearby-date fallback candidate."""
    if candidate_df.empty:
        return None

    best_index = None
    best_row = None
    best_date_difference = float("inf")
    best_amount_difference = float("inf")
    for books_index, books_row in candidate_df.iterrows():
        if not _fallback_amounts_match(gstr_row, books_row, amount_tolerance):
            continue

        date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
        if date_difference is None or date_difference > fallback_date_tolerance_days:
            continue

        amount_difference = _fallback_amount_difference(gstr_row, books_row)
        if date_difference < best_date_difference or (
            date_difference == best_date_difference and amount_difference < best_amount_difference
        ):
            best_index = books_index
            best_row = books_row
            best_date_difference = date_difference
            best_amount_difference = amount_difference

    if best_index is None or best_row is None:
        return None
    return best_index, best_row, round(best_amount_difference, 2)


def _best_amount_date_supplier_candidate(
    gstr_row: pd.Series,
    candidate_df: pd.DataFrame,
    amount_tolerance: float,
    fallback_date_tolerance_days: int,
) -> tuple[int, pd.Series, float] | None:
    """Return the best amount/date candidate with supplier-name support when present."""
    if candidate_df.empty:
        return None

    best_index = None
    best_row = None
    best_date_difference = float("inf")
    best_amount_difference = float("inf")
    for books_index, books_row in candidate_df.iterrows():
        if not _invoice_value_close(gstr_row, books_row, amount_tolerance):
            continue

        date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
        if date_difference is None or date_difference > fallback_date_tolerance_days:
            continue

        if not _supplier_names_match(gstr_row, books_row):
            continue

        amount_difference = _single_amount_difference(gstr_row.get("gstr_invoice_value"), books_row.get("books_invoice_value"))
        if date_difference < best_date_difference or (
            date_difference == best_date_difference and amount_difference < best_amount_difference
        ):
            best_index = books_index
            best_row = books_row
            best_date_difference = date_difference
            best_amount_difference = amount_difference

    if best_index is None or best_row is None:
        return None
    return best_index, best_row, round(best_amount_difference, 2)


def _missing_in_gstr_safeguard_candidate(
    books_row: pd.Series,
    gstr_df: pd.DataFrame,
    amount_tolerance: float,
    fallback_date_tolerance_days: int,
) -> dict[str, Any] | None:
    """Search selected GSTR again before a books row is called missing."""
    if gstr_df.empty:
        return None

    invoice_key = str(books_row.get("invoice_key", "") or "")
    clean_invoice_key = str(books_row.get("clean_invoice_key", "") or "")
    stripped_hash_invoice_key = str(books_row.get("stripped_hash_invoice_key", "") or "")
    party_name_key = str(books_row.get("party_name_key", "") or "")
    gstin_key = str(books_row.get("gstin_key", "") or "")

    same_gstin_gstr_df = gstr_df[gstr_df["gstin_key"] == gstin_key] if gstin_key else gstr_df.iloc[0:0]

    exact_candidates = same_gstin_gstr_df[(same_gstin_gstr_df["invoice_key"] == invoice_key) & (invoice_key != "")]
    if not exact_candidates.empty:
        gstr_row, amount_difference = _best_gstr_candidate_for_books(books_row, exact_candidates, amount_tolerance)
        return _safeguard_match_payload(
            gstr_row,
            books_row,
            amount_difference,
            "P6_SAFEGUARD_INVOICE",
            0.70,
            "Invoice exists in GSTR with different invoice number format",
            amount_tolerance,
        )

    compact_candidates = same_gstin_gstr_df[
        (same_gstin_gstr_df["clean_invoice_key"] == clean_invoice_key) & (clean_invoice_key != "")
    ]
    if not compact_candidates.empty:
        gstr_row, amount_difference = _best_gstr_candidate_for_books(books_row, compact_candidates, amount_tolerance)
        reason = _invoice_format_match_reason(gstr_row, books_row)
        return _safeguard_match_payload(
            gstr_row,
            books_row,
            amount_difference,
            "P6_SAFEGUARD_COMPACT_INVOICE",
            0.66,
            reason,
            amount_tolerance,
        )

    stripped_hash_candidates = same_gstin_gstr_df[
        (same_gstin_gstr_df["invoice_key"] == stripped_hash_invoice_key) & (stripped_hash_invoice_key != "")
    ]
    if not stripped_hash_candidates.empty:
        gstr_row, amount_difference = _best_gstr_candidate_for_books(books_row, stripped_hash_candidates, amount_tolerance)
        return _safeguard_match_payload(
            gstr_row,
            books_row,
            amount_difference,
            "P6_SAFEGUARD_HASH_STRIPPED",
            0.72,
            "Invoice exists in GSTR with different invoice number format",
            amount_tolerance,
        )

    token_rows = []
    for _, gstr_row in same_gstin_gstr_df.iterrows():
        if _invoice_format_evidence_matches(gstr_row, books_row):
            token_rows.append(gstr_row)

    if token_rows:
        candidate_df = pd.DataFrame(token_rows)
        gstr_row, amount_difference = _best_gstr_candidate_for_books(books_row, candidate_df, amount_tolerance)
        return _safeguard_match_payload(
            gstr_row,
            books_row,
            amount_difference,
            "P6_SAFEGUARD_INVOICE_TOKEN",
            0.60,
            _invoice_format_match_reason(gstr_row, books_row),
            amount_tolerance,
        )

    supplier_token_candidates = []
    for _, gstr_row in gstr_df.iterrows():
        date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
        if date_difference is None or date_difference > fallback_date_tolerance_days:
            continue
        if not party_name_key or not _supplier_names_match(gstr_row, books_row):
            continue
        if _invoice_format_evidence_matches(gstr_row, books_row):
            supplier_token_candidates.append(gstr_row)

    if supplier_token_candidates:
        candidate_df = pd.DataFrame(supplier_token_candidates)
        gstr_row, amount_difference = _best_gstr_candidate_for_books(books_row, candidate_df, amount_tolerance)
        return _safeguard_match_payload(
            gstr_row,
            books_row,
            amount_difference,
            "P6_SAFEGUARD_SUPPLIER_INVOICE_TOKEN",
            0.55,
            _invoice_format_match_reason(gstr_row, books_row),
            amount_tolerance,
        )

    all_exact_candidates = gstr_df[(gstr_df["invoice_key"] == invoice_key) & (invoice_key != "")]
    if not all_exact_candidates.empty:
        gstr_row, amount_difference = _best_gstr_candidate_for_books(books_row, all_exact_candidates, amount_tolerance)
        return {
            "gstr_row": gstr_row,
            "match_level": "P6_SAFEGUARD_INVOICE",
            "confidence_score": 0.55,
            "amount_difference": amount_difference,
            "match_reason": "Invoice number exists in selected GSTR, but GSTIN/date/value differs or the GSTR row was already matched elsewhere.",
        }

    all_compact_candidates = gstr_df[(gstr_df["clean_invoice_key"] == clean_invoice_key) & (clean_invoice_key != "")]
    if not all_compact_candidates.empty:
        gstr_row, amount_difference = _best_gstr_candidate_for_books(books_row, all_compact_candidates, amount_tolerance)
        return {
            "gstr_row": gstr_row,
            "match_level": "P6_SAFEGUARD_COMPACT_INVOICE",
            "confidence_score": 0.52,
            "amount_difference": amount_difference,
            "match_reason": "Compact invoice number exists in selected GSTR, but GSTIN/date/value differs or formatting needs review.",
        }

    amount_date_candidates = []
    supplier_amount_candidates = []
    for _, gstr_row in gstr_df.iterrows():
        invoice_value_close = _invoice_value_close(gstr_row, books_row, amount_tolerance)
        date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
        date_close = date_difference is not None and date_difference <= fallback_date_tolerance_days
        supplier_close = _supplier_names_match(gstr_row, books_row)
        if invoice_value_close and date_close:
            amount_date_candidates.append(gstr_row)
        elif invoice_value_close and party_name_key and supplier_close:
            supplier_amount_candidates.append(gstr_row)

    if amount_date_candidates:
        candidate_df = pd.DataFrame(amount_date_candidates)
        gstr_row, amount_difference = _best_gstr_candidate_for_books(books_row, candidate_df, amount_tolerance)
        return {
            "gstr_row": gstr_row,
            "match_level": "P6_SAFEGUARD_AMOUNT_DATE",
            "confidence_score": 0.48,
            "amount_difference": amount_difference,
            "match_reason": "Invoice value and nearby date exist in selected GSTR; review before treating as missing.",
        }

    if supplier_amount_candidates:
        candidate_df = pd.DataFrame(supplier_amount_candidates)
        gstr_row, amount_difference = _best_gstr_candidate_for_books(books_row, candidate_df, amount_tolerance)
        return {
            "gstr_row": gstr_row,
            "match_level": "P6_SAFEGUARD_SUPPLIER_AMOUNT",
            "confidence_score": 0.45,
            "amount_difference": amount_difference,
            "match_reason": "Supplier name and invoice value exist in selected GSTR; review before treating as missing.",
        }

    return None


def _safeguard_match_payload(
    gstr_row: pd.Series,
    books_row: pd.Series,
    amount_difference: float,
    match_level: str,
    confidence_score: float,
    reason: str,
    amount_tolerance: float,
) -> dict[str, Any]:
    """Build a safeguard match, promoting only confident format-only differences to matched."""
    date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
    amounts_match, _ = _gst_amounts_match(gstr_row, books_row, amount_tolerance)
    same_gstin = str(gstr_row.get("gstin_key", "") or "") == str(books_row.get("gstin_key", "") or "")
    exact_or_hash = match_level in {"P6_SAFEGUARD_INVOICE", "P6_SAFEGUARD_HASH_STRIPPED"}
    is_confident_match = same_gstin and amounts_match and date_difference == 0 and exact_or_hash

    return {
        "gstr_row": gstr_row,
        "match_level": match_level,
        "confidence_score": 0.90 if is_confident_match else confidence_score,
        "amount_difference": amount_difference,
        "match_status": "matched" if is_confident_match else "possible_match",
        "match_reason": reason if date_difference in {0, None} else "Invoice exists in GSTR but date differs",
    }


def _best_gstr_candidate_for_books(
    books_row: pd.Series,
    candidate_df: pd.DataFrame,
    amount_tolerance: float,
) -> tuple[pd.Series, float]:
    """Pick the closest GSTR row for a books-side safeguard candidate."""
    best_row = candidate_df.iloc[0]
    best_date_difference = float("inf")
    best_amount_difference = float("inf")
    for _, gstr_row in candidate_df.iterrows():
        _amounts_match, amount_difference = _gst_amounts_match(gstr_row, books_row, amount_tolerance)
        date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
        date_sort_value = float("inf") if date_difference is None else date_difference
        if date_sort_value < best_date_difference or (
            date_sort_value == best_date_difference and amount_difference < best_amount_difference
        ):
            best_row = gstr_row
            best_date_difference = date_sort_value
            best_amount_difference = amount_difference
    return best_row, round(best_amount_difference, 2)


def _invoice_value_close(gstr_row: pd.Series, books_row: pd.Series, amount_tolerance: float) -> bool:
    """Return True when invoice totals are known and within tolerance."""
    if _is_missing_money_value(gstr_row.get("gstr_invoice_value")) or _is_missing_money_value(books_row.get("books_invoice_value")):
        return False
    return _single_amount_difference(gstr_row.get("gstr_invoice_value"), books_row.get("books_invoice_value")) <= amount_tolerance


def _single_amount_difference(left: Any, right: Any) -> float:
    """Return absolute rounded difference between two amount values."""
    return round(abs(_amount(left) - _amount(right)), 2)


def _supplier_names_match(gstr_row: pd.Series, books_row: pd.Series) -> bool:
    """Return True when supplier names are blank or sufficiently similar."""
    gstr_name = str(gstr_row.get("supplier_name_key", "") or "")
    books_name = str(books_row.get("party_name_key", "") or "")
    if not gstr_name or not books_name:
        return True
    return SequenceMatcher(None, gstr_name, books_name).ratio() >= SUPPLIER_NAME_SIMILARITY


def _gstin_possible_typo(gstr_row: pd.Series, books_row: pd.Series) -> bool:
    """Detect common GSTIN manual/OCR confusions while still requiring different GSTIN text."""
    gstr_gstin = str(gstr_row.get("gstin_key", "") or "")
    books_gstin = str(books_row.get("gstin_key", "") or "")
    if not gstr_gstin or not books_gstin or gstr_gstin == books_gstin:
        return False
    return _gstin_typo_key(gstr_gstin) == _gstin_typo_key(books_gstin)


def _gstin_typo_key(value: str) -> str:
    """Canonicalize GSTIN characters commonly confused in manual entry or OCR."""
    return value.translate(str.maketrans({"I": "1", "L": "1", "O": "0"}))


def _gst_amounts_match(
    gstr_row: pd.Series,
    books_row: pd.Series,
    amount_tolerance: float = AMOUNT_TOLERANCE,
) -> tuple[bool, float]:
    """Compare taxable, tax buckets, and invoice value within tolerance."""
    compared_pairs = _gst_amount_pairs(gstr_row, books_row)
    has_missing_value = any(_is_missing_money_value(left) or _is_missing_money_value(right) for left, right in compared_pairs)
    differences = [abs(_amount(left) - _amount(right)) for left, right in compared_pairs]
    max_difference = round(max(differences), 2)
    return not has_missing_value and all(difference <= amount_tolerance for difference in differences), max_difference


def _gst_amount_pairs(gstr_row: pd.Series, books_row: pd.Series) -> list[tuple[Any, Any]]:
    """Return required P1-P3 amount pairs in comparison order."""
    return [
        (gstr_row.get("gstr_taxable_value"), books_row.get("books_taxable_value")),
        (gstr_row.get("gstr_igst_amount"), books_row.get("books_igst_amount")),
        (gstr_row.get("gstr_cgst_amount"), books_row.get("books_cgst_amount")),
        (gstr_row.get("gstr_sgst_amount"), books_row.get("books_sgst_amount")),
        (gstr_row.get("gstr_invoice_value"), books_row.get("books_invoice_value")),
    ]


def _has_missing_gst_amount_value(gstr_row: pd.Series, books_row: pd.Series) -> bool:
    """Return True when any required GST amount value is unknown."""
    return any(
        _is_missing_money_value(left) or _is_missing_money_value(right)
        for left, right in _gst_amount_pairs(gstr_row, books_row)
    )


def _fallback_amounts_match(gstr_row: pd.Series, books_row: pd.Series, amount_tolerance: float) -> bool:
    """Return True when P4 taxable and invoice values are within tolerance."""
    compared_pairs = [
        (gstr_row.get("gstr_taxable_value"), books_row.get("books_taxable_value")),
        (gstr_row.get("gstr_invoice_value"), books_row.get("books_invoice_value")),
    ]
    if any(_is_missing_money_value(left) or _is_missing_money_value(right) for left, right in compared_pairs):
        return False
    differences = [abs(_amount(left) - _amount(right)) for left, right in compared_pairs]
    return all(difference <= amount_tolerance for difference in differences)


def _fallback_amount_difference(gstr_row: pd.Series, books_row: pd.Series) -> float:
    """Return the largest taxable/invoice value difference for P4 ordering."""
    return round(
        max(
            abs(_amount(gstr_row.get("gstr_taxable_value")) - _amount(books_row.get("books_taxable_value"))),
            abs(_amount(gstr_row.get("gstr_invoice_value")) - _amount(books_row.get("books_invoice_value"))),
        ),
        2,
    )


def _date_difference_days(left_date: Any, right_date: Any) -> int | None:
    """Return absolute day difference between two date-like values."""
    left = pd.to_datetime(left_date, errors="coerce")
    right = pd.to_datetime(right_date, errors="coerce")
    if pd.isna(left) or pd.isna(right):
        return None
    return abs((left.date() - right.date()).days)


def _possible_match_action_required(
    gstr_row: pd.Series,
    books_row: pd.Series,
    match_level: str,
    amount_difference: float | None,
) -> str:
    """Return finance-friendly review steps for possible GST matches."""
    actions: list[str] = []
    match_level = str(match_level or "").upper()

    gstr_invoice_key = str(gstr_row.get("invoice_key", "") or "")
    books_invoice_key = str(books_row.get("invoice_key", "") or "")
    gstr_clean_key = str(gstr_row.get("clean_invoice_key", "") or "")
    books_clean_key = str(books_row.get("clean_invoice_key", "") or "")
    if match_level in {
        "P3_FORMAT",
        "P4_INVOICE_TOKEN",
        "P6_SAFEGUARD_COMPACT_INVOICE",
        "P6_SAFEGUARD_HASH_STRIPPED",
        "P6_SAFEGUARD_INVOICE_TOKEN",
        "P6_SAFEGUARD_SUPPLIER_INVOICE_TOKEN",
    } or (
        gstr_clean_key and books_clean_key and gstr_clean_key == books_clean_key and gstr_invoice_key != books_invoice_key
    ):
        actions.append("Review invoice number format")

    gstr_gstin = str(gstr_row.get("gstin_key", "") or "")
    books_gstin = str(books_row.get("gstin_key", "") or "")
    if gstr_gstin and books_gstin and gstr_gstin != books_gstin:
        actions.append("Check GSTIN difference")

    date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
    if date_difference is None or date_difference > 0:
        actions.append("Check invoice date difference")

    if amount_difference is not None and abs(_amount(amount_difference)) > AMOUNT_TOLERANCE:
        actions.append("Check amount/tax difference")
    elif match_level in {"P5_AMOUNT_DATE_CANDIDATE", "P6_SAFEGUARD_AMOUNT_DATE", "P6_SAFEGUARD_SUPPLIER_AMOUNT"}:
        actions.append("Check amount/tax difference")

    actions.append("Confirm manually before marking as matched")
    return "; ".join(dict.fromkeys(actions))


def _action_required(
    status: str,
    gstr_row: pd.Series,
    books_row: pd.Series,
    match_level: str,
    amount_difference: float | None,
) -> str:
    """Return the action text shown to finance users."""
    if status == "possible_match" and _gstin_possible_typo(gstr_row, books_row):
        return "Check vendor GSTIN in Zoho/books."
    if status == "possible_match":
        return _possible_match_action_required(gstr_row, books_row, match_level, amount_difference)
    if status == "missing_in_books":
        override = str(books_row.get("missing_in_books_action_required", "") or "").strip()
        if override:
            return override
    return ACTION_REQUIRED.get(status, "Review manually.")


def _gstr_result_row(
    gstr_row: pd.Series,
    books_row: pd.Series,
    status: str,
    confidence: float,
    reason: str,
    amount_difference: float | None,
    match_level: str,
) -> dict:
    """Build one finance-friendly GST reconciliation output row."""
    rounded_difference = round(amount_difference or 0, 2)
    return {
        "match_status": status,
        "status_label": STATUS_LABELS.get(status, status.replace("_", " ").title()),
        "match_level": match_level,
        "confidence_score": confidence,
        "source_side": "both" if status in {"matched", "amount_mismatch", "tax_component_mismatch", "possible_match"} else "gstr",
        "books_source_type": books_row.get("books_source_type", ""),
        "zoho_supplier_name": books_row.get("party_name", ""),
        "gstr_supplier_name": gstr_row.get("supplier_name", ""),
        "zoho_gstin": books_row.get("gstin", ""),
        "gstr_gstin": gstr_row.get("gstin", ""),
        "zoho_invoice_number": books_row.get("invoice_number", ""),
        "gstr_invoice_number": gstr_row.get("invoice_number", ""),
        "zoho_invoice_date": books_row.get("books_invoice_date", ""),
        "gstr_invoice_date": gstr_row.get("invoice_date", ""),
        "zoho_taxable_value": books_row.get("books_taxable_value", None),
        "gstr_taxable_value": gstr_row.get("gstr_taxable_value", 0),
        "zoho_invoice_value": books_row.get("books_invoice_value", None),
        "gstr_invoice_value": gstr_row.get("gstr_invoice_value", 0),
        "difference_amount": rounded_difference,
        "supplier_name": gstr_row.get("supplier_name", ""),
        "party_name": books_row.get("party_name", ""),
        "gstin": gstr_row.get("gstin", ""),
        "invoice_number": gstr_row.get("invoice_number", ""),
        "invoice_date": gstr_row.get("invoice_date", ""),
        "return_period": gstr_row.get("return_period", ""),
        "taxable_value_gstr": gstr_row.get("gstr_taxable_value", 0),
        "taxable_value_books": books_row.get("books_taxable_value", None),
        "igst_gstr": gstr_row.get("gstr_igst_amount", 0),
        "igst_books": books_row.get("books_igst_amount", None),
        "cgst_gstr": gstr_row.get("gstr_cgst_amount", 0),
        "cgst_books": books_row.get("books_cgst_amount", None),
        "sgst_gstr": gstr_row.get("gstr_sgst_amount", 0),
        "sgst_books": books_row.get("books_sgst_amount", None),
        "invoice_value_gstr": gstr_row.get("gstr_invoice_value", 0),
        "invoice_value_books": books_row.get("books_invoice_value", None),
        "raw_invoice_value": books_row.get("books_raw_invoice_value", None),
        "calculated_invoice_value": books_row.get("books_calculated_invoice_value", None),
        "invoice_value_source": books_row.get("books_invoice_value_source", ""),
        "tax_amount_gstr": gstr_row.get("gstr_tax_amount", 0),
        "tax_amount_books": books_row.get("books_tax_amount", None),
        "amount_difference": rounded_difference,
        "match_reason": reason,
        "action_required": _action_required(status, gstr_row, books_row, match_level, rounded_difference),
        "gstr_line_id": gstr_row.get("gstr_line_id", ""),
        "books_record_id": books_row.get("books_record_id", ""),
        "gstr_upload_id": gstr_row.get("upload_id", ""),
        "normalized_gstin": gstr_row.get("gstin_key", ""),
        "normalized_invoice_number_gstr": gstr_row.get("invoice_key", ""),
        "normalized_invoice_number_books": books_row.get("invoice_key", ""),
        "cleaned_invoice_number_gstr": gstr_row.get("clean_invoice_key", ""),
        "cleaned_invoice_number_books": books_row.get("clean_invoice_key", ""),
        "gstr_duplicate_count": gstr_row.get("gstr_duplicate_count", 1),
        "books_duplicate_count": books_row.get("books_duplicate_count", None),
        "gstr_raw_ids": gstr_row.get("gstr_raw_ids", ""),
        "books_raw_ids": books_row.get("books_raw_ids", ""),
        "gstr_created_at": gstr_row.get("gstr_created_at", ""),
        "books_source_type": books_row.get("books_source_type", ""),
        "books_party_id": books_row.get("books_party_id", ""),
        "books_status": books_row.get("books_status", ""),
        "source_org_key": books_row.get("source_org_key", ""),
        "source_org_id": books_row.get("source_org_id", ""),
        "source_org_name": books_row.get("source_org_name", ""),
        "run_id": books_row.get("run_id", ""),
        "source_record_id": books_row.get("source_record_id", ""),
        "loaded_at": books_row.get("loaded_at", ""),
        "gstr_raw_rows": gstr_row.get("gstr_raw_rows", ""),
        "books_raw_rows": books_row.get("books_raw_rows", ""),
    }


def _books_missing_in_gstr_row(books_row: pd.Series) -> dict:
    """Build a books-side row that has no uploaded GSTR match."""
    return {
        "match_status": "missing_in_gstr",
        "status_label": STATUS_LABELS["missing_in_gstr"],
        "match_level": "P6_MISSING_IN_GSTR",
        "confidence_score": 0.0,
        "source_side": "books",
        "books_source_type": books_row.get("books_source_type", ""),
        "zoho_supplier_name": books_row.get("party_name", ""),
        "gstr_supplier_name": "",
        "zoho_gstin": books_row.get("gstin", ""),
        "gstr_gstin": "",
        "zoho_invoice_number": books_row.get("invoice_number", ""),
        "gstr_invoice_number": "",
        "zoho_invoice_date": books_row.get("books_invoice_date", ""),
        "gstr_invoice_date": "",
        "zoho_taxable_value": books_row.get("books_taxable_value", 0),
        "gstr_taxable_value": None,
        "zoho_invoice_value": books_row.get("books_invoice_value", 0),
        "gstr_invoice_value": None,
        "difference_amount": 0.0,
        "supplier_name": "",
        "party_name": books_row.get("party_name", ""),
        "gstin": books_row.get("gstin", ""),
        "invoice_number": books_row.get("invoice_number", ""),
        "invoice_date": books_row.get("books_invoice_date", ""),
        "return_period": "",
        "taxable_value_gstr": None,
        "taxable_value_books": books_row.get("books_taxable_value", 0),
        "igst_gstr": None,
        "igst_books": books_row.get("books_igst_amount", 0),
        "cgst_gstr": None,
        "cgst_books": books_row.get("books_cgst_amount", 0),
        "sgst_gstr": None,
        "sgst_books": books_row.get("books_sgst_amount", 0),
        "invoice_value_gstr": None,
        "invoice_value_books": books_row.get("books_invoice_value", 0),
        "raw_invoice_value": books_row.get("books_raw_invoice_value", None),
        "calculated_invoice_value": books_row.get("books_calculated_invoice_value", None),
        "invoice_value_source": books_row.get("books_invoice_value_source", ""),
        "tax_amount_gstr": None,
        "tax_amount_books": books_row.get("books_tax_amount", 0),
        "amount_difference": 0.0,
        "match_reason": "Invoice exists in Zoho/books but was not found in selected uploaded GSTR.",
        "action_required": ACTION_REQUIRED["missing_in_gstr"],
        "gstr_line_id": "",
        "books_record_id": books_row.get("books_record_id", ""),
        "gstr_upload_id": "",
        "normalized_gstin": books_row.get("gstin_key", ""),
        "normalized_invoice_number_gstr": "",
        "normalized_invoice_number_books": books_row.get("invoice_key", ""),
        "cleaned_invoice_number_gstr": "",
        "cleaned_invoice_number_books": books_row.get("clean_invoice_key", ""),
        "gstr_duplicate_count": None,
        "books_duplicate_count": books_row.get("books_duplicate_count", 1),
        "gstr_raw_ids": "",
        "books_raw_ids": books_row.get("books_raw_ids", ""),
        "gstr_created_at": "",
        "books_source_type": books_row.get("books_source_type", ""),
        "books_party_id": books_row.get("books_party_id", ""),
        "books_status": books_row.get("books_status", ""),
        "source_org_key": books_row.get("source_org_key", ""),
        "source_org_id": books_row.get("source_org_id", ""),
        "source_org_name": books_row.get("source_org_name", ""),
        "run_id": books_row.get("run_id", ""),
        "source_record_id": books_row.get("source_record_id", ""),
        "loaded_at": books_row.get("loaded_at", ""),
        "gstr_raw_rows": "",
        "books_raw_rows": books_row.get("books_raw_rows", ""),
    }


def _sort_gst_results(results_df: pd.DataFrame) -> pd.DataFrame:
    """Sort exceptions first and matched rows last for finance review."""
    if results_df.empty:
        return results_df
    working_df = results_df.copy().assign(
        status_sort_order=results_df["match_status"].map(STATUS_SORT_ORDER).fillna(99)
    )
    working_df = working_df.sort_values(
        by=["status_sort_order", "gstin", "invoice_number", "invoice_date"],
        kind="stable",
    )
    return working_df.drop(columns=["status_sort_order"]).reset_index(drop=True)


def _summary(results: pd.DataFrame, uploaded_rows: int | None = None) -> dict:
    """Build Streamlit KPI counts from GST reconciliation results."""
    amount_mismatch_count = int((results["match_status"] == "amount_mismatch").sum())
    tax_component_mismatch_count = int((results["match_status"] == "tax_component_mismatch").sum())
    possible_match_count = int((results["match_status"] == "possible_match").sum())
    return {
        "uploaded_gstr_rows": int(uploaded_rows if uploaded_rows is not None else len(results.index)),
        "total_records": int(len(results.index)),
        "matched": int((results["match_status"] == "matched").sum()),
        "amount_mismatch": amount_mismatch_count,
        "tax_component_mismatch": tax_component_mismatch_count,
        "possible_match": possible_match_count,
        "missing_in_books": int((results["match_status"] == "missing_in_books").sum()),
        "missing_in_gstr": int((results["match_status"] == "missing_in_gstr").sum()),
        "exact_matches": int((results["match_status"] == "matched").sum()),
        "mismatch": amount_mismatch_count,
        "mismatches": amount_mismatch_count,
    }


def _missing_in_books_action_required(books_df: pd.DataFrame) -> str:
    """Return action text based on which purchase-side books sources were searched."""
    if books_df.empty or "books_source_type" not in books_df.columns:
        return "Not found in Bills. Check Expenses or card transactions."

    source_types = {
        str(source_type or "").strip().lower()
        for source_type in books_df["books_source_type"].dropna().astype(str)
        if str(source_type or "").strip()
    }
    purchase_side_sources = {"expense", "vendor_credit", "journal", "manual_purchase_entry"}
    if source_types & purchase_side_sources:
        return "Not found in any synced books source. Record may need to be entered in Zoho."
    return "Not found in Bills. Check Expenses or card transactions."


def _metadata_from_upload_dataframe(
    dataframe: pd.DataFrame,
    source_type: str,
    upload_id: str | None = None,
) -> dict[str, Any]:
    """Build minimal metadata when tests pass dataframes directly."""
    resolved_upload_id = upload_id or ""
    if not resolved_upload_id and "upload_id" in dataframe.columns and not dataframe.empty:
        resolved_upload_id = str(dataframe["upload_id"].dropna().astype(str).iloc[0])
    return {
        "upload_id": resolved_upload_id,
        "file_name": "Provided dataframe",
        "uploaded_at": "",
        "source_type": source_type,
        "row_count": len(dataframe.index),
    }


def _add_run_metadata(
    results: pd.DataFrame,
    upload_metadata: dict[str, Any],
    reconciliation_timestamp: str,
) -> pd.DataFrame:
    """Add selected upload details to every result row for Excel auditability."""
    enriched_df = results.copy()
    enriched_df["selected_upload_id"] = upload_metadata.get("upload_id", "")
    enriched_df["selected_file_name"] = upload_metadata.get("file_name", "")
    enriched_df["reconciliation_timestamp"] = reconciliation_timestamp
    enriched_df["selected_upload_min_date"] = upload_metadata.get("selected_upload_min_date", "")
    enriched_df["selected_upload_max_date"] = upload_metadata.get("selected_upload_max_date", "")
    enriched_df["books_rows_before_period_filter"] = upload_metadata.get("books_rows_before_period_filter", 0)
    enriched_df["books_rows_after_period_filter"] = upload_metadata.get("books_rows_after_period_filter", 0)
    return enriched_df


def _apply_deterministic_gst_insights(results_df: pd.DataFrame) -> pd.DataFrame:
    """Fill blank AI columns with deterministic, row-specific review text."""
    enriched_df = results_df.copy()
    for column_name in AI_COLUMNS:
        if column_name not in enriched_df.columns:
            enriched_df[column_name] = ""

    for row_index, row in enriched_df.iterrows():
        fallback = _deterministic_gst_insight(row)
        for column_name in AI_COLUMNS:
            if not str(enriched_df.at[row_index, column_name] or "").strip():
                enriched_df.at[row_index, column_name] = fallback[column_name]
    return enriched_df


def _deterministic_gst_insight(row: pd.Series) -> dict[str, str]:
    """Create finance-friendly fallback insight text without calling Vertex AI."""
    status = str(row.get("match_status", "")).lower()
    match_level = str(row.get("match_level", "")).upper()
    invoice_number = str(row.get("invoice_number", "") or "this invoice")
    supplier_name = str(row.get("supplier_name", "") or row.get("party_name", "") or "the supplier")
    amount_difference = _amount(row.get("amount_difference"))

    if status == "missing_in_books":
        summary = f"Invoice {invoice_number} from {supplier_name} is present in GSTR but missing in books."
        recommendation = "Check whether the bill exists in Zoho or needs to be recorded."
        risk_level = "high"
    elif status == "missing_in_gstr":
        summary = f"Invoice {invoice_number} exists in books but is missing from the uploaded GSTR report."
        recommendation = "Verify filing period, supplier GSTIN, and invoice number in the GSTR portal."
        risk_level = "high"
    elif status == "amount_mismatch":
        summary = f"Invoice {invoice_number} matched by GSTIN and invoice number, but values differ by INR {amount_difference:.2f}."
        recommendation = "Check invoice taxable value and IGST, CGST, and SGST amounts before filing."
        risk_level = "high" if amount_difference >= 1000 else "medium"
    elif status == "tax_component_mismatch":
        summary = f"Invoice {invoice_number} total amount matches, but GST tax component type differs between books and GSTR."
        recommendation = "Check place of supply and GST tax treatment in Zoho/books."
        risk_level = "medium"
    elif status == "possible_match":
        if match_level == "P4_WEAK_INVOICE":
            summary = f"Invoice {invoice_number} exists in GSTR, but GSTIN, date, value, or formatting needs review."
            recommendation = "Compare GSTIN, invoice date, and values before treating this as reconciled."
        elif match_level.startswith("P6_SAFEGUARD"):
            summary = f"Invoice {invoice_number} has evidence in the selected GSTR upload and must not be treated as missing without review."
            recommendation = "Review the candidate GSTR row against Zoho/books before taking filing action."
        elif match_level == "P3_FORMAT":
            summary = f"Invoice {invoice_number} matched after invoice-number formatting cleanup and needs review."
            recommendation = "Confirm the formatted invoice number against source documents before matching."
        elif match_level == "P5_AMOUNT_DATE_CANDIDATE":
            summary = f"Invoice {invoice_number} is an amount, nearby-date, and supplier-name candidate requiring manual review."
            recommendation = "Compare source invoice, books record, and GSTR row before matching."
        else:
            summary = f"Invoice {invoice_number} is a possible GST reconciliation candidate and needs manual review."
            recommendation = "Confirm invoice details against source documents before matching."
        risk_level = "medium"
    else:
        summary = f"Invoice {invoice_number} matches between books and GSTR with no tax difference."
        recommendation = "No action needed beyond routine review."
        risk_level = "low"

    return {
        "ai_summary": summary,
        "ai_recommendation": recommendation,
        "ai_risk_level": risk_level,
    }


def _summary_sheet(
    summary: dict,
    upload_metadata: dict[str, Any],
    reconciliation_timestamp: str,
    ai_metadata: dict,
    amount_tolerance: float,
    fallback_date_tolerance_days: int,
) -> pd.DataFrame:
    """Build the Excel summary sheet."""
    period_start = upload_metadata.get("selected_upload_min_date", "")
    period_end = upload_metadata.get("selected_upload_max_date", "")
    return pd.DataFrame(
        [
            {"Metric": "Selected GSTR file", "Value": upload_metadata.get("file_name", ""), "Explanation": ""},
            {"Metric": "Reconciliation period", "Value": f"{period_start} to {period_end}", "Explanation": ""},
            {"Metric": "Uploaded GSTR rows", "Value": summary["uploaded_gstr_rows"]},
            {"Metric": "Exact matched", "Value": summary["matched"]},
            {
                "Metric": "Tax type mismatch",
                "Value": summary["tax_component_mismatch"],
                "Explanation": "Total matches but IGST/CGST/SGST breakup differs",
            },
            {"Metric": "Amount mismatch", "Value": summary["amount_mismatch"]},
            {
                "Metric": "Possible match",
                "Value": summary["possible_match"],
                "Explanation": "Likely same invoice but needs manual review",
            },
            {
                "Metric": "Missing in GSTR",
                "Value": summary["missing_in_gstr"],
                "Explanation": "Books bill not found in uploaded GSTR",
            },
            {
                "Metric": "Missing in Books",
                "Value": summary["missing_in_books"],
                "Explanation": "GSTR invoice not found in Zoho/books",
            },
        ]
    )


def _finance_sheet(results_df: pd.DataFrame, statuses: list[str]) -> pd.DataFrame:
    """Build a user-facing workbook sheet for one reconciliation status group."""
    status_df = results_df[results_df["match_status"].isin(statuses)].copy()
    if statuses == ["possible_match"]:
        available_columns = [column for column in POSSIBLE_MATCH_COLUMNS if column in status_df.columns]
        return status_df.loc[:, available_columns]
    available_columns = [column for column in FINANCE_COLUMNS if column in status_df.columns]
    return status_df.loc[:, available_columns]


def _technical_audit_sheet(results_df: pd.DataFrame) -> pd.DataFrame:
    """Build a technical audit sheet with IDs, normalized keys, and raw row references."""
    available_columns = [column for column in TECHNICAL_AUDIT_COLUMNS if column in results_df.columns]
    return results_df.loc[:, available_columns]


def run_gst_reconciliation(
    output_dir: str | Path,
    project_id: str | None = None,
    selected_upload_id: str | None = None,
    selected_upload_metadata: dict[str, Any] | None = None,
    gstr_lines_df: pd.DataFrame | None = None,
    books_gst_df: pd.DataFrame | None = None,
    generate_ai_insights: bool = True,
    max_ai_rows: int = GST_AI_MAX_ROWS,
    amount_tolerance: float = AMOUNT_TOLERANCE,
    fallback_date_tolerance_days: int = FALLBACK_DATE_TOLERANCE_DAYS,
    stage_callback: Callable[[str], None] | None = None,
) -> dict:
    """Run GST reconciliation and write finance-friendly Excel output."""
    upload_metadata = selected_upload_metadata
    period_metadata: dict[str, Any] = {}
    if gstr_lines_df is None or books_gst_df is None:
        try:
            resolved_project_id = get_project_id(project_id)
            _notify_stage(stage_callback, "Loading selected GSTR upload")
            upload_metadata = get_upload_metadata("gstr", selected_upload_id, resolved_project_id)
            if not upload_metadata:
                raise RuntimeError("Please upload a GSTR file first.")

            _notify_stage(stage_callback, "Loading accounting GST records")
            gstr_lines_df, books_gst_df, period_metadata = fetch_gst_reconciliation_data(
                resolved_project_id,
                upload_metadata["upload_id"],
            )
        except Exception as error:
            print(f"[GST Reconciliation] BigQuery load failed: {error}")
            if str(error) == GSTR_PERIOD_ERROR:
                raise RuntimeError(GSTR_PERIOD_ERROR) from error
            raise RuntimeError(f"Could not load selected GSTR upload or books records: {error}") from error
    elif upload_metadata is None:
        upload_metadata = _metadata_from_upload_dataframe(gstr_lines_df, "gstr", selected_upload_id)

    active_upload_id = str(upload_metadata.get("upload_id", "") or selected_upload_id or "")
    gstr_lines_df = _filter_selected_gstr_upload(gstr_lines_df, active_upload_id)
    if not period_metadata:
        period_start, period_end = _selected_gstr_period(gstr_lines_df)
        books_gst_df, books_rows_before, books_rows_after = _filter_books_to_gstr_period(
            books_gst_df,
            period_start,
            period_end,
        )
        period_metadata = _period_filter_metadata(period_start, period_end, books_rows_before, books_rows_after)

    uploaded_rows = len(gstr_lines_df.index)
    upload_metadata = {**upload_metadata, "row_count": uploaded_rows, **period_metadata}
    _log_period_filter(upload_metadata, period_metadata)
    _notify_stage(stage_callback, "Running rule-based GST matching")
    try:
        results = reconcile_gst_data(
            gstr_lines_df,
            books_gst_df,
            amount_tolerance=amount_tolerance,
            selected_upload_id=active_upload_id,
            fallback_date_tolerance_days=fallback_date_tolerance_days,
        )
    except Exception as error:
        print(f"[GST Reconciliation] Rule-based matching failed: {error}")
        raise RuntimeError(f"GST rule-based matching failed: {error}") from error

    if generate_ai_insights:
        _notify_stage(stage_callback, "Generating Vertex AI insights")
        try:
            results, ai_metadata = add_gst_ai_insights(results, max_rows=max_ai_rows)
        except Exception as error:
            print(f"[GST Reconciliation] Vertex AI insights failed: {error}")
            ai_metadata = {
                "ai_status": "unavailable",
                "ai_enabled": False,
                "ai_message": "Vertex AI insights unavailable. Showing fallback insights from rule-based reconciliation signals.",
                "ai_rows_processed": 0,
                "ai_max_rows": max_ai_rows,
            }
    else:
        ai_metadata = {
            "ai_status": "skipped",
            "ai_enabled": False,
            "ai_message": "Vertex AI insights skipped. Showing fallback insights from rule-based reconciliation signals.",
            "ai_rows_processed": 0,
            "ai_max_rows": 0,
        }

    results = _apply_deterministic_gst_insights(results)
    reconciliation_timestamp = datetime.now(timezone.utc).isoformat()
    results = _add_run_metadata(results, upload_metadata, reconciliation_timestamp)
    summary = _summary(results, uploaded_rows)

    _notify_stage(stage_callback, "Writing Excel output")
    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)
    export_path = destination_folder / "gst_reconciliation_results.xlsx"

    with pd.ExcelWriter(export_path, engine="openpyxl") as writer:
        _summary_sheet(
            summary,
            upload_metadata,
            reconciliation_timestamp,
            ai_metadata,
            amount_tolerance,
            fallback_date_tolerance_days,
        ).to_excel(
            writer,
            sheet_name="Summary",
            index=False,
        )
        _finance_sheet(results, ["tax_component_mismatch"]).to_excel(writer, sheet_name="Tax Type Mismatches", index=False)
        _finance_sheet(results, ["amount_mismatch"]).to_excel(writer, sheet_name="Amount Mismatches", index=False)
        _finance_sheet(results, ["missing_in_books"]).to_excel(writer, sheet_name="Missing in Books", index=False)
        _finance_sheet(results, ["missing_in_gstr"]).to_excel(writer, sheet_name="Missing in GSTR", index=False)
        _finance_sheet(results, ["possible_match"]).to_excel(writer, sheet_name="Possible Matches", index=False)
        _finance_sheet(results, ["matched"]).to_excel(writer, sheet_name="Matched", index=False)
        _technical_audit_sheet(results).to_excel(writer, sheet_name="Technical Audit", index=False)

    return {
        "summary": summary,
        "results": results,
        "export_path": export_path,
        "selected_upload": upload_metadata,
        "reconciliation_timestamp": reconciliation_timestamp,
        "selected_upload_min_date": period_metadata.get("selected_upload_min_date", ""),
        "selected_upload_max_date": period_metadata.get("selected_upload_max_date", ""),
        "books_rows_before_period_filter": period_metadata.get("books_rows_before_period_filter", 0),
        "books_rows_after_period_filter": period_metadata.get("books_rows_after_period_filter", 0),
        "tolerance_used": amount_tolerance,
        "fallback_date_tolerance_days": fallback_date_tolerance_days,
        "is_placeholder": False,
        "message": "GST reconciliation completed using selected-upload rule-based matching.",
        **ai_metadata,
    }


def _notify_stage(stage_callback: Callable[[str], None] | None, message: str) -> None:
    """Print and forward lightweight stage messages."""
    print(f"[GST Reconciliation] {message}")
    if stage_callback:
        stage_callback(message)


def _raw_json_series(dataframe: pd.DataFrame) -> pd.Series:
    """Return one JSON object per raw row for technical audit output."""
    return dataframe.apply(
        lambda row: json.dumps(
            {str(key): _json_safe_value(value) for key, value in row.to_dict().items()},
            ensure_ascii=True,
            sort_keys=True,
        ),
        axis=1,
    )


def _json_safe_value(value: Any) -> Any:
    """Convert pandas/numpy scalars and timestamps to JSON-safe values."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _join_unique_text(series: pd.Series) -> str:
    """Join nonblank values in first-seen order for audit ID fields."""
    values = []
    for value in series.fillna("").astype(str):
        cleaned = value.strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return "; ".join(values)


def _normalise_gstin_key(value: Any) -> str:
    """Normalize GSTIN for matching."""
    return _clean_text_key(value, compact_hidden=True)


def _normalise_invoice_key(value: Any) -> str:
    """Normalize invoice number for P1/P2 matching while keeping separators significant."""
    text = _clean_text_key(value)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([/\-_.])\s*", r"\1", text)
    return text


def strip_hash_invoice(value: Any) -> str:
    """Remove a leading invoice hash while preserving the rest of the invoice number."""
    return re.sub(r"^#+", "", _normalise_invoice_key(value)).strip()


def compact_invoice_number(value: Any) -> str:
    """Remove common invoice separators, whitespace, backslashes, and hashes."""
    text = _normalise_invoice_key(value)
    return re.sub(r"[\s/\-._\\#]", "", text)


def numeric_invoice_token(value: Any) -> str:
    """Extract the meaningful numeric invoice token after known prefixes/year markers."""
    text = strip_hash_invoice(value)
    if not text:
        return ""

    trimmed = re.sub(r"([/\-_.\\])20\d{2}-\d{2}$", "", text)
    tokens = re.findall(r"\d+", trimmed)
    if not tokens:
        return ""
    return tokens[-1]


def zero_tolerant_invoice_compare(left: Any, right: Any) -> bool:
    """Compare invoice numbers by meaningful numeric token while ignoring leading zeroes."""
    left_token = numeric_invoice_token(left)
    right_token = numeric_invoice_token(right)
    return bool(left_token and right_token and _zero_tolerant_numeric_equal(left_token, right_token))


def _clean_invoice_key(value: Any) -> str:
    """Clean invoice number for P3 by removing spaces and common separators."""
    return compact_invoice_number(value)


def _zero_tolerant_invoice_token(value: Any) -> str:
    """Return numeric invoice token with leading zeroes removed for matching."""
    token = numeric_invoice_token(value)
    if not token:
        return ""
    return str(int(token)) if token.isdigit() else token.lstrip("0")


def _zero_tolerant_numeric_equal(left: Any, right: Any) -> bool:
    """Return True when numeric strings are equal after removing leading zeroes."""
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text or not left_text.isdigit() or not right_text.isdigit():
        return False
    return int(left_text) == int(right_text)


def _invoice_alpha_skeleton(value: Any) -> str:
    """Return invoice letters after dropping digits and separators."""
    return re.sub(r"[\d\s/\-._\\#]", "", _normalise_invoice_key(value))


def _invoice_format_match_reason(gstr_row: pd.Series, books_row: pd.Series) -> str:
    """Describe why two invoice numbers are a format-based match."""
    date_difference = _date_difference_days(gstr_row.get("invoice_date"), books_row.get("books_invoice_date"))
    if date_difference is not None and date_difference > 0:
        return "Invoice exists in GSTR but date differs"

    gstr_invoice = str(gstr_row.get("invoice_number", "") or "")
    books_invoice = str(books_row.get("invoice_number", "") or "")
    if strip_hash_invoice(gstr_invoice) == strip_hash_invoice(books_invoice) and gstr_invoice != books_invoice:
        return "Invoice exists in GSTR with different invoice number format"

    gstr_token = str(gstr_row.get("numeric_invoice_token", "") or numeric_invoice_token(gstr_invoice))
    books_token = str(books_row.get("numeric_invoice_token", "") or numeric_invoice_token(books_invoice))
    if gstr_token and books_token and gstr_token != books_token and _zero_tolerant_numeric_equal(gstr_token, books_token):
        return "Invoice exists in GSTR with leading zero difference"

    gstr_compact = str(gstr_row.get("clean_invoice_key", "") or compact_invoice_number(gstr_invoice))
    books_compact = str(books_row.get("clean_invoice_key", "") or compact_invoice_number(books_invoice))
    if gstr_compact and books_compact and (gstr_compact.endswith(books_compact) or books_compact.endswith(gstr_compact)):
        return "Invoice exists in GSTR with prefix/suffix difference"

    return "Invoice exists in GSTR with different invoice number format"


def _normalise_name_key(value: Any) -> str:
    """Normalize supplier/vendor names for fallback matching."""
    return re.sub(r"\s+", " ", _clean_text_key(value)).strip()


def _clean_text_key(value: Any, compact_hidden: bool = False) -> str:
    """Return uppercase text with placeholder and hidden characters removed."""
    if value is None:
        return ""
    text = str(value)
    if text.strip().lower() in {"", "nan", "none", "null", "nat", "undefined"}:
        return ""
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"[\u200b-\u200f\ufeff\x00-\x1f\x7f]", "", text)
    text = text.strip().upper()
    if compact_hidden:
        text = re.sub(r"\s+", "", text)
    return text


def _display_gstin(value: Any) -> str:
    """Return uppercase GSTIN for display."""
    return _clean_text_key(value, compact_hidden=True)


def _display_invoice_number(value: Any) -> str:
    """Return a trimmed original invoice number for finance display."""
    return re.sub(r"\s+", " ", _clean_text_key(value)).strip()


def _date_series(dataframe: pd.DataFrame, column_names: list[str]) -> pd.Series:
    """Return date text from the first available date column."""
    source = _first_existing_text(dataframe, column_names)
    parsed = pd.to_datetime(source, errors="coerce")
    date_text = parsed.dt.date.astype(str)
    return date_text.where(date_text != "NaT", "")


def _money_series(dataframe: pd.DataFrame, column_names: list[str]) -> pd.Series:
    """Return rounded numeric money values from the first available column."""
    for column_name in column_names:
        if column_name in dataframe.columns:
            return pd.to_numeric(dataframe[column_name], errors="coerce").round(2)
    return pd.Series(float("nan"), index=dataframe.index, dtype="float64")


def _sum_money_series(series_list: list[pd.Series], require_all: bool = False) -> pd.Series:
    """Sum money columns only where at least one source value is present."""
    if not series_list:
        return pd.Series(dtype="float64")

    total = pd.Series(0.0, index=series_list[0].index, dtype="float64")
    has_value = pd.Series(False, index=series_list[0].index, dtype="bool")
    all_values_present = pd.Series(True, index=series_list[0].index, dtype="bool")
    for series in series_list:
        numeric_series = pd.to_numeric(series, errors="coerce")
        total = total.add(numeric_series.fillna(0), fill_value=0)
        has_value = has_value | numeric_series.notna()
        all_values_present = all_values_present & numeric_series.notna()
    return total.round(2).where(all_values_present if require_all else has_value)


def _fill_missing_or_zero_money(source: pd.Series, fallback: pd.Series) -> pd.Series:
    """Use a derived money value when the source total is missing or blank zero."""
    source_numeric = pd.to_numeric(source, errors="coerce").round(2)
    fallback_numeric = pd.to_numeric(fallback, errors="coerce").round(2)
    return source_numeric.where(source_numeric.notna() & (source_numeric != 0), fallback_numeric)


def _books_invoice_value_for_gst(
    taxable_value: pd.Series,
    igst_amount: pd.Series,
    cgst_amount: pd.Series,
    sgst_amount: pd.Series,
    tax_amount: pd.Series,
    raw_invoice_value: pd.Series,
    amount_tolerance: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Choose books invoice value for GST matching, keeping Zoho total for audit."""
    component_total = _sum_money_series([igst_amount, cgst_amount, sgst_amount])
    gst_total = _fill_missing_or_zero_money(component_total, tax_amount)
    calculated_invoice_value = _sum_money_series([taxable_value, gst_total], require_all=True)
    raw_numeric = pd.to_numeric(raw_invoice_value, errors="coerce").round(2)
    calculated_numeric = pd.to_numeric(calculated_invoice_value, errors="coerce").round(2)

    has_taxable = pd.to_numeric(taxable_value, errors="coerce").notna()
    has_gst_value = (
        pd.to_numeric(igst_amount, errors="coerce").notna()
        | pd.to_numeric(cgst_amount, errors="coerce").notna()
        | pd.to_numeric(sgst_amount, errors="coerce").notna()
        | pd.to_numeric(tax_amount, errors="coerce").notna()
    )
    raw_differs = raw_numeric.notna() & ((raw_numeric - calculated_numeric).abs() > amount_tolerance)
    use_calculated = has_taxable & has_gst_value & calculated_numeric.notna() & raw_differs

    invoice_value = raw_numeric.where(~use_calculated, calculated_numeric)
    invoice_value = _fill_missing_or_zero_money(invoice_value, calculated_numeric)
    invoice_value_source = pd.Series("zoho_total_amount", index=raw_invoice_value.index, dtype="object")
    invoice_value_source.loc[use_calculated] = "calculated_from_tax_components"
    return invoice_value, calculated_invoice_value, invoice_value_source


def _matched_amount_reason(books_row: pd.Series) -> str:
    """Return a match reason when books invoice value was corrected from GST components."""
    if str(books_row.get("books_invoice_value_source", "") or "") == "calculated_from_tax_components":
        return "Matched using calculated books invoice value from taxable + GST components"
    return ""


def _tax_component_type_differs(gstr_row: pd.Series, books_row: pd.Series, amount_tolerance: float) -> bool:
    """Return True when taxable, total tax, and invoice totals match but GST buckets differ."""
    total_pairs = [
        (gstr_row.get("gstr_taxable_value"), books_row.get("books_taxable_value")),
        (gstr_row.get("gstr_tax_amount"), books_row.get("books_tax_amount")),
        (gstr_row.get("gstr_invoice_value"), books_row.get("books_invoice_value")),
    ]
    if any(_is_missing_money_value(left) or _is_missing_money_value(right) for left, right in total_pairs):
        return False
    totals_match = all(abs(_amount(left) - _amount(right)) <= amount_tolerance for left, right in total_pairs)
    component_pairs = [
        (gstr_row.get("gstr_igst_amount"), books_row.get("books_igst_amount")),
        (gstr_row.get("gstr_cgst_amount"), books_row.get("books_cgst_amount")),
        (gstr_row.get("gstr_sgst_amount"), books_row.get("books_sgst_amount")),
    ]
    components_differ = any(abs(_amount(left) - _amount(right)) > amount_tolerance for left, right in component_pairs)
    return totals_match and components_differ


def _tax_component_type_differs_reason(gstr_row: pd.Series, books_row: pd.Series) -> str:
    """Describe IGST versus CGST/SGST bucket differences for finance review."""
    return "Total amount matches, but GST tax component type differs between books and GSTR."


def _first_existing_text(dataframe: pd.DataFrame, column_names: list[str]) -> pd.Series:
    """Return the first available text column from a list of candidates."""
    for column_name in column_names:
        if column_name in dataframe.columns:
            return _text_series(dataframe, column_name)
    return pd.Series("", index=dataframe.index, dtype="object")


def _text_series(dataframe: pd.DataFrame, column_name: str) -> pd.Series:
    """Return a text series with blanks for missing values."""
    if column_name not in dataframe.columns:
        return pd.Series("", index=dataframe.index, dtype="object")
    return dataframe[column_name].fillna("").astype(str)


def _amount(value: Any) -> float:
    """Convert possible numeric values to float."""
    return float(pd.to_numeric(pd.Series([value]), errors="coerce").fillna(0).iloc[0])


def _is_missing_money_value(value: Any) -> bool:
    """Return True for unknown money values that must not be treated as zero."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in {"", "nan", "none", "nat"}
