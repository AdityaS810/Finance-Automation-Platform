"""JSON parser for GSTR uploads."""

from __future__ import annotations

import json

import pandas as pd

from src.utils.validation import normalize_dataframe_columns


EXPECTED_COLUMNS = ["gstin", "invoice_number", "taxable_value", "igst", "cgst", "sgst", "period"]


def _extract_invoice_records(payload: object) -> list[dict]:
    """Extract likely invoice rows from nested JSON structures."""
    invoice_records = []

    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"invoices", "records", "data"} and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        invoice_records.append(item)
            else:
                invoice_records.extend(_extract_invoice_records(value))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                invoice_records.append(item)
            else:
                invoice_records.extend(_extract_invoice_records(item))

    return invoice_records


def parse_gstr_json(uploaded_file) -> pd.DataFrame:
    """Flatten a simple GSTR JSON payload into a dataframe."""
    payload = json.load(uploaded_file)
    invoice_records = _extract_invoice_records(payload)

    if not invoice_records and isinstance(payload, dict):
        invoice_records = [payload]

    df = pd.json_normalize(invoice_records)
    df = normalize_dataframe_columns(df)

    alias_map = {
        "gst_number": "gstin",
        "supplier_gstin": "gstin",
        "invoice_no": "invoice_number",
        "invoice_num": "invoice_number",
        "taxable_amount": "taxable_value",
        "integrated_tax": "igst",
        "central_tax": "cgst",
        "state_tax": "sgst",
        "return_period": "period",
    }

    df = df.rename(columns={column: alias_map.get(column, column) for column in df.columns})

    for column in EXPECTED_COLUMNS:
        if column not in df.columns:
            df[column] = None

    return df[EXPECTED_COLUMNS + [column for column in df.columns if column not in EXPECTED_COLUMNS]]
