"""Tests for bank parser helpers."""

from __future__ import annotations

from io import BytesIO, StringIO

import pandas as pd

from src.ingestion.bank_csv_parser import parse_bank_csv, parse_bank_excel
from src.ingestion.bank_pdf_parser import (
    StatementLine,
    _transactions_from_statement_lines,
    get_bank_pdf_template,
    load_bank_pdf_templates,
    load_bank_pdf_templates_dataframe,
    save_bank_pdf_templates_dataframe,
    validate_bank_pdf_templates_dataframe,
)
from src.utils.validation import validate_required_columns


HSBC_COLUMNS = {"debit": 360.0, "credit": 445.0, "balance": 530.0}


def _hsbc_line(text: str, amount_positions: dict[str, float] | None = None) -> StatementLine:
    """Build a synthetic HSBC visual line with enough coordinates for parser tests."""
    amount_positions = amount_positions or {}
    words = []
    x0 = 40.0
    for word in text.split():
        clean_word = word.rstrip("+-")
        clean_word = clean_word[:-2] if clean_word.upper().endswith(("CR", "DR")) else clean_word
        words.append({"text": word, "x0": amount_positions.get(word, amount_positions.get(clean_word, x0)), "top": 10.0})
        x0 += 32.0
    return StatementLine(text=text, words=words)


def _hsbc_amount_positions(*, debit: str | None = None, credit: str | None = None, balance: str) -> dict[str, float]:
    positions = {balance: HSBC_COLUMNS["balance"]}
    if debit:
        positions[debit] = HSBC_COLUMNS["debit"]
    if credit:
        positions[credit] = HSBC_COLUMNS["credit"]
    return positions


def _parse_hsbc_lines(lines: list[StatementLine]) -> pd.DataFrame:
    return pd.DataFrame(
        _transactions_from_statement_lines(lines, HSBC_COLUMNS),
        columns=["date", "narration", "debit", "credit", "balance_amount"],
    )


def test_bank_pdf_templates_load_default_fallback_when_config_missing(tmp_path):
    missing_config = tmp_path / "missing_bank_pdf_templates.yaml"

    templates = load_bank_pdf_templates(path=missing_config)

    assert templates[0]["template_name"] == "HSBC Default"
    assert templates[0]["bank_name"] == "HSBC"
    assert templates[0]["active"] is True


def test_bank_pdf_missing_template_name_returns_active_default(tmp_path):
    config_path = tmp_path / "bank_pdf_templates.yaml"
    dataframe = load_bank_pdf_templates_dataframe(path=config_path)
    result = save_bank_pdf_templates_dataframe(dataframe, path=config_path)

    selected_template = get_bank_pdf_template("Template That Does Not Exist", path=config_path)

    assert result["status"] == "success"
    assert selected_template["template_name"] == "HSBC Default"


def test_bank_pdf_invalid_template_config_falls_back_to_default(tmp_path):
    config_path = tmp_path / "bank_pdf_templates.yaml"
    config_path.write_text("bank_pdf_templates: [", encoding="utf-8")

    templates = load_bank_pdf_templates(path=config_path)

    assert templates[0]["template_name"] == "HSBC Default"
    assert templates[0]["bank_name"] == "HSBC"


def test_bank_pdf_template_config_is_readable_from_yaml(tmp_path):
    config_path = tmp_path / "bank_pdf_templates.yaml"
    dataframe = pd.DataFrame(
        [
            {
                "Template Name": "Test Bank Template",
                "Bank Name": "Test Bank",
                "Statement Type": "Current Account",
                "Date Patterns": r"\d{4}-\d{2}-\d{2}",
                "Amount Pattern": r"\d+\.\d{2}",
                "Opening Balance Keywords": "opening balance",
                "Ignore Line Keywords": "page total",
                "Debit Column X Min": "100",
                "Debit Column X Max": "160",
                "Credit Column X Min": "200",
                "Credit Column X Max": "260",
                "Balance Column X Min": "300",
                "Balance Column X Max": "360",
                "Active/Inactive": "Active",
            }
        ]
    )

    result = save_bank_pdf_templates_dataframe(dataframe, path=config_path)
    templates = load_bank_pdf_templates(path=config_path)

    assert result["status"] == "success"
    assert templates[0]["template_name"] == "Test Bank Template"
    assert templates[0]["bank_name"] == "Test Bank"
    assert templates[0]["date_patterns"] == [r"\d{4}-\d{2}-\d{2}"]
    assert templates[0]["debit_column_x_min"] == 100.0


