"""Data Sync page."""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from backend.services.sync_service import (
    friendly_sync_error,
    get_latest_sync_run,
    get_sync_history,
    is_cloud_auth_error,
    run_zoho_sync,
)
from src.ui import load_css, metric_card, page_header, section_card, status_badge
from src.utils.file_helpers import ensure_directories


ensure_directories()
load_css()

page_header(
    "Data Sync",
    "Sync latest accounting data from Zoho Books into BigQuery. Data moves through Raw, Enrich, and Consume layers.",
)

default_end_date = date.today()
default_start_date = default_end_date - timedelta(days=30)
PERIOD_TRACKING_MESSAGE = (
    "Selected period is tracked for reporting visibility. Current extractor syncs latest available "
    "Zoho records where endpoint filtering is not supported."
)


def _format_date(value: Any) -> str:
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y")
    except ValueError:
        return str(value)


def _format_datetime(value: Any) -> str:
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %I:%M %p")
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y, %I:%M %p")
    except ValueError:
        return str(value)


def _period_from_metadata(metadata: dict[str, Any]) -> tuple[str, str]:
    from_date = metadata.get("from_date") or metadata.get("selected_ui_from_date")
    to_date = metadata.get("to_date") or metadata.get("selected_ui_to_date")
    return _format_date(from_date), _format_date(to_date)


def _records_loaded(run: dict[str, Any] | None) -> int | None:
    if not run:
        return None
    records_loaded = run.get("records_loaded")
    metadata = run.get("metadata") or {}
    entity_counts = metadata.get("entity_record_counts") or {}
    if records_loaded is not None:
        return int(records_loaded)
    if entity_counts:
        return sum(sum(int(value or 0) for value in org_counts.values()) for org_counts in entity_counts.values())
    return None


