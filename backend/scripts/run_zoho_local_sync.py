from datetime import datetime, timezone
from dotenv import load_dotenv

from backend.config.zoho_entities import get_zoho_entity_configs
from backend.config.zoho_organizations import get_zoho_organizations
from backend.zoho.extract_zoho import fetch_entity_records
from backend.zoho.local_storage import save_json_locally

load_dotenv()


def main():
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    entities = get_zoho_entity_configs()
    organizations = get_zoho_organizations()

    for organization in organizations:
        print(f"Starting local Zoho sync for {organization['organization_name']} ({organization['org_key']})...")

        for entity in entities:
            print(f"Fetching {entity.name} from Zoho org {organization['org_key']}...")

            data = fetch_entity_records(entity, organization_id=organization["organization_id"])

            print(f"Fetched {len(data)} records for {entity.name} from org {organization['org_key']}")

            file_path = save_json_locally(
                entity_name=entity.name,
                run_id=run_id,
                data=data,
                org_key=organization["org_key"],
            )

            print(f"Saved {entity.name} locally at: {file_path}")

    print("Local Zoho sync completed successfully.")


if __name__ == "__main__":
    main()
