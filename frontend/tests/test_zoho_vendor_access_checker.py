"""Tests for the read-only Zoho vendor reconciliation access checker."""

from backend.scripts.debug import zoho_vendor_access_checker as checker


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.request = None

    def get(self, url, **kwargs):
        self.request = {"url": url, **kwargs}
        return self.response


def test_success_counts_records_without_returning_payload(monkeypatch):
    session = FakeSession(FakeResponse(200, {"code": 0, "contacts": [{"contact_name": "Private Vendor"}]}))
    monkeypatch.setenv("ZOHO_BOOKS_BASE_URL", "https://www.zohoapis.example/books/v3")
    monkeypatch.setattr(checker, "get_zoho_http_session", lambda: session)

    result = checker.check_endpoint(
        organization_key="india",
        organization_id="org-private",
        endpoint="/contacts",
        collection_key="contacts",
        access_token="secret-token",
    )

    assert result.success is True
    assert result.record_count == 1
    assert not hasattr(result, "payload")
    assert session.request["params"] == {
        "organization_id": "org-private",
        "page": 1,
        "per_page": 1,
    }
    assert session.request["timeout"] == checker.ZOHO_REQUEST_TIMEOUT_SECONDS


def test_failure_preserves_safe_zoho_error_and_redacts_sensitive_values(monkeypatch):
    session = FakeSession(
        FakeResponse(
            403,
            {
                "code": 57,
                "message": (
                    "You are not authorized. access_token=private "
                    "user@example.com organization 123456789"
                ),
                "contacts": [{"contact_name": "Must Not Be Read"}],
            },
        )
    )
    monkeypatch.setenv("ZOHO_BOOKS_BASE_URL", "https://www.zohoapis.example/books/v3")
    monkeypatch.setattr(checker, "get_zoho_http_session", lambda: session)

    result = checker.check_endpoint(
        organization_key="us",
        organization_id="org-private",
        endpoint="/contacts",
        collection_key="contacts",
        access_token="secret-token",
    )

    assert result.success is False
    assert result.http_status == 403
    assert result.error_code == "57"
    assert "private" not in result.error_message
    assert "user@example.com" not in result.error_message
    assert "123456789" not in result.error_message
    assert "[REDACTED]" in result.error_message


def test_missing_collection_is_a_safe_failure(monkeypatch):
    session = FakeSession(FakeResponse(200, {"code": 0, "message": "success"}))
    monkeypatch.setenv("ZOHO_BOOKS_BASE_URL", "https://www.zohoapis.example/books/v3")
    monkeypatch.setattr(checker, "get_zoho_http_session", lambda: session)

    result = checker.check_endpoint(
        organization_key="india",
        organization_id="org-private",
        endpoint="/bills",
        collection_key="bills",
        access_token="secret-token",
    )

    assert result.success is False
    assert result.error_code == "missing_collection"
    assert result.record_count == 0
