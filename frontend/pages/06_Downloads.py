"""Downloads page."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src.ui import empty_state, file_summary_card, load_css, page_header, section_card
from src.utils.file_helpers import list_output_files


load_css()

page_header(
    "Downloads",
    "Download generated MIS reports and reconciliation outputs.",
)

project_root = Path(__file__).resolve().parents[1]
report_files = list_output_files(project_root / "outputs" / "reports")
reconciliation_files = list_output_files(project_root / "outputs" / "reconciliation_exports")
all_files = report_files + reconciliation_files

if all_files:
    section_card(
        "Latest Outputs",
        body_html="<p>Review the generated files and download the latest output packages.</p>",
    )
    display_rows = [
        {
            "File Name": item["file_name"],
            "Type": item["type"],
            "Last Modified": datetime.fromtimestamp(item["last_modified"]).strftime("%d %b %Y, %I:%M %p"),
        }
        for item in all_files
    ]
    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

    download_columns = st.columns(2)
    for index, item in enumerate(all_files):
        with download_columns[index % 2]:
            file_path = Path(item["path"])
            file_summary_card(item["file_name"], item["type"])
            st.caption(f"Last modified: {datetime.fromtimestamp(item['last_modified']).strftime('%d %b %Y, %I:%M %p')}")
            with open(file_path, "rb") as download_file:
                st.download_button(
                    label="Download Results",
                    data=download_file.read(),
                    file_name=file_path.name,
                    mime="application/octet-stream",
                    key=f"download_{file_path.name}",
                    use_container_width=True,
                )
else:
    empty_state(
        "No outputs available yet",
        "Generate an MIS report or run reconciliation to make download files available here.",
    )
