"""Excel parser for GSTR uploads.

Real GST portal workbooks often have report titles before the table headers.
This parser scans the workbook, finds the most likely invoice header row, and
then standardizes the columns that the rest of the app expects.
"""

from __future__ import annotations

import re

import pandas as pd


REQUIRED_GSTR_COLUMNS = ["gstin", "invoice_number", "taxable_value", "igst", "cgst", "sgst", "period"]
AMOUNT_COLUMNS = ["invoice_value", "taxable_value", "igst", "cgst", "sgst", "cess"]
HEADER_SCAN_ROWS = 50

# Keep this map explicit and readable. Each key is the app's standard name;
# each value contains common labels seen in GST portal exports and old samples.
GSTR_COLUMN_ALIASES: dict[str, list[str]] = {
    "gstin": [
        "gstin",
        "gst number",
        "gst no",
        "gstin of supplier",
        "gstin/uin of supplier",
        "gstin/uin of the supplier",
        "supplier gstin",
        "supplier_gstin",
    ],
    "supplier_name": [
        "trade/legal name",
        "trade legal name",
        "supplier name",
        "legal name",
        "trade name",
    ],
    "invoice_number": [
        "invoice number",
        "invoice no",
        "invoice no.",
        "invoice num",
        "invoice_number",
        "inum",
    ],
    "invoice_date": [
        "invoice date",
        "invoice dt",
        "invoice_date",
        "idt",
    ],
    "invoice_value": [
        "invoice value",
        "invoice value(₹)",
        "invoice value (₹)",
        "invoice value rs",
        "invoice value inr",
        "val",
    ],
    "taxable_value": [
        "taxable value",
        "taxable value(₹)",
        "taxable value (₹)",
        "taxable amount",
        "taxable value rs",
        "taxable value inr",
        "txval",
    ],
    "igst": [
        "integrated tax",
        "integrated tax(₹)",
        "integrated tax (₹)",
        "integrated tax amount",
        "igst",
        "igst amount",
        "iamt",
    ],
    "cgst": [
        "central tax",
        "central tax(₹)",
        "central tax (₹)",
        "central tax amount",
        "cgst",
        "cgst amount",
        "camt",
    ],
    "sgst": [
        "state/ut tax",
        "state/ut tax(₹)",
        "state/ut tax (₹)",
        "state tax",
        "state tax amount",
        "sgst",
        "sgst amount",
        "samt",
    ],
    "cess": [
        "cess",
        "cess(₹)",
        "cess (₹)",
        "cess amount",
        "csamt",
    ],
    "place_of_supply": [
        "place of supply",
        "pos",
    ],
    "period": [
        "return period",
        "filing period",
        "tax period",
        "period",
    ],
    "invoice_type": [
        "invoice type",
    ],
    "reverse_charge": [
        "supply attract reverse charge",
        "reverse charge",
    ],
}

def parse_gstr_excel(uploaded_file) -> pd.DataFrame:
    """Read a GSTR Excel file and return the app's standard GSTR structure."""
    sheet_name, raw_sheet, header_row = _find_best_gstr_sheet(uploaded_file)
    table_df = _build_table_from_header(raw_sheet, header_row)
    standardized_df = _standardize_gstr_table(table_df)

    if standardized_df.empty:
        raise ValueError(
            f"No invoice rows were found in the GSTR sheet '{sheet_name}'. "
            "Check that the uploaded workbook contains supplier invoice details."
        )

    missing_columns = [column for column in REQUIRED_GSTR_COLUMNS if column not in standardized_df.columns]
    if missing_columns:
        raise ValueError(
            "GSTR file is missing required fields after parsing: "
            + ", ".join(missing_columns)
            + ". Please upload the supplier invoice section from the GST portal export."
        )

    return standardized_df


