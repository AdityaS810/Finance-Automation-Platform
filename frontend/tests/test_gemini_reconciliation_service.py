"""Tests for safe, advisory Gemini reconciliation suggestions."""

from __future__ import annotations

import json

from backend.services import gemini_reconciliation_service as service


def test_missing_api_key_returns_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    result = service.suggest_reconciliation_match(
        {"bank_line_id": "bank-1"},
        [{"accounting_record_id": "books-1"}],
        "bank",
    )

    assert result == service.FALLBACK_SUGGESTION


def test_invalid_gemini_response_returns_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(service, "_generate_gemini_response", lambda *_: "not valid json")

    result = service.suggest_reconciliation_match({}, [], "gst")

    assert result == service.FALLBACK_SUGGESTION


def test_valid_mocked_response_returns_structured_output(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    mocked_response = {
        "gemini_suggestion": "review_match",
        "gemini_confidence": "high",
        "gemini_reason": "The amount and reference agree, but the dates differ by one day.",
        "gemini_recommendation": "Compare the source voucher before accepting the rule-based result.",
        "gemini_candidate_id": "books-1",
    }
    monkeypatch.setattr(
        service,
        "_generate_gemini_response",
        lambda *_: json.dumps(mocked_response),
    )

    result = service.suggest_reconciliation_match(
        {"bank_line_id": "bank-1"},
        [{"accounting_record_id": "books-1"}],
        "bank",
    )

    assert result == mocked_response


def test_disallowed_final_match_response_returns_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    unsafe_response = {
        "gemini_suggestion": "matched",
        "gemini_confidence": "high",
        "gemini_reason": "The model considers this final.",
        "gemini_recommendation": "Mark it as matched.",
        "gemini_candidate_id": "books-1",
    }
    monkeypatch.setattr(
        service,
        "_generate_gemini_response",
        lambda *_: json.dumps(unsafe_response),
    )

    result = service.suggest_reconciliation_match({}, [], "bank")

    assert result == service.FALLBACK_SUGGESTION
