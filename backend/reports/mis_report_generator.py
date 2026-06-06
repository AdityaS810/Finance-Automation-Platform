"""Simple backend MIS report generation for the Streamlit app."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


def get_mis_metrics(financial_year: str) -> dict:
    """Return the current placeholder MIS metrics from backend-owned code."""
    return {
        "Gross Margin FY": "41.8%",
        "Monthly OpEx Run-rate": "INR 18.4L",
        "Monthly Gross Burn": "INR 9.6L",
        "Revenue per India FTE": "INR 4.2L",
        "Total People Cost": "INR 1.12Cr",
        "Closing Cash": "INR 73.5L",
        "Cash Runway": "7.7 months",
        "Financial Year": financial_year,
    }


def generate_mis_report(financial_year: str, output_dir: str | Path) -> dict:
    """Generate a simple Excel MIS file using backend-owned logic."""
    metrics = get_mis_metrics(financial_year)
    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)

    file_name = f"mis_report_{financial_year.replace('-', '_')}.xlsx"
    report_path = destination_folder / file_name

    metrics_rows = [
        {"Metric": metric_name, "Value": metric_value}
        for metric_name, metric_value in metrics.items()
        if metric_name != "Financial Year"
    ]
    metadata_rows = [
        {"Field": "Financial Year", "Value": financial_year},
        {"Field": "Generated At", "Value": datetime.now().strftime("%d %b %Y, %I:%M %p")},
        {"Field": "Data Source", "Value": "Current backend placeholder metrics"},
    ]

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        pd.DataFrame(metadata_rows).to_excel(writer, sheet_name="MIS Summary", index=False, startrow=0)
        pd.DataFrame(metrics_rows).to_excel(writer, sheet_name="MIS Summary", index=False, startrow=6)

    return {
        "status": "success",
        "message": "MIS report generated successfully.",
        "metrics": metrics,
        "report_path": report_path,
        "is_placeholder": True,
    }
