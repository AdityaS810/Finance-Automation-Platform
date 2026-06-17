"""Vertex AI explanations for reconciliation exceptions.

The functions here never change rule-based statuses, confidence scores, dates,
amounts, GSTINs, invoice numbers, or accounting identifiers. They only append
short review notes for rows that need human attention.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from backend.ai.vertex_gemini_client import generate_vertex_text, is_vertex_gemini_configured


MAX_AI_ROWS = 20
STRONG_BANK_MATCH_THRESHOLD = 0.78
AI_UNAVAILABLE_MESSAGE = "Vertex AI insights unavailable. Showing rule-based reconciliation only."
AI_COLUMNS = ["ai_summary", "ai_recommendation", "ai_risk_level"]


def add_bank_ai_insights(results_df: pd.DataFrame, max_rows: int = MAX_AI_ROWS) -> tuple[pd.DataFrame, dict]:
    """Add Gemini explanation columns to uncertain bank reconciliation rows."""
    enriched_df = _with_ai_columns(results_df)
    uncertain_indexes = _bank_uncertain_indexes(enriched_df).head(max_rows).index
    if len(uncertain_indexes) == 0:
        return enriched_df, _ai_metadata("not_required", "No uncertain bank rows required Vertex AI insights.", 0)

    if not is_vertex_gemini_configured():
        return enriched_df, _ai_metadata("unavailable", AI_UNAVAILABLE_MESSAGE, 0)

    rows_enriched = 0
    for row_index in uncertain_indexes:
        prompt = _bank_prompt(enriched_df.loc[row_index])
        insight_text = generate_vertex_text(prompt)
        if insight_text is None:
            if rows_enriched == 0:
                return enriched_df, _ai_metadata("unavailable", AI_UNAVAILABLE_MESSAGE, 0)
            continue

        parsed = _parse_ai_response(insight_text)
        for column_name in AI_COLUMNS:
            enriched_df.at[row_index, column_name] = parsed[column_name]
        rows_enriched += 1

    status = "enabled" if rows_enriched > 0 else "unavailable"
    message = (
        f"Vertex AI insights added to {rows_enriched} bank exception row(s)."
        if rows_enriched > 0
        else AI_UNAVAILABLE_MESSAGE
    )
    return enriched_df, _ai_metadata(status, message, rows_enriched)


def add_gst_ai_insights(results_df: pd.DataFrame, max_rows: int = MAX_AI_ROWS) -> tuple[pd.DataFrame, dict]:
    """Add Gemini explanation columns to GST reconciliation exception rows."""
    enriched_df = _with_ai_columns(results_df)
    exception_indexes = _gst_exception_indexes(enriched_df).head(max_rows).index
    if len(exception_indexes) == 0:
        return enriched_df, _ai_metadata("not_required", "No GST exceptions required Vertex AI insights.", 0)

    if not is_vertex_gemini_configured():
        return enriched_df, _ai_metadata("unavailable", AI_UNAVAILABLE_MESSAGE, 0)

    rows_enriched = 0
    for row_index in exception_indexes:
        prompt = _gst_prompt(enriched_df.loc[row_index])
        insight_text = generate_vertex_text(prompt)
        if insight_text is None:
            if rows_enriched == 0:
                return enriched_df, _ai_metadata("unavailable", AI_UNAVAILABLE_MESSAGE, 0)
            continue

        parsed = _parse_ai_response(insight_text)
        for column_name in AI_COLUMNS:
            enriched_df.at[row_index, column_name] = parsed[column_name]
        rows_enriched += 1

    status = "enabled" if rows_enriched > 0 else "unavailable"
    message = (
        f"Vertex AI insights added to {rows_enriched} GST exception row(s)."
        if rows_enriched > 0
        else AI_UNAVAILABLE_MESSAGE
    )
    return enriched_df, _ai_metadata(status, message, rows_enriched)


def _with_ai_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with stable AI columns for Streamlit and Excel exports."""
    enriched_df = dataframe.copy()
    for column_name in AI_COLUMNS:
        if column_name not in enriched_df.columns:
            enriched_df[column_name] = ""
    return enriched_df


