import json
from pathlib import Path
from datetime import datetime

def save_json_locally(entity_name: str, run_id: str, data: list[dict] | dict, org_key: str | None = None) -> str:
    today = datetime.utcnow()
    organization_folder = org_key or "default"

    folder_path = (
        Path("data")
        / "raw"
        / "zoho_books"
        / organization_folder
        / entity_name
        / f"year={today.year}"
        / f"month={today.month:02d}"
        / f"day={today.day:02d}"
        / f"run_id={run_id}"
    )

    folder_path.mkdir(parents=True, exist_ok=True)

    file_path = folder_path / f"{entity_name}.json"

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, default=str)

    return str(file_path)
