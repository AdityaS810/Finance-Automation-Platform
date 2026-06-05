import json
from google.cloud import storage


def upload_json_to_gcs(
    bucket_name: str,
    destination_blob_name: str,
    data: list[dict] | dict,
) -> str:
    """
    Uploads JSON data to Google Cloud Storage.
    Returns the full gs:// path.
    """

    storage_client = storage.Client()

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    json_text = json.dumps(data, indent=2, default=str)

    blob.upload_from_string(
        json_text,
        content_type="application/json",
    )

    return f"gs://{bucket_name}/{destination_blob_name}"