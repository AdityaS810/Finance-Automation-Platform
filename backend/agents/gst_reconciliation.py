"""Backend-owned placeholder GST reconciliation flow."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def run_gst_reconciliation(output_dir: str | Path) -> dict:
    """Return the current backend placeholder GST reconciliation results."""
    results = pd.DataFrame(
        [
            {
                "gstin": "29ABCDE1234F1Z7",
                "invoice_number": "INV-2201",
                "books_amount": 55000,
                "gstr_amount": 55000,
                "difference": 0,
                "risk_level": "Low",
                "status": "Exact Match",
            },
            {
                "gstin": "27PQRSX5678L1Z2",
                "invoice_number": "INV-2210",
                "books_amount": 78000,
                "gstr_amount": 74250,
                "difference": 3750,
                "risk_level": "Medium",
                "status": "Mismatch",
            },
            {
                "gstin": "07LMNOP9876Q1Z5",
                "invoice_number": "INV-2214",
                "books_amount": 42000,
                "gstr_amount": 0,
                "difference": 42000,
                "risk_level": "High",
                "status": "Missing in GSTR",
            },
        ]
    )

    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)
    export_path = destination_folder / "gst_reconciliation_results.csv"
    results.to_csv(export_path, index=False)

    return {
        "summary": {
            "exact_matches": 242,
            "mismatches": 17,
            "missing_in_gstr": 6,
            "high_risk_itc_issues": 3,
        },
        "results": results,
        "export_path": export_path,
        "is_placeholder": True,
        "message": "Current backend GST reconciliation still uses placeholder comparison output.",
    }
