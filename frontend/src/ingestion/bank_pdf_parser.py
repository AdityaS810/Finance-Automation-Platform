"""PDF parser for HSBC-style bank statement transaction history."""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = ["date", "narration", "debit", "credit", "balance_amount"]
MONTH_PATTERN = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
DATE_SEPARATOR_PATTERN = r"[\s-]+"
MONTH_FIRST_DATE_PATTERN = re.compile(
    rf"^({MONTH_PATTERN}{DATE_SEPARATOR_PATTERN}\d{{1,2}}{DATE_SEPARATOR_PATTERN}\d{{4}})\b",
    re.IGNORECASE,
)
DAY_FIRST_DATE_PATTERN = re.compile(
    rf"^(\d{{1,2}}{DATE_SEPARATOR_PATTERN}{MONTH_PATTERN}{DATE_SEPARATOR_PATTERN}\d{{4}})\b",
    re.IGNORECASE,
)
EMBEDDED_MONTH_FIRST_DATE_PATTERN = re.compile(
    rf"\b({MONTH_PATTERN}{DATE_SEPARATOR_PATTERN}\d{{1,2}}{DATE_SEPARATOR_PATTERN}\d{{4}})\b",
    re.IGNORECASE,
)
EMBEDDED_DAY_FIRST_DATE_PATTERN = re.compile(
    rf"\b(\d{{1,2}}{DATE_SEPARATOR_PATTERN}{MONTH_PATTERN}{DATE_SEPARATOR_PATTERN}\d{{4}})\b",
    re.IGNORECASE,
)
AMOUNT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?:[-+]|\s?(?:CR|DR))?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
REFERENCE_PATTERN = re.compile(r"\b[A-Z]{2,}[A-Z0-9-]{4,}\b")
INCOMPLETE_EXTRACTION_PAGE_THRESHOLD = 20
INCOMPLETE_EXTRACTION_ROW_THRESHOLD = 100
logger = logging.getLogger(__name__)


@dataclass
class StatementLine:
    """One extracted PDF line with text and word coordinates."""

    text: str
    words: list[dict[str, Any]]


def _empty_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=REQUIRED_COLUMNS)


def _parse_amount(value: Any) -> float | None:
    """Parse a bank amount string into a float."""
    if value is None:
        return None

    cleaned_value = str(value).strip().replace(",", "")
    if not cleaned_value:
        return None

    is_trailing_negative = cleaned_value.endswith("-")
    cleaned_value = cleaned_value.rstrip("+-")
    cleaned_value = re.sub(r"(?:CR|DR)$", "", cleaned_value, flags=re.IGNORECASE).strip()

    try:
        amount = float(cleaned_value)
        return -amount if is_trailing_negative else amount
    except ValueError:
        return None


def _is_date_line(text: str) -> bool:
    """Return True when a PDF text line starts with an HSBC date."""
    stripped_text = text.strip()
    return bool(MONTH_FIRST_DATE_PATTERN.match(stripped_text) or DAY_FIRST_DATE_PATTERN.match(stripped_text))


def _clean_narration(value: str) -> str:
    """Normalize statement narration without removing useful references."""
    return re.sub(r"\s+", " ", value or "").strip()


def _extract_references(value: str) -> list[str]:
    """Extract likely payment/reference tokens for future diagnostics."""
    return REFERENCE_PATTERN.findall((value or "").upper())


def _is_ignored_line(text: str) -> bool:
    """Filter HSBC page headers, footers, and browser controls."""
    normalized = re.sub(r"\s+", " ", text or "").strip().lower()
    if not normalized:
        return True

    ignored_patterns = [
        r"^account number\b",
        r"^select transaction history criteria\b",
        r"^transaction history$",
        r"^date transaction narrative debit credit ledger balance value$",
        r"^dd history page\b",
        r"https?://",
        r"\bwww\.",
        r"^url\b",
        r"^display previous$",
        r"^display next$",
        r"^cancel$",
        r"^print form$",
        r"^display previous display next cancel print form$",
    ]
    return any(re.search(pattern, normalized) for pattern in ignored_patterns)


def _safe_seek_start(uploaded_file) -> None:
    try:
        uploaded_file.seek(0)
    except Exception:
        pass


def _line_sort_key(word: dict[str, Any]) -> tuple[float, float]:
    return float(word.get("top", 0)), float(word.get("x0", 0))


