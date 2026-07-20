"""PDF parser for HSBC-style bank statement transaction history."""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


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

DEFAULT_BANK_PDF_TEMPLATE = {
    "template_name": "HSBC Default",
    "bank_name": "HSBC",
    "statement_type": "DD History",
    "date_patterns": [
        rf"{MONTH_PATTERN}{DATE_SEPARATOR_PATTERN}\d{{1,2}}{DATE_SEPARATOR_PATTERN}\d{{4}}",
        rf"\d{{1,2}}{DATE_SEPARATOR_PATTERN}{MONTH_PATTERN}{DATE_SEPARATOR_PATTERN}\d{{4}}",
    ],
    "amount_pattern": r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?:[-+]|\s?(?:CR|DR))?(?![A-Za-z0-9])",
    "opening_balance_keywords": ["balance b/f", "balance bf"],
    "ignore_line_keywords": [
        "^account number\\b",
        "^select transaction history criteria\\b",
        "^transaction history$",
        "^date transaction narrative debit credit ledger balance value$",
        "^dd history page\\b",
        "https?://",
        "\\bwww\\.",
        "^url\\b",
        "^display previous$",
        "^display next$",
        "^cancel$",
        "^print form$",
        "^display previous display next cancel print form$",
    ],
    "debit_column_x_min": 330.0,
    "debit_column_x_max": 410.0,
    "credit_column_x_min": 415.0,
    "credit_column_x_max": 500.0,
    "balance_column_x_min": 505.0,
    "balance_column_x_max": 590.0,
    "active": True,
}

TEMPLATE_COLUMNS = [
    "Template Name",
    "Bank Name",
    "Statement Type",
    "Date Patterns",
    "Amount Pattern",
    "Opening Balance Keywords",
    "Ignore Line Keywords",
    "Debit Column X Min",
    "Debit Column X Max",
    "Credit Column X Min",
    "Credit Column X Max",
    "Balance Column X Min",
    "Balance Column X Max",
    "Active/Inactive",
]


@dataclass
class StatementLine:
    """One extracted PDF line with text and word coordinates."""

    text: str
    words: list[dict[str, Any]]


@dataclass
class BankPdfTemplateRules:
    """Compiled parsing rules from one bank PDF template."""

    template_name: str
    bank_name: str
    statement_type: str
    date_patterns: list[re.Pattern]
    amount_pattern: re.Pattern
    opening_balance_keywords: list[str]
    ignore_line_keywords: list[str]
    column_positions: dict[str, float]


def _empty_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=REQUIRED_COLUMNS)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def bank_pdf_templates_path() -> Path:
    return _repo_root() / "config" / "bank_pdf_templates.yaml"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _split_config_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[\n,]+", text) if item.strip()]


def _list_to_text(value: Any) -> str:
    return "\n".join(_split_config_list(value))


def _to_float_or_none(value: Any) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_active(value: Any) -> bool:
    return str(value or "").strip().lower() in {"active", "true", "yes", "1"}


def _status_label(value: Any) -> str:
    return "Active" if bool(value) else "Inactive"


def _default_template_copy() -> dict[str, Any]:
    return {
        key: list(value) if isinstance(value, list) else value
        for key, value in DEFAULT_BANK_PDF_TEMPLATE.items()
    }


def _read_template_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or bank_pdf_templates_path()
    if not config_path.exists():
        return {"bank_pdf_templates": [_default_template_copy()]}
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream) or {}
    except Exception:
        logger.warning("Bank PDF template config could not be read; falling back to HSBC defaults.")
        return {"bank_pdf_templates": [_default_template_copy()]}

    templates = config.get("bank_pdf_templates")
    if not isinstance(templates, list) or not templates:
        return {"bank_pdf_templates": [_default_template_copy()]}
    return {"bank_pdf_templates": templates}


def _write_template_config(config: dict[str, Any], path: Path | None = None) -> None:
    config_path = path or bank_pdf_templates_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream, sort_keys=False, allow_unicode=False)