def _find_best_gstr_sheet(uploaded_file) -> tuple[str, pd.DataFrame, int]:
    """Read all sheets and choose the row that looks most like GSTR headers."""
    workbook = pd.read_excel(uploaded_file, sheet_name=None, header=None, dtype=object)
    best_match: tuple[int, int, str, pd.DataFrame, int] | None = None

    for sheet_order, (sheet_name, raw_sheet) in enumerate(workbook.items()):
        header_candidate = _find_header_row(raw_sheet)
        if header_candidate is None:
            continue

        header_row, score = header_candidate
        ranking = (score, -sheet_order, sheet_name, raw_sheet, header_row)
        if best_match is None or ranking[:2] > best_match[:2]:
            best_match = ranking

    if best_match is None:
        raise ValueError(
            "Could not find a GSTR invoice header row. The parser looks for fields such as "
            "GSTIN of supplier, Invoice number, Taxable Value, IGST, CGST, and SGST."
        )

    _, _, sheet_name, raw_sheet, header_row = best_match
    return sheet_name, raw_sheet, header_row


def _find_header_row(raw_sheet: pd.DataFrame) -> tuple[int, int] | None:
    """Return the best header row index and score for one worksheet."""
    best_row: tuple[int, int] | None = None
    rows_to_scan = min(len(raw_sheet.index), HEADER_SCAN_ROWS)

    for row_index in range(rows_to_scan):
        row_values = raw_sheet.iloc[row_index].tolist()
        matched_columns = {
            ALIAS_LOOKUP[column_key]
            for column_key in (_clean_column_key(value) for value in row_values)
            if column_key in ALIAS_LOOKUP
        }

        # Metadata rows may mention GSTIN or period. A real invoice header should
        # also mention invoice fields and at least one supplier or amount field.
        has_invoice_field = bool({"invoice_number", "invoice_date", "invoice_value"} & matched_columns)
        has_supplier_or_amount = bool(
            {"gstin", "supplier_name", "taxable_value", "igst", "cgst", "sgst"} & matched_columns
        )
        if not has_invoice_field or not has_supplier_or_amount:
            continue

        score = len(matched_columns)
        score += 2 if "invoice_number" in matched_columns else 0
        score += 2 if "gstin" in matched_columns else 0
        score += 2 if "taxable_value" in matched_columns else 0

        if best_row is None or score > best_row[1]:
            best_row = (row_index, score)

    return best_row


def _build_table_from_header(raw_sheet: pd.DataFrame, header_row: int) -> pd.DataFrame:
    """Turn a raw sheet with title rows into a normal dataframe."""
    header_values = raw_sheet.iloc[header_row].tolist()
    table_df = raw_sheet.iloc[header_row + 1 :].copy()
    table_df.columns = [_header_label(value, index) for index, value in enumerate(header_values)]
    table_df = table_df.dropna(how="all")
    return table_df.reset_index(drop=True)


def _standardize_gstr_table(table_df: pd.DataFrame) -> pd.DataFrame:
    """Map portal/sample columns to standard columns and derive safe values."""
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

    for amount_column in AMOUNT_COLUMNS:
        if amount_column in standardized_df:
            standardized_df.loc[:, amount_column] = _to_number(standardized_df[amount_column])

    # GST files sometimes omit a tax bucket when it is zero. Keep required tax
    # columns present so validation and BigQuery loading remain stable.
    for tax_column in ["igst", "cgst", "sgst", "cess"]:
        if tax_column not in standardized_df:
            standardized_df.loc[:, tax_column] = 0.0

    if "taxable_value" not in standardized_df and "invoice_value" in standardized_df:
        tax_total = standardized_df[["igst", "cgst", "sgst", "cess"]].sum(axis=1)
        standardized_df.loc[:, "taxable_value"] = (standardized_df["invoice_value"] - tax_total).clip(lower=0)

    if "invoice_value" not in standardized_df and "taxable_value" in standardized_df:
        tax_total = standardized_df[["igst", "cgst", "sgst", "cess"]].sum(axis=1)
        standardized_df.loc[:, "invoice_value"] = standardized_df["taxable_value"] + tax_total

    parsed_invoice_dates = None
    if "invoice_date" in standardized_df:
        parsed_invoice_dates = _to_datetime(standardized_df["invoice_date"])
        standardized_df.loc[:, "invoice_date"] = parsed_invoice_dates.dt.date

    if "period" in standardized_df:
        standardized_df.loc[:, "period"] = _to_period(standardized_df["period"], parsed_invoice_dates)
    elif parsed_invoice_dates is not None:
        standardized_df.loc[:, "period"] = parsed_invoice_dates.dt.strftime("%Y-%m")

    standardized_df = _drop_non_invoice_rows(standardized_df)
    return standardized_df.reset_index(drop=True)


