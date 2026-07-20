"""MIS Report page."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

from backend.reports.mis_report_generator import (
    generate_consolidated_balance_sheet_report,
    generate_consolidated_pl_report,
    generate_mis_report,
)
from backend.reports.report_periods import (
    get_financial_year_dates,
    get_fy_label,
    get_month_periods,
)
from backend.services.mis_mapping_service import (
    MAPPING_COLUMNS,
    load_mis_mapping_dataframe,
    save_mis_mapping_dataframe,
    validate_mis_mapping_dataframe,
)
from src.ui import file_summary_card, load_css, metric_card, page_header, section_card


MAPPING_TABLE_COLUMNS = [
    "Excel Section",
    "Excel Row Label",
    "Zoho Account Code",
    "Zoho Account Name",
    "Organization",
    "Sign Rule",
    "Status",
    "Action",
]

MAPPING_EDITOR_COLUMNS = [
    "Report Template",
    "Excel Section",
    "Excel Row Label",
    "Zoho Account ID",
    "Zoho Account Code",
    "Zoho Account Name",
    "Organization",
    "Sign Rule",
    "Active",
    "Updated At",
    "Updated By",
]


def _mapping_for_editor(mapping_df):
    editor_df = mapping_df.copy()
    editor_df["Active"] = editor_df["Active/Inactive"].map(
        lambda value: str(value).strip().lower() == "active"
    )
    return editor_df.drop(columns=["Active/Inactive"]).reindex(columns=MAPPING_EDITOR_COLUMNS)


def _mapping_for_service(editor_df):
    mapping_df = editor_df.copy()
    mapping_df["Active/Inactive"] = mapping_df["Active"].map(
        lambda value: "Active" if str(value).strip().lower() == "true" else "Inactive"
    )
    return mapping_df.drop(columns=["Active"]).reindex(columns=MAPPING_COLUMNS)


def _option_index(options, value, default=0):
    return options.index(value) if value in options else default


def _mapping_status(row) -> str:
    has_account_details = bool(
        str(row.get("Zoho Account Code") or "").strip()
        or str(row.get("Zoho Account Name") or "").strip()
    )
    if not has_account_details:
        return "Not mapped"
    return "Active" if str(row.get("Active")).strip().lower() == "true" else "Inactive"


load_css()

page_header(
    "MIS Report",
    "Generate MIS, Consolidated P&L, and Consolidated Balance Sheet reports from available accounting data.",
)

generate_tab, mapping_tab = st.tabs(["Generate Report", "Mapping Configuration"])

with generate_tab:
    financial_year_options = [2024, 2025, 2026]
    report_type_options = [
        "Existing MIS Report",
        "Consolidated P&L",
        "Consolidated Balance Sheet",
    ]
    selected_report_type = st.selectbox("Report Type", options=report_type_options, index=0)
    existing_period_type_options = {
        "Full Year": "full_year",
        "Month": "month",
        "Quarter": "quarter",
        "Half Year": "half_year",
        "Custom Date Range": "custom",
    }
    consolidated_period_type_options = {
        "Quarter": "quarter",
        "6 Months": "half_year",
        "1 Year": "full_year",
    }
    period_type_options = (
        existing_period_type_options
        if selected_report_type == "Existing MIS Report"
        else consolidated_period_type_options
    )
    organization_options = {
        "All Organizations": "all",
        "India - Midoffice Data Solutions Private Limited": "india",
        "US - Midoffice Data International, Inc": "us",
    }

    selector_columns = st.columns([1.1, 1.1, 1.5, 1], vertical_alignment="bottom")
    with selector_columns[0]:
        selected_financial_year_start = st.selectbox(
            "Financial Year",
            options=financial_year_options,
            index=1,
            format_func=get_fy_label,
        )
    with selector_columns[1]:
        selected_period_type_label = st.selectbox("Period Type", options=list(period_type_options), index=0)
    with selector_columns[2]:
        selected_organization_label = st.selectbox(
            "Organization",
            options=list(organization_options),
            index=0,
            disabled=selected_report_type != "Existing MIS Report",
            help="Consolidated reports always combine available India and US organization data.",
        )

    selected_period_type = period_type_options[selected_period_type_label]
    selected_month = None
    selected_quarter = None
    selected_half = None
    custom_start_date = None
    custom_end_date = None

    fy_start_date, fy_end_date = get_financial_year_dates(selected_financial_year_start)
    month_periods = get_month_periods(selected_financial_year_start)

    if selected_period_type != "full_year":
        detail_columns = st.columns([1, 1], vertical_alignment="bottom")
        if selected_period_type == "month":
            with detail_columns[0]:
                selected_month = st.selectbox(
                    "Month",
                    options=month_periods,
                    format_func=lambda month: month["title_label"],
                )["month_number"]
        elif selected_period_type == "quarter":
            with detail_columns[0]:
                selected_quarter = st.selectbox("Quarter", options=["Q1", "Q2", "Q3", "Q4"], index=0)
        elif selected_period_type == "half_year":
            with detail_columns[0]:
                selected_half = st.selectbox("Half Year", options=["H1", "H2"], index=0)
        elif selected_period_type == "custom":
            with detail_columns[0]:
                custom_start_date = st.date_input(
                    "Start Date",
                    value=fy_start_date,
                    min_value=fy_start_date,
                    max_value=fy_end_date,
                )
            with detail_columns[1]:
                custom_end_date = st.date_input(
                    "End Date",
                    value=fy_end_date,
                    min_value=fy_start_date,
                    max_value=fy_end_date,
                )

    with selector_columns[3]:
        generate_clicked = st.button(
            f"Generate {selected_report_type}",
            type="primary",
            use_container_width=True,
        )

    if generate_clicked:
        try:
            report_output_dir = Path(__file__).resolve().parents[2] / "outputs"
            common_report_arguments = {
                "financial_year": selected_financial_year_start,
                "output_dir": report_output_dir,
                "period_type": selected_period_type,
                "selected_quarter": selected_quarter,
                "selected_half": selected_half,
            }
            if selected_report_type == "Consolidated P&L":
                result = generate_consolidated_pl_report(**common_report_arguments)
            elif selected_report_type == "Consolidated Balance Sheet":
                result = generate_consolidated_balance_sheet_report(**common_report_arguments)
            else:
                result = generate_mis_report(
                    **common_report_arguments,
                    org_filter=organization_options[selected_organization_label],
                    selected_month=selected_month,
                    custom_start_date=custom_start_date,
                    custom_end_date=custom_end_date,
                )
            st.session_state["mis_report_result"] = result
            st.session_state.pop("mis_report_error", None)
            st.success(result["message"])
        except Exception:
            st.session_state["mis_report_error"] = "The selected report could not be generated. Please check report inputs and available accounting data."
            st.session_state.pop("mis_report_result", None)

    mis_result = st.session_state.get("mis_report_result")
    mis_error = st.session_state.get("mis_report_error")

    if mis_result:
        if mis_result.get("report_type") == "Consolidated P&L":
            metric_items = [
                ("Revenue", mis_result["metrics"]["Revenue"], "Selected reporting window", "Success", "RV"),
                ("COGS", mis_result["metrics"]["COGS"], "Available consolidated classification", "Warning", "CG"),
                ("Operating Profit / EBITDA", mis_result["metrics"]["Operating Profit / EBITDA"], "Before other income/expenses", "Success", "OP"),
                ("Net Profit", mis_result["metrics"]["Net Profit"], "Consolidated INR result", "Success", "NP"),
            ]
        elif mis_result.get("report_type") == "Consolidated Balance Sheet":
            metric_items = [
                ("Assets", mis_result["metrics"]["Assets"], "Available balances", "Success", "AS"),
                ("Liabilities", mis_result["metrics"]["Liabilities"], "Available balances", "Warning", "LI"),
                ("Equity / Retained Earnings", mis_result["metrics"]["Equity / Retained Earnings"], "Available balances", "Info", "EQ"),
                ("As Of Date", mis_result["metrics"]["As Of Date"], "Selected period end date", "Info", "DT"),
            ]
        else:
            metric_items = [
                ("Revenue", mis_result["metrics"]["Revenue"], "Selected reporting window", "Success", "RV"),
                ("Expenses", mis_result["metrics"]["Expenses"], "Selected reporting window", "Warning", "EX"),
                ("Profit", mis_result["metrics"]["Profit"], "Revenue less expenses", "Success", "PF"),
                ("Currency", mis_result["metrics"]["Reporting Currency"], "Consolidated reporting", "Info", "INR"),
                ("Journal Adjustments", mis_result["metrics"]["Journal Adjustments"], "Currently derived from detail data", "Info", "JA"),
                ("Invoices", mis_result["metrics"]["Invoices"], "Selected period source rows", "Info", "IN"),
                ("Bills", mis_result["metrics"]["Bills"], "Selected period source rows", "Info", "BL"),
                ("Contacts", mis_result["metrics"]["Contacts"], "Dashboard fallback count", "Info", "CT"),
            ]

        metric_columns = st.columns(4)
        for index, item in enumerate(metric_items):
            with metric_columns[index % 4]:
                metric_card(*item)

    report_card_columns = st.columns([1.2, 0.9])
    with report_card_columns[0]:
        section_card(
            "Latest Report",
            body_html=(
                "<p>The selected workbook uses the financial year and reporting period to build its accounting date basis and output filename.</p>"
                "<p>Consolidated reports combine available India and US data in INR.</p>"
            ),
        )
        if mis_result and mis_result.get("report_path") and Path(mis_result["report_path"]).exists():
            report_path = mis_result["report_path"]
            file_path = Path(report_path)
            generated_time = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%d %b %Y, %I:%M %p")
            file_summary_card(file_path.name, mis_result.get("report_type", "Existing MIS Report"))
            st.caption(f"Report period: {mis_result['report_period']['header_title']}")
            st.caption(f"Generated time: {generated_time}")
            if mis_result.get("data_message"):
                st.info(mis_result["data_message"])
        elif mis_error:
            section_card(
                "Generation Failed",
                body_html="<p>The report could not be generated from the current MIS data sources.</p>",
            )
            st.error(mis_error)
        else:
            section_card(
                "Ready to Generate",
                body_html="<p>Select a financial year and reporting period, then generate the report.</p>",
            )

    with report_card_columns[1]:
        if mis_result and mis_result.get("report_path") and Path(mis_result["report_path"]).exists():
            report_path = mis_result["report_path"]
            with open(report_path, "rb") as report_file:
                st.download_button(
                    "Download Excel",
                    data=report_file.read(),
                    file_name=Path(report_path).name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

    if mis_result:
        section_card(
            "Report Preview",
            body_html="<p>Preview the available line-item values written into the selected workbook.</p>",
        )
        preview_df = mis_result.get("report_preview", mis_result.get("monthly_preview"))
        if preview_df is not None and not preview_df.empty:
            st.dataframe(preview_df, use_container_width=True, hide_index=True)
        else:
            st.info("No report detail was available for preview. The blank-safe workbook can still be downloaded.")

with mapping_tab:
    st.subheader("MIS Mapping Configuration")
    st.caption(
        "Map Zoho GL/accounts to MIS Excel rows. Only active mappings are used when generating MIS reports."
    )

    try:
        if st.session_state.pop("mis_mapping_saved", False):
            st.session_state.pop("mis_mapping_draft", None)
            st.success("Mapping configuration saved successfully.")

        if "mis_mapping_draft" not in st.session_state:
            st.session_state["mis_mapping_draft"] = _mapping_for_editor(load_mis_mapping_dataframe())
        draft_df = st.session_state["mis_mapping_draft"].copy()

        st.markdown("#### Add / Update Mapping")
        mapping_options = [-1, *range(len(draft_df))]

        def mapping_option_label(row_index):
            if row_index == -1:
                return "Add a new mapping"
            row = draft_df.iloc[row_index]
            row_label = str(row.get("Excel Row Label") or "Unnamed MIS row").strip()
            section = str(row.get("Excel Section") or "section").strip()
            code = str(row.get("Zoho Account Code") or "Not mapped").strip()
            return f"{row_index + 1}. {row_label} | {section} | {code}"

        selected_row_index = st.selectbox(
            "Choose a mapping",
            options=mapping_options,
            format_func=mapping_option_label,
        )
        selected_row = draft_df.iloc[selected_row_index] if selected_row_index >= 0 else None

        section_options = ["revenue", "expense", "note_tracking"]
        organization_options = ["all", "india", "us"]
        sign_rule_options = ["positive", "expense_positive", "negative"]

        form_key_suffix = str(selected_row_index).replace("-", "new_")
        with st.form(
            f"mis_mapping_form_{form_key_suffix}",
            border=True,
            clear_on_submit=True,
        ):
            form_columns = st.columns(2)
            with form_columns[0]:
                excel_section = st.selectbox(
                    "Excel Section",
                    options=section_options,
                    format_func=lambda value: value.replace("_", " ").title(),
                    index=_option_index(
                        section_options,
                        selected_row.get("Excel Section") if selected_row is not None else "expense",
                        default=1,
                    ),
                    key=f"mis_mapping_section_{form_key_suffix}",
                )
                excel_row_label = st.text_input(
                    "Excel Row Label",
                    value=str(selected_row.get("Excel Row Label") or "") if selected_row is not None else "",
                    key=f"mis_mapping_row_label_{form_key_suffix}",
                )
                organization = st.selectbox(
                    "Organization",
                    options=organization_options,
                    format_func=lambda value: {
                        "all": "All organizations",
                        "india": "India",
                        "us": "US",
                    }.get(value, value),
                    index=_option_index(
                        organization_options,
                        selected_row.get("Organization") if selected_row is not None else "all",
                    ),
                    key=f"mis_mapping_organization_{form_key_suffix}",
                )
            with form_columns[1]:
                zoho_account_code = st.text_input(
                    "Zoho Account Code",
                    value=str(selected_row.get("Zoho Account Code") or "") if selected_row is not None else "",
                    key=f"mis_mapping_account_code_{form_key_suffix}",
                )
                zoho_account_name = st.text_input(
                    "Zoho Account Name",
                    value=str(selected_row.get("Zoho Account Name") or "") if selected_row is not None else "",
                    key=f"mis_mapping_account_name_{form_key_suffix}",
                )
                sign_rule = st.selectbox(
                    "Sign Rule",
                    options=sign_rule_options,
                    format_func=lambda value: value.replace("_", " ").title(),
                    index=_option_index(
                        sign_rule_options,
                        selected_row.get("Sign Rule") if selected_row is not None else "positive",
                    ),
                    key=f"mis_mapping_sign_rule_{form_key_suffix}",
                )

            active = st.checkbox(
                "Active",
                value=(
                    str(selected_row.get("Active")).strip().lower() == "true"
                    if selected_row is not None
                    else False
                ),
                help="Only active mappings are used in MIS report generation.",
                key=f"mis_mapping_active_{form_key_suffix}",
            )
            update_mapping_clicked = st.form_submit_button("Add / Update Mapping", type="primary")

        if update_mapping_clicked:
            form_errors = []
            if active and not excel_row_label.strip():
                form_errors.append("Excel Row Label is required for an active mapping.")
            if active and not zoho_account_code.strip() and not zoho_account_name.strip():
                form_errors.append("Zoho Account Code or Zoho Account Name is required for an active mapping.")

            if form_errors:
                st.error(" ".join(form_errors))
            else:
                updated_values = {
                    "Excel Section": excel_section,
                    "Excel Row Label": excel_row_label.strip(),
                    "Zoho Account Code": zoho_account_code.strip(),
                    "Zoho Account Name": zoho_account_name.strip(),
                    "Organization": organization,
                    "Sign Rule": sign_rule,
                    "Active": active,
                }
                if selected_row_index >= 0:
                    for column, value in updated_values.items():
                        draft_df.at[selected_row_index, column] = value
                    message = "Mapping updated. Click Save Changes to apply it."
                else:
                    new_row = {column: "" for column in MAPPING_EDITOR_COLUMNS}
                    new_row.update(updated_values)
                    draft_df.loc[len(draft_df)] = [new_row[column] for column in MAPPING_EDITOR_COLUMNS]
                    message = "Mapping added. Click Save Changes to apply it."
                st.session_state["mis_mapping_draft"] = draft_df
                st.session_state["mis_mapping_form_message"] = message
                st.rerun()

        if form_message := st.session_state.pop("mis_mapping_form_message", None):
            st.success(form_message)

        st.markdown("#### Current Mappings")
        current_mappings_df = draft_df.copy()
        current_mappings_df["Status"] = current_mappings_df.apply(_mapping_status, axis=1)
        current_mappings_df["Action"] = "Select above to edit"
        not_mapped_count = int(current_mappings_df["Status"].eq("Not mapped").sum())
        if not_mapped_count:
            st.info(
                f"{not_mapped_count} MIS rows are not mapped yet. Add Zoho account details when ready."
            )
        st.dataframe(
            current_mappings_df[MAPPING_TABLE_COLUMNS],
            use_container_width=True,
            hide_index=True,
        )

        action_columns = st.columns([0.9, 0.9, 4])
        with action_columns[0]:
            save_clicked = st.button("Save Changes", type="primary")
        with action_columns[1]:
            st.download_button(
                "Download CSV",
                data=current_mappings_df[MAPPING_TABLE_COLUMNS[:-1]].to_csv(index=False).encode("utf-8"),
                file_name="mis_mapping_configuration.csv",
                mime="text/csv",
            )

        if save_clicked:
            service_mapping_df = _mapping_for_service(draft_df)
            validation_errors = validate_mis_mapping_dataframe(service_mapping_df)
            if validation_errors:
                st.error("Changes were not saved. " + " ".join(validation_errors))
            else:
                result = save_mis_mapping_dataframe(service_mapping_df)
                if result["status"] == "success":
                    st.session_state["mis_mapping_saved"] = True
                    st.rerun()
                else:
                    st.error("Changes were not saved. " + " ".join(result["errors"]))
    except Exception:
        st.error("MIS mapping configuration is temporarily unavailable. Please try again after checking app configuration.")
