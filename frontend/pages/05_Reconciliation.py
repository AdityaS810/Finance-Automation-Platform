"""Reconciliation page."""

from __future__ import annotations

import traceback
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


def _download_excel_button(label: str, export_path: Path) -> None:
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
        )


def _show_ai_status(result: dict) -> None:
    """Show whether optional Vertex AI exception notes were added."""
    ai_status = result.get("ai_status", "unavailable")
    ai_message = result.get("ai_message", "Vertex AI insights unavailable. Showing rule-based reconciliation only.")

    if ai_status == "enabled":
        st.success(f"Vertex AI insights enabled. {ai_message}")
    elif ai_status == "not_required":
        st.info(ai_message)
    elif ai_status == "skipped":
        st.info(ai_message)
    else:
        st.warning(ai_message)


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
        return _gst_preview_dataframe(results_df)

    status_df = results_df[results_df["match_status"].isin(statuses)].copy()
    if statuses == ["possible_match"]:
        status_df = status_df.sort_values(by=["gstin", "invoice_number"], kind="stable")
        available_columns = [column for column in GST_POSSIBLE_MATCH_COLUMNS if column in status_df.columns]
        return status_df.loc[:, available_columns].reset_index(drop=True)
    return _gst_preview_dataframe(status_df)


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


def _gst_technical_audit_dataframe(results_df):
    """Return GST technical audit columns with raw traceability fields."""
    available_columns = [column for column in GST_TECHNICAL_COLUMNS if column in results_df.columns]
    return results_df.loc[:, available_columns].copy()


def _split_ignored_opening_balances(results_df):
    """Keep opening balances separate from real bank transaction matching."""
    if "match_status" not in results_df.columns:
        return results_df, results_df.iloc[0:0]

    ignored_mask = results_df["match_status"] == "ignored_opening_balance"
    return results_df[~ignored_mask], results_df[ignored_mask]


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
        summary_columns = st.columns(5)
        with summary_columns[0]:
            metric_card("Uploaded Rows", str(bank_summary["uploaded_bank_rows"]), caption="Selected bank file", status="Info", icon="UR")
        with summary_columns[1]:
            metric_card("Matched", str(bank_summary["matched"]), caption="High-confidence matches", status="Success", icon="MT")
        with summary_columns[2]:
            metric_card("Possible Match", str(bank_summary["possible_match"]), caption="Needs review", status="Warning", icon="PM")
        with summary_columns[3]:
            metric_card("Unmatched", str(bank_summary["unmatched"]), caption="No candidate found", status="Error", icon="UM")
        with summary_columns[4]:
            metric_card(
                "Opening Balance",
                str(bank_summary["ignored_opening_balance"]),
                caption="Ignored from matching",
                status="Info",
                icon="OB",
            )

        transaction_results, opening_balance_results = _split_ignored_opening_balances(bank_result["results"])
        section_card(
            "AI Insights Preview",
            body_html="<p>Review rule-based match signals and optional Vertex AI notes before opening the full bank detail.</p>",
        )
        st.caption(bank_result["message"])
        _show_ai_status(bank_result)
        st.dataframe(_ai_preview_dataframe(transaction_results), use_container_width=True, hide_index=True)

        section_card(
            "Bank Reconciliation Detail",
            body_html="<p>Full transaction-level reconciliation output. Opening balance rows are kept separate from transaction matching.</p>",
        )
        st.dataframe(transaction_results, use_container_width=True, hide_index=True)
        if not opening_balance_results.empty:
            section_card(
                "Ignored Opening Balance Rows",
                body_html="<p>Opening balance rows are shown for auditability but are not used for transaction matching.</p>",
            )
            st.dataframe(opening_balance_results, use_container_width=True, hide_index=True)

        export_path = Path(bank_result["export_path"])
        file_summary_card(export_path.name, "Bank Reconciliation")
        _download_excel_button("Download Bank Results", export_path)
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

        st.caption(gst_result["message"])
        _show_ai_status(gst_result)

        (
            summary_tab,
            tax_type_tab,
            amount_tab,
            missing_books_tab,
            missing_gstr_tab,
            possible_tab,
            matched_tab,
            technical_tab,
        ) = st.tabs(
            [
                "Summary",
                "Tax type mismatch",
                "Amount mismatches",
                "Missing in books",
                "Missing in GSTR",
                "Possible matches",
                "Matched",
                "Technical audit",
            ]
        )

        with summary_tab:
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
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Status": "Missing in GSTR", "Explanation": "Books bill not found in uploaded GSTR"},
                        {"Status": "Missing in Books", "Explanation": "GSTR invoice not found in Zoho/books"},
                        {"Status": "Tax Type Mismatch", "Explanation": "Total matches but IGST/CGST/SGST breakup differs"},
                        {"Status": "Possible Match", "Explanation": "Likely same invoice but needs manual review"},
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

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
            st.dataframe(_gst_status_dataframe(gst_result["results"], ["possible_match"]), use_container_width=True, hide_index=True)
        with matched_tab:
            st.dataframe(_gst_status_dataframe(gst_result["results"], ["matched"]), use_container_width=True, hide_index=True)
        with technical_tab:
            st.dataframe(_gst_technical_audit_dataframe(gst_result["results"]), use_container_width=True, hide_index=True)

        export_path = Path(gst_result["export_path"])
        file_summary_card(export_path.name, "GST Reconciliation")
        _download_excel_button("Download GST Results", export_path)
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
