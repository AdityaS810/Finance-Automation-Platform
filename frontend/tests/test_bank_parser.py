"""Tests for bank parser helpers."""

from __future__ import annotations

from io import StringIO

from src.ingestion.bank_csv_parser import parse_bank_csv
from src.utils.validation import validate_required_columns


def test_parse_bank_csv_maps_common_columns():
    csv_content = StringIO(
        "Transaction Date,Description,Withdrawal,Deposit,Closing Balance\n"
        "2026-05-01,Opening balance,0,1000,1000\n"
    )

    df = parse_bank_csv(csv_content)

    assert {"date", "narration", "debit", "credit", "balance"}.issubset(df.columns)


def test_validate_required_bank_columns_returns_valid():
    csv_content = StringIO(
        "Transaction Date,Description,Withdrawal,Deposit,Closing Balance\n"
        "2026-05-01,Opening balance,0,1000,1000\n"
    )

    df = parse_bank_csv(csv_content)
    result = validate_required_columns(df, ["date", "narration", "debit", "credit", "balance"])

    assert result["is_valid"] is True
