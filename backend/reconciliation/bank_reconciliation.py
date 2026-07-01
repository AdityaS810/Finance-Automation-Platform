"""Deterministic bank reconciliation against accounting-side Gold data."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd
from google.cloud import bigquery

from backend.ai.reconciliation_insights import add_bank_ai_insights
from backend.reconciliation.upload_registry import (
    get_project_id,
    get_upload_metadata,
    query_to_dataframe,
    table_name,
)


BANK_LINES_VIEW = "finance_silver.fact_bank_statement_lines"
ACCOUNTING_INPUT_VIEW = "finance_gold.bank_reconciliation_input"
AMOUNT_TOLERANCE = 1.0
DATE_TOLERANCE_DAYS = 3
MATCHED_THRESHOLD = 0.78
POSSIBLE_MATCH_THRESHOLD = 0.50
BANK_NOT_IN_BOOKS_REASON = "Bank statement transaction was not found in accounting records."
BANK_NOT_IN_BOOKS_ACTION = "Check whether this bank transaction is recorded in Zoho/books or needs to be posted."
BOOKS_NOT_IN_BANK_REASON = "Accounting-side transaction was not found in uploaded bank statement."
BOOKS_NOT_IN_BANK_ACTION = "Check whether this transaction appears in another bank account, different date range, or is pending bank clearance."


def fetch_bank_reconciliation_data(
    project_id: str | None = None,
    upload_id: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch uploaded bank lines and accounting reconciliation input from BigQuery."""
    resolved_project_id = get_project_id(project_id)
    selected_upload = get_upload_metadata("bank_statement", upload_id, resolved_project_id)
    if not selected_upload:
        raise RuntimeError("Please upload a bank statement first.")

    client = bigquery.Client(project=resolved_project_id)

    bank_query = f"""
        SELECT
            COALESCE(bank_line_id, CONCAT(upload_id, '-', CAST(raw_row_number AS STRING))) AS bank_line_id,
            upload_id,
            raw_row_number,
            transaction_date AS bank_date,
            narration AS bank_narration,
            debit_amount,
            credit_amount,
            balance_amount,
            COALESCE(credit_amount, 0) - COALESCE(debit_amount, 0) AS bank_amount
        FROM {table_name(resolved_project_id, BANK_LINES_VIEW)}
        WHERE upload_id = @upload_id
        ORDER BY transaction_date, upload_id, raw_row_number
    """
    bank_df = query_to_dataframe(
        client,
        bank_query,
        [bigquery.ScalarQueryParameter("upload_id", "STRING", selected_upload["upload_id"])],
    )

    min_bank_date, max_bank_date = _bank_date_bounds(bank_df)
    if min_bank_date is None or max_bank_date is None:
        print("[Bank Reconciliation] No valid bank dates found. Accounting comparison query will return no rows.")
        accounting_query = f"""
        SELECT
            transaction_id AS accounting_record_id,
            transaction_date AS accounting_date,
            counterparty_name AS accounting_party_name,
            reference_number,
            transaction_number,
            transaction_type,
            transaction_amount AS accounting_amount
        FROM {table_name(resolved_project_id, ACCOUNTING_INPUT_VIEW)}
        WHERE FALSE
        ORDER BY transaction_date, transaction_id
    """
        return bank_df, query_to_dataframe(client, accounting_query)

    accounting_start_date = min_bank_date - timedelta(days=DATE_TOLERANCE_DAYS)
    accounting_end_date = max_bank_date + timedelta(days=DATE_TOLERANCE_DAYS)
    accounting_query = f"""
        SELECT
            transaction_id AS accounting_record_id,
            transaction_date AS accounting_date,
            counterparty_name AS accounting_party_name,
            reference_number,
            transaction_number,
            transaction_type,
            transaction_amount AS accounting_amount
        FROM {table_name(resolved_project_id, ACCOUNTING_INPUT_VIEW)}
        WHERE transaction_date BETWEEN @accounting_start_date AND @accounting_end_date
        ORDER BY transaction_date, transaction_id
    """
    accounting_params = [
        bigquery.ScalarQueryParameter("accounting_start_date", "DATE", accounting_start_date),
        bigquery.ScalarQueryParameter("accounting_end_date", "DATE", accounting_end_date),
    ]
    return bank_df, query_to_dataframe(client, accounting_query, accounting_params)


