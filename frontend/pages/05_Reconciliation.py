"""Reconciliation page."""

from __future__ import annotations

import traceback
from html import escape
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from backend.reconciliation.bank_reconciliation import run_bank_reconciliation
from backend.reconciliation.gst_reconciliation import run_gst_reconciliation
from backend.reconciliation.upload_registry import fetch_reconciliation_uploads
from backend.services.upload_service import delete_bank_upload, delete_gstr_upload
from src.ui import file_summary_card, load_css, page_header, section_card
from src.utils.file_helpers import get_output_root


load_css()

page_header(
    "Reconciliation",
    "Match uploaded bank and GST data against accounting records. Reconciliation uses final business-ready data from the Consume layer.",
)

st.markdown(
    """
    <style>
    div.block-container {
        padding-top: 1.25rem;
    }
    div[data-testid="stVerticalBlock"] {
        gap: 0.55rem;
    }
    div[data-testid="stTabs"] div[role="tablist"] {
        flex-wrap: wrap;
        gap: 0.35rem;
        border-bottom: 0;
        align-items: center;
        margin: 0.15rem 0 0.65rem;
    }
    div[data-testid="stTabs"] button[role="tab"] {
        min-height: 1.9rem;
        padding: 0.2rem 0.7rem;
        border: 1px solid #c8d7ea;
        border-radius: 8px;
        background: #f6f8fb;
        color: #1f2937;
        box-shadow: none;
    }
    div[data-testid="stTabs"] button[role="tab"] p {
        font-size: 0.82rem;
        line-height: 1.05;
        color: inherit;
        font-weight: 600;
    }
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
        border-color: #1d4ed8;
        background: #1d4ed8;
        color: #ffffff;
        font-weight: 700;
    }
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p {
        color: #ffffff;
    }
    div[data-testid="stButton"] > button {
        min-height: 2.35rem;
        border-radius: 8px;
        font-weight: 700;
    }
    div[data-testid="stButton"] > button:disabled,
    div[data-testid="stButton"] > button[disabled] {
        background: #e8eef7 !important;
        border-color: #b9c7da !important;
        color: #42526e !important;
        opacity: 1 !important;
    }
    div[data-testid="stCheckbox"] {
        padding-top: 0.35rem;
    }
    .recon-warning-card {
        border: 1px solid #f4c27a;
        border-left: 4px solid #d97706;
        border-radius: 8px;
        background: #fff8eb;
        padding: 0.75rem 0.9rem;
        margin: 0.1rem 0 0.35rem;
    }
    .recon-warning-card strong {
        display: block;
        color: #7c2d12;
        font-size: 0.92rem;
        margin-bottom: 0.15rem;
    }
    .recon-warning-card p {
        color: #3f2a13;
        font-size: 0.88rem;
        line-height: 1.35;
        margin: 0;
    }
    .recon-warning-card span {
        display: block;
        color: #7c3f00;
        font-size: 0.76rem;
        margin-top: 0.35rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

bank_tab, gst_tab = st.tabs(["Bank Reconciliation", "GST Reconciliation"])
output_dir = get_output_root() / "reconciliation_exports"
AI_PREVIEW_COLUMNS = [
    "match_status",
    "confidence_score",
    "match_reason",
    "ai_summary",
    "ai_recommendation",
    "ai_risk_level",
]
TECHNICAL_DEFAULT_HIDE_COLUMNS = [
    "selected_upload_id",
    "reconciliation_timestamp",
    "source_org_key",
    "source_record_id",
    "loaded_at",
    "run_id",
    "raw_json",
]
GEMINI_RESULT_COLUMNS = [
    "gemini_suggestion",
    "gemini_confidence",
    "gemini_reason",
    "gemini_recommendation",
    "gemini_candidate_id",
]
GEMINI_DISPLAY_LABELS = {
    "gemini_suggestion": "Gemini Suggestion",
    "gemini_confidence": "Confidence",
    "gemini_reason": "Reason",
    "gemini_recommendation": "Recommendation",
}
STATUS_LABELS = {
    "bank_not_in_books": "Bank Only",
    "books_not_in_bank": "Books Only",
    "possible_match": "Review Match",
    "missing_in_books": "Missing in Books",
    "missing_in_gstr": "Missing in GSTR",
    "amount_mismatch": "Amount Issue",
    "tax_component_mismatch": "Tax Type Issue",
    "exact_match": "Matched",
    "matched": "Matched",
}
BANK_NEEDS_REVIEW_STATUSES = ["bank_not_in_books", "books_not_in_bank", "possible_match"]
BANK_DETAIL_TABS = {
    "High Priority": [],
    "Bank Only": ["bank_not_in_books"],
    "Books Only": ["books_not_in_bank"],
    "Review Match": ["possible_match"],
    "Matched": ["matched"],
}
GST_NEEDS_REVIEW_STATUSES = [
    "missing_in_books",
    "missing_in_gstr",
    "amount_mismatch",
    "tax_component_mismatch",
    "possible_match",
]
GST_DETAIL_TABS = {
    "High Priority": [],
    "Missing in Books": ["missing_in_books"],
    "Missing in GSTR": ["missing_in_gstr"],
    "Tax Type Issue": ["tax_component_mismatch"],
    "Amount Issue": ["amount_mismatch"],
    "Review Match": ["possible_match"],
    "Matched": ["exact_match", "matched"],
}
BANK_REVIEW_COLUMNS = [
    "review_priority",
    "status_label",
    "ai_risk_level",
    "confidence_score",
    "bank_date",
    "bank_narration",
    "bank_amount",
    "accounting_date",
    "accounting_party_name",
    "accounting_amount",
    "transaction_type",
    "reference_number",
    "match_reason",
    "ai_summary",
    "ai_recommendation",
    "gemini_suggestion",
    "gemini_confidence",
    "gemini_reason",
    "gemini_recommendation",
]
BANK_TECHNICAL_COLUMNS = BANK_REVIEW_COLUMNS + [
    "bank_line_id",
    "upload_id",
    "raw_row_number",
    "debit_amount",
    "credit_amount",
    "balance_amount",
    "accounting_record_id",
    "transaction_number",
    "selected_upload_id",
    "selected_file_name",
    "reconciliation_timestamp",
]
GST_REVIEW_COLUMNS = [
    "review_priority",
    "status_label",
    "ai_risk_level",
    "gstr_invoice_number",
    "books_invoice_number",
    "zoho_invoice_number",
    "gstr_supplier_name",
    "party_name",
    "gstr_gstin",
    "gstin",
    "gstr_invoice_date",
    "books_invoice_date",
    "zoho_invoice_date",
    "taxable_value",
    "taxable_value_gstr",
    "taxable_value_books",
    "invoice_value",
    "invoice_value_gstr",
    "invoice_value_books",
    "match_reason",
    "ai_summary",
    "ai_recommendation",
    "gemini_suggestion",
    "gemini_confidence",
    "gemini_reason",
    "gemini_recommendation",
]
HIGH_PRIORITY_STATUSES = {
    "bank_not_in_books",
    "books_not_in_bank",
    "missing_in_books",
    "missing_in_gstr",
    "amount_mismatch",
}
MEDIUM_PRIORITY_STATUSES = {"possible_match"}
GST_PREVIEW_COLUMNS = [
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
GST_POSSIBLE_MATCH_COLUMNS = [
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
GST_TECHNICAL_COLUMNS = GST_PREVIEW_COLUMNS + [
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
GST_STAGE_PROGRESS = {
    "Loading selected GSTR upload": 15,
    "Loading accounting GST records": 35,
    "Running rule-based GST matching": 60,
    "Generating Vertex AI insights": 80,
    "Writing Excel output": 92,
}
BANK_STAGE_PROGRESS = {
    "Loading selected bank upload...": 15,
    "Running bank reconciliation...": 45,
    "Generating Vertex AI insights...": 72,
    "Preparing rule-based review notes...": 72,
    "Preparing review summary...": 92,
    "Bank reconciliation completed.": 100,
}


def _download_excel_button(label: str, export_path: Path, key: str | None = None) -> None:
    """Render a download button for a generated Excel workbook."""
    if not export_path.exists():
        return

    with open(export_path, "rb") as export_file:
        st.download_button(
            label,
            data=export_file.read(),
            file_name=export_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=key,
        )


def _download_dataframe_button(label: str, dataframe: pd.DataFrame, file_name: str, key: str) -> None:
    """Render an Excel download button for a dataframe subset."""
    if dataframe.empty:
        st.download_button(label, data=b"", file_name=file_name, disabled=True, use_container_width=True, key=key)
        return

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="Rows", index=False)

    st.download_button(
        label,
        data=output.getvalue(),
        file_name=file_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key=key,
    )


def _show_ai_status(result: dict) -> None:
    """Show whether optional Vertex AI exception notes were added."""
    ai_status = result.get("ai_status", "unavailable")

    if ai_status == "skipped":
        st.info("Review notes skipped. Matching remains rule-based.")
    else:
        st.info("Review notes are added for exception rows.")


def _show_ai_summary_card(result: dict, results_df: pd.DataFrame, title: str) -> None:
    """Render a top-level summary of generated or fallback review notes."""
    _show_ai_status(result)


def _summary_metric_card(title: str, value: object, caption: str = "") -> None:
    """Render a compact high-contrast finance summary card."""
    st.markdown(
        f"""
        <div style="border:1px solid #d8dee4;border-radius:8px;padding:12px 14px;background:#ffffff;">
            <div style="font-size:0.82rem;color:#57606a;margin-bottom:4px;">{title}</div>
            <div style="font-size:1.55rem;font-weight:700;color:#1f2328;line-height:1.2;">{value}</div>
            <div style="font-size:0.78rem;color:#6e7781;margin-top:4px;">{caption}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _show_summary_cards(cards: list[tuple[str, object, str]]) -> None:
    columns = st.columns(len(cards))
    for column, (title, value, caption) in zip(columns, cards):
        with column:
            _summary_metric_card(title, value, caption)


def _show_download_buttons(export_path: Path, review_df: pd.DataFrame, key_prefix: str) -> None:
    export_columns = st.columns(2)
    with export_columns[0]:
        _download_excel_button("Download full report", export_path, key=f"{key_prefix}_full_reconciliation_download")
    with export_columns[1]:
        _download_dataframe_button(
            "Download review exceptions",
            review_df,
            f"{key_prefix}_review_exceptions.xlsx",
            key=f"{key_prefix}_review_exceptions_download",
        )


def _show_suggested_review_order(items: list[str]) -> None:
    st.markdown("**Suggested review order**")
    st.markdown("\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1)))


def _review_priority(row: pd.Series) -> str:
    """Classify UI-only review priority from status and AI risk."""
    status = str(row.get("match_status", "") or "").lower()
    risk = str(row.get("ai_risk_level", "") or "").lower()
    if risk == "high" or status in HIGH_PRIORITY_STATUSES:
        return "High Priority"
    if risk == "medium" or status in MEDIUM_PRIORITY_STATUSES:
        return "Medium Priority"
    return "Low Priority"


def _with_review_priority(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Return a display copy with UI-only review priority."""
    display_df = dataframe.copy()
    if display_df.empty:
        display_df["review_priority"] = pd.Series(dtype="object")
        return display_df
    display_df["review_priority"] = display_df.apply(_review_priority, axis=1)
    return display_df


def _with_status_label(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Add UI-only status labels without changing backend status values."""
    display_df = dataframe.copy()
    if "match_status" not in display_df.columns:
        return display_df

    display_df["status_label"] = (
        display_df["match_status"]
        .fillna("")
        .astype(str)
        .map(STATUS_LABELS)
        .fillna(display_df["match_status"].fillna("").astype(str).str.replace("_", " ").str.title())
    )
    return display_df


def _friendly_dataframe(dataframe: pd.DataFrame, preferred_columns: list[str]) -> pd.DataFrame:
    """Order business-facing columns first and hide noisy audit columns by default."""
    display_df = _with_status_label(dataframe)
    available_preferred = [column for column in preferred_columns if column in display_df.columns]
    remaining = [
        column
        for column in display_df.columns
        if column not in available_preferred and column not in TECHNICAL_DEFAULT_HIDE_COLUMNS and column != "match_status"
    ]
    return display_df.loc[:, available_preferred + remaining].copy()


def _bank_review_dataframe(dataframe: pd.DataFrame, show_gemini: bool = False) -> pd.DataFrame:
    display_df = dataframe.drop(columns=GEMINI_RESULT_COLUMNS, errors="ignore") if not show_gemini else dataframe
    display_df = _friendly_dataframe(_with_review_priority(display_df), BANK_REVIEW_COLUMNS)
    display_df = display_df.drop(columns=["gemini_candidate_id"], errors="ignore")
    return display_df.rename(columns=GEMINI_DISPLAY_LABELS)


def _gst_review_dataframe(dataframe: pd.DataFrame, show_gemini: bool = False) -> pd.DataFrame:
    display_df = dataframe.drop(columns=GEMINI_RESULT_COLUMNS, errors="ignore") if not show_gemini else dataframe
    display_df = _friendly_dataframe(_with_review_priority(display_df), GST_REVIEW_COLUMNS)
    display_df = display_df.drop(columns=["gemini_candidate_id"], errors="ignore")
    return display_df.rename(columns=GEMINI_DISPLAY_LABELS)


def _bank_technical_dataframe(dataframe: pd.DataFrame, show_gemini: bool = False) -> pd.DataFrame:
    display_df = dataframe.drop(columns=GEMINI_RESULT_COLUMNS, errors="ignore") if not show_gemini else dataframe
    display_df = _with_review_priority(display_df).drop(columns=["gemini_candidate_id"], errors="ignore")
    available_columns = [column for column in BANK_TECHNICAL_COLUMNS if column in display_df.columns]
    remaining = [column for column in display_df.columns if column not in available_columns]
    return display_df.loc[:, available_columns + remaining].copy().rename(columns=GEMINI_DISPLAY_LABELS)


def _filter_search(dataframe: pd.DataFrame, search_text: str, candidates: list[str]) -> pd.DataFrame:
    """Filter rows when any candidate text column contains the search term."""
    search = (search_text or "").strip().lower()
    if not search:
        return dataframe

    available_columns = [column for column in candidates if column in dataframe.columns]
    if not available_columns:
        return dataframe

    search_frame = dataframe[available_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    return dataframe[search_frame.str.contains(search, regex=False)].copy()


def _filter_min_amount(dataframe: pd.DataFrame, minimum_amount: float, candidates: list[str]) -> pd.DataFrame:
    """Filter rows by absolute amount across available amount columns."""
    if minimum_amount <= 0:
        return dataframe

    available_columns = [column for column in candidates if column in dataframe.columns]
    if not available_columns:
        return dataframe

    amount_frame = dataframe[available_columns].apply(pd.to_numeric, errors="coerce").abs()
    return dataframe[amount_frame.max(axis=1).fillna(0) >= minimum_amount].copy()


def _ai_preview_dataframe(results_df):
    """Return a compact preview focused on rule and AI review signals."""
    available_columns = [column for column in AI_PREVIEW_COLUMNS if column in results_df.columns]
    return results_df.loc[:, available_columns]


def _gst_preview_dataframe(results_df):
    """Return finance-friendly GST preview columns with exceptions first."""
    preview_df = results_df.copy()
    if "match_status" not in preview_df.columns:
        available_columns = [column for column in GST_PREVIEW_COLUMNS if column in preview_df.columns]
        return preview_df.loc[:, available_columns].copy()

    status_order = {
        "amount_mismatch": 0,
        "mismatch": 0,
        "tax_component_mismatch": 1,
        "possible_match": 2,
        "missing_in_books": 3,
        "missing_in_gstr": 4,
        "matched": 5,
    }
    preview_df["status_sort_order"] = preview_df["match_status"].map(status_order).fillna(99)
    preview_df = preview_df.sort_values(by=["status_sort_order", "gstin", "invoice_number"], kind="stable")
    available_columns = [column for column in GST_PREVIEW_COLUMNS if column in preview_df.columns]
    return preview_df.loc[:, available_columns].reset_index(drop=True)


def _gst_status_dataframe(results_df, statuses):
    """Return user-facing GST rows for selected reconciliation statuses."""
    if "match_status" not in results_df.columns:
        return _gst_review_dataframe(results_df)

    status_df = results_df[results_df["match_status"].isin(statuses)].copy()
    if statuses == ["possible_match"] and {"gstin", "invoice_number"}.issubset(status_df.columns):
        status_df = status_df.sort_values(by=["gstin", "invoice_number"], kind="stable")
    return _gst_review_dataframe(status_df.reset_index(drop=True))


def _status_subset(dataframe: pd.DataFrame, statuses: list[str]) -> pd.DataFrame:
    """Return rows matching backend statuses when available."""
    if "match_status" not in dataframe.columns:
        return dataframe.iloc[0:0].copy()
    return dataframe[dataframe["match_status"].isin(statuses)].copy()


def _review_type_subset(dataframe: pd.DataFrame, review_type: str, options: dict[str, list[str]]) -> pd.DataFrame:
    """Return rows for a business-facing review type without changing statuses."""
    if review_type == "High Priority":
        if "review_priority" not in dataframe.columns:
            return dataframe.iloc[0:0].copy()
        return dataframe[dataframe["review_priority"] == "High Priority"].copy()

    statuses = options.get(review_type, [])
    if not statuses:
        return dataframe.copy()
    return _status_subset(dataframe, statuses)


def _first_available_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first available column from a preferred list."""
    return next((column for column in candidates if column in df.columns), None)


def _blank_count(df: pd.DataFrame, candidates: list[str]) -> int:
    """Count blank values in the first available column."""
    column = _first_available_column(df, candidates)
    if column is None:
        return len(df)

    return int(df[column].fillna("").astype(str).str.strip().eq("").sum())


def _present_count(df: pd.DataFrame, candidates: list[str]) -> int:
    """Count present values in the first available column."""
    column = _first_available_column(df, candidates)
    if column is None:
        return 0

    return int(df[column].fillna("").astype(str).str.strip().ne("").sum())


def _show_top_values(df: pd.DataFrame, title: str, candidates: list[str]) -> None:
    """Show the top nonblank values for an available analysis column."""
    column = _first_available_column(df, candidates)
    if column is None:
        st.caption(f"{title}: not available in this reconciliation output.")
        return

    top_values = (
        df[column]
        .fillna("")
        .astype(str)
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .value_counts()
        .head(10)
        .rename_axis(title)
        .reset_index(name="Rows")
    )
    if top_values.empty:
        st.caption(f"{title}: no populated values found.")
        return

    st.dataframe(top_values, use_container_width=True, hide_index=True)


def _show_missing_books_analysis(results_df: pd.DataFrame) -> None:
    """Render finance-friendly analysis for GSTR rows missing in books."""
    if "match_status" not in results_df.columns:
        st.info("Missing in Books analysis is unavailable because match status is not present.")
        return

    missing_books_df = results_df[results_df["match_status"] == "missing_in_books"].copy()
    if missing_books_df.empty:
        st.success("No Missing in Books rows found for the selected GSTR upload.")
        return

    st.info(
        "These invoices are present in uploaded GSTR but were not found in Zoho/books "
        "with reliable GSTIN, invoice number, date and amount match."
    )

    metric_columns = st.columns(4)
    with metric_columns[0]:
        st.metric("Missing in Books rows", len(missing_books_df))
    with metric_columns[1]:
        st.metric("Blank GSTIN", _blank_count(missing_books_df, ["gstr_gstin", "gstin"]))
    with metric_columns[2]:
        st.metric("Blank invoice number", _blank_count(missing_books_df, ["gstr_invoice_number", "invoice_number"]))
    with metric_columns[3]:
        st.metric("Invoice value present", _present_count(missing_books_df, ["gstr_invoice_value", "invoice_value_gstr", "invoice_value"]))

    supplier_column, gstin_column = st.columns(2)
    with supplier_column:
        st.markdown("**Top suppliers**")
        _show_top_values(missing_books_df, "Supplier", ["gstr_supplier_name", "supplier_name"])
    with gstin_column:
        st.markdown("**Top GSTINs**")
        _show_top_values(missing_books_df, "GSTIN", ["gstr_gstin", "gstin"])

    st.markdown("**Suggested actions**")
    st.markdown(
        "- Check whether invoices are recorded as Bills, Expenses, Vendor Credits, or reimbursements.\n"
        "- Verify supplier GSTIN and invoice number in Zoho.\n"
        "- If not booked, record the document in Zoho before claiming ITC."
    )


def _show_explanation_cards(card_rows: list[tuple[str, str]]) -> None:
    """Render short status explanations for finance reviewers."""
    columns = st.columns(len(card_rows))
    for column, (title, body) in zip(columns, card_rows):
        with column:
            st.markdown(f"**{title}**")
            st.caption(body)


def _show_bank_explanation_cards() -> None:
    _show_explanation_cards(
        [
            ("Bank Only", "Bank transaction exists but no matching Zoho/books record found."),
            ("Books Only", "Zoho/books entry exists but no matching bank statement row found."),
            ("Review Match", "Amount/date signals exist but narration/party/reference needs review."),
        ]
    )


def _show_gst_explanation_cards() -> None:
    _show_explanation_cards(
        [
            ("Missing in Books", "GSTR entry exists but Zoho purchase record not found."),
            ("Missing in GSTR", "Zoho record exists but GSTR upload does not show it."),
            ("Tax Type Mismatch", "GST tax split differs, e.g. IGST vs CGST/SGST."),
            ("Amount Mismatch", "GSTIN/invoice match exists but amount/tax differs."),
        ]
    )


def _show_bank_action_summary(dataframe: pd.DataFrame) -> None:
    """Render bank review counts and suggested order."""
    high_count = int((dataframe["review_priority"] == "High Priority").sum()) if "review_priority" in dataframe.columns else 0
    possible_count = int((dataframe.get("match_status", pd.Series(dtype=str)) == "possible_match").sum())
    bank_not_books_count = int((dataframe.get("match_status", pd.Series(dtype=str)) == "bank_not_in_books").sum())
    books_not_bank_count = int((dataframe.get("match_status", pd.Series(dtype=str)) == "books_not_in_bank").sum())

    metric_columns = st.columns(4)
    with metric_columns[0]:
        st.metric("High risk rows", high_count)
    with metric_columns[1]:
        st.metric("Review Match", possible_count)
    with metric_columns[2]:
        st.metric("Bank Only", bank_not_books_count)
    with metric_columns[3]:
        st.metric("Books Only", books_not_bank_count)

    st.markdown("**Suggested order**")
    st.markdown(
        "1. Review high-risk unmatched rows\n"
        "2. Approve/reject possible matches\n"
        "3. Check small bank charges separately\n"
        "4. Export final exception list"
    )


def _show_gst_action_summary(dataframe: pd.DataFrame) -> None:
    """Render GST review counts and suggested order."""
    high_count = int((dataframe["review_priority"] == "High Priority").sum()) if "review_priority" in dataframe.columns else 0
    possible_count = int((dataframe.get("match_status", pd.Series(dtype=str)) == "possible_match").sum())
    missing_books_count = int((dataframe.get("match_status", pd.Series(dtype=str)) == "missing_in_books").sum())
    missing_gstr_count = int((dataframe.get("match_status", pd.Series(dtype=str)) == "missing_in_gstr").sum())

    metric_columns = st.columns(4)
    with metric_columns[0]:
        st.metric("High risk rows", high_count)
    with metric_columns[1]:
        st.metric("Review Match", possible_count)
    with metric_columns[2]:
        st.metric("Missing in Books", missing_books_count)
    with metric_columns[3]:
        st.metric("Missing in GSTR", missing_gstr_count)

    st.markdown("**Suggested order**")
    st.markdown(
        "1. Review high-risk missing and mismatch rows\n"
        "2. Resolve tax type and amount mismatches\n"
        "3. Approve/reject possible matches\n"
        "4. Export final exception list"
    )


def _gst_technical_audit_dataframe(results_df, show_gemini: bool = False):
    """Return GST technical audit columns with raw traceability fields."""
    display_df = results_df.drop(columns=GEMINI_RESULT_COLUMNS, errors="ignore") if not show_gemini else results_df
    display_df = _with_review_priority(display_df).drop(columns=["gemini_candidate_id"], errors="ignore")
    available_columns = [column for column in GST_TECHNICAL_COLUMNS if column in display_df.columns]
    remaining = [column for column in display_df.columns if column not in available_columns]
    return display_df.loc[:, available_columns + remaining].copy().rename(columns=GEMINI_DISPLAY_LABELS)


def _split_ignored_opening_balances(results_df):
    """Keep opening balances separate from real bank transaction matching."""
    if "match_status" not in results_df.columns:
        return results_df, results_df.iloc[0:0]

    ignored_mask = results_df["match_status"] == "ignored_opening_balance"
    return results_df[~ignored_mask], results_df[ignored_mask]


def _bank_status_dataframe(results_df, status: str):
    """Return bank reconciliation rows for one status."""
    if "match_status" not in results_df.columns:
        return _bank_review_dataframe(results_df)

    return _bank_review_dataframe(results_df[results_df["match_status"] == status].copy().reset_index(drop=True))


def _friendly_timestamp(value) -> str:
    """Format BigQuery timestamps for upload selectors."""
    parsed_value = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed_value):
        return "upload time unavailable"
    return parsed_value.strftime("%Y-%m-%d %H:%M")


def _upload_label(upload_row: dict) -> str:
    """Build a readable upload dropdown label."""
    file_name = upload_row.get("file_name") or "Unnamed upload"
    uploaded_at = _friendly_timestamp(upload_row.get("uploaded_at"))
    row_count = upload_row.get("row_count", 0)
    return f"{file_name} | {uploaded_at} | {row_count} rows"


def _is_auth_error(error: Exception) -> bool:
    """Detect cloud auth failures without surfacing technical text to finance users."""
    error_text = str(error).lower()
    auth_markers = [
        "reauthentication",
        "application-default login",
        "default credentials",
        "invalid_grant",
        "unauthorized",
        "credentials",
        "permission denied",
    ]
    return any(marker in error_text for marker in auth_markers)


def _show_upload_warning(title: str, message: str, *, show_developer_note: bool = False) -> None:
    """Render one compact warning card for unavailable reconciliation uploads."""
    developer_note = (
        "<span>Developer note: run <code>gcloud auth application-default login</code></span>"
        if show_developer_note
        else ""
    )
    st.markdown(
        f"""
        <div class="recon-warning-card">
            <strong>{escape(title)}</strong>
            <p>{escape(message)}</p>
            {developer_note}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _friendly_delete_error(error: Exception) -> str:
    """Return a safe UI error for upload deletion failures."""
    if _is_auth_error(error):
        return "Upload could not be deleted. Please refresh cloud authentication and try again."
    return "Upload could not be deleted. Please check permissions and try again."


def _clear_reconciliation_state(source_key: str) -> None:
    """Clear cached UI state tied to a deleted upload."""
    if source_key == "bank":
        state_keys = [
            "bank_recon_result",
            "bank_recon_error",
            "selected_bank_upload_id",
            "confirm_delete_bank_upload",
            "confirm_delete_bank_upload_upload_id",
        ]
    else:
        state_keys = [
            "gst_recon_result",
            "gst_recon_error",
            "selected_gstr_upload_id",
            "confirm_delete_gstr_upload",
            "confirm_delete_gstr_upload_upload_id",
        ]

    for state_key in state_keys:
        st.session_state.pop(state_key, None)


def _render_delete_upload_button(
    selected_upload: dict | None,
    *,
    button_label: str,
    confirm_key: str,
) -> None:
    """Render the small delete trigger next to the upload selector."""
    if selected_upload is None:
        st.button(button_label, type="secondary", use_container_width=True, disabled=True, key=f"{confirm_key}_disabled")
        return

    selected_upload_id = str(selected_upload.get("upload_id", ""))
    pending_upload_id_key = f"{confirm_key}_upload_id"
    if st.session_state.get(confirm_key) and st.session_state.get(pending_upload_id_key) != selected_upload_id:
        st.session_state.pop(confirm_key, None)
        st.session_state.pop(pending_upload_id_key, None)

    if st.button(button_label, type="secondary", use_container_width=True, key=f"{confirm_key}_start"):
        st.session_state[confirm_key] = True
        st.session_state[pending_upload_id_key] = selected_upload_id


def _render_delete_upload_confirmation(
    *,
    source_key: str,
    confirm_key: str,
    delete_callback,
) -> None:
    """Render full-width confirmation controls for the selected upload."""
    if not st.session_state.get(confirm_key):
        return

    st.warning("This will remove the selected uploaded file from reconciliation input. It will not delete Zoho/Books data.")
    pending_upload_id_key = f"{confirm_key}_upload_id"
    confirm_columns = st.columns([1.7, 1.2, 4], vertical_alignment="center")
    with confirm_columns[0]:
        if st.button("Confirm delete", key=f"{confirm_key}_confirm"):
            try:
                delete_callback(st.session_state[pending_upload_id_key])
                _clear_reconciliation_state(source_key)
                st.session_state["upload_delete_success"] = "Upload deleted successfully."
                st.success("Upload deleted successfully.")
                st.rerun()
            except Exception as error:
                print(f"[Reconciliation UI] Upload delete failed for {source_key}: {error}")
                st.error(_friendly_delete_error(error))
    with confirm_columns[1]:
        if st.button("Cancel", key=f"{confirm_key}_cancel"):
            st.session_state.pop(confirm_key, None)
            st.session_state.pop(pending_upload_id_key, None)
            st.rerun()


def _render_upload_selector(
    source_type: str,
    title: str,
    empty_message: str,
    key: str,
    lookup_failed_message: str,
    *,
    delete_source_key: str,
    delete_confirm_key: str,
    delete_callback,
) -> dict | None:
    """Render latest/default upload selector for bank or GSTR reconciliation."""
    try:
        uploads_df = fetch_reconciliation_uploads(source_type)
    except Exception as error:
        print(f"[Reconciliation UI] Upload lookup failed for {source_type}: {error}")
        _show_upload_warning(
            title,
            lookup_failed_message,
            show_developer_note=_is_auth_error(error),
        )
        return None

    if uploads_df.empty:
        _show_upload_warning(title, empty_message)
        return None

    upload_records = uploads_df.to_dict(orient="records")
    latest_upload = upload_records[0]
    st.caption(f"{title}: {_upload_label(latest_upload)}")

    selector_column, delete_column = st.columns([5, 1], vertical_alignment="bottom")
    with selector_column:
        selected_upload_id = st.selectbox(
            "Select upload for reconciliation",
            options=[upload["upload_id"] for upload in upload_records],
            index=0,
            format_func=lambda upload_id: _upload_label(
                next(upload for upload in upload_records if upload["upload_id"] == upload_id)
            ),
            key=key,
            help="Latest upload is selected by default. Older uploads remain available for audit.",
        )
    selected_upload = next(upload for upload in upload_records if upload["upload_id"] == selected_upload_id)
    with delete_column:
        _render_delete_upload_button(
            selected_upload,
            button_label="Delete upload",
            confirm_key=delete_confirm_key,
        )
    _render_delete_upload_confirmation(
        source_key=delete_source_key,
        confirm_key=delete_confirm_key,
        delete_callback=delete_callback,
    )

    st.info(
        f"Selected file: {selected_upload.get('file_name', '')} | "
        f"Uploaded: {_friendly_timestamp(selected_upload.get('uploaded_at'))} | "
        f"Rows: {selected_upload.get('row_count', 0)}"
    )
    return selected_upload


delete_success_message = st.session_state.pop("upload_delete_success", None)
if delete_success_message:
    st.success(delete_success_message)


def _gst_stage_callback(progress_bar, status_placeholder):
    """Return a callback that logs and renders GST reconciliation stages."""
    def update_stage(message: str) -> None:
        print(f"[GST Reconciliation UI] {message}")
        status_placeholder.info(message)
        progress_bar.progress(GST_STAGE_PROGRESS.get(message, 50))

    return update_stage


def _bank_stage_callback(progress_bar, status_placeholder):
    """Return a callback that logs and renders Bank reconciliation stages."""
    def update_stage(message: str) -> None:
        print(f"[Bank Reconciliation UI] {message}")
        status_placeholder.info(message)
        progress_bar.progress(BANK_STAGE_PROGRESS.get(message, 50))

    return update_stage


with bank_tab:
    section_card(
        "Bank Matching",
        body_html=(
            "<p>Compares uploaded bank lines with accounting-side transactions using amount, date, and text similarity.</p>"
            "<p>Consume layer is where Excel/Reconciliation reads final business-ready data.</p>"
        ),
    )
    selected_bank_upload = _render_upload_selector(
        "bank_statement",
        "Latest Bank Upload",
        "Please upload a bank statement first.",
        "selected_bank_upload_id",
        "Bank upload could not be loaded. Please refresh cloud authentication and try again.",
        delete_source_key="bank",
        delete_confirm_key="confirm_delete_bank_upload",
        delete_callback=delete_bank_upload,
    )
    generate_bank_ai = st.checkbox(
        "Generate Vertex AI insights",
        value=False,
        key="generate_bank_ai_insights",
        help="Rule-based matching runs fastest. Enable Vertex AI only when you need exception explanations.",
    )
    use_bank_gemini = st.checkbox(
        "Use Gemini suggestions for unmatched Bank rows",
        value=False,
        key="use_bank_gemini_suggestions",
    )
    st.caption(
        "Gemini suggestions are assistive only. Final matching remains rule-based and finance-reviewed."
    )
    run_bank_clicked = st.button(
        "Run Bank Reconciliation",
        type="primary",
        use_container_width=True,
        disabled=selected_bank_upload is None,
    )

    if run_bank_clicked:
        progress_bar = st.progress(0)
        status_placeholder = st.empty()
        update_bank_stage = _bank_stage_callback(progress_bar, status_placeholder)
        try:
            update_bank_stage("Loading selected bank upload...")
            update_bank_stage("Running bank reconciliation...")
            update_bank_stage("Generating Vertex AI insights..." if generate_bank_ai else "Preparing rule-based review notes...")
            with st.spinner("Running bank reconciliation..."):
                st.session_state["bank_recon_result"] = run_bank_reconciliation(
                    output_dir,
                    selected_upload_id=selected_bank_upload["upload_id"],
                    selected_upload_metadata=selected_bank_upload,
                    generate_ai_insights=generate_bank_ai,
                    max_ai_rows=10,
                    use_gemini_suggestions=use_bank_gemini,
                )
                update_bank_stage("Preparing review summary...")
                update_bank_stage("Bank reconciliation completed.")
            st.session_state.pop("bank_recon_error", None)
            st.success("Bank reconciliation completed.")
        except Exception as error:
            print("[Bank Reconciliation UI] Failed")
            print(traceback.format_exc())
            st.session_state["bank_recon_error"] = str(error)
            st.session_state.pop("bank_recon_result", None)
            status_placeholder.error("Bank reconciliation failed. Check the terminal logs for details.")

    bank_result = st.session_state.get("bank_recon_result")
    bank_error = st.session_state.get("bank_recon_error")

    if bank_result:
        bank_summary = bank_result["summary"]
        selected_upload = bank_result.get("selected_upload", {})
        st.caption(
            f"Reconciled upload: {selected_upload.get('file_name', '')} | "
            f"Uploaded: {_friendly_timestamp(selected_upload.get('uploaded_at'))}"
        )

        transaction_results, opening_balance_results = _split_ignored_opening_balances(bank_result["results"])
        bank_working_results = _with_review_priority(transaction_results)
        bank_needs_review = _status_subset(bank_working_results, BANK_NEEDS_REVIEW_STATUSES)
        bank_tabs = st.tabs(["Summary", *BANK_DETAIL_TABS.keys(), "Technical Audit"])
        bank_summary_tab = bank_tabs[0]
        bank_technical_tab = bank_tabs[-1]
        with bank_summary_tab:
            _show_summary_cards(
                [
                    ("Uploaded Rows", bank_summary["uploaded_bank_rows"], "Selected bank file"),
                    ("Matched", bank_summary["matched"], "High-confidence matches"),
                    ("Bank Only", bank_summary.get("bank_not_in_books", bank_summary["unmatched"]), "Only in bank upload"),
                    ("Books Only", bank_summary.get("books_not_in_bank", 0), "Only in books"),
                    ("Review Match", bank_summary.get("possible_match", 0), "Needs finance confirmation"),
                ]
            )
            if not opening_balance_results.empty:
                st.caption(f"Opening balance rows ignored from matching: {len(opening_balance_results.index)}")
            _show_ai_summary_card(bank_result, transaction_results, "Bank reconciliation")
            _show_suggested_review_order(
                [
                    "Review High Priority exceptions first.",
                    "Clear Bank Only and Books Only rows.",
                    "Confirm or reject Review Match rows.",
                    "Download the final review exceptions for follow-up.",
                ]
            )
            export_path = Path(bank_result["export_path"])
            file_summary_card(export_path.name, "Bank Reconciliation")
            _show_download_buttons(
                export_path,
                _bank_review_dataframe(bank_needs_review, show_gemini=use_bank_gemini),
                "bank",
            )
        for tab, (label, statuses) in zip(bank_tabs[1:-1], BANK_DETAIL_TABS.items()):
            with tab:
                source_df = bank_working_results if label == "Matched" else bank_needs_review
                selected_bank_rows = _review_type_subset(source_df, label, {label: statuses})
                st.dataframe(
                    _bank_review_dataframe(selected_bank_rows, show_gemini=use_bank_gemini),
                    use_container_width=True,
                    hide_index=True,
                )
        with bank_technical_tab:
            st.dataframe(
                _bank_technical_dataframe(bank_result["results"], show_gemini=use_bank_gemini),
                use_container_width=True,
                hide_index=True,
            )
    elif bank_error and selected_bank_upload is not None:
        section_card(
            "Bank Reconciliation Not Completed",
            body_html="<p>The bank reconciliation could not run. Upload bank data and ensure BigQuery credentials are available.</p>",
        )
        st.error(bank_error)

with gst_tab:
    section_card(
        "GST Matching",
        body_html=(
            "<p>Compares uploaded GSTR lines with accounting GST records using GSTIN, invoice number, and tax amounts.</p>"
            "<p>Consume layer is where Excel/Reconciliation reads final business-ready data.</p>"
        ),
    )
    selected_gst_upload = _render_upload_selector(
        "gstr",
        "Latest GSTR Upload",
        "Please upload a GSTR file first.",
        "selected_gstr_upload_id",
        "GSTR upload could not be loaded. Please refresh cloud authentication and try again.",
        delete_source_key="gst",
        delete_confirm_key="confirm_delete_gstr_upload",
        delete_callback=delete_gstr_upload,
    )
    generate_gst_ai = st.checkbox(
        "Generate Vertex AI insights",
        value=False,
        key="generate_gst_ai_insights",
        help="Rule-based matching runs fastest. Enable Vertex AI for up to 10 unique exception explanations.",
    )
    use_gst_gemini = st.checkbox(
        "Use Gemini suggestions for unmatched GST rows",
        value=False,
        key="use_gst_gemini_suggestions",
    )
    st.caption(
        "Gemini suggestions are assistive only. Final matching remains rule-based and finance-reviewed."
    )
    run_gst_clicked = st.button(
        "Run GST Reconciliation",
        type="primary",
        use_container_width=True,
        disabled=selected_gst_upload is None,
    )

    if run_gst_clicked:
        progress_bar = st.progress(0)
        status_placeholder = st.empty()
        stage_callback = _gst_stage_callback(progress_bar, status_placeholder)
        try:
            with st.spinner("Running GST reconciliation..."):
                print("[GST Reconciliation UI] Starting selected-upload GST reconciliation")
                st.session_state["gst_recon_result"] = run_gst_reconciliation(
                    output_dir,
                    selected_upload_id=selected_gst_upload["upload_id"],
                    selected_upload_metadata=selected_gst_upload,
                    generate_ai_insights=generate_gst_ai,
                    max_ai_rows=10,
                    stage_callback=stage_callback,
                    use_gemini_suggestions=use_gst_gemini,
                )
                progress_bar.progress(100)
                status_placeholder.success("GST reconciliation completed.")
            st.session_state.pop("gst_recon_error", None)
            st.success("GST reconciliation completed.")
        except Exception as error:
            print("[GST Reconciliation UI] Failed")
            print(traceback.format_exc())
            st.session_state["gst_recon_error"] = str(error)
            st.session_state.pop("gst_recon_result", None)
            status_placeholder.error("GST reconciliation failed. Check the terminal logs for details.")

    gst_result = st.session_state.get("gst_recon_result")
    gst_error = st.session_state.get("gst_recon_error")

    if gst_result:
        gst_summary = gst_result["summary"]
        selected_upload = gst_result.get("selected_upload", {})
        st.caption(
            f"Reconciled upload: {selected_upload.get('file_name', '')} | "
            f"Uploaded: {_friendly_timestamp(selected_upload.get('uploaded_at'))} | "
            f"Rows: {selected_upload.get('row_count', gst_summary['uploaded_gstr_rows'])}"
        )
        period_text = f"{gst_result.get('selected_upload_min_date', '')} to {gst_result.get('selected_upload_max_date', '')}"
        gst_working_results = _with_review_priority(gst_result["results"])
        gst_needs_review = _status_subset(gst_working_results, GST_NEEDS_REVIEW_STATUSES)
        amount_tax_issues = int(gst_summary["amount_mismatch"]) + int(gst_summary["tax_component_mismatch"])

        gst_tabs = st.tabs(["Summary", *GST_DETAIL_TABS.keys(), "Technical Audit"])
        gst_summary_tab = gst_tabs[0]
        technical_tab = gst_tabs[-1]

        with gst_summary_tab:
            _show_summary_cards(
                [
                    ("Uploaded Rows", gst_summary["uploaded_gstr_rows"], "Selected GSTR file"),
                    ("Matched", gst_summary["matched"], "Matched in books and GSTR"),
                    ("Missing in Books", gst_summary["missing_in_books"], "GSTR only"),
                    ("Missing in GSTR", gst_summary["missing_in_gstr"], "Books only"),
                    ("Amount/Tax Issues", amount_tax_issues, "Value or tax split differs"),
                    ("Review Match", gst_summary["possible_match"], "Needs finance confirmation"),
                ]
            )
            st.caption(f"Reconciliation period: {period_text}")
            _show_ai_summary_card(gst_result, gst_result["results"], "GST reconciliation")
            _show_suggested_review_order(
                [
                    "Review High Priority exceptions first.",
                    "Resolve Missing in Books and Missing in GSTR rows.",
                    "Check Tax Type Issue and Amount Issue rows.",
                    "Confirm or reject Review Match rows.",
                ]
            )
            export_path = Path(gst_result["export_path"])
            file_summary_card(export_path.name, "GST Reconciliation")
            _show_download_buttons(
                export_path,
                _gst_review_dataframe(gst_needs_review, show_gemini=use_gst_gemini),
                "gst",
            )
        for tab, (label, statuses) in zip(gst_tabs[1:-1], GST_DETAIL_TABS.items()):
            with tab:
                source_df = gst_working_results if label == "Matched" else gst_needs_review
                selected_gst_rows = _review_type_subset(source_df, label, {label: statuses})
                st.dataframe(
                    _gst_review_dataframe(selected_gst_rows, show_gemini=use_gst_gemini),
                    use_container_width=True,
                    hide_index=True,
                )
        with technical_tab:
            st.dataframe(
                _gst_technical_audit_dataframe(gst_result["results"], show_gemini=use_gst_gemini),
                use_container_width=True,
                hide_index=True,
            )
    elif gst_error and selected_gst_upload is not None:
        section_card(
            "GST Reconciliation Not Completed",
            body_html="<p>The GST reconciliation could not run. Upload GSTR data and ensure BigQuery credentials are available.</p>",
        )
        st.error(gst_error)
