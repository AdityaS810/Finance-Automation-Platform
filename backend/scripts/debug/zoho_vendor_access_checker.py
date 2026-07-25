"""Check read-only Zoho API access needed for vendor reconciliation.

This diagnostic intentionally fetches at most one list record per endpoint,
never prints record contents, and never persists API responses.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

from backend.config.zoho_organizations import get_zoho_organizations
from backend.zoho.auth import get_zoho_access_token
from backend.zoho.http import ZOHO_REQUEST_TIMEOUT_SECONDS, get_zoho_http_session


load_dotenv()

ENDPOINTS = (
    ("/contacts", "contacts"),
    ("/bills", "bills"),
    ("/vendorpayments", "vendorpayments"),
    ("/banktransactions", "banktransactions"),
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(access_token|refresh_token|client_secret|authorization)\b"
    r"\s*[:=]\s*[^\s,;]+"
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_LONG_NUMBER = re.compile(r"\b\d{6,}\b")
_OAUTH_TOKEN = re.compile(r"(?i)\b(?:Zoho-oauthtoken\s+)?1000\.[A-Za-z0-9._-]+\b")
_MAX_SAFE_MESSAGE_LENGTH = 300


@dataclass(frozen=True)
class AccessResult:
    organization_key: str
    endpoint: str
    http_status: int | None
    success: bool
    collection_key: str
    record_count: int
    error_code: str
    error_message: str


def _safe_message(value: object) -> str:
    """Return a bounded message with common secret and PII shapes redacted."""
    message = " ".join(str(value or "").split())
    message = _SENSITIVE_ASSIGNMENT.sub(r"\1=[REDACTED]", message)
    message = _OAUTH_TOKEN.sub("[REDACTED_TOKEN]", message)
    message = _EMAIL.sub("[REDACTED_EMAIL]", message)
    message = _LONG_NUMBER.sub("[REDACTED_NUMBER]", message)
    return message[:_MAX_SAFE_MESSAGE_LENGTH]


def _safe_error_payload(response) -> tuple[str, str]:
    """Extract only Zoho's top-level error code and message."""
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return "non_json_response", "Zoho returned a non-JSON error response."

    if not isinstance(payload, dict):
        return "unexpected_response", "Zoho returned an unexpected error response."

    error_code = _safe_message(payload.get("code") or response.status_code)
    error_message = _safe_message(payload.get("message") or "Zoho request failed.")
    return error_code, error_message


def check_endpoint(
    *,
    organization_key: str,
    organization_id: str,
    endpoint: str,
    collection_key: str,
    access_token: str,
) -> AccessResult:
    """Run one bounded, read-only list request without exposing record data."""
    books_base_url = (os.getenv("ZOHO_BOOKS_BASE_URL") or "").rstrip("/")
    if not books_base_url:
        return AccessResult(
            organization_key,
            endpoint,
            None,
            False,
            collection_key,
            0,
            "configuration_error",
            "ZOHO_BOOKS_BASE_URL is not configured.",
        )

    try:
        response = get_zoho_http_session().get(
            f"{books_base_url}/{endpoint.lstrip('/')}",
            headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
            params={
                "organization_id": organization_id,
                "page": 1,
                "per_page": 1,
            },
            timeout=ZOHO_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as error:
        return AccessResult(
            organization_key,
            endpoint,
            None,
            False,
            collection_key,
            0,
            "request_error",
            _safe_message(error) or "Zoho request could not be completed.",
        )

    if response.status_code != 200:
        error_code, error_message = _safe_error_payload(response)
        return AccessResult(
            organization_key,
            endpoint,
            response.status_code,
            False,
            collection_key,
            0,
            error_code,
            error_message,
        )

    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return AccessResult(
            organization_key,
            endpoint,
            response.status_code,
            False,
            collection_key,
            0,
            "non_json_response",
            "Zoho returned a non-JSON success response.",
        )

    if not isinstance(payload, dict):
        return AccessResult(
            organization_key,
            endpoint,
            response.status_code,
            False,
            collection_key,
            0,
            "unexpected_response",
            "Zoho returned an unexpected success response.",
        )

    zoho_code = payload.get("code", 0)
    if zoho_code not in (0, "0", None):
        return AccessResult(
            organization_key,
            endpoint,
            response.status_code,
            False,
            collection_key,
            0,
            _safe_message(zoho_code),
            _safe_message(payload.get("message") or "Zoho request failed."),
        )

    records = payload.get(collection_key)
    if not isinstance(records, list):
        return AccessResult(
            organization_key,
            endpoint,
            response.status_code,
            False,
            collection_key,
            0,
            "missing_collection",
            f"Expected response collection '{collection_key}' was not returned.",
        )

    return AccessResult(
        organization_key,
        endpoint,
        response.status_code,
        True,
        collection_key,
        len(records),
        "",
        "",
    )


def run_checks() -> list[AccessResult]:
    """Check every endpoint for every configured organization."""
    organizations = get_zoho_organizations()
    try:
        access_token = get_zoho_access_token()
    except Exception as error:
        safe_error = _safe_message(error) or "Zoho authentication failed."
        return [
            AccessResult(
                organization["org_key"],
                endpoint,
                None,
                False,
                collection_key,
                0,
                "authentication_error",
                safe_error,
            )
            for organization in organizations
            for endpoint, collection_key in ENDPOINTS
        ]

    return [
        check_endpoint(
            organization_key=organization["org_key"],
            organization_id=organization["organization_id"],
            endpoint=endpoint,
            collection_key=collection_key,
            access_token=access_token,
        )
        for organization in organizations
        for endpoint, collection_key in ENDPOINTS
    ]


def _print_results(results: list[AccessResult]) -> None:
    for result in results:
        status = str(result.http_status) if result.http_status is not None else "N/A"
        outcome = "success" if result.success else "failure"
        print(
            f"organization_key={result.organization_key} | "
            f"endpoint={result.endpoint} | "
            f"http_status={status} | "
            f"result={outcome} | "
            f"collection_key={result.collection_key} | "
            f"returned_record_count={result.record_count} | "
            f"error_code={result.error_code} | "
            f"error_message={result.error_message}"
        )


def main() -> int:
    results = run_checks()
    _print_results(results)
    return 0 if all(result.success for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