def test_bank_pdf_template_validation_rejects_blank_and_bad_numeric_rows():
    dataframe = pd.DataFrame(
        [
            {
                "Template Name": "",
                "Bank Name": "",
                "Statement Type": "",
                "Date Patterns": "",
                "Amount Pattern": "",
                "Opening Balance Keywords": "",
                "Ignore Line Keywords": "",
                "Debit Column X Min": "left",
                "Debit Column X Max": "",
                "Credit Column X Min": "",
                "Credit Column X Max": "",
                "Balance Column X Min": "",
                "Balance Column X Max": "",
                "Active/Inactive": "",
            }
        ]
    )

    errors = validate_bank_pdf_templates_dataframe(dataframe)

    assert "Template Name is required" in " ".join(errors)
    assert "Bank Name is required" in " ".join(errors)
    assert "Date Pattern is required" in " ".join(errors)
    assert "Amount Pattern is required" in " ".join(errors)
    assert "Debit Column X Min must be numeric" in " ".join(errors)


def test_parse_bank_csv_maps_common_columns():
    csv_content = StringIO(
        "Transaction Date,Description,Withdrawal,Deposit,Closing Balance\n"
        "2026-05-01,Opening balance,0,1000,1000\n"
    )

    df = parse_bank_csv(csv_content)

    assert {"date", "narration", "debit", "credit", "balance_amount", "balance"}.issubset(df.columns)


def test_validate_required_bank_columns_returns_valid():
    csv_content = StringIO(
        "Transaction Date,Description,Withdrawal,Deposit,Closing Balance\n"
        "2026-05-01,Opening balance,0,1000,1000\n"
    )

    df = parse_bank_csv(csv_content)
    result = validate_required_columns(df, ["date", "narration", "debit", "credit", "balance_amount"])

    assert result["is_valid"] is True


def test_parse_bank_csv_coalesces_duplicate_date_columns():
    csv_content = StringIO(
        "Transaction Date,Value Date,Description,Withdrawal,Deposit,Closing Balance,Reference Number\n"
        "2026-05-01,2026-05-02,Opening balance,0,1000,1000,REF-001\n"
    )

    df = parse_bank_csv(csv_content)

    assert list(df.columns).count("date") == 1
    assert df.loc[0, "date"] == "2026-05-01"


def test_parse_bank_csv_splits_signed_amount_column():
    csv_content = StringIO(
        "Txn Date,Transaction Details,Transaction Amount,Running Balance\n"
        "2026-05-01,Client receipt,12500,12500\n"
        "2026-05-02,Vendor payment,-4500,8000\n"
    )

    df = parse_bank_csv(csv_content)

    assert df.loc[0, "credit"] == 12500
    assert df.loc[0, "debit"] == 0
    assert df.loc[1, "debit"] == 4500
    assert df.loc[1, "credit"] == 0
    assert df.loc[1, "balance_amount"] == 8000


def test_parse_bank_excel_handles_title_rows_and_real_bank_labels():
    workbook_rows = [
        ["Statement for Account 1234", None, None, None, None],
        ["Generated by Bank", None, None, None, None],
        ["Value Date", "Particulars", "Paid Out", "Paid In", "Closing Balance"],
        ["01/05/2026", "Opening balance", "", "₹1,000.00", "₹1,000.00"],
        ["02/05/2026", "Office rent", "₹250.00", "", "₹750.00"],
    ]

    buffer = BytesIO()
    pd.DataFrame(workbook_rows).to_excel(buffer, index=False, header=False)
    buffer.seek(0)

    df = parse_bank_excel(buffer)

    assert {"date", "narration", "debit", "credit", "balance_amount"}.issubset(df.columns)
    assert df.loc[0, "narration"] == "Opening balance"
    assert df.loc[0, "credit"] == 1000.0
    assert df.loc[1, "debit"] == 250.0


