"""Tests for bounded, connection-pooled Zoho HTTP calls."""

from __future__ import annotations

from backend.zoho import client as zoho_client
from backend.zoho.http import ZOHO_REQUEST_TIMEOUT_SECONDS, get_zoho_http_session


def test_zoho_get_reuses_session_and_sets_timeout(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"contacts": []}

    class FakeSession:
        def get(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setenv("ZOHO_BOOKS_BASE_URL", "https://www.zohoapis.example/books/v3")
    monkeypatch.setattr(zoho_client, "get_zoho_access_token", lambda: "test-token")
    monkeypatch.setattr(zoho_client, "get_zoho_http_session", lambda: FakeSession())

    result = zoho_client.zoho_get(
        "contacts",
        params={"page": 1},
        organization_id="org-1",
    )

    assert result == {"contacts": []}
    assert captured["timeout"] == ZOHO_REQUEST_TIMEOUT_SECONDS
    assert captured["params"] == {"page": 1, "organization_id": "org-1"}
    assert captured["headers"]["Authorization"] == "Zoho-oauthtoken test-token"


def test_zoho_session_retries_transient_statuses():
    get_zoho_http_session.cache_clear()
    session = get_zoho_http_session()
    retry_policy = session.get_adapter("https://").max_retries

    assert retry_policy.total == 3
    assert retry_policy.backoff_factor == 0.5
    assert 429 in retry_policy.status_forcelist
    assert 503 in retry_policy.status_forcelist
    assert {"GET", "POST"}.issubset(retry_policy.allowed_methods)
