"""
fetch_tron.py  —  STEP 1: fetch real TRON wallet data and print it as raw JSON.

Goal of this file: SEE what blockchain transaction data actually looks like,
so we know what fields we can use to detect fraud later. That's it. No fraud
logic yet — just fetch and look.

Run it with:   python fetch_tron.py
"""

import json      # turns Python data <-> JSON text (so we can pretty-print it)
import os        # lets us read environment variables (our API key)

import requests              # the library that makes the HTTP call to TronGrid
from dotenv import load_dotenv   # loads the .env file into os.environ

# 1) Load the .env file so os.getenv() can see TRONGRID_API_KEY.
load_dotenv()
API_KEY = os.getenv("TRONGRID_API_KEY")  # None or "" if you left it blank

# 2) The wallet we want to inspect.
#    Default = the USDT (TRC20) contract on TRON — a VERY active address, so
#    you'll always get lots of transactions to look at. Swap in any TRON
#    address (starts with "T") to inspect a different wallet.
WALLET = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

# 3) TronGrid's REST endpoint for "give me this account's transactions".
#    We ask for just 3 so the output is readable.
URL = f"https://api.trongrid.io/v1/accounts/{WALLET}/transactions"
PARAMS = {"limit": 3}

# 4) Headers. TronGrid works without a key, but if you have one we send it.
HEADERS = {"Accept": "application/json"}
if API_KEY:
    HEADERS["TRON-PRO-API-KEY"] = API_KEY
    print("Using your TronGrid API key.\n")
else:
    print("No API key set (running with lower rate limits — fine for testing).\n")


def main():
    print(f"Fetching last {PARAMS['limit']} transactions for wallet:\n{WALLET}\n")

    # 5) Make the request. This is the actual network call to the blockchain API.
    response = requests.get(URL, headers=HEADERS, params=PARAMS, timeout=15)

    # 6) Did it work? 200 = OK. Anything else = a problem we should see.
    print(f"HTTP status code: {response.status_code}")
    if response.status_code != 200:
        print("Request failed. Response text:")
        print(response.text)
        return

    # 7) Turn the JSON text into Python data, then pretty-print it back out.
    data = response.json()
    print("\n=================== RAW JSON RESPONSE ===================\n")
    print(json.dumps(data, indent=2))  # indent=2 makes it human-readable

    # 8) A tiny bit of orientation so the wall of JSON isn't scary.
    tx_list = data.get("data", [])
    print("\n=================== QUICK SUMMARY ===================")
    print(f"Number of transactions returned: {len(tx_list)}")
    if tx_list:
        first = tx_list[0]
        print("Top-level fields on the FIRST transaction:")
        for key in first.keys():
            print(f"  - {key}")


if __name__ == "__main__":
    main()