def _normalise_template(template: dict[str, Any]) -> dict[str, Any]:
    normalised = _default_template_copy()
    normalised.update(template or {})
    normalised["date_patterns"] = _split_config_list(normalised.get("date_patterns")) or list(DEFAULT_BANK_PDF_TEMPLATE["date_patterns"])
    normalised["opening_balance_keywords"] = (
        _split_config_list(normalised.get("opening_balance_keywords"))
        or list(DEFAULT_BANK_PDF_TEMPLATE["opening_balance_keywords"])
    )
    normalised["ignore_line_keywords"] = (
        _split_config_list(normalised.get("ignore_line_keywords"))
        or list(DEFAULT_BANK_PDF_TEMPLATE["ignore_line_keywords"])
    )
    normalised["active"] = bool(normalised.get("active"))
    return normalised


def load_bank_pdf_templates(path: Path | None = None) -> list[dict[str, Any]]:
    """Load bank PDF templates, falling back to the built-in HSBC template."""
    config = _read_template_config(path)
    templates = [_normalise_template(template) for template in config.get("bank_pdf_templates", []) if isinstance(template, dict)]
    return templates or [_default_template_copy()]


def active_bank_pdf_templates(path: Path | None = None) -> list[dict[str, Any]]:
    """Return active templates, falling back to active HSBC defaults."""
    templates = [template for template in load_bank_pdf_templates(path) if template.get("active")]
    return templates or [_default_template_copy()]


def load_bank_pdf_templates_dataframe(path: Path | None = None) -> pd.DataFrame:
    """Return bank PDF templates as an editable dataframe."""
    rows = []
    for template in load_bank_pdf_templates(path):
        rows.append(
            {
                "Template Name": _clean_text(template.get("template_name")),
                "Bank Name": _clean_text(template.get("bank_name")),
                "Statement Type": _clean_text(template.get("statement_type")),
                "Date Patterns": _list_to_text(template.get("date_patterns")),
                "Amount Pattern": _clean_text(template.get("amount_pattern")),
                "Opening Balance Keywords": _list_to_text(template.get("opening_balance_keywords")),
                "Ignore Line Keywords": _list_to_text(template.get("ignore_line_keywords")),
                "Debit Column X Min": template.get("debit_column_x_min"),
                "Debit Column X Max": template.get("debit_column_x_max"),
                "Credit Column X Min": template.get("credit_column_x_min"),
                "Credit Column X Max": template.get("credit_column_x_max"),
                "Balance Column X Min": template.get("balance_column_x_min"),
                "Balance Column X Max": template.get("balance_column_x_max"),
                "Active/Inactive": _status_label(template.get("active")),
            }
        )
    return pd.DataFrame(rows, columns=TEMPLATE_COLUMNS)


def validate_bank_pdf_templates_dataframe(dataframe: pd.DataFrame) -> list[str]:
    """Validate edited bank PDF template rows for user-friendly feedback."""
    errors = []
    if dataframe.empty:
        return ["At least one bank PDF template is required."]

    for index, row in dataframe.fillna("").iterrows():
        row_number = index + 1
        if not _clean_text(row.get("Template Name")):
            errors.append(f"Row {row_number}: Template Name is required.")
        if not _clean_text(row.get("Bank Name")):
            errors.append(f"Row {row_number}: Bank Name is required.")
        date_patterns = _split_config_list(row.get("Date Patterns"))
        if not date_patterns:
            errors.append(f"Row {row_number}: Date Pattern is required.")
        for pattern in date_patterns:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error:
                errors.append(f"Row {row_number}: Date Pattern is not a valid regular expression.")
                break
        amount_pattern = _clean_text(row.get("Amount Pattern"))
        if not amount_pattern:
            errors.append(f"Row {row_number}: Amount Pattern is required.")
        else:
            try:
                re.compile(amount_pattern, re.IGNORECASE)
            except re.error:
                errors.append(f"Row {row_number}: Amount Pattern is not a valid regular expression.")

        for column_name in [
            "Debit Column X Min",
            "Debit Column X Max",
            "Credit Column X Min",
            "Credit Column X Max",
            "Balance Column X Min",
            "Balance Column X Max",
        ]:
            value = _clean_text(row.get(column_name))
            if value and _to_float_or_none(value) is None:
                errors.append(f"Row {row_number}: {column_name} must be numeric when provided.")

    return errors


