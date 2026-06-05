from datetime import datetime
from dotenv import load_dotenv

from backend.zoho.extract_zoho import (
    fetch_accounts,
    fetch_contacts,
    fetch_invoices,
)

from backend.zoho.local_storage import save_json_locally

load_dotenv()

def main():
    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    entities = [
        {
            "name": "accounts",
            "fetch_func": fetch_accounts,
        },
        {
            "name": "contacts",
            "fetch_func": fetch_contacts,
        },
        {
            "name": "invoices",
            "fetch_func": fetch_invoices,
        },
    ]

    for entity in entities:
        print(f"Fetching {entity['name']} from Zoho...")

        data = entity["fetch_func"]()

        print(f"Fetched {len(data)} records for {entity['name']}")

        file_path = save_json_locally(
            entity_name=entity["name"],
            run_id=run_id,
            data=data,
        )

        print(f"Saved {entity['name']} locally at: {file_path}")

    print("Local Zoho sync completed successfully.")

if __name__ == "__main__":
    main()