def _extract_lines_from_page(page) -> tuple[list[StatementLine], dict[str, float]]:
    """Extract ordered lines and known column x positions from one PDF page."""
    words = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False) or []
    if not words:
        text = page.extract_text() or ""
        return [StatementLine(line.strip(), []) for line in text.splitlines() if line.strip()], {}

    sorted_words = sorted(words, key=_line_sort_key)
    raw_lines: list[list[dict[str, Any]]] = []
    current_line: list[dict[str, Any]] = []
    current_top: float | None = None

    for word in sorted_words:
        word_top = float(word.get("top", 0))
        if current_top is None or abs(word_top - current_top) <= 3:
            current_line.append(word)
            current_top = word_top if current_top is None else current_top
            continue

        raw_lines.append(sorted(current_line, key=lambda item: float(item.get("x0", 0))))
        current_line = [word]
        current_top = word_top

    if current_line:
        raw_lines.append(sorted(current_line, key=lambda item: float(item.get("x0", 0))))

    lines = [
        StatementLine(" ".join(str(word.get("text", "")).strip() for word in line_words).strip(), line_words)
        for line_words in raw_lines
    ]
    return lines, _extract_column_positions(lines)


def _extract_column_positions(lines: list[StatementLine]) -> dict[str, float]:
    """Find Debit/Credit/Ledger column anchors from a page header if present."""
    positions: dict[str, float] = {}
    for line in lines:
        normalized = line.text.lower()
        if "transaction narrative" not in normalized or "debit" not in normalized or "credit" not in normalized:
            continue

        for word in line.words:
            word_text = str(word.get("text", "")).strip().lower()
            if word_text == "debit":
                positions["debit"] = float(word.get("x0", 0))
            elif word_text == "credit":
                positions["credit"] = float(word.get("x0", 0))
            elif word_text == "ledger":
                positions["balance"] = float(word.get("x0", 0))
        break

    return positions


def _amount_tokens(lines: list[StatementLine]) -> list[dict[str, Any]]:
    """Return amount tokens in visual reading order."""
    tokens: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        if line.words:
            for word in line.words:
                word_text = str(word.get("text", "")).strip()
                if AMOUNT_PATTERN.fullmatch(word_text):
                    tokens.append(
                        {
                            "value": word_text,
                            "line_index": line_index,
                            "x0": float(word.get("x0", 0)),
                        }
                    )
        else:
            for match in AMOUNT_PATTERN.finditer(line.text):
                tokens.append({"value": match.group(0), "line_index": line_index, "x0": None})

    return tokens


def _amount_tokens_for_line(line: StatementLine) -> list[dict[str, Any]]:
    """Return amount tokens from one line, preserving x position when available."""
    if line.words:
        tokens = []
        for word in line.words:
            word_text = str(word.get("text", "")).strip()
            if AMOUNT_PATTERN.fullmatch(word_text):
                tokens.append({"value": word_text, "x0": float(word.get("x0", 0))})
        return sorted(tokens, key=lambda token: float(token.get("x0", 0)))

    return [{"value": match.group(0), "x0": None} for match in AMOUNT_PATTERN.finditer(line.text)]


def _strip_date_prefix(text: str) -> tuple[str | None, str]:
    """Return a detected date and the rest of the line text."""
    stripped_text = text.strip()
    date_match = MONTH_FIRST_DATE_PATTERN.match(stripped_text)
    if date_match:
        return date_match.group(1).replace("-", " "), stripped_text[date_match.end() :].strip()

    date_match = DAY_FIRST_DATE_PATTERN.match(stripped_text)
    if date_match:
        parsed_date = pd.to_datetime(date_match.group(1).replace("-", " "), format="%d %b %Y", errors="coerce")
        if not pd.isna(parsed_date):
            return parsed_date.strftime("%b %d %Y"), stripped_text[date_match.end() :].strip()

    return None, stripped_text


def _extract_embedded_date(text: str) -> tuple[str | None, str]:
    """Return a transaction date embedded before the amount columns, plus text without it."""
    stripped_text = text.strip()
    amount_match = AMOUNT_PATTERN.search(stripped_text)
    search_end = amount_match.start() if amount_match else len(stripped_text)
    search_window = stripped_text[:search_end]

    date_match = EMBEDDED_MONTH_FIRST_DATE_PATTERN.search(search_window)
    if date_match:
        date_text = date_match.group(1).replace("-", " ")
        cleaned_text = (stripped_text[: date_match.start()] + stripped_text[date_match.end() :]).strip()
        return date_text, _clean_narration(cleaned_text)

    date_match = EMBEDDED_DAY_FIRST_DATE_PATTERN.search(search_window)
    if date_match:
        parsed_date = pd.to_datetime(date_match.group(1).replace("-", " "), format="%d %b %Y", errors="coerce")
        if not pd.isna(parsed_date):
            cleaned_text = (stripped_text[: date_match.start()] + stripped_text[date_match.end() :]).strip()
            return parsed_date.strftime("%b %d %Y"), _clean_narration(cleaned_text)

    return None, stripped_text


