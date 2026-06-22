"""Reconciliation page."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from backend.reconciliation.bank_reconciliation import run_bank_reconciliation
from backend.reconciliation.gst_reconciliation import run_gst_reconciliation
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
    else:
        st.warning(ai_message)


def _ai_preview_dataframe(results_df):
    """Return a compact preview focused on rule and AI review signals."""
    available_columns = [column for column in AI_PREVIEW_COLUMNS if column in results_df.columns]
    return results_df.loc[:, available_columns]


def _split_ignored_opening_balances(results_df):
    """Keep opening balances separate from real bank transaction matching."""
    if "match_status" not in results_df.columns:
        return results_df, results_df.iloc[0:0]

    ignored_mask = results_df["match_status"] == "ignored_opening_balance"
    return results_df[~ignored_mask], results_df[ignored_mask]


with bank_tab:
    section_card(
        "Bank Matching",
        body_html="<p>Compares uploaded bank lines with accounting-side transactions using amount, date, and text similarity.</p>",
    )

    if st.button("Run Bank Reconciliation", type="primary", use_container_width=True):
        try:
            st.session_state["bank_recon_result"] = run_bank_reconciliation(output_dir)
            st.session_state.pop("bank_recon_error", None)
            st.success("Bank reconciliation completed.")
        except Exception as error:
            st.session_state["bank_recon_error"] = str(error)
            st.session_state.pop("bank_recon_result", None)

    bank_result = st.session_state.get("bank_recon_result")
    bank_error = st.session_state.get("bank_recon_error")

    if bank_result:
        bank_summary = bank_result["summary"]
        summary_columns = st.columns(5)
        with summary_columns[0]:
            metric_card("Transactions", str(bank_summary["transaction_records"]), caption="Bank lines matched", status="Info", icon="TR")
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

    if st.button("Run GST Reconciliation", type="primary", use_container_width=True):
        try:
            st.session_state["gst_recon_result"] = run_gst_reconciliation(output_dir)
            st.session_state.pop("gst_recon_error", None)
            st.success("GST reconciliation completed.")
        except Exception as error:
            st.session_state["gst_recon_error"] = str(error)
            st.session_state.pop("gst_recon_result", None)

    gst_result = st.session_state.get("gst_recon_result")
    gst_error = st.session_state.get("gst_recon_error")

    if gst_result:
        gst_summary = gst_result["summary"]
        missing_total = gst_summary["missing_in_books"] + gst_summary["missing_in_gstr"]
        summary_columns = st.columns(4)
        with summary_columns[0]:
            metric_card("Total Records", str(gst_summary["total_records"]), caption="Rows compared", status="Info", icon="TR")
        with summary_columns[1]:
            metric_card("Matched", str(gst_summary["matched"]), caption="Exact tax matches", status="Success", icon="MT")
        with summary_columns[2]:
            metric_card("Mismatch", str(gst_summary["mismatch"]), caption="Amount differences", status="Error", icon="MM")
        with summary_columns[3]:
            metric_card("Missing", str(missing_total), caption="Missing in books/GSTR", status="Error", icon="MS")

        section_card(
            "AI Insights Preview",
            body_html="<p>Review rule-based GST match signals and optional Vertex AI notes before opening the full invoice detail.</p>",
        )
        st.caption(gst_result["message"])
        _show_ai_status(gst_result)
        st.dataframe(_ai_preview_dataframe(gst_result["results"]), use_container_width=True, hide_index=True)

        section_card(
            "GST Reconciliation Detail",
            body_html="<p>Full invoice-level GST matching results from uploaded returns and accounting records.</p>",
        )
        st.dataframe(gst_result["results"], use_container_width=True, hide_index=True)

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
