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
MIN_SUMMARY_WORDS = 12
MAX_SUMMARY_WORDS = 35
MAX_RECOMMENDATION_WORDS = 29


def add_bank_ai_insights(results_df: pd.DataFrame, max_rows: int = MAX_AI_ROWS) -> tuple[pd.DataFrame, dict]:
    """Add Gemini explanation columns to uncertain bank reconciliation rows."""
    enriched_df = _with_ai_columns(results_df)
    uncertain_indexes = _bank_uncertain_indexes(enriched_df).index
    if len(uncertain_indexes) == 0:
        return enriched_df, _ai_metadata("not_required", "No uncertain bank rows required Vertex AI insights.", 0, max_rows)

    _apply_rule_based_insights(enriched_df, uncertain_indexes, "bank")
    vertex_indexes = list(uncertain_indexes[:max_rows])
    if not is_vertex_gemini_configured():
        return enriched_df, _ai_metadata("unavailable", AI_UNAVAILABLE_MESSAGE, 0, max_rows)

    rows_enriched = 0
    for row_index in vertex_indexes:
        prompt = _bank_prompt(enriched_df.loc[row_index])
        insight_text = generate_vertex_text(prompt)
        if not insight_text:
            continue

        parsed = _parse_ai_response(insight_text, enriched_df.loc[row_index], "bank")
        for column_name in AI_COLUMNS:
            enriched_df.at[row_index, column_name] = parsed[column_name]
        rows_enriched += 1

    status = "enabled" if rows_enriched > 0 else "unavailable"
    message = (
        f"Vertex AI insights added to {rows_enriched} bank exception row(s)."
        if rows_enriched > 0
        else AI_UNAVAILABLE_MESSAGE
    )
    return enriched_df, _ai_metadata(status, message, rows_enriched, max_rows)


def add_gst_ai_insights(results_df: pd.DataFrame, max_rows: int = MAX_AI_ROWS) -> tuple[pd.DataFrame, dict]:
    """Add Gemini explanation columns to GST reconciliation exception rows."""
    enriched_df = _with_ai_columns(results_df)
    exception_indexes = _gst_exception_indexes(enriched_df).index
    if len(exception_indexes) == 0:
        return enriched_df, _ai_metadata("not_required", "No GST exceptions required Vertex AI insights.", 0, max_rows)

    _apply_rule_based_insights(enriched_df, exception_indexes, "gst")
    vertex_indexes = list(exception_indexes[:max_rows])
    if not is_vertex_gemini_configured():
        return enriched_df, _ai_metadata("unavailable", AI_UNAVAILABLE_MESSAGE, 0, max_rows)

    rows_enriched = 0
    for row_index in vertex_indexes:
        prompt = _gst_prompt(enriched_df.loc[row_index])
        insight_text = generate_vertex_text(prompt)
        if not insight_text:
            continue

        parsed = _parse_ai_response(insight_text, enriched_df.loc[row_index], "gst")
        for column_name in AI_COLUMNS:
            enriched_df.at[row_index, column_name] = parsed[column_name]
        rows_enriched += 1

    status = "enabled" if rows_enriched > 0 else "unavailable"
    message = (
        f"Vertex AI insights added to {rows_enriched} GST exception row(s)."
        if rows_enriched > 0
        else AI_UNAVAILABLE_MESSAGE
    )
    return enriched_df, _ai_metadata(status, message, rows_enriched, max_rows)


