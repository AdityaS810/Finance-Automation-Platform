"""Zoho Books organizations that are safe to sync with shared credentials."""

from __future__ import annotations


ZOHO_ORGANIZATIONS = [
    {
        "org_key": "us",
        "organization_id": "916007477",
        "organization_name": "Midoffice Data International, Inc",
        "country": "U.S.A",
        "base_currency": "USD",
    },
    {
        "org_key": "india",
        "organization_id": "880373191",
        "organization_name": "Midoffice Data Solutions Private Limited",
        "country": "India",
        "base_currency": "INR",
    },
]


def get_zoho_organizations() -> list[dict]:
    """Return a copy of configured non-secret Zoho organization metadata."""
    return [dict(organization) for organization in ZOHO_ORGANIZATIONS]