def save_bank_pdf_templates_dataframe(dataframe: pd.DataFrame, path: Path | None = None) -> dict[str, Any]:
    """Persist edited bank PDF templates to YAML."""
    errors = validate_bank_pdf_templates_dataframe(dataframe)
    if errors:
        return {"status": "error", "errors": errors, "saved_count": 0}

    templates = []
    for _, row in dataframe.fillna("").iterrows():
        if not any(_clean_text(row.get(column)) for column in TEMPLATE_COLUMNS):
            continue
        templates.append(
            {
                "template_name": _clean_text(row.get("Template Name")),
                "bank_name": _clean_text(row.get("Bank Name")),
                "statement_type": _clean_text(row.get("Statement Type")),
                "date_patterns": _split_config_list(row.get("Date Patterns")),
                "amount_pattern": _clean_text(row.get("Amount Pattern")),
                "opening_balance_keywords": _split_config_list(row.get("Opening Balance Keywords")),
                "ignore_line_keywords": _split_config_list(row.get("Ignore Line Keywords")),
                "debit_column_x_min": _to_float_or_none(row.get("Debit Column X Min")),
                "debit_column_x_max": _to_float_or_none(row.get("Debit Column X Max")),
                "credit_column_x_min": _to_float_or_none(row.get("Credit Column X Min")),
                "credit_column_x_max": _to_float_or_none(row.get("Credit Column X Max")),
                "balance_column_x_min": _to_float_or_none(row.get("Balance Column X Min")),
                "balance_column_x_max": _to_float_or_none(row.get("Balance Column X Max")),
                "active": _is_active(row.get("Active/Inactive")),
            }
        )

    _write_template_config(
        {
            "storage_note": "Fallback local config for future migration to BigQuery.",
            "bank_pdf_templates": templates,
        },
        path,
    )
    return {"status": "success", "errors": [], "saved_count": len(templates)}


def _column_midpoint(template: dict[str, Any], min_key: str, max_key: str) -> float | None:
    min_value = _to_float_or_none(template.get(min_key))
    max_value = _to_float_or_none(template.get(max_key))
    if min_value is not None and max_value is not None:
        return (min_value + max_value) / 2
    return min_value if min_value is not None else max_value


def _compile_template_rules(template: dict[str, Any] | None = None) -> BankPdfTemplateRules:
    cleaned_template = _normalise_template(template or _default_template_copy())
    compiled_date_patterns = []
    for pattern in cleaned_template.get("date_patterns", []):
        try:
            compiled_date_patterns.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            logger.warning("Ignoring invalid bank PDF date pattern in template %s.", cleaned_template.get("template_name"))
    if not compiled_date_patterns:
        compiled_date_patterns = [MONTH_FIRST_DATE_PATTERN, DAY_FIRST_DATE_PATTERN]

    try:
        amount_pattern = re.compile(_clean_text(cleaned_template.get("amount_pattern")), re.IGNORECASE)
    except re.error:
        amount_pattern = AMOUNT_PATTERN

    column_positions = {}
    debit_midpoint = _column_midpoint(cleaned_template, "debit_column_x_min", "debit_column_x_max")
    credit_midpoint = _column_midpoint(cleaned_template, "credit_column_x_min", "credit_column_x_max")
    balance_midpoint = _column_midpoint(cleaned_template, "balance_column_x_min", "balance_column_x_max")
    if debit_midpoint is not None:
        column_positions["debit"] = debit_midpoint
    if credit_midpoint is not None:
        column_positions["credit"] = credit_midpoint
    if balance_midpoint is not None:
        column_positions["balance"] = balance_midpoint

    return BankPdfTemplateRules(
        template_name=_clean_text(cleaned_template.get("template_name")) or "HSBC Default",
        bank_name=_clean_text(cleaned_template.get("bank_name")) or "HSBC",
        statement_type=_clean_text(cleaned_template.get("statement_type")) or "DD History",
        date_patterns=compiled_date_patterns,
        amount_pattern=amount_pattern,
        opening_balance_keywords=[keyword.lower() for keyword in _split_config_list(cleaned_template.get("opening_balance_keywords"))],
        ignore_line_keywords=_split_config_list(cleaned_template.get("ignore_line_keywords")),
        column_positions=column_positions,
    )


