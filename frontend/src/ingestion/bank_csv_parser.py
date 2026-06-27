"""Bank statement parsers for CSV and Excel uploads.

Banks use many different column labels. These helpers convert common real-bank
formats into the stable schema used by the Uploads page and backend loader.
"""

from __future__ import annotations

import re

import pandas as pd

from src.utils.validation import coalesce_duplicate_columns


REQUIRED_BANK_COLUMNS = ["date", "narration", "debit", "credit", "balance_amount"]
AMOUNT_COLUMNS = ["debit", "credit", "balance_amount", "amount"]
HEADER_SCAN_ROWS = 30

BANK_COLUMN_ALIASES: dict[str, list[str]] = {
    "date": [
        "txn date",
        "transaction date",
        "date",
        "value date",
        "trans date",
        "posting date",
    ],
    "narration": [
        "description",
        "narration",
        "particulars",
        "transaction details",
        "transaction detail",
        "details",
        "remarks",
    ],
    "debit": [
        "withdrawal",
        "withdrawals",
        "debit",
        "debit amount",
        "paid out",
        "withdrawal amt",
        "dr amount",
        "dr",
    ],
    "credit": [
        "deposit",
        "deposits",
        "credit",
        "credit amount",
        "paid in",
        "deposit amt",
        "cr amount",
        "cr",
    ],
    "balance_amount": [
        "balance",
        "closing balance",
        "running balance",
        "amount balance",
        "balance amount",
        "available balance",
    ],
    "amount": [
        "amount",
        "transaction amount",
        "txn amount",
        "transaction amt",
    ],
    "reference_number": [
        "reference number",
        "reference no",
        "ref no",
        "cheque no",
        "chq no",
        "utr",
    ],
}


def parse_bank_csv(uploaded_file) -> pd.DataFrame:
    """Read a bank statement CSV and map common columns to a standard format."""
    raw_df = pd.read_csv(uploaded_file, header=None, dtype=object)
    table_df = _build_table_from_detected_header(raw_df)
    return _standardize_bank_table(table_df)


def parse_bank_excel(uploaded_file) -> pd.DataFrame:
    """Read a bank statement Excel file and map common columns to a standard format."""
    workbook = pd.read_excel(uploaded_file, sheet_name=None, header=None, dtype=object)
    best_match: tuple[int, int, str, pd.DataFrame] | None = None

    for sheet_order, (sheet_name, raw_sheet) in enumerate(workbook.items()):
        header_result = _find_header_row(raw_sheet)
        if header_result is None:
            continue

        header_row, score = header_result
        ranking = (score, -sheet_order, sheet_name, raw_sheet)
        if best_match is None or ranking[:2] > best_match[:2]:
            best_match = ranking
            best_header_row = header_row

    if best_match is None:
        raise ValueError(
            "Could not find bank statement headers. The parser looks for fields such as "
            "Transaction Date, Narration, Debit, Credit, Amount, and Balance."
        )

    _, _, _, raw_sheet = best_match
    table_df = _build_table_from_header(raw_sheet, best_header_row)
    return _standardize_bank_table(table_df)