def _remove_trailing_amounts(text: str) -> str:
    """Remove trailing amount tokens from transaction text before storing narration."""
    cleaned_text = text
    while True:
        updated_text = AMOUNT_PATTERN.sub("", cleaned_text, count=1)
        if updated_text == cleaned_text:
            break
        cleaned_text = updated_text

    return _clean_narration(cleaned_text)


def _looks_like_credit(narration: str) -> bool:
    """Conservative text fallback for credit classification without coordinates."""
    normalized = _clean_narration(narration).lower()
    credit_keywords = [
        "credit",
        "cr ",
        "received",
        "receipt",
        "inward",
        "remittance",
        "refund",
    ]
    return any(keyword in f"{normalized} " for keyword in credit_keywords)


def _classify_transaction_amount(
    transaction_amount: float | None,
    transaction_token: dict[str, Any] | None,
    narration: str,
    column_positions: dict[str, float],
) -> tuple[float, float]:
    """Classify the transaction amount into debit or credit."""
    if transaction_amount is None:
        return 0.0, 0.0

    x0 = transaction_token.get("x0") if transaction_token else None
    debit_x = column_positions.get("debit")
    credit_x = column_positions.get("credit")
    balance_x = column_positions.get("balance")

    if x0 is not None and debit_x is not None and credit_x is not None:
        if balance_x is not None and x0 >= (credit_x + balance_x) / 2:
            return 0.0, 0.0
        if x0 < (debit_x + credit_x) / 2:
            return transaction_amount, 0.0
        return 0.0, transaction_amount

    if _looks_like_credit(narration):
        return 0.0, transaction_amount

    return transaction_amount, 0.0


def _classify_line_amounts(
    amount_tokens: list[dict[str, Any]],
    narration: str,
    column_positions: dict[str, float],
) -> dict[str, Any]:
    """Classify one visual line into debit, credit, and ledger balance amounts."""
    fields: dict[str, Any] = {
        "debit": 0.0,
        "credit": 0.0,
        "balance_amount": None,
        "has_transaction_amount": False,
    }
    if not amount_tokens:
        return fields

    debit_x = column_positions.get("debit")
    credit_x = column_positions.get("credit")
    balance_x = column_positions.get("balance")
    tokens_with_x = [token for token in amount_tokens if token.get("x0") is not None]

    if tokens_with_x and balance_x is not None:
        credit_balance_boundary = (credit_x + balance_x) / 2 if credit_x is not None else balance_x - 20
        debit_credit_boundary = (debit_x + credit_x) / 2 if debit_x is not None and credit_x is not None else None
        balance_tokens = [token for token in tokens_with_x if float(token["x0"]) >= credit_balance_boundary]
        balance_token = balance_tokens[-1] if balance_tokens else None
        if balance_token:
            fields["balance_amount"] = _parse_amount(balance_token["value"])

        transaction_tokens = [token for token in tokens_with_x if balance_token is None or token is not balance_token]
        if transaction_tokens:
            transaction_token = transaction_tokens[-1]
            transaction_amount = _parse_amount(transaction_token["value"])
            fields["has_transaction_amount"] = transaction_amount is not None
            transaction_x = float(transaction_token["x0"])
            if debit_credit_boundary is not None and transaction_x < debit_credit_boundary:
                fields["debit"] = transaction_amount or 0.0
            elif debit_credit_boundary is not None:
                fields["credit"] = transaction_amount or 0.0
            else:
                debit, credit = _classify_transaction_amount(transaction_amount, transaction_token, narration, column_positions)
                fields["debit"] = debit
                fields["credit"] = credit
        return fields

    fields["balance_amount"] = _parse_amount(amount_tokens[-1]["value"])
    if len(amount_tokens) >= 2:
        transaction_token = amount_tokens[-2]
        transaction_amount = _parse_amount(transaction_token["value"])
        fields["has_transaction_amount"] = transaction_amount is not None
        debit, credit = _classify_transaction_amount(transaction_amount, transaction_token, narration, column_positions)
        fields["debit"] = debit
        fields["credit"] = credit

    return fields


