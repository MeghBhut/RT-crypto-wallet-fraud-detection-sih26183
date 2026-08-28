"""
forward_trace.py  —  FORWARD TRACE the money, and return it as a GRAPH.

Follow funds forward from a wallet, hop by hop, applying three brakes:
  * HOP CAP        -> stop after N jumps (prevents exponential explosion)
  * VALUE THRESHOLD-> only follow transfers above X (ignores dust / noise)
  * TIME FILTER    -> at each hop, only follow money that left within a window
                      of when it arrived (laundering moves FAST)
plus a BRANCH CAP (only the biggest few outflows per wallet).

The important function here is build_graph(), which returns:
    { "nodes": [ {id,label,hop,is_seed,risk_level,fan_in}, ... ],
      "edges": [ {from,to,amount,token,timestamp_ms}, ... ] }
exactly the shape API_CONTRACT.md promises for the animated trace graph.

For each wallet we visit we fetch its transactions LIVE from TronGrid and cache
them into tron_cache.db -- so tracing is real AND grows our offline dataset.

CLI:  python forward_trace.py [TWalletAddress]
"""

import sys
import sqlite3

# reuse the tested Step-2 pieces (don't rewrite them)
from cache_to_db import fetch_raw, clean_transaction, init_db, save_transactions, DB_FILE
# the engine gives each node its risk_level + fan_in, and classifies exit points
from fraud_engine import analyze_wallet, classify_address

try:
    sys.stdout.reconfigure(encoding="utf-8")   # Windows console safety
except Exception:
    pass

# ---- the brakes (defaults; the API can override) ----
HOP_CAP         = 3          # follow up to 3 jumps so chains can reach a cash-out
VALUE_THRESHOLD = 100.0
TIME_WINDOW_MS  = 24 * 3600 * 1000
BRANCH_CAP      = 4          # follow the 4 biggest outflows per wallet


def shorten(addr):
    """'TNeorv8DUs...9umQ' -> 'TNeorv..9umQ' for compact graph labels (ASCII-safe)."""
    return addr if not addr or len(addr) <= 14 else f"{addr[:6]}..{addr[-4:]}"


def get_outgoing(conn, wallet, fetched):
    """Fetch+cache a wallet's transactions once, then return its OUTGOING transfers.
    `fetched` is a per-trace set so we never hit the API twice for the same wallet."""
    if wallet not in fetched:
        fetched.add(wallet)
        try:
            raw = fetch_raw(wallet)
            cleaned = [c for tx in raw if (c := clean_transaction(tx, wallet)) is not None]
            save_transactions(conn, cleaned)
        except Exception:
            pass   # offline / rate-limited / unknown address: fall back to cache below

    # ALWAYS read from cache, even if the live fetch failed — this is what makes
    # offline mode and seeded demo wallets work.
    return conn.execute("""
        SELECT to_address, amount, token, timestamp_ms
        FROM transactions
        WHERE from_address = ? AND to_address IS NOT NULL AND success = 1
    """, (wallet,)).fetchall()


def build_graph(conn, seed,
                hop_cap=HOP_CAP, value_threshold=VALUE_THRESHOLD,
                time_window_ms=TIME_WINDOW_MS, branch_cap=BRANCH_CAP):
    """Walk the money forward from `seed` and return {nodes, edges} per the contract."""
    fetched = set()
    hop_of = {seed: 0}          # address -> its hop distance from the seed
    edges = []
    queue = [(seed, 0, None)]   # (address, hop, when-money-arrived-here)
    i = 0

    while i < len(queue):       # breadth-first walk
        addr, hop, arrival = queue[i]
        i += 1
        if hop >= hop_cap:                       # BRAKE 1: hop cap
            continue

        candidates = []
        for to_addr, amount, token, ts in get_outgoing(conn, addr, fetched):
            if amount < value_threshold:         # BRAKE 2: value threshold
                continue
            if arrival is not None and not (arrival <= ts <= arrival + time_window_ms):
                continue                         # BRAKE 3: time filter (not at root)
            candidates.append((to_addr, amount, token, ts))

        candidates.sort(key=lambda e: e[1], reverse=True)
        for to_addr, amount, token, ts in candidates[:branch_cap]:   # BRAKE 4: branch cap
            edges.append({
                "from": addr, "to": to_addr,
                "amount": round(amount, 2), "token": token, "timestamp_ms": ts,
            })
            if to_addr not in hop_of:            # new node -> explore it later
                hop_of[to_addr] = hop + 1
                queue.append((to_addr, hop + 1, ts))
            # if already seen, we still keep the edge (shows convergence/loops)

    # which addresses have onward flow we followed? (used to find terminal nodes)
    has_outflow = {e["from"] for e in edges}

    # turn every discovered address into a node, enriched by the fraud engine
    nodes = []
    for addr, hop in hop_of.items():
        res = analyze_wallet(conn, addr)         # reads cache only, no API
        fan_in = res["stats"]["fan_in"]
        if addr == seed:
            kind, note = "seed", None
        else:
            kind, note = classify_address(addr, fan_in)   # exchange? likely? wallet?
        nodes.append({
            "id": addr,
            "label": shorten(addr),
            "hop": hop,
            "is_seed": addr == seed,
            "risk_level": res["risk_level"],
            "fan_in": fan_in,
            "kind": kind,
            "note": note,
            "terminal": addr not in has_outflow and addr != seed,
        })

    # DESTINATIONS = where the money ends up. A destination is a terminal node
    # (money stops there in our trace) OR any node flagged as an exchange.
    dests = []
    for n in nodes:
        is_exchange = n["kind"] in ("exchange", "likely_exchange")
        if n["is_seed"] or not (n["terminal"] or is_exchange):
            continue
        received = round(sum(e["amount"] for e in edges if e["to"] == n["id"]), 2)
        dests.append({
            "address": n["id"], "label": n["label"], "kind": n["kind"],
            "note": n["note"], "received": received, "hop": n["hop"],
        })
    # rank: exchanges first, then by amount received
    dests.sort(key=lambda d: (d["kind"] not in ("exchange", "likely_exchange"), -d["received"]))

    return {"nodes": nodes, "edges": edges, "destinations": dests}


# ---------------------------------------------------------------------------
# CLI: print the graph so you can eyeball it without the web layer
# ---------------------------------------------------------------------------
def pick_default_seed(conn):
    row = conn.execute("""
        SELECT from_address, SUM(amount) s FROM transactions
        WHERE from_address IS NOT NULL AND success = 1
        GROUP BY from_address ORDER BY s DESC LIMIT 1
    """).fetchone()
    return row[0] if row else None


def main():
    conn = init_db()
    seed = sys.argv[1] if len(sys.argv) > 1 else pick_default_seed(conn)
    if not seed:
        print("No seed available. Run cache_to_db.py first.")
        return

    g = build_graph(conn, seed)
    print(f"=== TRACE GRAPH for {seed} ===")
    print(f"nodes: {len(g['nodes'])} | edges: {len(g['edges'])}\n")
    for n in sorted(g["nodes"], key=lambda x: x["hop"]):
        seed_mark = " (SEED)" if n["is_seed"] else ""
        print(f"  hop {n['hop']} | {n['risk_level']:>6} | fan-in {n['fan_in']:>2} | {n['id']}{seed_mark}")
    print()
    for e in g["edges"]:
        print(f"  {e['from']}  ->  {e['to']}   {e['amount']:,.2f} {e['token']}")
    conn.close()


if __name__ == "__main__":
    main()
