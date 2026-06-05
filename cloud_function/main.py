from backend.scripts.run_zoho_cloud_sync import main as run_sync


def zoho_sync(request):
    try:
        run_sync()
        return ("Zoho sync completed successfully.", 200)
    except Exception as error:
        return (f"Zoho sync failed: {str(error)}", 500)