def test_hsbc_pdf_parser_keeps_opening_balance_and_same_date_transactions_separate():
    lines = [
        _hsbc_line("Jan 27 2025 BALANCE B/F 901,879.35", _hsbc_amount_positions(balance="901,879.35")),
        _hsbc_line("Jan 28 2025 Balance Based Charges"),
        _hsbc_line("5,000.00 896,879.35", _hsbc_amount_positions(debit="5,000.00", balance="896,879.35")),
        _hsbc_line("CGST 450.00 896,429.35", _hsbc_amount_positions(debit="450.00", balance="896,429.35")),
        _hsbc_line("SGST 450.00 895,979.35", _hsbc_amount_positions(debit="450.00", balance="895,979.35")),
        _hsbc_line("Jan 29 2025 NEFT OUTWARD VENDOR A 10,000.00 885,979.35", _hsbc_amount_positions(debit="10,000.00", balance="885,979.35")),
        _hsbc_line("NEFT RETURN VENDOR A 10,000.00 895,979.35", _hsbc_amount_positions(credit="10,000.00", balance="895,979.35")),
        _hsbc_line("ATM CASH WITHDRAWAL 2,000.00 893,979.35", _hsbc_amount_positions(debit="2,000.00", balance="893,979.35")),
        _hsbc_line("Jan 31 2025 ONLINE PAYMENT OFFICE SUPPLIES 3,000.00 890,979.35", _hsbc_amount_positions(debit="3,000.00", balance="890,979.35")),
        _hsbc_line("Feb 05 2025 INTERNAL TRANSFER FROM HSBC 100,000.00 990,979.35", _hsbc_amount_positions(credit="100,000.00", balance="990,979.35")),
        _hsbc_line("Feb 10 2025 CREDIT INTEREST 79.35 991,058.70", _hsbc_amount_positions(credit="79.35", balance="991,058.70")),
        _hsbc_line("Feb 23 2025 INTERNAL TRANSFER FROM HSBC 200,000.00 1,191,058.70", _hsbc_amount_positions(credit="200,000.00", balance="1,191,058.70")),
        _hsbc_line("INTERNAL TRANSFER TO HSBC 200,000.00 991,058.70", _hsbc_amount_positions(debit="200,000.00", balance="991,058.70")),
        _hsbc_line("BANK CHARGES 100.00 990,958.70", _hsbc_amount_positions(debit="100.00", balance="990,958.70")),
    ]

    df = _parse_hsbc_lines(lines)

    assert len(df.index) == 13
    assert df.loc[0, "narration"] == "BALANCE B/F"
    assert df.loc[0, "debit"] == 0
    assert df.loc[0, "credit"] == 0
    assert df.loc[0, "balance_amount"] == 901879.35

    jan_28_rows = df[df["date"].astype(str) == "2025-01-28"]
    assert jan_28_rows["narration"].tolist() == ["Balance Based Charges", "CGST", "SGST"]
    assert jan_28_rows["debit"].tolist() == [5000.0, 450.0, 450.0]
    assert jan_28_rows["credit"].tolist() == [0.0, 0.0, 0.0]

    neft_return = df[df["narration"] == "NEFT RETURN VENDOR A"].iloc[0]
    assert neft_return["date"].isoformat() == "2025-01-29"
    assert neft_return["debit"] == 0
    assert neft_return["credit"] == 10000.0

    transfer_rows = df[df["narration"].str.contains("INTERNAL TRANSFER", regex=False)]
    assert transfer_rows["date"].astype(str).tolist() == ["2025-02-05", "2025-02-23", "2025-02-23"]
    assert transfer_rows["credit"].tolist() == [100000.0, 200000.0, 0.0]
    assert transfer_rows["debit"].tolist() == [0.0, 0.0, 200000.0]


