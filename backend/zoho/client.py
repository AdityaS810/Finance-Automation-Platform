import os
import requests
from dotenv import load_dotenv
from backend.zoho.auth import get_zoho_access_token

load_dotenv()


def zoho_get(endpoint: str, params: dict | None = None, organization_id: str | None = None) -> dict:
    """Run a Zoho Books GET request.

    Callers can pass ``organization_id`` for multi-org sync. When omitted, the
    legacy ``ZOHO_ORGANIZATION_ID`` environment variable is still used.
    """
    access_token = get_zoho_access_token()
    books_base_url = os.getenv("ZOHO_BOOKS_BASE_URL")
    resolved_organization_id = organization_id or os.getenv("ZOHO_ORGANIZATION_ID")

    if params is None:
        params = {}

    params = dict(params)
    if resolved_organization_id:
        params["organization_id"] = resolved_organization_id

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }

    url = f"{books_base_url}/{endpoint}"

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        raise Exception(f"Zoho API error for {endpoint}: {response.text}")

    return response.json()
