import os
import requests
from dotenv import load_dotenv

load_dotenv()

def get_zoho_access_token() -> str:
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

    return response.json()["access_token"]