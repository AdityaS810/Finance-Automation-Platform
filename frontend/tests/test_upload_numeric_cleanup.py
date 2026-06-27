"""Tests for BigQuery-safe upload currency formatting."""

from __future__ import annotations

import pandas as pd

from backend.services.upload_service import _clean_money_columns, _money_string


def test_money_string_removes_binary_float_precision_artifacts():
    assert _money_string(141.98999999999998) == "141.99"
    assert _money_string("₹1,234.995") == "1235.00"
    assert _money_string(None) == "0.00"


def test_clean_money_columns_handles_bank_and_amount_suffixes():
    dataframe = pd.DataFrame(
        [
            {
                "debit": 10.239999999999998,
                "credit": "",
                "balance_amount": "₹1,000.1",
                "custom_amount": 5.555,
                "narration": "Test row",
            }
        ]
    )

    cleaned_df = _clean_money_columns(dataframe, ["debit", "credit", "balance_amount", "amount"])

    assert cleaned_df.loc[0, "debit"] == "10.24"
    assert cleaned_df.loc[0, "credit"] == "0.00"
    assert cleaned_df.loc[0, "balance_amount"] == "1000.10"
    assert cleaned_df.loc[0, "custom_amount"] == "5.56"
