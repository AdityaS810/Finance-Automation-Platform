"""Reconciliation page."""

from __future__ import annotations

import traceback
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from backend.reconciliation.bank_reconciliation import run_bank_reconciliation
from backend.reconciliation.gst_reconciliation import run_gst_reconciliation
from backend.reconciliation.upload_registry import fetch_reconciliation_uploads
from src.ui import file_summary_card, load_css, metric_card, page_header, section_card


load_css()

page_header(
    "Reconciliation",
    "Match uploaded bank and GST data against accounting records from the warehouse.",
)

bank_tab, gst_tab = st.tabs(["Bank Reconciliation", "GST Reconciliation"])
output_dir = Path(__file__).resolve().parents[1] / "outputs" / "reconciliation_exports"
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
BANK_REVIEW_COLUMNS = [
    "review_priority",
    "match_status",
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
    "match_status",
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
    ai_message = result.get("ai_message", "Vertex AI insights unavailable. Showing fallback insights from rule-based reconciliation signals.")

    if ai_status == "enabled":
        st.success(f"Vertex AI insights enabled. {ai_message}")
        st.caption("AI insights are added only for selected exception rows. Matching remains rule-based.")
    elif ai_status == "not_required":
        st.info(ai_message)
    elif ai_status == "skipped":
        st.info("Showing rule-based fallback insights. Enable Vertex AI for richer row-level explanations.")
    else:
        st.warning("Showing rule-based fallback insights. Enable Vertex AI for richer row-level explanations.")


def _show_ai_summary_card(result: dict, results_df: pd.DataFrame, title: str) -> None:
    """Render a top-level summary of generated or fallback review notes."""
    section_card(
        "AI Review Summary",
        body_html="<p>High-level exception guidance before reviewing row-level notes.</p>",
    )
    if results_df.empty or "ai_risk_level" not in results_df.columns:
        _show_ai_status(result)
        st.caption(f"{title}: no insight rows are available yet.")
        return

    risk_values = results_df["ai_risk_level"].fillna("").astype(str).str.lower()
    insight_rows = int(results_df.get("ai_summary", pd.Series("", index=results_df.index)).fillna("").astype(str).str.strip().ne("").sum())
    high_count = int((risk_values == "high").sum())
    medium_count = int((risk_values == "medium").sum())
    low_count = int((risk_values == "low").sum())
    ai_status = result.get("ai_status", "unavailable")
    ai_message = result.get("ai_message", "Vertex AI insights unavailable. Showing fallback insights from rule-based reconciliation signals.")
    summary_text = (
        f"{title}: {insight_rows} review note(s), with {high_count} high-risk, "
        f"{medium_count} medium-risk, and {low_count} low-risk row(s)."
    )

    if ai_status == "enabled":
        st.success(f"{summary_text} {ai_message}")
        st.caption("AI insights are added only for selected exception rows. Matching remains rule-based.")
    elif ai_status == "not_required":
        st.info(f"{summary_text} {ai_message}")
    else:
        st.warning(
            f"{summary_text} Showing rule-based fallback insights. "
            "Enable Vertex AI for richer row-level explanations."
        )


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


def _friendly_dataframe(dataframe: pd.DataFrame, preferred_columns: list[str]) -> pd.DataFrame:
    """Order business-facing columns first and hide noisy audit columns by default."""
    available_preferred = [column for column in preferred_columns if column in dataframe.columns]
    remaining = [
        column
        for column in dataframe.columns
        if column not in available_preferred and column not in TECHNICAL_DEFAULT_HIDE_COLUMNS
    ]
    return dataframe.loc[:, available_preferred + remaining].copy()


def _bank_review_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    return _friendly_dataframe(_with_review_priority(dataframe), BANK_REVIEW_COLUMNS)


def _gst_review_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    return _friendly_dataframe(_with_review_priority(dataframe), GST_REVIEW_COLUMNS)


def _bank_technical_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    display_df = _with_review_priority(dataframe)
    available_columns = [column for column in BANK_TECHNICAL_COLUMNS if column in display_df.columns]
    remaining = [column for column in display_df.columns if column not in available_columns]
    return display_df.loc[:, available_columns + remaining].copy()


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


def _bank_filtered_dataframe(dataframe: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    """Render display-only bank filters and return filtered rows."""
    if dataframe.empty:
        return dataframe

    filter_columns = st.columns([1, 1, 2, 1])
    with filter_columns[0]:
        risk_filter = st.selectbox("Risk", ["All", "High", "Medium", "Low"], key=f"{key_prefix}_risk")
    with filter_columns[1]:
        statuses = sorted(str(status) for status in dataframe.get("match_status", pd.Series(dtype=str)).dropna().unique())
        status_filter = st.selectbox("Status", ["All", *statuses], key=f"{key_prefix}_status")
    with filter_columns[2]:
        search_text = st.text_input("Search narration / party / invoice / GSTIN / reference", key=f"{key_prefix}_search")
    with filter_columns[3]:
        minimum_amount = st.number_input("Minimum amount", min_value=0.0, value=0.0, step=100.0, key=f"{key_prefix}_amount")

    filtered_df = dataframe.copy()
    if risk_filter != "All":
        filtered_df = filtered_df[filtered_df["review_priority"].str.startswith(risk_filter)]
    if status_filter != "All":
        filtered_df = filtered_df[filtered_df["match_status"].astype(str) == status_filter]

    filtered_df = _filter_search(
        filtered_df,
        search_text,
        [
            "bank_narration",
            "accounting_party_name",
            "reference_number",
            "transaction_number",
            "transaction_type",
            "match_reason",
            "ai_summary",
        ],
    )
    return _filter_min_amount(filtered_df, minimum_amount, ["bank_amount", "accounting_amount"])


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
            ("Bank not in Books", "Bank transaction exists but no matching Zoho/books record found."),
            ("Books not in Bank", "Zoho/books entry exists but no matching bank statement row found."),
            ("Possible Match", "Amount/date signals exist but narration/party/reference needs review."),
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
        st.metric("Possible matches", possible_count)
    with metric_columns[2]:
        st.metric("Bank not in Books", bank_not_books_count)
    with metric_columns[3]:
        st.metric("Books not in Bank", books_not_bank_count)

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
        st.metric("Possible matches", possible_count)
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


def _gst_technical_audit_dataframe(results_df):
    """Return GST technical audit columns with raw traceability fields."""
    display_df = _with_review_priority(results_df)
    available_columns = [column for column in GST_TECHNICAL_COLUMNS if column in display_df.columns]
    remaining = [column for column in display_df.columns if column not in available_columns]
    return display_df.loc[:, available_columns + remaining].copy()


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


def _render_upload_selector(source_type: str, title: str, empty_message: str, key: str) -> dict | None:
    """Render latest/default upload selector for bank or GSTR reconciliation."""
    try:
        uploads_df = fetch_reconciliation_uploads(source_type)
    except Exception as error:
        st.warning(f"{empty_message} Upload lookup failed: {error}")
        return None

    if uploads_df.empty:
        st.warning(empty_message)
        return None

    upload_records = uploads_df.to_dict(orient="records")
    latest_upload = upload_records[0]
    st.caption(f"{title}: {_upload_label(latest_upload)}")

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
    st.info(
        f"Selected file: {selected_upload.get('file_name', '')} | "
        f"Uploaded: {_friendly_timestamp(selected_upload.get('uploaded_at'))} | "
        f"Rows: {selected_upload.get('row_count', 0)}"
    )
    return selected_upload


def _gst_stage_callback(progress_bar, status_placeholder):
    """Return a callback that logs and renders GST reconciliation stages."""
    def update_stage(message: str) -> None:
        print(f"[GST Reconciliation UI] {message}")
        status_placeholder.info(message)
        progress_bar.progress(GST_STAGE_PROGRESS.get(message, 50))

    return update_stage


with bank_tab:
    section_card(
        "Bank Matching",
        body_html="<p>Compares uploaded bank lines with accounting-side transactions using amount, date, and text similarity.</p>",
    )
    selected_bank_upload = _render_upload_selector(
        "bank_statement",
        "Latest Bank Upload",
        "Please upload a bank statement first.",
        "selected_bank_upload_id",
    )
    generate_bank_ai = st.checkbox(
        "Generate Vertex AI insights",
        value=False,
        key="generate_bank_ai_insights",
        help="Rule-based matching runs fastest. Enable Vertex AI only when you need exception explanations.",
    )

    if st.button("Run Bank Reconciliation", type="primary", use_container_width=True, disabled=selected_bank_upload is None):
        try:
            with st.spinner("Running bank reconciliation..."):
                print("[Bank Reconciliation UI] Running selected-upload bank reconciliation")
                st.session_state["bank_recon_result"] = run_bank_reconciliation(
                    output_dir,
                    selected_upload_id=selected_bank_upload["upload_id"],
                    selected_upload_metadata=selected_bank_upload,
                    generate_ai_insights=generate_bank_ai,
                    max_ai_rows=10,
                )
            st.session_state.pop("bank_recon_error", None)
            st.success("Bank reconciliation completed.")
        except Exception as error:
            print("[Bank Reconciliation UI] Failed")
            print(traceback.format_exc())
            st.session_state["bank_recon_error"] = str(error)
            st.session_state.pop("bank_recon_result", None)

    bank_result = st.session_state.get("bank_recon_result")
    bank_error = st.session_state.get("bank_recon_error")

    if bank_result:
        bank_summary = bank_result["summary"]
        selected_upload = bank_result.get("selected_upload", {})
        st.caption(
            f"Reconciled upload: {selected_upload.get('file_name', '')} | "
            f"Uploaded: {_friendly_timestamp(selected_upload.get('uploaded_at'))}"
        )
        summary_columns = st.columns(6)
        with summary_columns[0]:
            metric_card("Uploaded Rows", str(bank_summary["uploaded_bank_rows"]), caption="Selected bank file", status="Info", icon="UR")
        with summary_columns[1]:
            metric_card("Matched", str(bank_summary["matched"]), caption="High-confidence matches", status="Success", icon="MT")
        with summary_columns[2]:
            metric_card("Possible Match", str(bank_summary["possible_match"]), caption="Needs review", status="Warning", icon="PM")
        with summary_columns[3]:
            metric_card("Bank not in Books", str(bank_summary.get("bank_not_in_books", bank_summary["unmatched"])), caption="Bank only", status="Error", icon="BB")
        with summary_columns[4]:
            metric_card("Books not in Bank", str(bank_summary.get("books_not_in_bank", 0)), caption="Books only", status="Error", icon="BK")
        with summary_columns[5]:
            metric_card(
                "Opening Balance",
                str(bank_summary["ignored_opening_balance"]),
                caption="Ignored from matching",
                status="Info",
                icon="OB",
            )

        transaction_results, opening_balance_results = _split_ignored_opening_balances(bank_result["results"])
        bank_review_results = _bank_review_dataframe(transaction_results)
        section_card(
            "AI Insights Preview",
            body_html="<p>Review rule-based match signals and optional Vertex AI notes before opening the full bank detail.</p>",
        )
        st.caption(bank_result["message"])
        _show_ai_summary_card(bank_result, transaction_results, "Bank reconciliation")
        st.dataframe(_ai_preview_dataframe(bank_review_results), use_container_width=True, hide_index=True)

        section_card(
            "Bank Reconciliation Detail",
            body_html="<p>Review matched transactions and bank/books exceptions separately for faster finance follow-up.</p>",
        )
        _show_bank_explanation_cards()
        filtered_bank_results = _bank_filtered_dataframe(bank_review_results, "bank_detail")
        bank_high_priority = filtered_bank_results[filtered_bank_results["review_priority"] == "High Priority"].copy()
        bank_possible_matches = filtered_bank_results[filtered_bank_results["match_status"] == "possible_match"].copy()
        bank_not_books = filtered_bank_results[filtered_bank_results["match_status"] == "bank_not_in_books"].copy()
        books_not_bank = filtered_bank_results[filtered_bank_results["match_status"] == "books_not_in_bank"].copy()
        bank_unmatched = filtered_bank_results[
            filtered_bank_results["match_status"].isin(["bank_not_in_books", "books_not_in_bank"])
        ].copy()
        bank_matched = filtered_bank_results[filtered_bank_results["match_status"] == "matched"].copy()
        (
            bank_action_tab,
            bank_high_tab,
            bank_possible_tab,
            bank_not_books_tab,
            books_not_bank_tab,
            bank_matched_tab,
            bank_technical_tab,
        ) = st.tabs(
            [
                "Action Summary",
                "High Priority",
                "Possible Matches",
                "Bank not in Books",
                "Books not in Bank",
                "Matched",
                "Technical Audit",
            ]
        )
        with bank_action_tab:
            _show_bank_action_summary(bank_review_results)
            if not opening_balance_results.empty:
                st.caption(f"Opening balance rows ignored from matching: {len(opening_balance_results.index)}")
        with bank_not_books_tab:
            st.dataframe(bank_not_books, use_container_width=True, hide_index=True)
        with books_not_bank_tab:
            st.dataframe(books_not_bank, use_container_width=True, hide_index=True)
        with bank_possible_tab:
            st.dataframe(bank_possible_matches, use_container_width=True, hide_index=True)
        with bank_high_tab:
            st.dataframe(bank_high_priority, use_container_width=True, hide_index=True)
        with bank_matched_tab:
            st.dataframe(bank_matched, use_container_width=True, hide_index=True)
        with bank_technical_tab:
            st.dataframe(_bank_technical_dataframe(bank_result["results"]), use_container_width=True, hide_index=True)

        export_path = Path(bank_result["export_path"])
        file_summary_card(export_path.name, "Bank Reconciliation")
        export_columns = st.columns(4)
        with export_columns[0]:
            _download_excel_button("Download full reconciliation", export_path, key="bank_full_reconciliation_download")
        with export_columns[1]:
            _download_dataframe_button(
                "Download high priority exceptions",
                bank_high_priority,
                "bank_high_priority_exceptions.xlsx",
                key="bank_high_priority_download",
            )
        with export_columns[2]:
            _download_dataframe_button(
                "Download possible matches",
                bank_possible_matches,
                "bank_possible_matches.xlsx",
                key="bank_possible_matches_download",
            )
        with export_columns[3]:
            _download_dataframe_button(
                "Download bank/books unmatched exceptions",
                bank_unmatched,
                "bank_books_unmatched_exceptions.xlsx",
                key="bank_unmatched_download",
            )
    elif bank_error:
        section_card(
            "Bank Reconciliation Not Completed",
            body_html="<p>The bank reconciliation could not run. Upload bank data and ensure BigQuery credentials are available.</p>",
        )
        st.error(bank_error)
    else:
        section_card(
            "Ready to Run",
            body_html="<p>Click the button above after uploading bank statements.</p>",
        )

with gst_tab:
    section_card(
        "GST Matching",
        body_html="<p>Compares uploaded GSTR lines with accounting GST records using GSTIN, invoice number, and tax amounts.</p>",
    )
    selected_gst_upload = _render_upload_selector(
        "gstr",
        "Latest GSTR Upload",
        "Please upload a GSTR file first.",
        "selected_gstr_upload_id",
    )
    generate_gst_ai = st.checkbox(
        "Generate Vertex AI insights",
        value=False,
        key="generate_gst_ai_insights",
        help="Rule-based matching runs fastest. Enable Vertex AI for up to 10 unique exception explanations.",
    )

    if st.button("Run GST Reconciliation", type="primary", use_container_width=True, disabled=selected_gst_upload is None):
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
        st.info(
            "Reconciliation period: "
            f"{gst_result.get('selected_upload_min_date', '')} to {gst_result.get('selected_upload_max_date', '')}"
        )
        summary_columns = st.columns(7)
        with summary_columns[0]:
            metric_card("Uploaded GSTR rows", str(gst_summary["uploaded_gstr_rows"]), caption="Selected file", status="Info", icon="UR")
        with summary_columns[1]:
            metric_card("Exact matched", str(gst_summary["matched"]), caption="Matched in books and GSTR", status="Success", icon="MT")
        with summary_columns[2]:
            metric_card(
                "Tax type mismatch",
                str(gst_summary["tax_component_mismatch"]),
                caption="Tax breakup differs",
                status="Warning",
                icon="TT",
            )
        with summary_columns[3]:
            metric_card("Amount mismatch", str(gst_summary["amount_mismatch"]), caption="Values differ", status="Error", icon="AM")
        with summary_columns[4]:
            metric_card("Possible match", str(gst_summary["possible_match"]), caption="Manual review", status="Warning", icon="PM")
        with summary_columns[5]:
            metric_card("Missing in GSTR", str(gst_summary["missing_in_gstr"]), caption="Books not in upload", status="Error", icon="MG")
        with summary_columns[6]:
            metric_card("Missing in books", str(gst_summary["missing_in_books"]), caption="Upload not in books", status="Error", icon="MB")

        gst_review_results = _gst_review_dataframe(gst_result["results"])
        gst_high_priority = gst_review_results[gst_review_results["review_priority"] == "High Priority"].copy()
        gst_possible_matches = _gst_status_dataframe(gst_result["results"], ["possible_match"])
        gst_missing = gst_review_results[
            gst_review_results["match_status"].isin(["missing_in_books", "missing_in_gstr"])
        ].copy()
        st.caption(gst_result["message"])
        _show_ai_summary_card(gst_result, gst_result["results"], "GST reconciliation")

        (
            gst_action_tab,
            gst_high_tab,
            tax_type_tab,
            amount_tab,
            missing_books_tab,
            missing_gstr_tab,
            possible_tab,
            matched_tab,
            technical_tab,
        ) = st.tabs(
            [
                "Action Summary",
                "High Priority",
                "Tax type mismatch",
                "Amount mismatches",
                "Missing in books",
                "Missing in GSTR",
                "Possible matches",
                "Matched",
                "Technical audit",
            ]
        )

        with gst_action_tab:
            _show_gst_explanation_cards()
            _show_gst_action_summary(gst_review_results)
            period_text = f"{gst_result.get('selected_upload_min_date', '')} to {gst_result.get('selected_upload_max_date', '')}"
            summary_rows = pd.DataFrame(
                [
                    {"Item": "Selected GSTR file", "Value": selected_upload.get("file_name", "")},
                    {"Item": "Reconciliation period", "Value": period_text},
                    {"Item": "Uploaded GSTR rows", "Value": gst_summary["uploaded_gstr_rows"]},
                    {"Item": "Exact matched", "Value": gst_summary["matched"]},
                    {"Item": "Tax type mismatch", "Value": gst_summary["tax_component_mismatch"]},
                    {"Item": "Amount mismatch", "Value": gst_summary["amount_mismatch"]},
                    {"Item": "Possible match", "Value": gst_summary["possible_match"]},
                    {"Item": "Missing in GSTR", "Value": gst_summary["missing_in_gstr"]},
                    {"Item": "Missing in Books", "Value": gst_summary["missing_in_books"]},
                ]
            )
            st.dataframe(summary_rows, use_container_width=True, hide_index=True)
        with gst_high_tab:
            st.dataframe(gst_high_priority, use_container_width=True, hide_index=True)

        with tax_type_tab:
            st.dataframe(
                _gst_status_dataframe(gst_result["results"], ["tax_component_mismatch"]),
                use_container_width=True,
                hide_index=True,
            )
        with amount_tab:
            st.dataframe(_gst_status_dataframe(gst_result["results"], ["amount_mismatch"]), use_container_width=True, hide_index=True)
        with missing_books_tab:
            _show_missing_books_analysis(gst_result["results"])
            st.dataframe(_gst_status_dataframe(gst_result["results"], ["missing_in_books"]), use_container_width=True, hide_index=True)
        with missing_gstr_tab:
            st.dataframe(_gst_status_dataframe(gst_result["results"], ["missing_in_gstr"]), use_container_width=True, hide_index=True)
        with possible_tab:
            st.dataframe(gst_possible_matches, use_container_width=True, hide_index=True)
        with matched_tab:
            st.dataframe(_gst_status_dataframe(gst_result["results"], ["matched"]), use_container_width=True, hide_index=True)
        with technical_tab:
            st.dataframe(_gst_technical_audit_dataframe(gst_result["results"]), use_container_width=True, hide_index=True)

        export_path = Path(gst_result["export_path"])
        file_summary_card(export_path.name, "GST Reconciliation")
        export_columns = st.columns(4)
        with export_columns[0]:
            _download_excel_button("Download full reconciliation", export_path, key="gst_full_reconciliation_download")
        with export_columns[1]:
            _download_dataframe_button(
                "Download high priority exceptions",
                gst_high_priority,
                "gst_high_priority_exceptions.xlsx",
                key="gst_high_priority_download",
            )
        with export_columns[2]:
            _download_dataframe_button(
                "Download possible matches",
                gst_possible_matches,
                "gst_possible_matches.xlsx",
                key="gst_possible_matches_download",
            )
        with export_columns[3]:
            _download_dataframe_button(
                "Download missing exceptions",
                gst_missing,
                "gst_missing_exceptions.xlsx",
                key="gst_missing_download",
            )
    elif gst_error:
        section_card(
            "GST Reconciliation Not Completed",
            body_html="<p>The GST reconciliation could not run. Upload GSTR data and ensure BigQuery credentials are available.</p>",
        )
        st.error(gst_error)
    else:
        section_card(
            "Ready to Run",
            body_html="<p>Click the button above after uploading GSTR data.</p>",
        )
