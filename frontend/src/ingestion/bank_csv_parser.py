"""CSV parser for bank statement uploads."""

from __future__ import annotations

import pandas as pd

from src.utils.validation import coalesce_duplicate_columns, normalize_dataframe_columns


BANK_COLUMN_ALIASES = {
    "transaction_date": "date",
    "txn_date": "date",
    "value_date": "date",
    "description": "narration",
    "remarks": "narration",
    "particulars": "narration",
    "details": "narration",
    "withdrawal": "debit",
    "withdrawal_amt": "debit",
    "debit_amount": "debit",
    "deposit": "credit",
    "deposit_amt": "credit",
    "credit_amount": "credit",
    "closing_balance": "balance",
    "running_balance": "balance",
    "amount_balance": "balance",
}


def parse_bank_csv(uploaded_file) -> pd.DataFrame:
    """Read a bank statement CSV and map common columns to a standard format."""
    df = pd.read_csv(uploaded_file)
    df = normalize_dataframe_columns(df)
    renamed_columns = {column: BANK_COLUMN_ALIASES.get(column, column) for column in df.columns}
    df = df.rename(columns=renamed_columns)
    return coalesce_duplicate_columns(df)