def test_hsbc_pdf_parser_covers_midoffice_multiline_same_date_pattern():
    lines = [
        _hsbc_line("Sep 01 2024 BALANCE B/F 500,000.00", _hsbc_amount_positions(balance="500,000.00")),
        _hsbc_line("Sep 12 2024 SERVICE PAYMENT 2,430.00 497,570.00", _hsbc_amount_positions(debit="2,430.00", balance="497,570.00")),
        _hsbc_line("Nov 14 2024 VENDOR PAYMENT 27,540.00 470,030.00", _hsbc_amount_positions(debit="27,540.00", balance="470,030.00")),
        _hsbc_line("Nov 30 2024 INTERNAL FUNDING 1,370,000.00 1,840,030.00", _hsbc_amount_positions(credit="1,370,000.00", balance="1,840,030.00")),
        _hsbc_line("INTERNAL TRANSFER OUT 1,370,000.00 470,030.00", _hsbc_amount_positions(debit="1,370,000.00", balance="470,030.00")),
        _hsbc_line("NEFT RETURN CUSTOMER 1,370,000.00 1,840,030.00", _hsbc_amount_positions(credit="1,370,000.00", balance="1,840,030.00")),
        _hsbc_line("NEFT PAYMENT CUSTOMER 1,370,000.00 470,030.00", _hsbc_amount_positions(debit="1,370,000.00", balance="470,030.00")),
        _hsbc_line("Jan 28 2025 Balance Based Charges"),
        _hsbc_line("5,000.00 465,030.00", _hsbc_amount_positions(debit="5,000.00", balance="465,030.00")),
        _hsbc_line("CGST 450.00 464,580.00", _hsbc_amount_positions(debit="450.00", balance="464,580.00")),
        _hsbc_line("SGST 450.00 464,130.00", _hsbc_amount_positions(debit="450.00", balance="464,130.00")),
        _hsbc_line("Feb 05 2025 INTERNAL TRANSFER FROM HSBC 100,000.00 564,130.00", _hsbc_amount_positions(credit="100,000.00", balance="564,130.00")),
        _hsbc_line("Feb 23 2025 INTERNAL TRANSFER FROM HSBC 200,000.00 764,130.00", _hsbc_amount_positions(credit="200,000.00", balance="764,130.00")),
        _hsbc_line("INTERNAL TRANSFER TO HSBC 200,000.00 564,130.00", _hsbc_amount_positions(debit="200,000.00", balance="564,130.00")),
    ]

    df = _parse_hsbc_lines(lines)
    normal_rows = df[df["narration"] != "BALANCE B/F"]

    assert len(normal_rows.index) == 12
    assert len(df.index) == 13
    assert df.loc[0, "narration"] == "BALANCE B/F"
    assert df.loc[0, "debit"] == 0
    assert df.loc[0, "credit"] == 0

    expected = [
        ("2024-09-12", "SERVICE PAYMENT", 2430.0, 0.0),
        ("2024-11-14", "VENDOR PAYMENT", 27540.0, 0.0),
        ("2024-11-30", "INTERNAL FUNDING", 0.0, 1370000.0),
        ("2024-11-30", "INTERNAL TRANSFER OUT", 1370000.0, 0.0),
        ("2024-11-30", "NEFT RETURN CUSTOMER", 0.0, 1370000.0),
        ("2024-11-30", "NEFT PAYMENT CUSTOMER", 1370000.0, 0.0),
        ("2025-01-28", "Balance Based Charges", 5000.0, 0.0),
        ("2025-01-28", "CGST", 450.0, 0.0),
        ("2025-01-28", "SGST", 450.0, 0.0),
        ("2025-02-05", "INTERNAL TRANSFER FROM HSBC", 0.0, 100000.0),
        ("2025-02-23", "INTERNAL TRANSFER FROM HSBC", 0.0, 200000.0),
        ("2025-02-23", "INTERNAL TRANSFER TO HSBC", 200000.0, 0.0),
    ]
    actual = [
        (str(row.date), row.narration, row.debit, row.credit)
        for row in normal_rows.itertuples(index=False)
    ]
    assert actual == expected


def test_hsbc_pdf_parser_finalizes_amount_balance_line_with_inherited_date():
    lines = [
        _hsbc_line("Feb 23 2025 INTERNAL TRANSFER FROM HSBC 200,000.00 1,191,058.70", _hsbc_amount_positions(credit="200,000.00", balance="1,191,058.70")),
        _hsbc_line("INTERNAL TRANSFER TO HSBC 200,000.00 991,058.70", _hsbc_amount_positions(debit="200,000.00", balance="991,058.70")),
    ]

    df = _parse_hsbc_lines(lines)

    assert len(df.index) == 2
    assert df["date"].astype(str).tolist() == ["2025-02-23", "2025-02-23"]
    assert df["narration"].tolist() == ["INTERNAL TRANSFER FROM HSBC", "INTERNAL TRANSFER TO HSBC"]
    assert df["credit"].tolist() == [200000.0, 0.0]
    assert df["debit"].tolist() == [0.0, 200000.0]


