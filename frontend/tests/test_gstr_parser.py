"""Tests for GSTR parser helpers."""

from __future__ import annotations

from io import BytesIO, StringIO

import pandas as pd

from src.ingestion.gstr_excel_parser import parse_gstr_excel
from src.ingestion.gstr_json_parser import parse_gstr_json


def test_parse_gstr_excel_maps_expected_columns():
    sample_df = pd.DataFrame(
        [
            {
                "GST Number": "29ABCDE1234F1Z7",
                "Invoice No": "INV-1001",
                "Taxable Amount": 50000,
                "Integrated Tax": 9000,
                "Central Tax": 0,
                "State Tax": 0,
                "Return Period": "2026-05",
            }
        ]
    )

    buffer = BytesIO()
    sample_df.to_excel(buffer, index=False)
    buffer.seek(0)

    parsed_df = parse_gstr_excel(buffer)

    assert {"gstin", "invoice_number", "taxable_value", "igst", "cgst", "sgst", "period"}.issubset(parsed_df.columns)


def test_parse_gstr_json_flattens_basic_invoice_data():
    json_content = StringIO(
        '{"invoices":[{"gst_number":"29ABCDE1234F1Z7","invoice_no":"INV-1001","taxable_amount":50000,"integrated_tax":9000,"central_tax":0,"state_tax":0,"return_period":"2026-05"}]}'
    )

    parsed_df = parse_gstr_json(json_content)

    assert parsed_df.loc[0, "gstin"] == "29ABCDE1234F1Z7"
    assert parsed_df.loc[0, "invoice_number"] == "INV-1001"
