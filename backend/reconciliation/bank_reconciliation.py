"""Deterministic bank reconciliation against accounting-side Gold data."""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery

from backend.ai.reconciliation_insights import add_bank_ai_insights


DEFAULT_PROJECT_ID = "internal-project-work-497507"
BANK_LINES_VIEW = "finance_silver.fact_bank_statement_lines"
ACCOUNTING_INPUT_VIEW = "finance_gold.bank_reconciliation_input"
AMOUNT_TOLERANCE = 1.0
DATE_TOLERANCE_DAYS = 3
MATCHED_THRESHOLD = 0.78
POSSIBLE_MATCH_THRESHOLD = 0.50


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


def fetch_bank_reconciliation_data(project_id: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch uploaded bank lines and accounting reconciliation input from BigQuery."""
    resolved_project_id = _get_project_id(project_id)
    client = bigquery.Client(project=resolved_project_id)

    bank_query = f"""
        SELECT
            COALESCE(bank_line_id, CONCAT(upload_id, '-', CAST(raw_row_number AS STRING))) AS bank_line_id,
            upload_id,
            raw_row_number,
            transaction_date AS bank_date,
            narration AS bank_narration,
            COALESCE(credit_amount, 0) - COALESCE(debit_amount, 0) AS bank_amount
        FROM {_table_name(resolved_project_id, BANK_LINES_VIEW)}
        ORDER BY transaction_date, upload_id, raw_row_number
    """
    accounting_query = f"""
        SELECT
            transaction_id AS accounting_record_id,
            transaction_date AS accounting_date,
            counterparty_name AS accounting_party_name,
            reference_number,
            transaction_number,
            transaction_type,
            transaction_amount AS accounting_amount
        FROM {_table_name(resolved_project_id, ACCOUNTING_INPUT_VIEW)}
        ORDER BY transaction_date, transaction_id
    """

    return _query_to_dataframe(client, bank_query), _query_to_dataframe(client, accounting_query)


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
            match_status = "unmatched"

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
                "match_status": match_status,
                "confidence_score": round(best_score, 2),
                "match_reason": best_reason if best_index is not None else "No accounting-side match found.",
            }
        )

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
            "bank_date": pd.to_datetime(_value_series(working_df, bank_date_source), errors="coerce").dt.date.astype(str),
            "bank_narration": _text_series(working_df, narration),
            "bank_amount": bank_amount,
        }
    )
    prepared_df = prepared_df.assign(
        normalised_narration=prepared_df["bank_narration"].map(_normalise_text),
        bank_amount_key=prepared_df["bank_amount"].round(2),
    )
    prepared_df = prepared_df.assign(
        is_opening_balance=prepared_df["normalised_narration"].map(_is_opening_balance_text),
    )

    # BigQuery uploads can contain repeated headers/opening balances or repeated
    # raw rows across uploads. Keep one row per bank id and one row per visible
    # transaction signature so duplicate-looking rows do not create fake matches.
    prepared_df = prepared_df.drop_duplicates(subset=["bank_line_id"], keep="first")
    prepared_df = prepared_df.drop_duplicates(
        subset=["bank_date", "normalised_narration", "bank_amount_key"],
        keep="first",
    )
    return prepared_df.drop(columns=["normalised_narration", "bank_amount_key"]).reset_index(drop=True)


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
    return "opening balance" in normalised_text or "opening bal" in normalised_text


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
        "match_status": "ignored_opening_balance",
        "confidence_score": 0.0,
        "match_reason": "Opening balance row ignored for transaction matching.",
    }


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


def _summary(results: pd.DataFrame) -> dict:
    """Build Streamlit KPI counts from bank reconciliation results."""
    ignored_opening_balance = int((results["match_status"] == "ignored_opening_balance").sum())
    return {
        "total_records": int(len(results.index)),
        "transaction_records": int(len(results.index) - ignored_opening_balance),
        "matched": int((results["match_status"] == "matched").sum()),
        "possible_match": int((results["match_status"] == "possible_match").sum()),
        "unmatched": int((results["match_status"] == "unmatched").sum()),
        "ignored_opening_balance": ignored_opening_balance,
        "matched_records": int((results["match_status"] == "matched").sum()),
    }


def run_bank_reconciliation(
    output_dir: str | Path,
    project_id: str | None = None,
    bank_lines_df: pd.DataFrame | None = None,
    accounting_df: pd.DataFrame | None = None,
) -> dict:
    """Run deterministic bank reconciliation and write an Excel export."""
    if bank_lines_df is None or accounting_df is None:
        bank_lines_df, accounting_df = fetch_bank_reconciliation_data(project_id)

    results = reconcile_bank_data(bank_lines_df, accounting_df)
    results, ai_metadata = add_bank_ai_insights(results)
    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)
    export_path = destination_folder / "bank_reconciliation_results.xlsx"

    with pd.ExcelWriter(export_path, engine="openpyxl") as writer:
        results.to_excel(writer, sheet_name="Bank Reconciliation", index=False)

    return {
        "summary": _summary(results),
        "results": results,
        "export_path": export_path,
        "is_placeholder": False,
        "message": "Bank reconciliation completed using deterministic BigQuery-backed matching.",
        **ai_metadata,
    }
