"""
fan_in.py  —  STEP 3 (first signal): the FAN-IN counter.

Fan-in = how many DISTINCT wallets sent money INTO an address.
High fan-in = a "hub": an exchange, a collector, or a money-laundering funnel.

This script reads the CACHED database (works fully offline) and ranks addresses
by fan-in, so the busiest hubs float to the top. The idea: the top hubs should
turn out to be well-known exchange-style addresses -- if they do, our metric is
trustworthy ("we rediscovered a known hub from raw data").

Run it with:   python fan_in.py
"""

import sqlite3

DB_FILE = "tron_cache.db"


def top_fan_in(conn, limit=15):
    """Return addresses ranked by fan-in (distinct senders)."""
    # The whole detector is this ONE query:
    #   - GROUP BY to_address   -> one row per recipient
    #   - COUNT(DISTINCT ...)   -> unique senders  (fan-in, NOT tx count)
    #   - also grab tx count + total received, for context
    return conn.execute("""
        SELECT
            to_address                       AS address,
            COUNT(DISTINCT from_address)     AS fan_in,
            COUNT(*)                         AS total_tx,
            ROUND(SUM(amount), 2)            AS total_received
        FROM transactions
        WHERE to_address IS NOT NULL         -- ignore rows we couldn't decode
          AND success = 1                    -- only count transfers that worked
        GROUP BY to_address
        ORDER BY fan_in DESC                 -- biggest hubs first
        LIMIT ?
    """, (limit,)).fetchall()


def fan_out_for(conn, address):
    """Bonus: fan-out (distinct recipients) for one address, so we can compare.
    A true exchange hub tends to have BOTH high fan-in and high fan-out."""
    row = conn.execute("""
        SELECT COUNT(DISTINCT to_address)
        FROM transactions
        WHERE from_address = ? AND success = 1
    """, (address,)).fetchone()
    return row[0] if row else 0


def main():
    conn = sqlite3.connect(DB_FILE)

    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"Analyzing {total} cached transactions (offline, no API calls).\n")

    print("=== TOP ADDRESSES BY FAN-IN (most unique senders) ===\n")
    print(f"{'#':>2}  {'ADDRESS':<36} {'FAN-IN':>7} {'TXs':>5} {'RECEIVED':>15}")
    print("-" * 72)

    rows = top_fan_in(conn)
    for i, (addr, fan_in, tx, received) in enumerate(rows, start=1):
        print(f"{i:>2}  {addr:<36} {fan_in:>7} {tx:>5} {received:>15,.2f}")

    # Take the #1 hub and show its fan-out too, to characterize it.
    if rows:
        top_addr = rows[0][0]
        fo = fan_out_for(conn, top_addr)
        print("\n=== CHARACTERIZING THE #1 HUB ===")
        print(f"Address : {top_addr}")
        print(f"Fan-in  : {rows[0][1]} distinct senders")
        print(f"Fan-out : {fo} distinct recipients")
        if rows[0][1] >= 5 and fo == 0:
            print("Shape   : many-in / none-out  ->  looks like a COLLECTOR/deposit hub")
        elif rows[0][1] >= 5 and fo >= 5:
            print("Shape   : many-in / many-out  ->  looks like an EXCHANGE-style hub")
        else:
            print("Shape   : low volume -> need more data to classify confidently")

    print("\nNote: with only a small cache this is a demo of the MECHANIC.")
    print("Fetch more transactions and the real hubs stand out sharply.")
    conn.close()


if __name__ == "__main__":
    main()