def _bank_uncertain_indexes(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Select bank rows where Gemini can help explain a review decision."""
    confidence = pd.to_numeric(dataframe.get("confidence_score", 0), errors="coerce").fillna(0)
    status = dataframe.get("match_status", pd.Series("", index=dataframe.index)).astype(str)
    return dataframe[
        status.isin(["possible_match", "unmatched"])
        | (confidence < STRONG_BANK_MATCH_THRESHOLD)
    ]


def _gst_exception_indexes(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Select GST rows where Gemini can help explain a review decision."""
    status = dataframe.get("match_status", pd.Series("", index=dataframe.index)).astype(str)
    return dataframe[status.isin(["mismatch", "missing_in_books", "missing_in_gstr"])]


def _bank_prompt(row: pd.Series) -> str:
    """Build a limited-context prompt for bank reconciliation review notes."""
    context = {
        "bank_date": _safe_value(row.get("bank_date")),
        "bank_narration": _safe_value(row.get("bank_narration")),
        "bank_amount": _safe_value(row.get("bank_amount")),
        "accounting_date": _safe_value(row.get("accounting_date")),
        "accounting_party_name": _safe_value(row.get("accounting_party_name")),
        "accounting_amount": _safe_value(row.get("accounting_amount")),
        "rule_based_match_status": _safe_value(row.get("match_status")),
        "rule_based_match_reason": _safe_value(row.get("match_reason")),
    }
    return _prompt_from_context("bank reconciliation exception", context)


def _gst_prompt(row: pd.Series) -> str:
    """Build a limited-context prompt for GST reconciliation review notes."""
    context = {
        "GSTIN": _safe_value(row.get("gstin")),
        "invoice_number": _safe_value(row.get("invoice_number")),
        "gstr_taxable_value": _safe_value(row.get("gstr_taxable_value")),
        "books_taxable_value": _safe_value(row.get("books_taxable_value")),
        "gstr_tax_amount": _safe_value(row.get("gstr_tax_amount")),
        "books_tax_amount": _safe_value(row.get("books_tax_amount")),
        "rule_based_match_status": _safe_value(row.get("match_status")),
        "rule_based_match_reason": _safe_value(row.get("match_reason")),
    }
    return _prompt_from_context("GST reconciliation exception", context)


def _prompt_from_context(exception_type: str, context: dict[str, Any]) -> str:
    """Create a strict JSON prompt that keeps Gemini in explanation mode."""
    return (
        "You explain finance reconciliation exceptions for human review. "
        "Do not calculate amounts. Do not decide the match. Do not change any "
        "finance value, date, GSTIN, invoice number, or accounting data. The "
        "rule-based match status and reason are the source of truth.\n\n"
        f"Exception type: {exception_type}\n"
        f"Limited context JSON: {json.dumps(context, ensure_ascii=True)}\n\n"
        "Return only valid JSON with exactly these keys: "
        "ai_summary, ai_recommendation, ai_risk_level. "
        "Use ai_risk_level as low, medium, or high. Keep each text value under 25 words."
    )


def _parse_ai_response(response_text: str) -> dict[str, str]:
    """Parse Gemini JSON, with a forgiving fallback for plain text responses."""
    payload = _extract_json_object(response_text)
    if payload:
        try:
            parsed = json.loads(payload)
            return {
                "ai_summary": _short_text(parsed.get("ai_summary")),
                "ai_recommendation": _short_text(parsed.get("ai_recommendation")),
                "ai_risk_level": _risk_level(parsed.get("ai_risk_level")),
            }
        except json.JSONDecodeError:
            pass

    return {
        "ai_summary": _short_text(response_text),
        "ai_recommendation": "Review supporting documents and confirm with the finance owner.",
        "ai_risk_level": "medium",
    }


def _extract_json_object(text: str) -> str | None:
    """Return the first JSON object from a model response, if present."""
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    return match.group(0)


def _safe_value(value: Any) -> str:
    """Convert values to prompt-safe strings without leaking extra context."""
    if value is None or pd.isna(value):
        return ""
    return str(value)[:300]


def _short_text(value: Any, max_length: int = 180) -> str:
    """Keep AI text concise enough for previews and Excel columns."""
    text = "" if value is None else str(value).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _risk_level(value: Any) -> str:
    """Normalise AI risk labels to a small, predictable vocabulary."""
    text = "" if value is None else str(value).strip().lower()
    if text in {"low", "medium", "high"}:
        return text
    if "high" in text:
        return "high"
    if "low" in text:
        return "low"
    return "medium"


def _ai_metadata(status: str, message: str, rows_processed: int) -> dict:
    """Build consistent metadata for backend returns and Streamlit messages."""
    return {
        "ai_status": status,
        "ai_enabled": status == "enabled",
        "ai_message": message,
        "ai_rows_processed": rows_processed,
        "ai_max_rows": MAX_AI_ROWS,
    }