def test_hsbc_pdf_parser_finalizes_when_amount_and_balance_are_split_across_lines():
    lines = [
        _hsbc_line("Feb 24 2025 CARD SETTLEMENT"),
        _hsbc_line("1,500.00", {"1,500.00": HSBC_COLUMNS["debit"]}),
        _hsbc_line("989,458.70", {"989,458.70": HSBC_COLUMNS["balance"]}),
        _hsbc_line("NEFT RETURN CUSTOMER"),
        _hsbc_line("2,500.00", {"2,500.00": HSBC_COLUMNS["credit"]}),
        _hsbc_line("991,958.70", {"991,958.70": HSBC_COLUMNS["balance"]}),
    ]

    df = _parse_hsbc_lines(lines)

    assert len(df.index) == 2
    assert df["date"].astype(str).tolist() == ["2025-02-24", "2025-02-24"]
    assert df["narration"].tolist() == ["CARD SETTLEMENT", "NEFT RETURN CUSTOMER"]
    assert df["debit"].tolist() == [1500.0, 0.0]
    assert df["credit"].tolist() == [0.0, 2500.0]
    assert df["balance_amount"].tolist() == [989458.70, 991958.70]


def test_hsbc_pdf_parser_accepts_day_first_dates_and_dr_cr_amount_suffixes():
    lines = [
        _hsbc_line("01 Sep 2024 BALANCE B/F 500,000.00", _hsbc_amount_positions(balance="500,000.00")),
        _hsbc_line("12 Sep 2024 SERVICE PAYMENT 2,430.00DR 497,570.00", _hsbc_amount_positions(debit="2,430.00", balance="497,570.00")),
        _hsbc_line("30 Nov 2024 NEFT RETURN CUSTOMER 1,370,000.00CR 1,867,570.00", _hsbc_amount_positions(credit="1,370,000.00", balance="1,867,570.00")),
        _hsbc_line("INTERNAL TRANSFER TO HSBC 1,370,000.00DR 497,570.00", _hsbc_amount_positions(debit="1,370,000.00", balance="497,570.00")),
    ]

    df = _parse_hsbc_lines(lines)

    assert df["date"].astype(str).tolist() == ["2024-09-01", "2024-09-12", "2024-11-30", "2024-11-30"]
    assert df["narration"].tolist() == [
        "BALANCE B/F",
        "SERVICE PAYMENT",
        "NEFT RETURN CUSTOMER",
        "INTERNAL TRANSFER TO HSBC",
    ]
    assert df["debit"].tolist() == [0.0, 2430.0, 0.0, 1370000.0]
    assert df["credit"].tolist() == [0.0, 0.0, 1370000.0, 0.0]


def test_hsbc_pdf_parser_embedded_dates_override_previous_current_date():
    lines = [
        _hsbc_line("Jan 28 2026 Balance Based Chgs"),
        _hsbc_line("5,000.00 495,000.00", _hsbc_amount_positions(debit="5,000.00", balance="495,000.00")),
        _hsbc_line(
            "HEXAGON INTERNALTRANS Feb 05 2026 100,000.00 595,000.00",
            _hsbc_amount_positions(credit="100,000.00", balance="595,000.00"),
        ),
        _hsbc_line(
            "DROPADIPAY Feb 23 2026 200,000.00 795,000.00",
            _hsbc_amount_positions(credit="200,000.00", balance="795,000.00"),
        ),
        _hsbc_line(
            "DROPADIGOYAL Feb 23 2026 200,000.00 595,000.00",
            _hsbc_amount_positions(debit="200,000.00", balance="595,000.00"),
        ),
    ]

    df = _parse_hsbc_lines(lines)

    assert df["date"].astype(str).tolist() == ["2026-01-28", "2026-02-05", "2026-02-23", "2026-02-23"]
    assert df["narration"].tolist() == ["Balance Based Chgs", "HEXAGON INTERNALTRANS", "DROPADIPAY", "DROPADIGOYAL"]
    assert df["credit"].tolist() == [0.0, 100000.0, 200000.0, 0.0]
    assert df["debit"].tolist() == [5000.0, 0.0, 0.0, 200000.0]


