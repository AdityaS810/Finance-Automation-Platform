from backend.zoho.extract_zoho import fetch_accounts

def main():
    accounts = fetch_accounts()
    print(f"Fetched {len(accounts)} accounts from Zoho.")

    if accounts:
        print("First account sample:")
        print(accounts[0])

if __name__ == "__main__":
    main()