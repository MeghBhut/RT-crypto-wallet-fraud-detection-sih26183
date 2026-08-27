"""
cache_to_db.py  —  STEP 2: fetch TRON transactions, CLEAN them, and CACHE them
                            into a local SQLite database so all future code can
                            run OFFLINE with no API calls.

What this file does, in order:
  1. Fetch raw transactions from TronGrid (the messy JSON we saw in Step 1).
  2. CLEAN each transaction into a simple shape: {from, to, amount, token, time}.
  3. STORE the clean rows in a SQLite file (tron_cache.db) on disk.

After running this once, you can unplug the internet and every later script
(the fraud engine, the backend) reads from tron_cache.db instead of the API.

Run it with:
    python cache_to_db.py                # caches the default demo wallet
    python cache_to_db.py TXyz...addr    # caches any wallet you pass
"""

import os
import sys
import json
import sqlite3          # Python's built-in SQL database — no install needed
import hashlib          # used to convert TRON hex addresses -> friendly "T..." form
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
load_dotenv()
API_KEY = os.getenv("TRONGRID_API_KEY")

DB_FILE = "tron_cache.db"                 # our on-disk SQLite database
DEFAULT_WALLET = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # USDT contract (very active)
HOW_MANY = 200                            # transactions to fetch (200 = TronGrid max/page)

# Known token contracts, so we can label amounts nicely and scale decimals.
# key = contract address in TRON hex form (starts with 41), value = (symbol, decimals)
KNOWN_TOKENS = {
    "41a614f803b6fd780986a42c78ec9c7f77e6ded13c": ("USDT", 6),
}

# TRC20 "transfer(address,uint256)" always starts with this 8-char function selector.
TRANSFER_SELECTOR = "a9059cbb"

# Base58 alphabet used by TRON addresses (same as Bitcoin's).
B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# ---------------------------------------------------------------------------
# HELPER 1: convert a TRON hex address (41....) into the friendly "T..." form
# ---------------------------------------------------------------------------
def hex_to_base58(hex_addr):
    """TRON stores addresses as hex like '41ee2d..'. Users see 'TXyz..'.
    This converts hex -> the T-form using base58check (double-SHA256 checksum)."""
    if not hex_addr:
        return None
    try:
        raw = bytes.fromhex(hex_addr)                       # hex string -> bytes
        checksum = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
        full = raw + checksum                               # append 4-byte checksum
        num = int.from_bytes(full, "big")                   # bytes -> one big number
        encoded = ""
        while num > 0:                                      # base58-encode the number
            num, rem = divmod(num, 58)
            encoded = B58_ALPHABET[rem] + encoded
        # each leading zero byte becomes a leading '1'
        pad = 0
        for b in full:
            if b == 0:
                pad += 1
            else:
                break
        return "1" * pad + encoded
    except ValueError:
        return hex_addr  # if it wasn't valid hex, just return what we got


# ---------------------------------------------------------------------------
# HELPER 2: turn ONE raw messy transaction into a clean simple dictionary
# ---------------------------------------------------------------------------
def clean_transaction(tx, queried_wallet):
    """Takes one raw tx (from the API) and returns a tidy dict, or None if we
    can't understand it. This is where the 'messy -> clean' magic happens."""
    try:
        contract = tx["raw_data"]["contract"][0]
        ctype = contract["type"]                 # 'TransferContract' or 'TriggerSmartContract'
        value = contract["parameter"]["value"]
        success = tx.get("ret", [{}])[0].get("contractRet") == "SUCCESS"
        ts_ms = tx.get("block_timestamp", 0)
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat() if ts_ms else None

        from_addr = hex_to_base58(value.get("owner_address"))

        if ctype == "TransferContract":
            # Native TRX transfer — the easy case, fields are right there.
            to_addr = hex_to_base58(value.get("to_address"))
            amount = value.get("amount", 0) / 1_000_000     # SUN -> TRX (6 decimals)
            token = "TRX"

        elif ctype == "TriggerSmartContract":
            # Token (e.g. USDT) transfer — amount + recipient are ENCODED in 'data'.
            data = value.get("data", "")
            contract_hex = value.get("contract_address", "")
            symbol, decimals = KNOWN_TOKENS.get(contract_hex, ("TOKEN", 6))
            if data[:8] == TRANSFER_SELECTOR and len(data) >= 136:
                # recipient = last 40 hex chars of the first 32-byte argument, prefixed 41
                to_hex = "41" + data[8 + 24: 8 + 64]
                to_addr = hex_to_base58(to_hex)
                # amount = the second 32-byte argument, read as an integer
                amount = int(data[8 + 64: 8 + 128], 16) / (10 ** decimals)
                token = symbol
            else:
                # some other contract call we don't decode — keep it but mark unknown
                to_addr, amount, token = None, 0, symbol
        else:
            return None  # a tx type we don't care about for fraud (e.g. votes)

        return {
            "txid": tx["txID"],
            "queried_wallet": queried_wallet,
            "from_address": from_addr,
            "to_address": to_addr,
            "amount": amount,
            "token": token,
            "timestamp_ms": ts_ms,
            "datetime_utc": dt,
            "tx_type": ctype,
            "success": 1 if success else 0,
            "raw_json": json.dumps(tx),   # keep the original, so nothing is ever lost
        }
    except (KeyError, IndexError, TypeError):
        return None  # malformed tx — skip it rather than crash