def _format_records(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _format_duration(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        duration_text = str(value).strip()
        return duration_text or "-"
    if seconds < 60:
        return f"{seconds} sec"
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {remaining_seconds} sec"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours} hr {remaining_minutes} min"


def _format_triggered_by(value: Any) -> str:
    if not value:
        return "-"
    return str(value).replace("_", " ").title()


def _status_label(value: Any) -> str:
    return str(value or "unknown").strip().title()


def _status_tone(status: str) -> str:
    normalized = status.strip().lower()
    if normalized == "success":
        return "Success"
    if normalized == "failed":
        return "Failed"
    if normalized == "running":
        return "Info"
    return "Neutral"


def _display_error_message(error_message: Any) -> str:
    if not error_message:
        return ""
    text = str(error_message)
    if is_cloud_auth_error(Exception(text)):
        return "Cloud authentication is required. Please refresh Google authentication and try again."
    return "Sync failed before completion. Please review configuration and try again."


def _build_history_dataframe(history: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for run in history:
        metadata = run.get("metadata") or {}
        from_date, to_date = _period_from_metadata(metadata)
        rows.append(
            {
                "Loaded From": from_date,
                "Loaded To": to_date,
                "Status": _status_label(run.get("status")),
                "Completed At": _format_datetime(run.get("completed_at")),
                "Duration": _format_duration(run.get("duration_seconds")),
                "Records": _format_records(run.get("records_loaded")),
                "Triggered By": _format_triggered_by(run.get("triggered_by")),
            }
        )
    return pd.DataFrame(rows)


def _organization_lookup(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    organizations = metadata.get("organizations") or []
    return {
        str(organization.get("org_key")): organization
        for organization in organizations
        if organization.get("org_key")
    }


def _friendly_org_name(org_key: str, organization: dict[str, Any] | None = None) -> str:
    organization = organization or {}
    country = organization.get("country")
    if country:
        return str(country)
    if org_key.lower() == "india":
        return "India"
    if org_key.lower() == "us":
        return "US"
    return org_key.replace("_", " ").title()


def _entity_label(value: Any) -> str:
    return str(value or "-").replace("_", " ").title()


def _build_organization_summary_dataframe(
    run: dict[str, Any] | None,
    session_row_counts: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    if session_row_counts:
        rows = [
            {
                "Organization": row.get("organization_name") or _friendly_org_name(str(row.get("org_key") or "")),
                "Currency": row.get("source_currency") or "-",
                "Entity": _entity_label(row.get("entity")),
                "Records Loaded": int(row.get("rows_loaded") or 0),
            }
            for row in session_row_counts
        ]
        return pd.DataFrame(rows).sort_values(["Organization", "Entity"], ignore_index=True)

    metadata = (run or {}).get("metadata") or {}
    entity_counts = metadata.get("entity_record_counts") or {}
    organizations = _organization_lookup(metadata)
    rows = []
    for org_key, org_counts in entity_counts.items():
        organization = organizations.get(str(org_key), {})
        for entity, records_loaded in sorted((org_counts or {}).items()):
            rows.append(
                {
                    "Organization": _friendly_org_name(str(org_key), organization),
                    "Currency": organization.get("base_currency") or "-",
                    "Entity": _entity_label(entity),
                    "Records Loaded": int(records_loaded or 0),
                }
            )
    return pd.DataFrame(rows)


def _organization_summary_lines(summary_df: pd.DataFrame) -> list[str]:
    if summary_df.empty:
        return []
    lines = []
    for organization, org_df in summary_df.groupby("Organization", sort=False):
        counts = [
            f"{str(row['Entity']).lower()} {int(row['Records Loaded']):,}"
            for _, row in org_df.sort_values("Entity").iterrows()
        ]
        lines.append(f"{organization} - {', '.join(counts)}")
    return lines


def _find_latest_successful_run(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in history:
        if str(run.get("status") or "").lower() == "success":
            return run
    return None

section_card(
    "Sync Window",
    body_html=(
        "<p>Select the accounting period to bring into the reporting warehouse.</p>"
        "<p>Raw captures source data, Enrich standardizes it, and Consume provides final business-ready data for MIS, dashboards, and reconciliation.</p>"
    ),
)

date_columns = st.columns([1, 1, 1.1], vertical_alignment="bottom")
with date_columns[0]:
    start_date = st.date_input("From Date", value=default_start_date)
with date_columns[1]:
    end_date = st.date_input("To Date", value=default_end_date)
with date_columns[2]:
    sync_clicked = st.button("Sync Latest Data", type="primary", use_container_width=True)

if sync_clicked:
    started_at = datetime.now()
    try:
        result = run_zoho_sync(start_date, end_date)
        completed_at = datetime.now()
        duration_seconds = max(int((completed_at - started_at).total_seconds()), 1)
        st.session_state["data_sync_result"] = {
            **result,
            "completed_at": completed_at,
            "duration": f"{duration_seconds} sec",
        }
        st.session_state.pop("data_sync_error", None)
    except ValueError as error:
        st.session_state["data_sync_error"] = str(error)
        st.session_state.pop("data_sync_result", None)
    except Exception as error:
        st.session_state["data_sync_error"] = friendly_sync_error(error)
        st.session_state.pop("data_sync_result", None)

sync_result = st.session_state.get("data_sync_result")
sync_error = st.session_state.get("data_sync_error")
sync_history = []
latest_sync = None
history_error = None

try:
    sync_history = get_sync_history(limit=25)
    latest_sync = get_latest_sync_run()
except Exception as error:
    history_error = friendly_sync_error(error) if is_cloud_auth_error(error) else "Sync history is temporarily unavailable."

if latest_sync is None and sync_result:
    latest_sync = {
        "status": sync_result.get("status"),
        "started_at": sync_result.get("synced_at"),
        "completed_at": sync_result.get("completed_at"),
        "duration_seconds": str(sync_result.get("duration", "")).replace(" sec", ""),
        "records_loaded": sync_result.get("records_loaded"),
        "metadata": sync_result.get("metadata") or {},
        "triggered_by": "streamlit_user",
        "error_message": None,
    }

latest_successful_sync = _find_latest_successful_run(sync_history)
if latest_sync and str(latest_sync.get("status") or "").lower() == "success":
    latest_successful_sync = latest_sync

if latest_sync:
    latest_metadata = latest_sync.get("metadata") or {}
    latest_from_date, latest_to_date = _period_from_metadata(latest_metadata)
    latest_status = _status_label(latest_sync.get("status"))
    _, latest_success_to_date = _period_from_metadata(
        (latest_successful_sync or {}).get("metadata") or {}
    )
    latest_attempt_failed = latest_status.lower() == "failed"
    failure_html = ""
    if latest_attempt_failed:
        failure_html = "<p><strong>Latest attempt failed.</strong></p>"
        if latest_successful_sync and latest_successful_sync != latest_sync:
            failure_html += (
                "<p>Last successful data loaded up to: "
                f"<strong>{html.escape(latest_success_to_date)}</strong></p>"
            )
    section_card(
        "Latest Data Loaded",
        body_html=(
            f"{failure_html}"
            f"<p>Loaded Period: <strong>{html.escape(latest_from_date)} to {html.escape(latest_to_date)}</strong></p>"
            f"<p>Status: {status_badge(latest_status)}</p>"
            f"<p>Completed At: <strong>{html.escape(_format_datetime(latest_sync.get('completed_at')))}</strong></p>"
            f"<p>Duration: <strong>{html.escape(_format_duration(latest_sync.get('duration_seconds')))}</strong></p>"
            f"<p>Total Records Loaded: <strong>{html.escape(_format_records(_records_loaded(latest_sync)))}</strong></p>"
            f"<p>Triggered By: <strong>{html.escape(_format_triggered_by(latest_sync.get('triggered_by')))}</strong></p>"
            "<p>US and India Zoho organizations are synced separately, then consolidated in INR for reporting.</p>"
            f"<p>{html.escape(PERIOD_TRACKING_MESSAGE)}</p>"
        ),
    )

    kpi_columns = st.columns(4)
    loaded_to_value = latest_success_to_date if latest_attempt_failed and latest_successful_sync else latest_to_date
    total_records_value = _records_loaded(latest_successful_sync if latest_attempt_failed and latest_successful_sync else latest_sync)
    organization_metadata = (latest_successful_sync if latest_attempt_failed and latest_successful_sync else latest_sync).get("metadata") or {}
    entity_counts = organization_metadata.get("entity_record_counts") or {}
    organizations_synced = organization_metadata.get("organizations_synced") or []
    organization_count = len(entity_counts) or len(organizations_synced)
    with kpi_columns[0]:
        metric_card("Last Loaded To Date", loaded_to_value, "Latest successful reporting coverage", "Info")
    with kpi_columns[1]:
        metric_card("Total Records Loaded", _format_records(total_records_value), "Rows synced into reporting data", "Success")
    with kpi_columns[2]:
        metric_card("Organizations Synced", _format_records(organization_count), "Zoho organizations included", "Info")
    with kpi_columns[3]:
        metric_card("Last Sync Status", latest_status, "Most recent sync attempt", _status_tone(latest_status))

if sync_result:
    st.success(sync_result["message"])
    organization_columns = st.columns(len(sync_result.get("organizations", [])) or 1)
    for index, organization in enumerate(sync_result.get("organizations", [])):
        with organization_columns[index]:
            st.metric(
                organization["org_key"].upper(),
                organization["base_currency"],
                help=organization["organization_name"],
            )
elif sync_error:
    section_card(
        "Sync Not Completed",
        body_html=(
            "<p>The backend sync could not run.</p>"
            "<p>Please refresh authentication or configuration, then try again.</p>"
        ),
    )
    st.error(sync_error)
elif latest_sync is None:
    section_card(
        "Integration Status",
        body_html=(
            "<p>Use the button above to run the existing backend Zoho sync.</p>"
            f"<p>{html.escape(PERIOD_TRACKING_MESSAGE)}</p>"
        ),
    )

section_card(
    "Organization Sync Summary",
    body_html=(
        "<p>Latest available volume summary by Zoho organization and entity. Consolidated reporting uses INR values.</p>"
        "<p>Data moves through Raw, Enrich, and Consume layers.</p>"
    ),
)
organization_summary_df = _build_organization_summary_dataframe(
    latest_successful_sync or latest_sync,
    sync_result.get("row_counts") if sync_result else None,
)
if not organization_summary_df.empty:
    for summary_line in _organization_summary_lines(organization_summary_df):
        st.caption(summary_line)
    st.dataframe(organization_summary_df, use_container_width=True, hide_index=True)
else:
    st.info("Organization sync summary will appear after a successful sync is available.")

section_card(
    "Sync History",
    body_html=(
        "<p>Sync history helps finance users confirm which reporting period has already been loaded.</p>"
        "<p>Persisted sync runs from BigQuery audit history.</p>"
    ),
)
if history_error:
    st.warning(history_error)
elif sync_history:
    st.dataframe(_build_history_dataframe(sync_history), use_container_width=True, hide_index=True)
    failed_runs = [
        run
        for run in sync_history
        if str(run.get("status") or "").lower() == "failed" and run.get("error_message")
    ]
    if failed_runs:
        with st.expander("Failed sync details"):
            for run in failed_runs:
                metadata = run.get("metadata") or {}
                from_date, to_date = _period_from_metadata(metadata)
                st.warning(
                    f"{from_date} to {to_date}: {_display_error_message(run.get('error_message'))}"
                )
else:
    st.info("No persisted sync history is available yet.")
