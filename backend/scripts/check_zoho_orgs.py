from dotenv import load_dotenv
from backend.zoho.client import zoho_get

load_dotenv()

response = zoho_get("organizations")
organizations = response.get("organizations", [])

print(f"Found {len(organizations)} Zoho organization(s):")
for org in organizations:
    print("-" * 60)
    print("Name:", org.get("name") or org.get("organization_name"))
    print("Org ID:", org.get("organization_id"))
    print("Country:", org.get("country"))
    print("Currency:", org.get("currency_code"))
