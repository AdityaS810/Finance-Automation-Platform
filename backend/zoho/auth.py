import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from backend.zoho.http import ZOHO_REQUEST_TIMEOUT_SECONDS, get_zoho_http_session

load_dotenv()


_ACCESS_TOKEN_CACHE: dict[str, object] = {
    "access_token": None,
    "expires_at": datetime.min.replace(tzinfo=timezone.utc),
}


def get_zoho_access_token() -> str:
    cached_token = _ACCESS_TOKEN_CACHE["access_token"]
    expires_at = _ACCESS_TOKEN_CACHE["expires_at"]
    if cached_token and isinstance(expires_at, datetime) and datetime.now(timezone.utc) < expires_at:
        return str(cached_token)

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

    response = get_zoho_http_session().post(
        url,
        params=params,
        timeout=ZOHO_REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise Exception(f"Failed to get Zoho access token: {response.text}")

    response_json = response.json()
    access_token = response_json["access_token"]
    expires_in_seconds = int(response_json.get("expires_in", 3600))
    _ACCESS_TOKEN_CACHE["access_token"] = access_token
    _ACCESS_TOKEN_CACHE["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in_seconds - 120, 60))

    return access_token
