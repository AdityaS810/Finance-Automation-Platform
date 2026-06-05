from backend.zoho.extract_zoho import fetch_journals

def main():
    journals = fetch_journals()
    print(f"Fetched {len(journals)} journals from Zoho.")

    if journals:
        print("First journal sample:")
        print(journals[0])

if __name__ == "__main__":
    main()