def _build_transaction_row(
    date_text: str,
    narration_parts: list[str],
    debit: float,
    credit: float,
    balance_amount: float | None,
    narration_override: str | None = None,
) -> dict[str, Any] | None:
    """Build one normalized transaction row."""
    parsed_date = pd.to_datetime(date_text, format="%b %d %Y", errors="coerce")
    if pd.isna(parsed_date):
        return None

    narration = narration_override or _clean_narration(" ".join(narration_parts))
    _extract_references(narration)
    return {
        "date": parsed_date.date(),
        "narration": narration,
        "debit": debit,
        "credit": credit,
        "balance_amount": balance_amount,
    }


def _build_debug_summary(pages_processed: int, transactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a compact parser diagnostic summary."""
    dates = [row.get("date") for row in transactions if row.get("date")]
    return {
        "pages_processed": pages_processed,
        "rows_extracted": len(transactions),
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
    }


def _debug_first_rows(dataframe: pd.DataFrame, row_count: int = 5) -> list[dict[str, Any]]:
    """Return a terminal-friendly preview of parsed bank rows."""
    if dataframe.empty:
        return []
    return dataframe.head(row_count).astype({"date": "string"}).to_dict(orient="records")


def _incomplete_extraction_warning(pages_processed: int, rows_extracted: int) -> str | None:
    """Return a non-blocking warning when a large PDF yields suspiciously few rows."""
    if pages_processed <= INCOMPLETE_EXTRACTION_PAGE_THRESHOLD or rows_extracted >= INCOMPLETE_EXTRACTION_ROW_THRESHOLD:
        return None

    return (
        "[Bank PDF Parser] Extraction may be incomplete: "
        f"{rows_extracted} rows from {pages_processed} pages."
    )


def _transactions_from_statement_lines(
    statement_lines: list[StatementLine],
    column_positions: dict[str, float],
) -> list[dict[str, Any]]:
    """Parse visual statement lines into transaction rows.

    HSBC PDFs can keep the date on the first line of a group while placing
    amount and ledger balance on a later line. The current date therefore stays
    active, while narration is scoped to the transaction being built.
    """
    transactions: list[dict[str, Any]] = []
    current_date: str | None = None
    narration_parts: list[str] = []
    pending_transaction_amount: dict[str, float] | None = None

    for line in statement_lines:
        detected_date, line_text_without_date = _strip_date_prefix(line.text)
        if detected_date:
            if current_date and detected_date != current_date and narration_parts:
                narration_parts = []
                pending_transaction_amount = None
            current_date = detected_date

        embedded_date, line_text_without_embedded_date = _extract_embedded_date(line_text_without_date)
        if embedded_date:
            if current_date != embedded_date and narration_parts:
                narration_parts = []
                pending_transaction_amount = None
            current_date = embedded_date
            line_text_without_date = line_text_without_embedded_date

        if not current_date:
            continue

        line_narration = _remove_trailing_amounts(line_text_without_date)
        amount_tokens = _amount_tokens_for_line(line)
        line_amounts = _classify_line_amounts(amount_tokens, line_narration, column_positions)

        if line_narration:
            narration_parts.append(line_narration)

        accumulated_narration = _clean_narration(" ".join(narration_parts))
        is_opening_balance = bool(re.search(r"\bbalance\s+b/?f\b", accumulated_narration, re.IGNORECASE))
        has_balance = line_amounts["balance_amount"] is not None
        has_transaction_amount = bool(line_amounts["has_transaction_amount"])

        if is_opening_balance and has_balance:
            row = _build_transaction_row(
                current_date,
                narration_parts,
                0.0,
                0.0,
                line_amounts["balance_amount"],
                narration_override="BALANCE B/F",
            )
            if row:
                transactions.append(row)
            narration_parts = []
            pending_transaction_amount = None
            continue

        if has_transaction_amount and has_balance:
            narration = _remove_trailing_amounts(accumulated_narration)
            row = _build_transaction_row(
                current_date,
                [narration],
                line_amounts["debit"],
                line_amounts["credit"],
                line_amounts["balance_amount"],
            )
            if row:
                transactions.append(row)
            narration_parts = []
            pending_transaction_amount = None
            continue

        if has_transaction_amount:
            pending_transaction_amount = {
                "debit": float(line_amounts["debit"] or 0.0),
                "credit": float(line_amounts["credit"] or 0.0),
            }
            continue

        if pending_transaction_amount and has_balance:
            narration = _remove_trailing_amounts(accumulated_narration)
            row = _build_transaction_row(
                current_date,
                [narration],
                pending_transaction_amount["debit"],
                pending_transaction_amount["credit"],
                line_amounts["balance_amount"],
            )
            if row:
                transactions.append(row)
            narration_parts = []
            pending_transaction_amount = None

    return transactions


def _parse_transaction(
    date_text: str,
    lines: list[StatementLine],
    column_positions: dict[str, float],
) -> dict[str, Any] | None:
    """Parse one grouped transaction into the standard upload schema."""
    if not lines:
        return None

    combined_text = _clean_narration(" ".join(line.text for line in lines))
    narration_text = MONTH_FIRST_DATE_PATTERN.sub("", combined_text, count=1).strip()
    narration_text = DAY_FIRST_DATE_PATTERN.sub("", narration_text, count=1).strip()
    amount_tokens = _amount_tokens(lines)

    balance_amount = _parse_amount(amount_tokens[-1]["value"]) if amount_tokens else None
    transaction_token = amount_tokens[-2] if len(amount_tokens) >= 2 else None
    transaction_amount = _parse_amount(transaction_token["value"]) if transaction_token else None

    narration = _remove_trailing_amounts(narration_text)
    _extract_references(narration)

    if re.search(r"\bbalance\s+b/?f\b", narration, re.IGNORECASE):
        return {
            "date": pd.to_datetime(date_text, format="%b %d %Y", errors="coerce").date(),
            "narration": "BALANCE B/F",
            "debit": 0.0,
            "credit": 0.0,
            "balance_amount": balance_amount,
        }

    debit, credit = _classify_transaction_amount(transaction_amount, transaction_token, narration, column_positions)
    return {
        "date": pd.to_datetime(date_text, format="%b %d %Y", errors="coerce").date(),
        "narration": narration,
        "debit": debit,
        "credit": credit,
        "balance_amount": balance_amount,
    }


def parse_bank_pdf(uploaded_file) -> pd.DataFrame:
    """Parse HSBC DD History PDF bank statements into normalized bank rows."""
    try:
        import pdfplumber
    except ImportError as error:
        raise RuntimeError("PDF parsing requires pdfplumber. Please install pdfplumber to upload bank PDFs.") from error

    _safe_seek_start(uploaded_file)
    statement_lines: list[StatementLine] = []
    column_positions: dict[str, float] = {}
    pages_processed = 0

    with pdfplumber.open(uploaded_file) as pdf:
        pages_processed = len(pdf.pages)
        for page in pdf.pages:
            page_lines, page_column_positions = _extract_lines_from_page(page)
            column_positions.update({key: value for key, value in page_column_positions.items() if key not in column_positions})
            statement_lines.extend(line for line in page_lines if not _is_ignored_line(line.text))

    transactions = _transactions_from_statement_lines(statement_lines, column_positions)

    if not transactions:
        print("[Bank PDF Parser] No transactions found in uploaded PDF.")
        parsed_df = _empty_dataframe()
        parsed_df.attrs["parser_debug_summary"] = _build_debug_summary(pages_processed, transactions)
        parsed_df.attrs["parser_first_rows"] = []
        warning_message = _incomplete_extraction_warning(pages_processed, 0)
        if warning_message:
            logger.warning(warning_message)
            print(warning_message)
            parsed_df.attrs["parser_warning"] = warning_message
        return parsed_df

    parsed_df = pd.DataFrame(transactions, columns=REQUIRED_COLUMNS)
    parsed_df["date"] = pd.to_datetime(parsed_df["date"], errors="coerce").dt.date
    for amount_column in ["debit", "credit", "balance_amount"]:
        parsed_df[amount_column] = pd.to_numeric(parsed_df[amount_column], errors="coerce").fillna(0.0)

    debug_summary = _build_debug_summary(pages_processed, transactions)
    parsed_df.attrs["parser_debug_summary"] = debug_summary
    parsed_df.attrs["parser_first_rows"] = _debug_first_rows(parsed_df)
    logger.info("[Bank PDF Parser] Summary: %s", debug_summary)
    print(f"[Bank PDF Parser] Summary: {debug_summary}")
    print(f"[Bank PDF Parser] Extracted rows: {len(parsed_df.index)}")
    print(f"[Bank PDF Parser] First parsed rows: {parsed_df.attrs['parser_first_rows']}")
    warning_message = _incomplete_extraction_warning(pages_processed, len(parsed_df.index))
    if warning_message:
        logger.warning(warning_message)
        print(warning_message)
        parsed_df.attrs["parser_warning"] = warning_message

    output_df = parsed_df.loc[:, REQUIRED_COLUMNS]
    output_df.attrs.update(parsed_df.attrs)
    return output_df