def _bank_date_bounds(bank_df: pd.DataFrame) -> tuple[Any | None, Any | None]:
    """Return the selected bank upload's min/max transaction dates."""
    if bank_df.empty or "bank_date" not in bank_df.columns:
        return None, None

    bank_dates = pd.to_datetime(bank_df["bank_date"], errors="coerce").dropna()
    if bank_dates.empty:
        return None, None

    return bank_dates.min().date(), bank_dates.max().date()


def _normalise_text(value: Any) -> str:
    """Lowercase and simplify text before similarity scoring."""
    text = "" if pd.isna(value) else str(value)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _text_similarity(left: str, right: str) -> float:
    """Score narration and counterparty/reference similarity from 0 to 1."""
    left_text = _normalise_text(left)
    right_text = _normalise_text(right)
    if not left_text or not right_text:
        return 0.0

    sequence_score = SequenceMatcher(None, left_text, right_text).ratio()
    left_tokens = set(left_text.split())
    right_tokens = set(right_text.split())
    token_score = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return max(sequence_score, token_score)


def _date_difference_days(left_date: Any, right_date: Any) -> int | None:
    """Return absolute day difference between two dates, or None if invalid."""
    left = pd.to_datetime(left_date, errors="coerce")
    right = pd.to_datetime(right_date, errors="coerce")
    if pd.isna(left) or pd.isna(right):
        return None
    return abs((left.date() - right.date()).days)


def _candidate_score(bank_row: pd.Series, accounting_row: pd.Series) -> tuple[float, str]:
    """Score one bank/accounting candidate pair and explain the result."""
    bank_amount = float(bank_row.get("bank_amount", 0) or 0)
    accounting_amount = float(accounting_row.get("accounting_amount", 0) or 0)
    amount_difference = abs(bank_amount - accounting_amount)
    amount_score = 1.0 if amount_difference <= AMOUNT_TOLERANCE else max(0.0, 1 - (amount_difference / max(abs(bank_amount), 1)))

    date_difference = _date_difference_days(bank_row.get("bank_date"), accounting_row.get("accounting_date"))
    date_score = 0.0 if date_difference is None else max(0.0, 1 - (date_difference / (DATE_TOLERANCE_DAYS + 1)))

    accounting_text = " ".join(
        str(accounting_row.get(column_name, "") or "")
        for column_name in ["accounting_party_name", "reference_number", "transaction_number", "transaction_type"]
    )
    text_score = _text_similarity(str(bank_row.get("bank_narration", "")), accounting_text)
    confidence = round((amount_score * 0.45) + (date_score * 0.25) + (text_score * 0.30), 4)

    reasons = []
    if amount_difference <= AMOUNT_TOLERANCE:
        reasons.append("amount matched within tolerance")
    else:
        reasons.append(f"amount differs by {amount_difference:.2f}")

    if date_difference is not None and date_difference <= DATE_TOLERANCE_DAYS:
        reasons.append(f"date within {date_difference} day(s)")
    elif date_difference is not None:
        reasons.append(f"date differs by {date_difference} day(s)")

    if text_score >= 0.55:
        reasons.append("narration/party text is similar")
    elif text_score > 0:
        reasons.append("weak narration/party text similarity")

    return confidence, "; ".join(reasons)


