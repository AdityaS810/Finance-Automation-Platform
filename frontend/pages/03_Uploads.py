"""Uploads page for bank statements and GSTR files."""

from __future__ import annotations

import streamlit as st

from backend.services.upload_service import save_bank_statement_upload, save_gstr_upload
from src.ingestion.bank_csv_parser import parse_bank_csv, parse_bank_excel
from src.ingestion.bank_pdf_parser import parse_bank_pdf
from src.ingestion.gstr_excel_parser import parse_gstr_excel
from src.ingestion.gstr_json_parser import parse_gstr_json
from src.ui import file_summary_card, load_css, page_header, section_card, status_badge
from src.utils.validation import validate_required_columns


load_css()

page_header(
    "Uploads",
    "Upload and process bank statements and GSTR reports.",
)

bank_required_columns = ["date", "narration", "debit", "credit", "balance_amount"]
gstr_required_columns = ["gstin", "invoice_number", "taxable_value", "igst", "cgst", "sgst", "period"]


def render_validation_state(is_valid: bool, missing_columns: list[str]) -> None:
    """Render a validation badge and helper copy."""
    if is_valid:
        st.markdown(status_badge("Ready to Save"), unsafe_allow_html=True)
    else:
        st.markdown(status_badge("Missing Columns"), unsafe_allow_html=True)
        st.caption("Missing columns: " + ", ".join(missing_columns))


def remove_duplicate_columns(df):
    """Remove duplicate columns so Streamlit can safely preview the dataframe."""
    return df.loc[:, ~df.columns.duplicated()].copy()


def prepare_bank_df_for_save(bank_df):
    """
    The frontend/parser uses balance_amount.
    The backend save function currently expects balance.
    This function adds balance for backend compatibility.
    """
    bank_df_to_save = bank_df.copy()

    if "balance" not in bank_df_to_save.columns and "balance_amount" in bank_df_to_save.columns:
        bank_df_to_save["balance"] = bank_df_to_save["balance_amount"]

    return bank_df_to_save


bank_tab, gstr_tab = st.tabs(["Bank Statement", "GSTR Report"])

with bank_tab:
    section_card(
        "Upload Bank Statement",
        body_html="<p>Accepted formats: CSV, Excel, PDF</p>",
    )

    uploaded_bank_file = st.file_uploader(
        "Upload and Process",
        type=["csv", "xlsx", "xls", "pdf"],
        key="bank_statement_upload",
        label_visibility="collapsed",
    )

    if uploaded_bank_file is not None:
        try:
            if uploaded_bank_file.name.lower().endswith(".csv"):
                bank_df = parse_bank_csv(uploaded_bank_file)
                bank_df = remove_duplicate_columns(bank_df)
                validation_result = validate_required_columns(bank_df, bank_required_columns)

            elif uploaded_bank_file.name.lower().endswith((".xlsx", ".xls")):
                bank_df = parse_bank_excel(uploaded_bank_file)
                bank_df = remove_duplicate_columns(bank_df)
                validation_result = validate_required_columns(bank_df, bank_required_columns)

            elif uploaded_bank_file.name.lower().endswith(".pdf"):
                bank_df = parse_bank_pdf(uploaded_bank_file)
                bank_df = remove_duplicate_columns(bank_df)
                validation_result = validate_required_columns(bank_df, bank_required_columns)
                st.info(f"PDF parser extracted {len(bank_df.index)} rows. Please review before saving.")

            else:
                bank_df = None
                validation_result = {"is_valid": False, "missing_columns": bank_required_columns}
                st.markdown(status_badge("Unsupported File"), unsafe_allow_html=True)
        except ValueError as error:
            bank_df = None
            validation_result = {"is_valid": False, "missing_columns": bank_required_columns}
            st.markdown(status_badge("Missing Columns"), unsafe_allow_html=True)
            st.error(str(error))
        except Exception as error:
            bank_df = None
            validation_result = {"is_valid": False, "missing_columns": bank_required_columns}
            st.markdown(status_badge("Upload Error"), unsafe_allow_html=True)
            st.error(f"Bank statement could not be parsed: {error}")

        if bank_df is not None:
            file_summary_card(uploaded_bank_file.name, "Bank Statement", row_count=len(bank_df.index))
            render_validation_state(validation_result["is_valid"], validation_result["missing_columns"])

            section_card(
                "Parsed Preview",
                body_html="<p>Preview the standardized statement structure before saving.</p>",
            )

            st.dataframe(bank_df.head(20), use_container_width=True, hide_index=True)

            if st.button("Save Bank Data", key="save_bank_data", use_container_width=True):
                if not validation_result["is_valid"]:
                    st.error("Bank file cannot be saved yet because required columns are missing.")
                else:
                    try:
                        bank_df_to_save = prepare_bank_df_for_save(bank_df)
                        result = save_bank_statement_upload(bank_df_to_save, uploaded_bank_file.name)
                        st.session_state["bank_upload_result"] = result
                        st.success(result["message"])
                    except Exception as error:
                        st.error(f"Bank upload failed: {error}")

            bank_upload_result = st.session_state.get("bank_upload_result")
            if bank_upload_result:
                st.caption(
                    f"Latest bank upload ID: {bank_upload_result['upload_id']} | Rows saved: {bank_upload_result['records_parsed']}"
                )

    else:
        section_card(
            "Bank Statement Status",
            body_html="<p>Upload a file to review the parsed structure and validation result.</p>",
        )

