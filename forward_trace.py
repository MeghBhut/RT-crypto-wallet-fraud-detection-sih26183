"""
forward_trace.py  —  STEP 3 (second signal): FORWARD TRACE the money.

Follow funds FORWARD from a wallet, hop by hop, to see where they end up.
Three "brakes" keep the trace tractable and meaningful:

  * HOP CAP        -> stop after N jumps (prevents exponential explosion)
  * VALUE THRESHOLD-> only follow transfers above X (ignores dust / noise)
  * TIME FILTER    -> at each hop, only follow money that left within a window
                      of when it arrived (laundering moves FAST)

For each wallet we visit, we fetch its outgoing transactions LIVE from TronGrid
and also cache them into tron_cache.db -- so the trace is real AND it grows our
offline dataset as a side effect.

Run it with:
    python forward_trace.py                 # auto-picks the biggest sender in cache
    python forward_trace.py TXyz...addr     # trace a wallet you choose
"""

import sys
import sqlite3

# On Windows the console defaults to cp1252 and chokes on fancy characters.
# Force UTF-8 so our output prints safely on any machine.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Reuse the pieces we already built and tested in Step 2 (don't rewrite them):
from cache_to_db import fetch_raw, clean_transaction, init_db, save_transactions, DB_FILE

# ---- the three brakes (change these to see their effect) ----
HOP_CAP = 2                 # how many jumps to follow
VALUE_THRESHOLD = 100.0     # only follow transfers >= this many token units
TIME_WINDOW_MS = 24 * 3600 * 1000   # 24 hours, in milliseconds
BRANCH_CAP = 3              # at each wallet, follow at most this many (biggest) outflows

# caches so we never fetch the same wallet twice in one run
_fetched = set()
_visited = set()


def get_outgoing(conn, wallet):
    """Fetch + clean + cache a wallet's transactions, then return only the
    OUTGOING ones (where this wallet is the sender)."""
    if wallet not in _fetched:
        _fetched.add(wallet)
        try:
            raw = fetch_raw(wallet)
        except Exception as e:
            print(f"      (could not fetch {wallet[:10]}...: {e})")
            return []
        cleaned = [c for tx in raw if (c := clean_transaction(tx, wallet)) is not None]
        save_transactions(conn, cleaned)   # grow the offline cache as we go

    # read this wallet's outgoing transfers back out of the DB (offline-friendly)
    return conn.execute("""
        SELECT to_address, amount, token, timestamp_ms
        FROM transactions
        WHERE from_address = ? AND to_address IS NOT NULL AND success = 1
    """, (wallet,)).fetchall()


def trace(conn, wallet, hop, arrival_ms, indent):
    """Recursively follow the money forward, applying the three brakes."""
    if hop > HOP_CAP:                      # BRAKE 1: hop cap
        return

    edges = []
    for to_addr, amount, token, ts in get_outgoing(conn, wallet):
        if amount < VALUE_THRESHOLD:       # BRAKE 2: value threshold
            continue
        if arrival_ms is not None:         # BRAKE 3: time filter (not at the root)
            if not (arrival_ms <= ts <= arrival_ms + TIME_WINDOW_MS):
                continue
        edges.append((to_addr, amount, token, ts))

    # follow only the biggest few outflows (another practical brake on explosion)
    edges.sort(key=lambda e: e[1], reverse=True)
    edges = edges[:BRANCH_CAP]

    pad = "    " * indent
    if not edges:
        print(f"{pad}(no further qualifying outflows)")
        return

    for to_addr, amount, token, ts in edges:
        print(f"{pad}|- {amount:,.2f} {token}  ->  {to_addr}")
        if to_addr in _visited:
            print(f"{pad}     (already visited — stopping to avoid a loop)")
            continue
        _visited.add(to_addr)
        trace(conn, to_addr, hop + 1, ts, indent + 1)


def pick_default_seed(conn):
    """If the user didn't pass an address, start from the biggest sender we have."""
    row = conn.execute("""
        SELECT from_address, SUM(amount) AS sent
        FROM transactions
        WHERE from_address IS NOT NULL AND success = 1
        GROUP BY from_address
        ORDER BY sent DESC
        LIMIT 1
    """).fetchone()
    return row[0] if row else None


def main():
    conn = init_db()
    seed = sys.argv[1] if len(sys.argv) > 1 else pick_default_seed(conn)
    if not seed:
        print("No seed address available. Run cache_to_db.py first.")
        return

    print("=== FORWARD TRACE ===")
    print(f"Seed wallet     : {seed}")
    print(f"Hop cap         : {HOP_CAP} hops")
    print(f"Value threshold : >= {VALUE_THRESHOLD} token units")
    print(f"Time window     : {TIME_WINDOW_MS // 3600000}h after money arrives")
    print(f"Branch cap      : {BRANCH_CAP} biggest outflows per wallet\n")

    _visited.add(seed)
    print(f"[hop 0] {seed}")
    trace(conn, seed, hop=0, arrival_ms=None, indent=1)

    print(f"\nWallets visited in this trace: {len(_visited)}")
    print(f"tron_cache.db now enriched with every wallet we fetched.")
    conn.close()


if __name__ == "__main__":
    main()