def _match_source_columns(columns: pd.Index) -> dict[object, str]:
    """Return source-column to standard-column matches for known GSTR labels."""
    column_map: dict[object, str] = {}
    for source_column in columns:
        column_key = _clean_column_key(source_column)
        if column_key in ALIAS_LOOKUP:
            column_map[source_column] = ALIAS_LOOKUP[column_key]
    return column_map


def _clean_column_key(value: object) -> str:
    """Normalize column labels by removing currency signs, spaces, and punctuation."""
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().lower()
    if text.startswith("unnamed"):
        return ""
    text = text.replace("₹", "")
    return re.sub(r"[^a-z0-9]+", "", text)


ALIAS_LOOKUP = {
    _clean_column_key(alias): standard_name
    for standard_name, aliases in GSTR_COLUMN_ALIASES.items()
    for alias in aliases
}


def _header_label(value: object, index: int) -> str:
    """Keep readable source labels while giving blank headers stable names."""
    if value is None or pd.isna(value) or str(value).strip() == "":
        return f"unnamed_column_{index + 1}"
    return str(value).strip()


def _to_number(series: pd.Series) -> pd.Series:
    """Convert GST amount text like '₹1,234.00' into numeric values."""
    cleaned = series.astype(str).str.strip()
    cleaned = cleaned.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    cleaned = cleaned.str.replace(r"[₹,\s]", "", regex=True)
    cleaned = cleaned.str.replace(r"[^0-9.\-]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)


def _to_datetime(series: pd.Series) -> pd.Series:
    """Parse normal date strings and Excel serial date numbers."""
    text_dates = pd.to_datetime(series, errors="coerce", dayfirst=True)
    serial_numbers = pd.to_numeric(series, errors="coerce")
    serial_dates = pd.to_datetime(serial_numbers, unit="D", origin="1899-12-30", errors="coerce")
    return text_dates.fillna(serial_dates)


def _to_period(series: pd.Series, fallback_dates: pd.Series | None) -> pd.Series:
    """Standardize return periods to YYYY-MM and fill blanks from invoice dates."""
    parsed_periods = pd.to_datetime(series, errors="coerce", dayfirst=True)
    period_values = parsed_periods.dt.strftime("%Y-%m")

    text_values = series.astype(str).str.strip()
    yyyy_mm = text_values.str.extract(r"(?P<period>\d{4}[-/]\d{1,2})", expand=False)
    period_values = period_values.fillna(yyyy_mm.str.replace("/", "-", regex=False))

    if fallback_dates is not None:
        period_values = period_values.fillna(fallback_dates.dt.strftime("%Y-%m"))

    return period_values


def _drop_non_invoice_rows(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Remove report total/title rows that do not represent supplier invoices."""
    working_df = dataframe.copy()
    if "gstin" in working_df:
        working_df = working_df[~_is_blank(working_df["gstin"])]
    if "invoice_number" in working_df:
        working_df = working_df[~_is_blank(working_df["invoice_number"])]
    return working_df


def _fill_blank_values(primary: pd.Series, fallback: pd.Series) -> pd.Series:
    """Use fallback values only where the primary mapped column is blank."""
    return primary.where(~_is_blank(primary), fallback)


def _is_blank(series: pd.Series) -> pd.Series:
    """Return True for nulls, empty strings, and common Excel blank markers."""
    text_values = series.astype(str).str.strip().str.lower()
    return series.isna() | text_values.isin(["", "nan", "none", "nat"])