def get_bank_pdf_template(template_name: str | None = None, path: Path | None = None) -> dict[str, Any]:
    """Return a named active template, or the first active/default template."""
    templates = active_bank_pdf_templates(path)
    if template_name:
        requested = _clean_text(template_name).lower()
        for template in templates:
            if _clean_text(template.get("template_name")).lower() == requested:
                return template
    return templates[0] if templates else _default_template_copy()


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


def _is_date_line(text: str, rules: BankPdfTemplateRules | None = None) -> bool:
    """Return True when a PDF text line starts with an HSBC date."""
    stripped_text = text.strip()
    if rules:
        return any(pattern.match(stripped_text) for pattern in rules.date_patterns)
    return bool(MONTH_FIRST_DATE_PATTERN.match(stripped_text) or DAY_FIRST_DATE_PATTERN.match(stripped_text))


def _clean_narration(value: str) -> str:
    """Normalize statement narration without removing useful references."""
    return re.sub(r"\s+", " ", value or "").strip()


def _extract_references(value: str) -> list[str]:
    """Extract likely payment/reference tokens for future diagnostics."""
    return REFERENCE_PATTERN.findall((value or "").upper())


def _is_ignored_line(text: str, rules: BankPdfTemplateRules | None = None) -> bool:
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
    configured_patterns = rules.ignore_line_keywords if rules else []
    return any(re.search(pattern, normalized) for pattern in [*ignored_patterns, *configured_patterns])


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


def _amount_tokens(lines: list[StatementLine], amount_pattern: re.Pattern = AMOUNT_PATTERN) -> list[dict[str, Any]]:
    """Return amount tokens in visual reading order."""
    tokens: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        if line.words:
            for word in line.words:
                word_text = str(word.get("text", "")).strip()
                if amount_pattern.fullmatch(word_text):
                    tokens.append(
                        {
                            "value": word_text,
                            "line_index": line_index,
                            "x0": float(word.get("x0", 0)),
                        }
                    )
        else:
            for match in amount_pattern.finditer(line.text):
                tokens.append({"value": match.group(0), "line_index": line_index, "x0": None})

    return tokens


def _amount_tokens_for_line(line: StatementLine, amount_pattern: re.Pattern = AMOUNT_PATTERN) -> list[dict[str, Any]]:
    """Return amount tokens from one line, preserving x position when available."""
    if line.words:
        tokens = []
        for word in line.words:
            word_text = str(word.get("text", "")).strip()
            if amount_pattern.fullmatch(word_text):
                tokens.append({"value": word_text, "x0": float(word.get("x0", 0))})
        return sorted(tokens, key=lambda token: float(token.get("x0", 0)))

    return [{"value": match.group(0), "x0": None} for match in amount_pattern.finditer(line.text)]


def _strip_date_prefix(text: str, rules: BankPdfTemplateRules | None = None) -> tuple[str | None, str]:
    """Return a detected date and the rest of the line text."""
    stripped_text = text.strip()
    if rules:
        for pattern in rules.date_patterns:
            date_match = pattern.match(stripped_text)
            if not date_match:
                continue
            date_text = date_match.group(1) if date_match.groups() else date_match.group(0)
            parsed_date = pd.to_datetime(date_text.replace("-", " "), errors="coerce")
            if not pd.isna(parsed_date):
                return parsed_date.strftime("%b %d %Y"), stripped_text[date_match.end() :].strip()

    date_match = MONTH_FIRST_DATE_PATTERN.match(stripped_text)
    if date_match:
        return date_match.group(1).replace("-", " "), stripped_text[date_match.end() :].strip()

    date_match = DAY_FIRST_DATE_PATTERN.match(stripped_text)
    if date_match:
        parsed_date = pd.to_datetime(date_match.group(1).replace("-", " "), format="%d %b %Y", errors="coerce")
        if not pd.isna(parsed_date):
            return parsed_date.strftime("%b %d %Y"), stripped_text[date_match.end() :].strip()

    return None, stripped_text