# ---------------------------------------------------------------------------
# DATABASE: create the table (safe to call every run)
# ---------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            txid           TEXT PRIMARY KEY,   -- unique id (stops duplicates)
            queried_wallet TEXT,               -- whose history this row came from
            from_address   TEXT,               -- sender (T-form)
            to_address     TEXT,               -- recipient (T-form)
            amount         REAL,               -- human units (e.g. 12.5 USDT)
            token          TEXT,               -- 'TRX', 'USDT', ...
            timestamp_ms   INTEGER,            -- raw time in milliseconds
            datetime_utc   TEXT,               -- human-readable time
            tx_type        TEXT,               -- contract type
            success        INTEGER,            -- 1 = succeeded, 0 = failed
            raw_json       TEXT                -- full original tx (backup)
        )
    """)
    conn.commit()
    return conn


def save_transactions(conn, rows):
    """Insert clean rows. 'INSERT OR IGNORE' skips ones we already have (by txid),
    so running this repeatedly never creates duplicates."""
    cur = conn.executemany("""
        INSERT OR IGNORE INTO transactions
        (txid, queried_wallet, from_address, to_address, amount, token,
         timestamp_ms, datetime_utc, tx_type, success, raw_json)
        VALUES
        (:txid, :queried_wallet, :from_address, :to_address, :amount, :token,
         :timestamp_ms, :datetime_utc, :tx_type, :success, :raw_json)
    """, rows)
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# FETCH: get raw transactions from TronGrid
# ---------------------------------------------------------------------------
def fetch_raw(wallet):
    url = f"https://api.trongrid.io/v1/accounts/{wallet}/transactions"
    headers = {"Accept": "application/json"}
    if API_KEY:
        headers["TRON-PRO-API-KEY"] = API_KEY
    resp = requests.get(url, headers=headers, params={"limit": HOW_MANY}, timeout=20)
    resp.raise_for_status()          # raises if status != 200
    return resp.json().get("data", [])


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    wallet = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WALLET
    print(f"Key in use: {'yes' if API_KEY else 'no (rate-limited)'}")
    print(f"Fetching up to {HOW_MANY} transactions for:\n  {wallet}\n")

    raw = fetch_raw(wallet)
    print(f"Got {len(raw)} raw transactions from the API.")

    # clean every raw tx; drop the ones we couldn't parse
    cleaned = [c for tx in raw if (c := clean_transaction(tx, wallet)) is not None]
    print(f"Cleaned {len(cleaned)} of them into simple rows.")

    conn = init_db()
    new_rows = save_transactions(conn, cleaned)
    print(f"Stored {new_rows} NEW rows into {DB_FILE} (duplicates skipped).")

    # show a few clean rows so you can SEE the difference vs the messy JSON
    print("\n=== Sample of what's now cached (clean & readable) ===")
    for row in conn.execute("""
        SELECT datetime_utc, from_address, to_address, amount, token
        FROM transactions ORDER BY timestamp_ms DESC LIMIT 5
    """):
        dt, frm, to, amt, tok = row
        print(f"  {dt} | {frm} -> {to} | {amt} {tok}")

    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"\nTotal rows now cached in {DB_FILE}: {total}")
    print("You can now unplug the internet — future scripts read from this DB.")
    conn.close()


if __name__ == "__main__":
    main()
