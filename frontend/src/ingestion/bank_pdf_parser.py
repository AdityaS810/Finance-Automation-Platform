"""PDF parser for HSBC-style bank statement transaction history."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = ["date", "narration", "debit", "credit", "balance_amount"]
MONTH_PATTERN = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
DATE_PATTERN = re.compile(rf"^({MONTH_PATTERN}\s+\d{{1,2}}\s+\d{{4}})\b", re.IGNORECASE)
AMOUNT_PATTERN = re.compile(r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![A-Za-z0-9])")
REFERENCE_PATTERN = re.compile(r"\b[A-Z]{2,}[A-Z0-9-]{4,}\b")


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

    try:
        return float(cleaned_value)
    except ValueError:
        return None


def _is_date_line(text: str) -> bool:
    """Return True when a PDF text line starts with an HSBC date."""
    return bool(DATE_PATTERN.match(text.strip()))


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


def _parse_transaction(
    date_text: str,
    lines: list[StatementLine],
    column_positions: dict[str, float],
) -> dict[str, Any] | None:
    """Parse one grouped transaction into the standard upload schema."""
    if not lines:
        return None

    combined_text = _clean_narration(" ".join(line.text for line in lines))
    narration_text = DATE_PATTERN.sub("", combined_text, count=1).strip()
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

    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            page_lines, page_column_positions = _extract_lines_from_page(page)
            column_positions.update({key: value for key, value in page_column_positions.items() if key not in column_positions})
            statement_lines.extend(line for line in page_lines if not _is_ignored_line(line.text))

    transactions: list[dict[str, Any]] = []
    current_date: str | None = None
    current_lines: list[StatementLine] = []

    for line in statement_lines:
        if _is_date_line(line.text):
            if current_date and current_lines:
                parsed_transaction = _parse_transaction(current_date, current_lines, column_positions)
                if parsed_transaction:
                    transactions.append(parsed_transaction)

            current_date = DATE_PATTERN.match(line.text.strip()).group(1)
            current_lines = [line]
            continue

        if current_date:
            current_lines.append(line)

    if current_date and current_lines:
        parsed_transaction = _parse_transaction(current_date, current_lines, column_positions)
        if parsed_transaction:
            transactions.append(parsed_transaction)

    if not transactions:
        print("[Bank PDF Parser] No transactions found in uploaded PDF.")
        return _empty_dataframe()

    parsed_df = pd.DataFrame(transactions, columns=REQUIRED_COLUMNS)
    parsed_df["date"] = pd.to_datetime(parsed_df["date"], errors="coerce").dt.date
    for amount_column in ["debit", "credit", "balance_amount"]:
        parsed_df[amount_column] = pd.to_numeric(parsed_df[amount_column], errors="coerce").fillna(0.0)

    return parsed_df.loc[:, REQUIRED_COLUMNS]