def _extract_embedded_date(text: str, rules: BankPdfTemplateRules | None = None) -> tuple[str | None, str]:
    """Return a transaction date embedded before the amount columns, plus text without it."""
    stripped_text = text.strip()
    amount_pattern = rules.amount_pattern if rules else AMOUNT_PATTERN
    amount_match = amount_pattern.search(stripped_text)
    search_end = amount_match.start() if amount_match else len(stripped_text)
    search_window = stripped_text[:search_end]

    if rules:
        for pattern in rules.date_patterns:
            date_match = pattern.search(search_window)
            if not date_match:
                continue
            date_text = date_match.group(1) if date_match.groups() else date_match.group(0)
            parsed_date = pd.to_datetime(date_text.replace("-", " "), errors="coerce")
            if not pd.isna(parsed_date):
                cleaned_text = (stripped_text[: date_match.start()] + stripped_text[date_match.end() :]).strip()
                return parsed_date.strftime("%b %d %Y"), _clean_narration(cleaned_text)

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


def _remove_trailing_amounts(text: str, amount_pattern: re.Pattern = AMOUNT_PATTERN) -> str:
    """Remove trailing amount tokens from transaction text before storing narration."""
    cleaned_text = text
    while True:
        updated_text = amount_pattern.sub("", cleaned_text, count=1)
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
    rules: BankPdfTemplateRules | None = None,
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
    amount_pattern = rules.amount_pattern if rules else AMOUNT_PATTERN
    opening_keywords = rules.opening_balance_keywords if rules else ["balance b/f", "balance bf"]

    for line in statement_lines:
        detected_date, line_text_without_date = _strip_date_prefix(line.text, rules)
        if detected_date:
            if current_date and detected_date != current_date and narration_parts:
                narration_parts = []
                pending_transaction_amount = None
            current_date = detected_date

        embedded_date, line_text_without_embedded_date = _extract_embedded_date(line_text_without_date, rules)
        if embedded_date:
            if current_date != embedded_date and narration_parts:
                narration_parts = []
                pending_transaction_amount = None
            current_date = embedded_date
            line_text_without_date = line_text_without_embedded_date

        if not current_date:
            continue

        line_narration = _remove_trailing_amounts(line_text_without_date, amount_pattern)
        amount_tokens = _amount_tokens_for_line(line, amount_pattern)
        line_amounts = _classify_line_amounts(amount_tokens, line_narration, column_positions)

        if line_narration:
            narration_parts.append(line_narration)

        accumulated_narration = _clean_narration(" ".join(narration_parts))
        normalized_narration = accumulated_narration.lower()
        is_opening_balance = bool(re.search(r"\bbalance\s+b/?f\b", accumulated_narration, re.IGNORECASE)) or any(
            keyword in normalized_narration for keyword in opening_keywords
        )
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
            narration = _remove_trailing_amounts(accumulated_narration, amount_pattern)
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
            narration = _remove_trailing_amounts(accumulated_narration, amount_pattern)
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


def parse_bank_pdf(uploaded_file, template_name: str | None = None, template_config: dict[str, Any] | None = None) -> pd.DataFrame:
    """Parse bank PDF statements into normalized bank rows using an active template."""
    try:
        import pdfplumber
    except ImportError as error:
        raise RuntimeError("PDF parsing requires pdfplumber. Please install pdfplumber to upload bank PDFs.") from error

    selected_template = template_config or get_bank_pdf_template(template_name)
    rules = _compile_template_rules(selected_template)
    _safe_seek_start(uploaded_file)
    statement_lines: list[StatementLine] = []
    column_positions: dict[str, float] = dict(rules.column_positions)
    pages_processed = 0

    with pdfplumber.open(uploaded_file) as pdf:
        pages_processed = len(pdf.pages)
        for page in pdf.pages:
            page_lines, page_column_positions = _extract_lines_from_page(page)
            column_positions.update({key: value for key, value in page_column_positions.items() if key not in column_positions})
            statement_lines.extend(line for line in page_lines if not _is_ignored_line(line.text, rules))

    transactions = _transactions_from_statement_lines(statement_lines, column_positions, rules)

    if not transactions:
        print("[Bank PDF Parser] No transactions found in uploaded PDF.")
        parsed_df = _empty_dataframe()
        parsed_df.attrs["parser_debug_summary"] = _build_debug_summary(pages_processed, transactions)
        parsed_df.attrs["parser_template"] = rules.template_name
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
    parsed_df.attrs["parser_template"] = rules.template_name
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
