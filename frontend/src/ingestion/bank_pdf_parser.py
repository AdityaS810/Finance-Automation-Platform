"""Placeholder PDF parser for bank statements."""

from __future__ import annotations

import pandas as pd


def parse_bank_pdf(uploaded_file) -> pd.DataFrame:
    """Return an empty placeholder dataframe for future PDF extraction work."""
    # TODO: Implement PDF table extraction with pdfplumber or bank-specific templates.
    _ = uploaded_file
    return pd.DataFrame(columns=["date", "narration", "debit", "credit", "balance"])
