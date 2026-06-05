from backend.zoho.auth import get_zoho_access_token

def main():
    token = get_zoho_access_token()
    print("Zoho authentication successful.")
    print("Access token starts with:", token[:10])

if __name__ == "__main__":
    main()