def _with_ai_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with stable AI columns for Streamlit and Excel exports."""
    enriched_df = dataframe.copy()
    for column_name in AI_COLUMNS:
        if column_name not in enriched_df.columns:
            enriched_df[column_name] = ""
    return enriched_df


def _apply_rule_based_insights(dataframe: pd.DataFrame, row_indexes: pd.Index, reconciliation_type: str) -> None:
    """Fill exception rows with useful deterministic explanations first."""
    for row_index in row_indexes:
        insight = _rule_based_insight(dataframe.loc[row_index], reconciliation_type)
        for column_name in AI_COLUMNS:
            dataframe.at[row_index, column_name] = insight[column_name]


def _rule_based_insight(row: pd.Series | dict[str, Any] | None, reconciliation_type: str) -> dict[str, str]:
    """Build a stable fallback insight from deterministic reconciliation fields."""
    if reconciliation_type == "gst":
        summary = _gst_fallback_summary(row)
        recommendation = _gst_fallback_recommendation(row)
    else:
        summary = _bank_fallback_summary(row)
        recommendation = _bank_fallback_recommendation(row)

    return {
        "ai_summary": _ensure_sentence(_limit_words(summary, MAX_SUMMARY_WORDS)),
        "ai_recommendation": _ensure_sentence(_limit_words(recommendation, MAX_RECOMMENDATION_WORDS)),
        "ai_risk_level": _risk_level_from_row(row, reconciliation_type),
    }


def _bank_uncertain_indexes(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Select bank rows where Gemini can help explain a review decision."""
    confidence = pd.to_numeric(dataframe.get("confidence_score", 0), errors="coerce").fillna(0)
    status = dataframe.get("match_status", pd.Series("", index=dataframe.index)).astype(str)
    return dataframe[
        (status != "ignored_opening_balance")
        & (
            status.isin(["possible_match", "unmatched", "bank_not_in_books", "books_not_in_bank"])
            | (confidence < STRONG_BANK_MATCH_THRESHOLD)
        )
    ]


