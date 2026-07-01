import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

_cached_access_token: str | None = None
_cached_access_token_expires_at = 0.0
_TOKEN_EXPIRY_BUFFER_SECONDS = 300


def get_zoho_access_token() -> str:
    global _cached_access_token, _cached_access_token_expires_at

    now = time.time()
    if _cached_access_token and now < _cached_access_token_expires_at - _TOKEN_EXPIRY_BUFFER_SECONDS:
        return _cached_access_token

    accounts_base_url = os.getenv("ZOHO_ACCOUNTS_BASE_URL")
    client_id = os.getenv("ZOHO_CLIENT_ID")
    client_secret = os.getenv("ZOHO_CLIENT_SECRET")
    refresh_token = os.getenv("ZOHO_REFRESH_TOKEN")

    url = f"{accounts_base_url}/oauth/v2/token"

    params = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }

    response = requests.post(url, params=params)

    if response.status_code != 200:
        raise Exception(f"Failed to get Zoho access token: {response.text}")

    token_response = response.json()
    _cached_access_token = token_response["access_token"]
    expires_in = int(token_response.get("expires_in", 3600))
    _cached_access_token_expires_at = now + expires_in

    return _cached_access_token
