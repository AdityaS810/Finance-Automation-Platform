"""Excel parser for GSTR uploads."""

from __future__ import annotations

import pandas as pd

from src.utils.validation import normalize_dataframe_columns


GSTR_COLUMN_ALIASES = {
    "gst_number": "gstin",
    "supplier_gstin": "gstin",
    "invoice_no": "invoice_number",
    "invoice_num": "invoice_number",
    "invoice_value": "taxable_value",
    "taxable_amount": "taxable_value",
    "integrated_tax": "igst",
    "central_tax": "cgst",
    "state_tax": "sgst",
    "return_period": "period",
    "filing_period": "period",
}


def parse_gstr_excel(uploaded_file) -> pd.DataFrame:
    """Read a GSTR Excel file and map common fields to a standard structure."""
    df = pd.read_excel(uploaded_file)
    df = normalize_dataframe_columns(df)
    renamed_columns = {column: GSTR_COLUMN_ALIASES.get(column, column) for column in df.columns}
    df = df.rename(columns=renamed_columns)
    return df