def test_hsbc_pdf_parser_allows_embedded_date_to_start_statement_rows():
    lines = [
        _hsbc_line(
            "HSBC TRANSACTION NARRATIVE Sep 12 2025 SERVICE PAYMENT 2,430.00 497,570.00",
            _hsbc_amount_positions(debit="2,430.00", balance="497,570.00"),
        ),
        _hsbc_line(
            "HSBC TRANSACTION NARRATIVE Nov 30 2025 INTERNAL FUNDING 1,370,000.00 1,840,030.00",
            _hsbc_amount_positions(credit="1,370,000.00", balance="1,840,030.00"),
        ),
    ]

    df = _parse_hsbc_lines(lines)

    assert df["date"].astype(str).tolist() == ["2025-09-12", "2025-11-30"]
    assert df["debit"].tolist() == [2430.0, 0.0]
    assert df["credit"].tolist() == [0.0, 1370000.0]


def test_hsbc_pdf_parser_covers_extracted_midoffice_layout_with_long_embedded_dates():
    long_internal_prefix = "HEXAGON INTERNALTRANS CUSTOMER REFERENCE BENEFICIARY ACCOUNT NARRATIVE"
    long_dropadi_prefix = "DROPADI PAYMENT GATEWAY INTERNAL TRANSFER CUSTOMER REFERENCE NARRATIVE"
    lines = [
        _hsbc_line("Sep 12 2025 SERVICE PAYMENT 2,430.00 497,570.00", _hsbc_amount_positions(debit="2,430.00", balance="497,570.00")),
        _hsbc_line("Nov 14 2025 VENDOR PAYMENT 27,540.00 470,030.00", _hsbc_amount_positions(debit="27,540.00", balance="470,030.00")),
        _hsbc_line("Nov 30 2025 INTERNAL FUNDING 1,370,000.00 1,840,030.00", _hsbc_amount_positions(credit="1,370,000.00", balance="1,840,030.00")),
        _hsbc_line("INTERNAL TRANSFER OUT 1,370,000.00 470,030.00", _hsbc_amount_positions(debit="1,370,000.00", balance="470,030.00")),
        _hsbc_line("NEFT RETURN CUSTOMER 1,370,000.00 1,840,030.00", _hsbc_amount_positions(credit="1,370,000.00", balance="1,840,030.00")),
        _hsbc_line("NEFT PAYMENT CUSTOMER 1,370,000.00 470,030.00", _hsbc_amount_positions(debit="1,370,000.00", balance="470,030.00")),
        _hsbc_line("Jan 28 2026 Balance Based Charges"),
        _hsbc_line("5,000.00 465,030.00", _hsbc_amount_positions(debit="5,000.00", balance="465,030.00")),
        _hsbc_line("CGST 450.00 464,580.00", _hsbc_amount_positions(debit="450.00", balance="464,580.00")),
        _hsbc_line("SGST 450.00 464,130.00", _hsbc_amount_positions(debit="450.00", balance="464,130.00")),
        _hsbc_line(
            f"{long_internal_prefix} Feb 05 2026 100,000.00 564,130.00",
            _hsbc_amount_positions(credit="100,000.00", balance="564,130.00"),
        ),
        _hsbc_line(
            f"{long_dropadi_prefix} Feb 23 2026 200,000.00 764,130.00",
            _hsbc_amount_positions(credit="200,000.00", balance="764,130.00"),
        ),
        _hsbc_line(
            f"{long_dropadi_prefix} Feb 23 2026 200,000.00 564,130.00",
            _hsbc_amount_positions(debit="200,000.00", balance="564,130.00"),
        ),
    ]

    df = _parse_hsbc_lines(lines)

    assert len(df.index) == 12
    assert df["date"].astype(str).tolist() == [
        "2025-09-12",
        "2025-11-14",
        "2025-11-30",
        "2025-11-30",
        "2025-11-30",
        "2025-11-30",
        "2026-01-28",
        "2026-01-28",
        "2026-01-28",
        "2026-02-05",
        "2026-02-23",
        "2026-02-23",
    ]
    assert df["debit"].tolist() == [
        2430.0,
        27540.0,
        0.0,
        1370000.0,
        0.0,
        1370000.0,
        5000.0,
        450.0,
        450.0,
        0.0,
        0.0,
        200000.0,
    ]
    assert df["credit"].tolist() == [
        0.0,
        0.0,
        1370000.0,
        0.0,
        1370000.0,
        0.0,
        0.0,
        0.0,
        0.0,
        100000.0,
        200000.0,
        0.0,
    ]
