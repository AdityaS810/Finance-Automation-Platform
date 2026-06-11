"""Backend-owned placeholder bank reconciliation flow."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def run_bank_reconciliation(output_dir: str | Path) -> dict:
    """Return the current backend placeholder bank reconciliation results."""
    results = pd.DataFrame(
        [
            {
                "bank_date": "2026-05-02",
                "bank_narration": "Client Receipt - Alpha Co",
                "bank_amount": 125000,
                "zoho_date": "2026-05-02",
                "zoho_reference": "REC-1024",
                "confidence": 0.98,
                "status": "Matched",
            },
            {
                "bank_date": "2026-05-04",
                "bank_narration": "Vendor Payment - Beta Services",
                "bank_amount": -48250,
                "zoho_date": "2026-05-05",
                "zoho_reference": "BILL-347",
                "confidence": 0.76,
                "status": "Review",
            },
            {
                "bank_date": "2026-05-07",
                "bank_narration": "UPI Credit",
                "bank_amount": 7600,
                "zoho_date": "",
                "zoho_reference": "",
                "confidence": 0.35,
                "status": "Unmatched",
            },
        ]
    )

    destination_folder = Path(output_dir)
    destination_folder.mkdir(parents=True, exist_ok=True)
    export_path = destination_folder / "bank_reconciliation_results.csv"
    results.to_csv(export_path, index=False)

    return {
        "summary": {
            "matched_records": 186,
            "unmatched_bank_records": 12,
            "unmatched_zoho_records": 9,
            "gemini_suggestions_pending_review": 5,
        },
        "results": results,
        "export_path": export_path,
        "is_placeholder": True,
        "message": "Current backend bank reconciliation still uses placeholder matching output.",
    }
