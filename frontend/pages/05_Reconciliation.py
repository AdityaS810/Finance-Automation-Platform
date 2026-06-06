"""Reconciliation page."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from backend.agents.bank_reconciliation import run_bank_reconciliation
from backend.agents.gst_reconciliation import run_gst_reconciliation
from src.ui import load_css, metric_card, page_header, section_card


load_css()

page_header(
    "Reconciliation",
    "Review bank and GST reconciliation results with AI-assisted suggestions.",
)

bank_tab, gst_tab = st.tabs(["Bank Reconciliation", "GST Reconciliation"])
output_dir = Path(__file__).resolve().parents[1] / "outputs" / "reconciliation_exports"

with bank_tab:
    if st.button("Run Bank Reconciliation", type="primary"):
        try:
            st.session_state["bank_recon_result"] = run_bank_reconciliation(output_dir)
            st.success("Bank reconciliation completed. Review the latest matching results below.")
        except Exception as error:
            st.error(f"Bank reconciliation failed: {error}")

    bank_result = st.session_state.get("bank_recon_result")
    if bank_result is None:
        bank_result = run_bank_reconciliation(output_dir)
    bank_df = bank_result["results"].copy()
    bank_df["status"] = bank_df["status"].replace({"Review": "Under Review"})
    total_transactions = len(bank_df.index)
    matched_count = int((bank_df["status"] == "Matched").sum())
    unmatched_count = int((bank_df["status"] == "Unmatched").sum())
    under_review_count = int((bank_df["status"] == "Under Review").sum())

    summary_columns = st.columns(4)
    with summary_columns[0]:
        metric_card("Total Transactions", str(total_transactions), caption="Processed records", status="Info", icon="TT")
    with summary_columns[1]:
        metric_card("Matched", str(matched_count), caption="Confirmed matches", status="Success", icon="MT")
    with summary_columns[2]:
        metric_card("Unmatched", str(unmatched_count), caption="Require follow-up", status="Error", icon="UM")
    with summary_columns[3]:
        metric_card("Under Review", str(under_review_count), caption="AI-assisted suggestions pending", status="Warning", icon="RV")

    section_card(
        "Reconciliation Review",
        body_html="<p>Confidence scores support review decisions. AI-assisted suggestions are shown for guidance and are not final decisions.</p>",
    )
    st.caption(bank_result["message"])
    st.data_editor(
        bank_df.assign(review_action="Pending"),
        use_container_width=True,
        hide_index=True,
        column_config={
            "review_action": st.column_config.SelectboxColumn(
                "Review Action",
                options=["Pending", "Approve", "Reject"],
            ),
            "confidence": st.column_config.ProgressColumn(
                "Confidence",
                min_value=0.0,
                max_value=1.0,
                format="%.0f%%",
            ),
        },
        disabled=["bank_date", "bank_narration", "bank_amount", "zoho_date", "zoho_reference", "confidence", "status"],
    )

    export_path = Path(bank_result["export_path"])
    if export_path.exists():
        with open(export_path, "rb") as export_file:
            st.download_button(
                "Download Results",
                data=export_file.read(),
                file_name=export_path.name,
                mime="text/csv",
                use_container_width=True,
            )

with gst_tab:
    if st.button("Run GST Reconciliation", type="primary"):
        try:
            st.session_state["gst_recon_result"] = run_gst_reconciliation(output_dir)
            st.success("GST reconciliation completed. Review the latest invoice comparison below.")
        except Exception as error:
            st.error(f"GST reconciliation failed: {error}")

    gst_result = st.session_state.get("gst_recon_result")
    if gst_result is None:
        gst_result = run_gst_reconciliation(output_dir)
    summary = gst_result["summary"]
    summary_columns = st.columns(4)
    with summary_columns[0]:
        metric_card("Exact Matches", str(summary["exact_matches"]), caption="Invoices aligned", status="Success", icon="EM")
    with summary_columns[1]:
        metric_card("Mismatches", str(summary["mismatches"]), caption="Amount variance detected", status="Warning", icon="MM")
    with summary_columns[2]:
        metric_card("Missing in GSTR", str(summary["missing_in_gstr"]), caption="Not found in return", status="Warning", icon="MG")
    with summary_columns[3]:
        metric_card("High-risk ITC Issues", str(summary["high_risk_itc_issues"]), caption="Immediate review recommended", status="Error", icon="HR")

    gst_df = gst_result["results"].copy()
    section_card(
        "GST Reconciliation Review",
        body_html="<p>High-risk ITC issues should be reviewed before final submission. Suggested matches remain subject to finance approval.</p>",
    )
    st.caption(gst_result["message"])
    st.dataframe(
        gst_df,
        use_container_width=True,
        hide_index=True,
    )

    export_path = Path(gst_result["export_path"])
    if export_path.exists():
        with open(export_path, "rb") as export_file:
            st.download_button(
                "Download GST Results",
                data=export_file.read(),
                file_name=export_path.name,
                mime="text/csv",
                use_container_width=True,
            )