def reconcile_bank_data(bank_lines_df: pd.DataFrame, accounting_df: pd.DataFrame) -> pd.DataFrame:
    """Match uploaded bank lines to accounting records using deterministic rules."""
    if bank_lines_df.empty:
        raise RuntimeError("No uploaded bank statement data found in finance_silver.fact_bank_statement_lines.")

    bank_df = _prepare_bank_lines(bank_lines_df)
    opening_balance_df = bank_df[bank_df["is_opening_balance"]].copy()
    transaction_bank_df = bank_df[~bank_df["is_opening_balance"]].copy()
    accounting_working_df = _prepare_accounting_lines(accounting_df)
    used_accounting_indexes: set[int] = set()
    result_rows = []

    for _, opening_row in opening_balance_df.iterrows():
        result_rows.append(_opening_balance_result_row(opening_row))

    for _, bank_row in transaction_bank_df.iterrows():
        best_index = None
        best_score = 0.0
        best_date_difference = None
        best_reason = "No accounting candidate found."

        for accounting_index, accounting_row in accounting_working_df.iterrows():
            if accounting_index in used_accounting_indexes:
                continue

            date_difference = _date_difference_days(bank_row.get("bank_date"), accounting_row.get("accounting_date"))
            amount_difference = abs(float(bank_row["bank_amount"]) - float(accounting_row["accounting_amount"]))
            if amount_difference > max(abs(float(bank_row["bank_amount"])) * 0.15, AMOUNT_TOLERANCE) and (
                date_difference is None or date_difference > DATE_TOLERANCE_DAYS
            ):
                continue

            score, reason = _candidate_score(bank_row, accounting_row)
            if _is_better_candidate(score, date_difference, best_score, best_date_difference):
                best_score = score
                best_index = accounting_index
                best_date_difference = date_difference
                best_reason = reason

        if best_index is not None and best_score >= MATCHED_THRESHOLD:
            match_status = "matched"
            used_accounting_indexes.add(best_index)
        elif best_index is not None and best_score >= POSSIBLE_MATCH_THRESHOLD:
            match_status = "possible_match"
            used_accounting_indexes.add(best_index)
        else:
            match_status = "bank_not_in_books"
            best_index = None
            best_score = 0.0
            best_reason = BANK_NOT_IN_BOOKS_REASON

        accounting_row = accounting_working_df.loc[best_index] if best_index is not None else pd.Series(dtype="object")
        result_rows.append(
            {
                "bank_line_id": bank_row["bank_line_id"],
                "bank_date": bank_row["bank_date"],
                "bank_narration": bank_row["bank_narration"],
                "bank_amount": bank_row["bank_amount"],
                "accounting_record_id": accounting_row.get("accounting_record_id", ""),
                "accounting_date": accounting_row.get("accounting_date", ""),
                "accounting_party_name": accounting_row.get("accounting_party_name", ""),
                "accounting_amount": accounting_row.get("accounting_amount", None),
                "transaction_type": accounting_row.get("transaction_type", ""),
                "reference_number": accounting_row.get("reference_number", ""),
                "transaction_number": accounting_row.get("transaction_number", ""),
                "match_status": match_status,
                "confidence_score": round(best_score, 2),
                "match_reason": best_reason,
                "action_required": _action_for_bank_status(match_status),
            }
        )

    for accounting_index, accounting_row in accounting_working_df.iterrows():
        if accounting_index not in used_accounting_indexes:
            result_rows.append(_books_not_in_bank_result_row(accounting_row))

    return pd.DataFrame(result_rows)