def _gst_exception_indexes(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Select GST rows where Gemini can help explain a review decision."""
    status = dataframe.get("match_status", pd.Series("", index=dataframe.index)).astype(str)
    exceptions_df = dataframe[
        status.isin(
            [
                "amount_mismatch",
                "mismatch",
                "tax_component_mismatch",
                "possible_match",
                "missing_in_books",
                "missing_in_gstr",
            ]
        )
    ]
    dedupe_columns = [
        column_name
        for column_name in [
            "match_status",
            "gstin",
            "invoice_number",
            "invoice_date",
            "taxable_value_gstr",
            "taxable_value_books",
            "igst_gstr",
            "igst_books",
            "cgst_gstr",
            "cgst_books",
            "sgst_gstr",
            "sgst_books",
        ]
        if column_name in exceptions_df.columns
    ]
    if dedupe_columns:
        return exceptions_df.drop_duplicates(subset=dedupe_columns, keep="first")
    return exceptions_df


def _bank_fallback_summary(row: pd.Series | dict[str, Any] | None) -> str:
    """Create a useful bank exception sentence from rule-based fields."""
    reason = _row_text(row, "match_reason")
    reason_lower = reason.lower()
    status = _row_text(row, "match_status").lower()
    amount_difference = _amount_difference_from_reason(reason) or _bank_amount_difference(row)
    date_difference = _date_difference_from_reason(reason) or _bank_date_difference(row)
    status_label = f"status {status}" if status else "bank exception"
    bank_amount = _numeric_value(_row_value(row, "bank_amount"))
    accounting_amount = _numeric_value(_row_value(row, "accounting_amount"))

    if status == "bank_not_in_books" or "no accounting" in reason_lower or status == "unmatched":
        amount_text = f" for {bank_amount:.2f}" if bank_amount is not None else ""
        return (
            f"{status_label} bank line{amount_text} has no accounting match; narration, party, or reference evidence is insufficient."
        )
    if status == "books_not_in_bank":
        amount_text = f" for {accounting_amount:.2f}" if accounting_amount is not None else ""
        return (
            f"{status_label} accounting entry{amount_text} has no bank line; check if it belongs to another period or account."
        )
    if amount_difference is not None and amount_difference > 0 and "weak" in reason_lower:
        return (
            f"{status_label} shows amount difference {amount_difference:.2f} with weak narration, party, or reference similarity."
        )
    if amount_difference is not None and amount_difference > 0:
        return f"{status_label} shows amount difference {amount_difference:.2f} between bank and accounting records."
    if date_difference is not None and date_difference > 0 and "date differs" in reason_lower:
        return f"{status_label} has a {date_difference} day date gap; verify whether this is timing or the wrong voucher."
    if "weak" in reason_lower or "similarity" in reason_lower:
        return f"{status_label} has weak narration, party, or reference similarity despite partial amount/date signals."
    if status == "possible_match":
        return f"{status_label} has reviewable amount/date signals but weak narration, party, or reference support."
    return f"{status_label} needs review because bank amount, date, narration, party, or reference signals are incomplete."


def _bank_fallback_recommendation(row: pd.Series | dict[str, Any] | None) -> str:
    """Recommend a bank review action from the deterministic reason."""
    reason_lower = _row_text(row, "match_reason").lower()
    status = _row_text(row, "match_status").lower()
    amount_difference = _amount_difference_from_reason(reason_lower) or _bank_amount_difference(row)
    date_difference = _date_difference_from_reason(reason_lower) or _bank_date_difference(row)

    if status == "bank_not_in_books" or "no accounting" in reason_lower or status == "unmatched":
        return "Create missing Zoho payment or expense entry if the bank debit or receipt is genuine."
    if status == "books_not_in_bank":
        return "Check whether this accounting entry cleared in another bank account, period, or grouped settlement."
    if "salary" in reason_lower or "tax" in reason_lower or "charge" in reason_lower or "transfer" in reason_lower:
        return "Check whether this is salary, tax, bank charge, or internal transfer before posting."
    if "weak" in reason_lower or "similarity" in reason_lower:
        return "Verify bank narration against Zoho voucher and approve only if party or reference matches."
    if date_difference and date_difference > 0:
        return "Confirm whether the date gap is normal clearing timing or a duplicate/wrong voucher."
    if amount_difference and amount_difference > 0:
        return "Compare bank amount with voucher total, bank charges, TDS, or grouped settlement lines."
    return "Review party, reference, voucher, and bank narration before final approval."


def _gst_fallback_summary(row: pd.Series | dict[str, Any] | None) -> str:
    """Create a useful GST exception sentence from rule-based fields."""
    reason = _row_text(row, "match_reason")
    reason_lower = reason.lower()
    status = _row_text(row, "match_status").lower()
    match_level = _row_text(row, "match_level").upper()
    tax_difference = _gst_tax_difference(row)
    taxable_difference = _gst_taxable_difference(row)
    component_difference = _gst_component_difference(row)
    invoice_number = _row_text(row, "invoice_number") or "this invoice"
    gstin = _row_text(row, "gstin") or _row_text(row, "zoho_gstin") or _row_text(row, "gstr_gstin") or "GSTIN unavailable"
    supplier_name = _row_text(row, "supplier_name") or _row_text(row, "party_name") or "the supplier"
    amount_difference = max(tax_difference or 0, taxable_difference or 0, component_difference or 0)

    if status == "missing_in_books" or "no accounting" in reason_lower:
        return f"missing_in_books invoice {invoice_number} for {gstin} is in GSTR but absent from books."
    if status == "missing_in_gstr" or "not found in uploaded gstr" in reason_lower:
        return f"missing_in_gstr invoice {invoice_number} for {supplier_name} exists in books but not the uploaded GSTR."
    if status == "matched":
        return f"Invoice {invoice_number} matches between books and GSTR with no tax difference."
    if status == "tax_component_mismatch":
        return f"tax_component_mismatch invoice {invoice_number} has matching value but IGST, CGST, or SGST classification differs."
    if status in {"amount_mismatch", "mismatch"} and amount_difference > 0:
        return f"{status} invoice {invoice_number} matched GSTIN/invoice signals but amount or tax differs by {amount_difference:.2f}."
    if status == "possible_match" and match_level == "P4_WEAK_INVOICE":
        return f"possible_match invoice {invoice_number} has GSTIN, date, amount, or invoice-format weakness."
    if status == "possible_match" and match_level.startswith("P6_SAFEGUARD"):
        return f"possible_match invoice {invoice_number} has selected-upload evidence but needs GSTIN, invoice, date, or amount review."
    if status == "possible_match" and match_level == "P3_FORMAT":
        return f"possible_match invoice {invoice_number} matched after invoice-number formatting cleanup; confirm GSTIN, date, and amount."
    if status == "possible_match" and match_level == "P5_AMOUNT_DATE_CANDIDATE":
        return f"possible_match invoice {invoice_number} has amount, nearby-date, and supplier-name signals but weak invoice/GSTIN support."
    if status == "possible_match":
        return f"possible_match invoice {invoice_number} has partial GSTIN, invoice, date, amount, or tax evidence."
    if "gstin" in reason_lower and "invoice" not in reason_lower:
        return f"GSTIN signal is weak for invoice {invoice_number}, so books and GSTR party identity need review."
    if "invoice" in reason_lower and "matched" not in reason_lower:
        return f"Invoice number signal is weak for {invoice_number}, though GSTIN/date/amount may still indicate a candidate."
    if tax_difference is not None and tax_difference > 0:
        return f"Tax amount differs by {tax_difference:.2f} between GSTR and books for invoice {invoice_number}."
    if taxable_difference is not None and taxable_difference > 0:
        return f"Taxable value differs by {taxable_difference:.2f} between GSTR and books for invoice {invoice_number}."
    if "tax amount" in reason_lower or "tax amounts differ" in reason_lower:
        return f"GSTIN and invoice number match for {invoice_number}, but tax amounts differ."
    return f"GST exception for invoice {invoice_number} needs review across GSTIN, invoice, date, amount, or tax signals."


def _gst_fallback_recommendation(row: pd.Series | dict[str, Any] | None) -> str:
    """Recommend a GST review action from the deterministic reason."""
    status = _row_text(row, "match_status").lower()
    match_level = _row_text(row, "match_level").upper()
    reason_lower = _row_text(row, "match_reason").lower()
    if status == "missing_in_books":
        return "Record the missing purchase bill in Zoho if the GSTR entry is valid."
    if status == "missing_in_gstr":
        return "Verify supplier invoice number and GSTIN before claiming or following up on ITC."
    if status == "tax_component_mismatch":
        return "Check CGST/SGST versus IGST classification and place of supply before filing."
    if status == "possible_match":
        if match_level == "P4_WEAK_INVOICE":
            return "Compare GSTIN, invoice date, invoice number, and values before treating this as reconciled."
        if match_level.startswith("P6_SAFEGUARD"):
            return "Review the candidate GSTR row against Zoho/books before taking filing action."
        if match_level == "P3_FORMAT":
            return "Confirm formatted invoice number against source documents and supplier GSTIN."
        if match_level == "P5_AMOUNT_DATE_CANDIDATE":
            return "Compare source invoice, books record, GSTR row, and supplier GSTIN."
        return "Confirm invoice number, GSTIN, date, and values before matching."
    if "invoice" in reason_lower or "tax" in reason_lower or status in {"amount_mismatch", "mismatch"}:
        return "Check invoice number, taxable value, and tax amounts before filing."
    if "gstin" in reason_lower:
        return "Verify GSTIN against vendor master and source invoice."
    return "Review GSTIN, invoice number, date, taxable value, and tax breakup before filing."


def _bank_prompt(row: pd.Series) -> str:
    """Build a limited-context prompt for bank reconciliation review notes."""
    context = {
        "bank_date": _safe_value(row.get("bank_date")),
        "bank_narration": _safe_value(row.get("bank_narration")),
        "bank_amount": _safe_value(row.get("bank_amount")),
        "accounting_date": _safe_value(row.get("accounting_date")),
        "accounting_party_name": _safe_value(row.get("accounting_party_name")),
        "accounting_amount": _safe_value(row.get("accounting_amount")),
        "reference_number": _safe_value(row.get("reference_number")),
        "transaction_number": _safe_value(row.get("transaction_number")),
        "transaction_type": _safe_value(row.get("transaction_type")),
        "confidence_score": _safe_value(row.get("confidence_score")),
        "rule_based_match_status": _safe_value(row.get("match_status")),
        "rule_based_match_reason": _safe_value(row.get("match_reason")),
        "action_required": _safe_value(row.get("action_required")),
    }
    return _prompt_from_context("bank reconciliation exception", context)


def _gst_prompt(row: pd.Series) -> str:
    """Build a limited-context prompt for GST reconciliation review notes."""
    context = {
        "GSTIN": _safe_value(row.get("gstin")),
        "invoice_number": _safe_value(row.get("invoice_number")),
        "invoice_date": _safe_value(row.get("invoice_date")),
        "zoho_invoice_date": _safe_value(row.get("zoho_invoice_date")),
        "gstr_invoice_date": _safe_value(row.get("gstr_invoice_date")),
        "taxable_value_gstr": _safe_value(row.get("taxable_value_gstr")),
        "taxable_value_books": _safe_value(row.get("taxable_value_books")),
        "tax_amount_gstr": _safe_value(row.get("tax_amount_gstr")),
        "tax_amount_books": _safe_value(row.get("tax_amount_books")),
        "igst_gstr": _safe_value(row.get("igst_gstr")),
        "igst_books": _safe_value(row.get("igst_books")),
        "cgst_gstr": _safe_value(row.get("cgst_gstr")),
        "cgst_books": _safe_value(row.get("cgst_books")),
        "sgst_gstr": _safe_value(row.get("sgst_gstr")),
        "sgst_books": _safe_value(row.get("sgst_books")),
        "invoice_value_gstr": _safe_value(row.get("invoice_value_gstr")),
        "invoice_value_books": _safe_value(row.get("invoice_value_books")),
        "rule_based_match_level": _safe_value(row.get("match_level")),
        "rule_based_match_status": _safe_value(row.get("match_status")),
        "rule_based_match_reason": _safe_value(row.get("match_reason")),
        "action_required": _safe_value(row.get("action_required")),
    }
    return _prompt_from_context("GST reconciliation exception", context)


def _prompt_from_context(exception_type: str, context: dict[str, Any]) -> str:
    """Create a strict JSON prompt that keeps Gemini in explanation mode."""
    bank_guidance = (
        "For bank reconciliation, write like a finance reviewer. The summary must mention match_status, "
        "amount/date signal if available, narration/party/reference weakness, and whether it appears to be "
        "missing booking, missing bank entry, or a reviewable possible match. Recommendations should name "
        "the next finance action, such as verifying bank narration against Zoho voucher, creating a missing "
        "payment/expense entry, or checking salary, tax, bank charge, or internal transfer treatment."
    )
    gst_guidance = (
        "For GST reconciliation, write like a GST reviewer. The summary must mention GSTIN/invoice/date/"
        "amount/tax component signal when available and identify missing_in_books, missing_in_gstr, "
        "tax_component_mismatch, amount_mismatch, or possible_match. Recommendations should name the next "
        "filing action, such as verifying supplier invoice/GSTIN, checking CGST/SGST versus IGST, or recording "
        "a missing purchase bill in Zoho."
    )
    type_guidance = bank_guidance if exception_type.startswith("bank") else gst_guidance
    return (
        "You are writing concise finance-review notes for reconciliation exception tables. "
        "Do not calculate amounts. Do not decide the match. Do not change any "
        "finance value, date, GSTIN, invoice number, or accounting data. The "
        "rule-based match status, confidence score, and reason are the source of truth. "
        "Explain the actual row-specific reason using the supplied amount, date, "
        "narration, party, reference, GSTIN, invoice, or tax fields where relevant. Avoid "
        "generic repeated wording unless two rows are truly identical. Do not mention Gemini, AI, model, or prompt.\n\n"
        f"Exception type: {exception_type}\n"
        f"Review guidance: {type_guidance}\n"
        f"Limited context JSON: {json.dumps(context, ensure_ascii=True)}\n\n"
        "Return strict JSON only. Do not wrap it in markdown fences. Do not add prose. "
        "Use exactly these keys: ai_summary, ai_recommendation, ai_risk_level. "
        "ai_summary must be one full sentence between 12 and 35 words. It must mention "
        "at least one concrete reason from the row, such as amount difference, date "
        "difference, weak narration or party similarity, missing accounting record, "
        "missing bank line, GSTIN mismatch, invoice mismatch, or tax amount/component difference. "
        "ai_recommendation must be specific and under 30 words. "
        "ai_risk_level must be exactly low, medium, or high. Risk rules: high for missing_in_books, "
        "missing_in_gstr, bank_not_in_books, books_not_in_bank, or large amount mismatch; medium for "
        "possible_match, amount_mismatch, or tax_component_mismatch; low only for minor formatting, "
        "date, or reference issues."
    )


def _parse_ai_response(
    response_text: str,
    row: pd.Series | dict[str, Any] | None = None,
    reconciliation_type: str = "bank",
) -> dict[str, str]:
    """Parse Gemini JSON, with a forgiving fallback for plain text responses."""
    cleaned_response = _clean_ai_text(response_text)
    payload = _extract_json_object(cleaned_response)
    fallback = _rule_based_insight(row, reconciliation_type)
    if payload:
        try:
            parsed = json.loads(payload)
            summary = _summary_or_fallback(parsed.get("ai_summary"), fallback["ai_summary"])
            recommendation = _recommendation_or_fallback(
                parsed.get("ai_recommendation"),
                fallback["ai_recommendation"],
            )
            return {
                "ai_summary": summary,
                "ai_recommendation": recommendation,
                "ai_risk_level": fallback["ai_risk_level"],
            }
        except json.JSONDecodeError:
            pass

    summary = _summary_or_fallback(cleaned_response, fallback["ai_summary"])
    return {
        "ai_summary": summary,
        "ai_recommendation": fallback["ai_recommendation"],
        "ai_risk_level": fallback["ai_risk_level"],
    }


def _summary_or_fallback(value: Any, fallback_summary: str) -> str:
    """Accept only useful one-sentence summaries, otherwise use fallback."""
    summary = _ensure_sentence(_short_text(value, max_words=MAX_SUMMARY_WORDS, max_length=220))
    if _is_useful_summary(summary):
        return summary
    return fallback_summary


def _recommendation_or_fallback(value: Any, fallback_recommendation: str) -> str:
    """Keep recommendations specific enough for reviewer action."""
    recommendation = _ensure_sentence(_short_text(value, max_words=MAX_RECOMMENDATION_WORDS, max_length=180))
    if _word_count(recommendation) >= 4 and not _is_generic_recommendation(recommendation):
        return recommendation
    return fallback_recommendation


def _is_useful_summary(summary: str) -> bool:
    """Reject short, generic, or reason-free model summaries."""
    words = _word_count(summary)
    if words < MIN_SUMMARY_WORDS or words > MAX_SUMMARY_WORDS:
        return False
    lowered = summary.lower().strip(" .")
    if lowered in {"bank", "bank transaction", "bank and accounting", "gst", "gst transaction"}:
        return False
    return _mentions_concrete_reason(lowered)


def _mentions_concrete_reason(text: str) -> bool:
    """Return True when summary names a concrete reconciliation reason."""
    concrete_terms = [
        "amount",
        "date",
        "narration",
        "party",
        "similarity",
        "missing",
        "accounting record",
        "gstin",
        "invoice",
        "tax",
        "taxable",
        "rounding",
        "timing",
        "duplicate",
    ]
    return any(term in text for term in concrete_terms)


def _is_generic_recommendation(recommendation: str) -> bool:
    """Detect model recommendations that are too vague for review workflow."""
    lowered = recommendation.lower().strip(" .")
    return lowered in {
        "review",
        "check",
        "verify",
        "review manually",
        "needs review",
        "review before approval",
    }


def _extract_json_object(text: str) -> str | None:
    """Return the first JSON object from a model response, if present."""
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    return match.group(0)


def _clean_ai_text(text: str) -> str:
    """Remove markdown fences and JSON wrapper artifacts from AI output."""
    cleaned = "" if text is None else str(text).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.replace("```json", "").replace("```", "")
    return cleaned.strip()


def _safe_value(value: Any) -> str:
    """Convert values to prompt-safe strings without leaking extra context."""
    if value is None or pd.isna(value):
        return ""
    return str(value)[:300]


def _short_text(value: Any, max_words: int = 30, max_length: int = 180) -> str:
    """Keep AI text concise enough for previews and Excel columns."""
    text = _clean_ai_text("" if value is None else str(value).strip())
    text = text.replace("{", "").replace("}", "").replace("`", "")
    text = re.sub(r"\bjson\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r'"?ai_summary"?\s*:\s*', "", text, flags=re.IGNORECASE)
    text = re.sub(r'"?ai_recommendation"?\s*:\s*.*$', "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'"?ai_risk_level"?\s*:\s*.*$', "", text, flags=re.IGNORECASE | re.DOTALL)
    text = text.replace('"', "").strip(" ,:-")
    text = re.sub(r"\s+", " ", text)
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _limit_words(text: str, max_words: int) -> str:
    """Limit text by words while keeping the beginning readable."""
    words = re.sub(r"\s+", " ", text).strip().split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def _ensure_sentence(text: str) -> str:
    """Make sure reviewer-facing text reads as a complete sentence."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned[0].upper() + cleaned[1:]


def _word_count(text: str) -> int:
    """Count words in a reviewer-facing sentence."""
    return len(re.findall(r"\b[\w/.-]+\b", text or ""))


def _row_value(row: pd.Series | dict[str, Any] | None, column_name: str) -> Any:
    """Read a value from a Series/dict row without raising for missing fields."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(column_name)
    return row.get(column_name)


def _row_text(row: pd.Series | dict[str, Any] | None, column_name: str) -> str:
    """Read a row value as text."""
    value = _row_value(row, column_name)
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _numeric_value(value: Any) -> float | None:
    """Convert scalar values to floats, returning None for blanks."""
    if value is None or pd.isna(value):
        return None
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    return float(numeric)


def _amount_difference_from_reason(reason: str) -> float | None:
    """Extract an amount difference from deterministic match_reason text."""
    match = re.search(r"amount differs by\s+([0-9,.]+)", str(reason), flags=re.IGNORECASE)
    if not match:
        return None
    return _numeric_value(match.group(1).replace(",", ""))


def _date_difference_from_reason(reason: str) -> int | None:
    """Extract a day difference from deterministic match_reason text."""
    match = re.search(r"date differs by\s+(\d+)", str(reason), flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _bank_amount_difference(row: pd.Series | dict[str, Any] | None) -> float | None:
    """Compute bank/accounting amount difference if both values are present."""
    bank_amount = _numeric_value(_row_value(row, "bank_amount"))
    accounting_amount = _numeric_value(_row_value(row, "accounting_amount"))
    if bank_amount is None or accounting_amount is None:
        return None
    return abs(bank_amount - accounting_amount)


def _bank_date_difference(row: pd.Series | dict[str, Any] | None) -> int | None:
    """Compute bank/accounting date difference if both values are present."""
    bank_date = pd.to_datetime(_row_value(row, "bank_date"), errors="coerce")
    accounting_date = pd.to_datetime(_row_value(row, "accounting_date"), errors="coerce")
    if pd.isna(bank_date) or pd.isna(accounting_date):
        return None
    return abs((bank_date.date() - accounting_date.date()).days)


def _gst_tax_difference(row: pd.Series | dict[str, Any] | None) -> float | None:
    """Compute total GST tax difference if both values are present."""
    gstr_tax = _numeric_value(_row_value(row, "tax_amount_gstr"))
    if gstr_tax is None:
        gstr_tax = _numeric_value(_row_value(row, "gstr_tax_amount"))
    books_tax = _numeric_value(_row_value(row, "tax_amount_books"))
    if books_tax is None:
        books_tax = _numeric_value(_row_value(row, "books_tax_amount"))
    if gstr_tax is None or books_tax is None:
        return None
    return abs(gstr_tax - books_tax)


def _gst_taxable_difference(row: pd.Series | dict[str, Any] | None) -> float | None:
    """Compute taxable value difference if both values are present."""
    gstr_taxable = _numeric_value(_row_value(row, "taxable_value_gstr"))
    if gstr_taxable is None:
        gstr_taxable = _numeric_value(_row_value(row, "gstr_taxable_value"))
    books_taxable = _numeric_value(_row_value(row, "taxable_value_books"))
    if books_taxable is None:
        books_taxable = _numeric_value(_row_value(row, "books_taxable_value"))
    if gstr_taxable is None or books_taxable is None:
        return None
    return abs(gstr_taxable - books_taxable)


def _gst_component_difference(row: pd.Series | dict[str, Any] | None) -> float | None:
    """Compute GST component difference across IGST/CGST/SGST when available."""
    differences = []
    for left_column, right_column in [
        ("igst_gstr", "igst_books"),
        ("cgst_gstr", "cgst_books"),
        ("sgst_gstr", "sgst_books"),
    ]:
        left_value = _numeric_value(_row_value(row, left_column))
        right_value = _numeric_value(_row_value(row, right_column))
        if left_value is not None and right_value is not None:
            differences.append(abs(left_value - right_value))
    if not differences:
        return None
    return sum(differences)


def _risk_level_from_row(row: pd.Series | dict[str, Any] | None, reconciliation_type: str) -> str:
    """Set risk from deterministic exception size and type."""
    reason_lower = _row_text(row, "match_reason").lower()
    status = _row_text(row, "match_status").lower()
    if "missing" in status or "missing" in reason_lower or "no accounting" in reason_lower:
        return "high"

    if reconciliation_type == "gst":
        if status in {"missing_in_books", "missing_in_gstr"}:
            return "high"
        amount_difference = max(
            _gst_tax_difference(row) or 0,
            _gst_taxable_difference(row) or 0,
            _gst_component_difference(row) or 0,
        )
        if status in {"possible_match", "amount_mismatch", "tax_component_mismatch"}:
            if amount_difference >= 1000:
                return "high"
            return "medium"
        if amount_difference >= 1000:
            return "high"
        if amount_difference > 1:
            return "medium"
        return "low"

    amount_difference = _amount_difference_from_reason(reason_lower) or _bank_amount_difference(row) or 0
    date_difference = _date_difference_from_reason(reason_lower) or _bank_date_difference(row) or 0
    if amount_difference >= 1000 or date_difference > 7:
        return "high"
    if "weak" in reason_lower or "similarity" in reason_lower or status == "possible_match":
        return "medium"
    if amount_difference <= 1 and date_difference <= 3:
        return "low"
    return "medium"


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


def _ai_metadata(status: str, message: str, rows_processed: int, max_rows: int = MAX_AI_ROWS) -> dict:
    """Build consistent metadata for backend returns and Streamlit messages."""
    return {
        "ai_status": status,
        "ai_enabled": status == "enabled",
        "ai_message": message,
        "ai_rows_processed": rows_processed,
        "ai_max_rows": max_rows,
    }