def _build_table_from_detected_header(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Find the header row in CSV-style data and return only transaction rows."""
    header_result = _find_header_row(raw_df)
    if header_result is None:
        raise ValueError(
            "Could not find bank statement headers. The parser looks for fields such as "
            "Transaction Date, Narration, Debit, Credit, Amount, and Balance."
        )

    header_row, _ = header_result
    return _build_table_from_header(raw_df, header_row)


def _find_header_row(raw_df: pd.DataFrame) -> tuple[int, int] | None:
    """Return the row that best matches bank statement column labels."""
    best_row: tuple[int, int] | None = None
    rows_to_scan = min(len(raw_df.index), HEADER_SCAN_ROWS)

    for row_index in range(rows_to_scan):
        row_values = raw_df.iloc[row_index].tolist()
        matched_columns = {
            ALIAS_LOOKUP[column_key]
            for column_key in (_clean_column_key(value) for value in row_values)
            if column_key in ALIAS_LOOKUP
        }

        has_date = "date" in matched_columns
        has_narration = "narration" in matched_columns
        has_money = bool({"debit", "credit", "amount", "balance_amount"} & matched_columns)
        if not has_date or not has_money:
            continue

        score = len(matched_columns)
        score += 2 if has_narration else 0
        score += 2 if {"debit", "credit"} <= matched_columns else 0
        score += 1 if "amount" in matched_columns else 0
        score += 1 if "balance_amount" in matched_columns else 0

        if best_row is None or score > best_row[1]:
            best_row = (row_index, score)

    return best_row


def _build_table_from_header(raw_df: pd.DataFrame, header_row: int) -> pd.DataFrame:
    """Create a normal dataframe from raw rows once the header row is known."""
    header_values = raw_df.iloc[header_row].tolist()
    table_df = raw_df.iloc[header_row + 1 :].copy()
    table_df.columns = [_header_label(value, index) for index, value in enumerate(header_values)]
    table_df = table_df.dropna(how="all")
    return table_df.reset_index(drop=True)


def _standardize_bank_table(table_df: pd.DataFrame) -> pd.DataFrame:
    """Map source columns to date/narration/debit/credit/balance_amount."""
    column_map = _match_source_columns(table_df.columns)
    standardized_df = pd.DataFrame(index=table_df.index)

    for source_column, standard_column in column_map.items():
        source_series = table_df[source_column]
        if standard_column in standardized_df:
            standardized_df.loc[:, standard_column] = _fill_blank_values(
                standardized_df[standard_column],
                source_series,
            )
        else:
            standardized_df.loc[:, standard_column] = source_series

    standardized_df = coalesce_duplicate_columns(standardized_df)

    for amount_column in AMOUNT_COLUMNS:
        if amount_column in standardized_df:
            standardized_df.loc[:, amount_column] = _to_number(standardized_df[amount_column])

    if "amount" in standardized_df:
        signed_amount = standardized_df["amount"]
        if "debit" not in standardized_df:
            standardized_df.loc[:, "debit"] = signed_amount.where(signed_amount < 0, 0).abs()
        else:
            standardized_df.loc[:, "debit"] = _fill_zero_values(
                standardized_df["debit"],
                signed_amount.where(signed_amount < 0, 0).abs(),
            )

        if "credit" not in standardized_df:
            standardized_df.loc[:, "credit"] = signed_amount.where(signed_amount > 0, 0)
        else:
            standardized_df.loc[:, "credit"] = _fill_zero_values(
                standardized_df["credit"],
                signed_amount.where(signed_amount > 0, 0),
            )

    for amount_column in ["debit", "credit", "balance_amount"]:
        if amount_column not in standardized_df:
            standardized_df.loc[:, amount_column] = 0.0
        else:
            standardized_df.loc[:, amount_column] = pd.to_numeric(
                standardized_df[amount_column],
                errors="coerce",
            ).fillna(0.0)

    if "balance" not in standardized_df and "balance_amount" in standardized_df:
        standardized_df.loc[:, "balance"] = standardized_df["balance_amount"]

    standardized_df = _drop_non_transaction_rows(standardized_df)

    missing_columns = [column for column in REQUIRED_BANK_COLUMNS if column not in standardized_df.columns]
    if missing_columns:
        raise ValueError(
            "Bank statement is missing required fields after parsing: "
            + ", ".join(missing_columns)
            + ". Please upload a statement with date, narration, amount/debit/credit, and balance columns."
        )

    return standardized_df.reset_index(drop=True)


def _match_source_columns(columns: pd.Index) -> dict[object, str]:
    """Return source-column to standard-column matches for known bank labels."""
    column_map: dict[object, str] = {}
    for source_column in columns:
        column_key = _clean_column_key(source_column)
        if column_key in ALIAS_LOOKUP:
            column_map[source_column] = ALIAS_LOOKUP[column_key]
    return column_map


def _clean_column_key(value: object) -> str:
    """Normalize labels by removing spaces, punctuation, currency symbols, and newlines."""
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().lower()
    if text.startswith("unnamed"):
        return ""
    text = text.replace("₹", "")
    return re.sub(r"[^a-z0-9]+", "", text)


ALIAS_LOOKUP = {
    _clean_column_key(alias): standard_name
    for standard_name, aliases in BANK_COLUMN_ALIASES.items()
    for alias in aliases
}


def _header_label(value: object, index: int) -> str:
    """Keep readable headers while giving blank cells stable names."""
    if value is None or pd.isna(value) or str(value).strip() == "":
        return f"unnamed_column_{index + 1}"
    return str(value).strip()


def _to_number(series: pd.Series) -> pd.Series:
    """Convert bank amount text like '₹1,234.00 Dr' into signed numbers."""
    text_values = series.astype(str).str.strip().str.lower()
    is_parentheses_negative = text_values.str.match(r"^\(.*\)$", na=False)
    has_debit_marker = text_values.str.contains(r"\bdr\b|debit", regex=True, na=False)
    has_credit_marker = text_values.str.contains(r"\bcr\b|credit", regex=True, na=False)

    cleaned = text_values.str.replace(r"^\((.*)\)$", r"\1", regex=True)
    cleaned = cleaned.str.replace(r"[₹,\s]", "", regex=True)
    cleaned = cleaned.str.replace(r"[^0-9.\-]", "", regex=True)
    numeric_values = pd.to_numeric(cleaned, errors="coerce").fillna(0.0)

    should_be_negative = is_parentheses_negative | (has_debit_marker & ~has_credit_marker)
    numeric_values = numeric_values.where(~should_be_negative, -numeric_values.abs())
    numeric_values = numeric_values.where(~has_credit_marker, numeric_values.abs())
    return numeric_values


def _drop_non_transaction_rows(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Remove empty report/title rows that do not contain transaction data."""
    working_df = dataframe.copy()
    if "date" in working_df:
        working_df = working_df[~_is_blank(working_df["date"])]
    if "narration" in working_df:
        working_df = working_df[~_is_blank(working_df["narration"])]
    return working_df


def _fill_blank_values(primary: pd.Series, fallback: pd.Series) -> pd.Series:
    """Use fallback values only where the primary column is blank."""
    return primary.where(~_is_blank(primary), fallback)


def _fill_zero_values(primary: pd.Series, fallback: pd.Series) -> pd.Series:
    """Use signed amount values only where debit/credit is empty or zero."""
    numeric_primary = pd.to_numeric(primary, errors="coerce").fillna(0.0)
    return numeric_primary.where(numeric_primary != 0, fallback)


def _is_blank(series: pd.Series) -> pd.Series:
    """Return True for nulls, empty strings, and common spreadsheet blank markers."""
    text_values = series.astype(str).str.strip().str.lower()
    return series.isna() | text_values.isin(["", "nan", "none", "nat"])
