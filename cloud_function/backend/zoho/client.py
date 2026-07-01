import os
import time
import requests
from dotenv import load_dotenv
from backend.zoho.auth import get_zoho_access_token

load_dotenv()

_MAX_ZOHO_GET_ATTEMPTS = 4
_INITIAL_BACKOFF_SECONDS = 2
_MAX_BACKOFF_SECONDS = 30


def _is_rate_limited(response: requests.Response) -> bool:
    response_text = response.text.lower()
    return (
        response.status_code == 429
        or "too many requests" in response_text
        or "rate limit" in response_text
        or "rate-limit" in response_text
    )


def _retry_delay_seconds(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        except ValueError:
            pass

    return min(_INITIAL_BACKOFF_SECONDS * (2 ** attempt), _MAX_BACKOFF_SECONDS)


def zoho_get(endpoint: str, params: dict | None = None) -> dict:
    access_token = get_zoho_access_token()
    books_base_url = os.getenv("ZOHO_BOOKS_BASE_URL")
    organization_id = os.getenv("ZOHO_ORGANIZATION_ID")

    if params is None:
        params = {}

    params["organization_id"] = organization_id

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }

    url = f"{books_base_url}/{endpoint}"

    for attempt in range(_MAX_ZOHO_GET_ATTEMPTS):
        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 200:
            return response.json()

        if not _is_rate_limited(response) or attempt == _MAX_ZOHO_GET_ATTEMPTS - 1:
            break

        time.sleep(_retry_delay_seconds(response, attempt))

    raise Exception(f"Zoho API error for {endpoint}: {response.text}")