def _prepare_bank_lines(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Normalise and deduplicate bank line columns before matching."""
    working_df = dataframe.copy().reset_index(drop=True)
    if "bank_line_id" not in working_df.columns:
        upload = working_df.get("upload_id", pd.Series("upload", index=working_df.index)).fillna("upload").astype(str)
        row_number = working_df.get("raw_row_number", pd.Series(range(1, len(working_df.index) + 1))).astype(str)
        bank_line_id = upload + "-" + row_number
    else:
        bank_line_id = working_df["bank_line_id"].fillna("").astype(str)
        blank_id_mask = bank_line_id.str.strip() == ""
        bank_line_id.loc[blank_id_mask] = "bank-row-" + (working_df.index[blank_id_mask] + 1).astype(str)

    if "bank_amount" not in working_df.columns:
        debit = _numeric_series(working_df, "debit_amount")
        credit = _numeric_series(working_df, "credit_amount")
        bank_amount = credit - debit
    else:
        bank_amount = _numeric_series(working_df, "bank_amount")
        debit = _numeric_series(working_df, "debit_amount")
        credit = _numeric_series(working_df, "credit_amount")
        debit = debit.where(debit != 0, bank_amount.where(bank_amount < 0, 0).abs())
        credit = credit.where(credit != 0, bank_amount.where(bank_amount > 0, 0))

    balance_amount = _numeric_series(working_df, "balance_amount")
    upload_id = _text_series(working_df, working_df.get("upload_id", ""))
    raw_row_number = _text_series(working_df, working_df.get("raw_row_number", ""))

    if "bank_date" not in working_df.columns and "transaction_date" in working_df.columns:
        bank_date_source = working_df["transaction_date"]
    else:
        bank_date_source = working_df.get("bank_date", pd.Series("", index=working_df.index))

    if "bank_narration" not in working_df.columns and "narration" in working_df.columns:
        narration = working_df["narration"]
    else:
        narration = working_df.get("bank_narration", pd.Series("", index=working_df.index))

    prepared_df = pd.DataFrame(
        {
            "bank_line_id": bank_line_id,
            "upload_id": upload_id,
            "raw_row_number": raw_row_number,
            "bank_date": pd.to_datetime(_value_series(working_df, bank_date_source), errors="coerce").dt.date.astype(str),
            "bank_narration": _text_series(working_df, narration),
            "debit_amount": debit,
            "credit_amount": credit,
            "balance_amount": balance_amount,
            "bank_amount": bank_amount,
        }
    )
    prepared_df = prepared_df.assign(
        normalised_narration=prepared_df["bank_narration"].map(_normalise_text),
        debit_key=prepared_df["debit_amount"].round(2),
        credit_key=prepared_df["credit_amount"].round(2),
        balance_key=prepared_df["balance_amount"].round(2),
    )
    prepared_df = prepared_df.assign(
        is_opening_balance=prepared_df["normalised_narration"].map(_is_opening_balance_text),
    )

    # BigQuery uploads can contain repeated headers/opening balances or repeated
    # raw rows across uploads. Keep one row per bank id and one row per visible
    # transaction signature so duplicate-looking rows do not create fake matches.
    prepared_df = prepared_df.drop_duplicates(subset=["bank_line_id"], keep="first")
    prepared_df = prepared_df.drop_duplicates(
        subset=["bank_date", "normalised_narration", "debit_key", "credit_key", "balance_key"],
        keep="first",
    )
    return prepared_df.drop(columns=["normalised_narration", "debit_key", "credit_key", "balance_key"]).reset_index(drop=True)


def _is_better_candidate(
    score: float,
    date_difference: int | None,
    best_score: float,
    best_date_difference: int | None,
) -> bool:
    """Return True when a candidate beats the current best match."""
    if score > best_score:
        return True
    if score != best_score:
        return False
    if best_date_difference is None:
        return date_difference is not None
    if date_difference is None:
        return False
    return date_difference < best_date_difference


def _is_opening_balance_text(normalised_text: str) -> bool:
    """Detect bank statement opening-balance rows that are not transactions."""
    return (
        "opening balance" in normalised_text
        or "opening bal" in normalised_text
        or "balance b f" in normalised_text
        or "balance bf" in normalised_text
    )


def _opening_balance_result_row(bank_row: pd.Series) -> dict:
    """Build an ignored row for opening balances so they are visible but separate."""
    return {
        "bank_line_id": bank_row["bank_line_id"],
        "bank_date": bank_row["bank_date"],
        "bank_narration": bank_row["bank_narration"],
        "bank_amount": bank_row["bank_amount"],
        "accounting_record_id": "",
        "accounting_date": "",
        "accounting_party_name": "",
        "accounting_amount": None,
        "transaction_type": "",
        "reference_number": "",
        "transaction_number": "",
        "match_status": "ignored_opening_balance",
        "confidence_score": 0.0,
        "match_reason": "Opening balance row ignored for transaction matching.",
        "action_required": _action_for_bank_status("ignored_opening_balance"),
    }


def _books_not_in_bank_result_row(accounting_row: pd.Series) -> dict:
    """Build an accounting-side exception row that was not found in the bank upload."""
    return {
        "bank_line_id": "",
        "bank_date": "",
        "bank_narration": "",
        "bank_amount": None,
        "accounting_record_id": accounting_row.get("accounting_record_id", ""),
        "accounting_date": accounting_row.get("accounting_date", ""),
        "accounting_party_name": accounting_row.get("accounting_party_name", ""),
        "accounting_amount": accounting_row.get("accounting_amount", None),
        "transaction_type": accounting_row.get("transaction_type", ""),
        "reference_number": accounting_row.get("reference_number", ""),
        "transaction_number": accounting_row.get("transaction_number", ""),
        "match_status": "books_not_in_bank",
        "confidence_score": 0.0,
        "match_reason": BOOKS_NOT_IN_BANK_REASON,
        "action_required": BOOKS_NOT_IN_BANK_ACTION,
    }


def _action_for_bank_status(match_status: str) -> str:
    """Return a finance-friendly action for each bank reconciliation status."""
    actions = {
        "matched": "No action required.",
        "possible_match": "Review manually before confirming.",
        "ignored_opening_balance": "Opening balance row ignored.",
        "bank_not_in_books": BANK_NOT_IN_BOOKS_ACTION,
        "books_not_in_bank": BOOKS_NOT_IN_BANK_ACTION,
    }
    return actions.get(match_status, "Review manually before confirming.")


def _prepare_accounting_lines(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Normalise accounting columns before matching."""
    working_df = dataframe.copy().reset_index(drop=True)
    if working_df.empty:
        return pd.DataFrame(
            columns=[
                "accounting_record_id",
                "accounting_date",
                "accounting_party_name",
                "reference_number",
                "transaction_number",
                "transaction_type",
                "accounting_amount",
            ]
        )

    accounting_record_id = working_df["accounting_record_id"] if "accounting_record_id" in working_df.columns else working_df.get("transaction_id", "")
    accounting_date = working_df["accounting_date"] if "accounting_date" in working_df.columns else working_df.get("transaction_date", "")
    accounting_party_name = (
        working_df["accounting_party_name"]
        if "accounting_party_name" in working_df.columns
        else working_df.get("counterparty_name", "")
    )
    accounting_amount = (
        working_df["accounting_amount"]
        if "accounting_amount" in working_df.columns
        else working_df.get("transaction_amount", 0)
    )

    return pd.DataFrame(
        {
            "accounting_record_id": _text_series(working_df, accounting_record_id),
            "accounting_date": pd.to_datetime(_value_series(working_df, accounting_date), errors="coerce").dt.date.astype(str),
            "accounting_party_name": _text_series(working_df, accounting_party_name),
            "reference_number": _text_series(working_df, working_df.get("reference_number", "")),
            "transaction_number": _text_series(working_df, working_df.get("transaction_number", "")),
            "transaction_type": _text_series(working_df, working_df.get("transaction_type", "")),
            "accounting_amount": pd.to_numeric(_value_series(working_df, accounting_amount), errors="coerce").fillna(0),
        }
    )


def _numeric_series(dataframe: pd.DataFrame, column_name: str) -> pd.Series:
    """Return a numeric series with zeros for missing values."""
    if column_name not in dataframe.columns:
        return pd.Series(0, index=dataframe.index, dtype="float64")
    return pd.to_numeric(dataframe[column_name], errors="coerce").fillna(0)


def _text_series(dataframe: pd.DataFrame, value: Any) -> pd.Series:
    """Return text as a Series aligned to a dataframe index."""
    return _value_series(dataframe, value).fillna("").astype(str)


def _value_series(dataframe: pd.DataFrame, value: Any) -> pd.Series:
    """Return a scalar or Series as a Series aligned to a dataframe index."""
    if isinstance(value, pd.Series):
        return value
    return pd.Series(value, index=dataframe.index, dtype="object")


def _summary(results: pd.DataFrame, uploaded_rows: int | None = None) -> dict:
    """Build Streamlit KPI counts from bank reconciliation results."""
    ignored_opening_balance = int((results["match_status"] == "ignored_opening_balance").sum())
    bank_not_in_books = int((results["match_status"] == "bank_not_in_books").sum())
    books_not_in_bank = int((results["match_status"] == "books_not_in_bank").sum())
    return {
        "uploaded_bank_rows": int(uploaded_rows if uploaded_rows is not None else len(results.index)),
        "total_records": int(len(results.index)),
        "transaction_records": int(len(results.index) - ignored_opening_balance),
        "matched": int((results["match_status"] == "matched").sum()),
        "possible_match": int((results["match_status"] == "possible_match").sum()),
        "bank_not_in_books": bank_not_in_books,
        "books_not_in_bank": books_not_in_bank,
        "unmatched": bank_not_in_books,
        "ignored_opening_balance": ignored_opening_balance,
        "matched_records": int((results["match_status"] == "matched").sum()),
    }


def _metadata_from_upload_dataframe(dataframe: pd.DataFrame, source_type: str) -> dict[str, Any]:
    """Build minimal metadata when tests pass dataframes directly."""
    upload_id = ""
    if "upload_id" in dataframe.columns and not dataframe.empty:
        upload_id = str(dataframe["upload_id"].dropna().astype(str).iloc[0])
    return {
        "upload_id": upload_id,
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
    enriched_df.insert(0, "reconciliation_timestamp", reconciliation_timestamp)
    enriched_df.insert(0, "selected_file_name", upload_metadata.get("file_name", ""))
    enriched_df.insert(0, "selected_upload_id", upload_metadata.get("upload_id", ""))
    return enriched_df


def _metadata_sheet(upload_metadata: dict[str, Any], reconciliation_timestamp: str) -> pd.DataFrame:
    """Create a one-row workbook metadata sheet."""
    return pd.DataFrame(
        [
            {
                "selected_upload_id": upload_metadata.get("upload_id", ""),
                "selected_file_name": upload_metadata.get("file_name", ""),
                "selected_uploaded_at": str(upload_metadata.get("uploaded_at", "")),
                "source_type": upload_metadata.get("source_type", "bank_statement"),
                "uploaded_row_count": upload_metadata.get("row_count", ""),
                "reconciliation_timestamp": reconciliation_timestamp,
            }
        ]
    )


def run_bank_reconciliation(
    output_dir: str | Path,
    project_id: str | None = None,
    selected_upload_id: str | None = None,
    selected_upload_metadata: dict[str, Any] | None = None,
    bank_lines_df: pd.DataFrame | None = None,
    accounting_df: pd.DataFrame | None = None,
    generate_ai_insights: bool = True,
    max_ai_rows: int = 10,
) -> dict:
    """Run deterministic bank reconciliation and write an Excel export."""
    upload_metadata = selected_upload_metadata
    if bank_lines_df is None or accounting_df is None:
        resolved_project_id = get_project_id(project_id)
        upload_metadata = get_upload_metadata("bank_statement", selected_upload_id, resolved_project_id)
        if not upload_metadata:
            raise RuntimeError("Please upload a bank statement first.")
        bank_lines_df, accounting_df = fetch_bank_reconciliation_data(
            resolved_project_id,
            upload_metadata["upload_id"],
        )
    elif upload_metadata is None:
        upload_metadata = _metadata_from_upload_dataframe(bank_lines_df, "bank_statement")

    uploaded_rows = len(bank_lines_df.index)
    results = reconcile_bank_data(bank_lines_df, accounting_df)
    if generate_ai_insights:
        try:
            results, ai_metadata = add_bank_ai_insights(results, max_rows=max_ai_rows)
        except Exception as error:
            print(f"[Bank Reconciliation] Vertex AI insights failed: {error}")
            results = _ensure_ai_columns(results)
            ai_metadata = {
                "ai_status": "unavailable",
                "ai_enabled": False,
                "ai_message": "Vertex AI insights unavailable. Showing rule-based reconciliation only.",
                "ai_rows_processed": 0,
                "ai_max_rows": max_ai_rows,
            }
    else:
        results = _ensure_ai_columns(results)
        ai_metadata = {
            "ai_status": "skipped",
            "ai_enabled": False,
            "ai_message": "Vertex AI insights skipped. Showing rule-based reconciliation only.",
            "ai_rows_processed": 0,
            "ai_max_rows": 0,
        }
    reconciliation_timestamp = datetime.now(timezone.utc).isoformat()
    results = _add_run_metadata(results, upload_metadata, reconciliation_timestamp)
    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)
    export_path = destination_folder / "bank_reconciliation_results.xlsx"

    with pd.ExcelWriter(export_path, engine="openpyxl") as writer:
        results.to_excel(writer, sheet_name="Bank Reconciliation", index=False)
        _metadata_sheet(upload_metadata, reconciliation_timestamp).to_excel(writer, sheet_name="Run Metadata", index=False)

    return {
        "summary": _summary(results, uploaded_rows),
        "results": results,
        "export_path": export_path,
        "selected_upload": upload_metadata,
        "reconciliation_timestamp": reconciliation_timestamp,
        "is_placeholder": False,
        "message": "Bank reconciliation completed using deterministic BigQuery-backed matching.",
        **ai_metadata,
    }


def _ensure_ai_columns(results: pd.DataFrame) -> pd.DataFrame:
    """Keep Streamlit/Excel columns stable when Vertex AI is skipped."""
    enriched_df = results.copy()
    for column_name in ["ai_summary", "ai_recommendation", "ai_risk_level"]:
        if column_name not in enriched_df.columns:
            enriched_df[column_name] = ""
    return enriched_df
