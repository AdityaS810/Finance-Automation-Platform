"""Downloads page."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src.ui import file_summary_card, load_css, page_header, section_card
from src.utils.file_helpers import list_output_files


load_css()

page_header(
    "Downloads",
    "Download generated MIS reports and reconciliation outputs.",
)

frontend_root = Path(__file__).resolve().parents[1]
repo_root = Path(__file__).resolve().parents[2]


EXPORT_GROUPS = [
    {
        "title": "MIS Reports",
        "label": "MIS Report",
        "empty": "No MIS reports are available yet.",
        "keywords": ["mis"],
    },
    {
        "title": "Bank Reconciliation",
        "label": "Bank Reconciliation Report",
        "empty": "No bank reconciliation reports are available yet.",
        "keywords": ["bank_reconciliation", "bank reconciliation"],
    },
    {
        "title": "GST Reconciliation",
        "label": "GST Reconciliation Report",
        "empty": "No GST reconciliation reports are available yet.",
        "keywords": ["gst_reconciliation", "gst reconciliation"],
    },
    {
        "title": "Other Exports",
        "label": "Export File",
        "empty": "No other exports are available yet.",
        "keywords": [],
    },
]


def _collect_output_files() -> list[dict]:
    """Collect output files from current and legacy output folders."""
    folders = [
        frontend_root / "outputs",
        frontend_root / "outputs" / "reports",
        frontend_root / "outputs" / "reconciliation_exports",
        repo_root / "outputs",
        repo_root / "outputs" / "reports",
        repo_root / "outputs" / "reconciliation_exports",
    ]
    seen_paths = set()
    files = []

    for folder in folders:
        for item in list_output_files(folder):
            file_path = Path(item["path"]).resolve()
            if file_path in seen_paths:
                continue
            seen_paths.add(file_path)
            item["path"] = file_path
            item["extension"] = file_path.suffix.lower()
            files.append(item)

    return sorted(files, key=lambda item: item["last_modified"], reverse=True)


def _group_key(file_item: dict) -> str:
    """Classify an output file into a user-facing report group."""
    file_name = file_item["file_name"].lower()

    if "mis" in file_name:
        return "MIS Reports"
    if "bank_reconciliation" in file_name or "bank reconciliation" in file_name:
        return "Bank Reconciliation"
    if "gst_reconciliation" in file_name or "gst reconciliation" in file_name:
        return "GST Reconciliation"
    return "Other Exports"


def _latest_main_file(files: list[dict]) -> dict | None:
    """Prefer the latest XLSX file for the main card, then any latest file."""
    xlsx_files = [item for item in files if item["extension"] == ".xlsx"]
    if xlsx_files:
        return xlsx_files[0]
    return files[0] if files else None


def _older_files(files: list[dict], main_file: dict | None) -> list[dict]:
    """Return files that should live in the older exports expander."""
    if main_file is None:
        return files
    return [item for item in files if Path(item["path"]) != Path(main_file["path"])]


def _modified_label(file_item: dict) -> str:
    """Format modified time for cards and tables."""
    return datetime.fromtimestamp(file_item["last_modified"]).strftime("%d %b %Y, %I:%M %p")


def _mime_type(file_path: Path) -> str:
    """Return a useful download MIME type for common export files."""
    if file_path.suffix.lower() == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if file_path.suffix.lower() == ".csv":
        return "text/csv"
    return "application/octet-stream"


def _download_button(file_item: dict, label: str, key_prefix: str) -> None:
    """Render a download button for one file."""
    file_path = Path(file_item["path"])
    with open(file_path, "rb") as download_file:
        st.download_button(
            label=label,
            data=download_file.read(),
            file_name=file_path.name,
            mime=_mime_type(file_path),
            key=f"{key_prefix}_{file_path.name}_{int(file_item['last_modified'])}",
            use_container_width=True,
        )


def _older_exports_table(files: list[dict]) -> pd.DataFrame:
    """Build a compact table for older exports."""
    return pd.DataFrame(
        [
            {
                "File Name": item["file_name"],
                "Type": item["type"],
                "Last Modified": _modified_label(item),
            }
            for item in files
        ]
    )


all_files = _collect_output_files()
grouped_files = {group["title"]: [] for group in EXPORT_GROUPS}
for file_item in all_files:
    grouped_files[_group_key(file_item)].append(file_item)

section_card(
    "Latest Downloads",
    body_html="<p>Each section shows the latest XLSX export first. Older and CSV exports remain available in details.</p>",
)

for group in EXPORT_GROUPS:
    files = grouped_files[group["title"]]
    section_card(group["title"], body_html=f"<p>{group['label']} downloads.</p>")

    if not files:
        st.info(group["empty"])
        continue

    main_file = _latest_main_file(files)
    older = _older_files(files, main_file)

    if main_file:
        file_summary_card(main_file["file_name"], group["label"])
        st.caption(f"Last modified: {_modified_label(main_file)}")
        _download_button(main_file, f"Download {group['label']}", f"latest_{group['title']}")

    if older:
        with st.expander("Show older exports"):
            st.dataframe(_older_exports_table(older), use_container_width=True, hide_index=True)
            for older_file in older:
                _download_button(older_file, f"Download {older_file['file_name']}", f"older_{group['title']}")