with gstr_tab:
    section_card(
        "Upload GSTR Report",
        body_html="<p>Accepted formats: Excel, JSON</p>",
    )

    uploaded_gstr_file = st.file_uploader(
        "Upload and Process",
        type=["xlsx", "xls", "json"],
        key="gstr_report_upload",
        label_visibility="collapsed",
    )

    if uploaded_gstr_file is not None:
        try:
            if uploaded_gstr_file.name.lower().endswith(".json"):
                gstr_df = parse_gstr_json(uploaded_gstr_file)
                gstr_df = remove_duplicate_columns(gstr_df)

            elif uploaded_gstr_file.name.lower().endswith((".xlsx", ".xls")):
                gstr_df = parse_gstr_excel(uploaded_gstr_file)
                gstr_df = remove_duplicate_columns(gstr_df)

            else:
                gstr_df = None
                st.markdown(status_badge("Unsupported File"), unsafe_allow_html=True)
        except ValueError as error:
            gstr_df = None
            st.markdown(status_badge("Missing Columns"), unsafe_allow_html=True)
            st.error(str(error))
        except Exception as error:
            gstr_df = None
            st.markdown(status_badge("Upload Error"), unsafe_allow_html=True)
            st.error(f"GSTR file could not be parsed: {error}")

        if gstr_df is not None:
            validation_result = validate_required_columns(gstr_df, gstr_required_columns)

            file_summary_card(uploaded_gstr_file.name, "GSTR Report", row_count=len(gstr_df.index))
            render_validation_state(validation_result["is_valid"], validation_result["missing_columns"])

            section_card(
                "Parsed Preview",
                body_html="<p>Preview the standardized GSTR structure before saving.</p>",
            )

            st.dataframe(gstr_df.head(20), use_container_width=True, hide_index=True)

            if st.button("Save GSTR Data", key="save_gstr_data", use_container_width=True):
                if not validation_result["is_valid"]:
                    st.error("GSTR file cannot be saved yet because required columns are missing.")
                else:
                    try:
                        result = save_gstr_upload(gstr_df, uploaded_gstr_file.name)
                        st.session_state["gstr_upload_result"] = result
                        st.success(result["message"])
                    except Exception as error:
                        st.error(f"GSTR upload failed: {error}")

            gstr_upload_result = st.session_state.get("gstr_upload_result")
            if gstr_upload_result:
                st.caption(
                    f"Latest GSTR upload ID: {gstr_upload_result['upload_id']} | Rows saved: {gstr_upload_result['records_parsed']}"
                )

    else:
        section_card(
            "GSTR Report Status",
            body_html="<p>Upload a file to review the parsed structure and validation result.</p>",
        )
