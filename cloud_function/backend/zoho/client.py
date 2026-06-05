import os
import requests
from dotenv import load_dotenv
from backend.zoho.auth import get_zoho_access_token

load_dotenv()

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

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        raise Exception(f"Zoho API error for {endpoint}: {response.text}")

    return response.json()
