"""
seed_demo.py  —  inject a crafted high-risk wallet AND a laundering chain that
ends at a known exchange, so the demo tells a complete story:

    25 senders ->  [SEED]  ->  mule1  ->  mule2  ->  [EXCHANGE]  (cash-out)
                (hub+rapid              (layering chain the tracer follows)
                 +whale+layering)

Analyzing the seed shows: HIGH risk, RED card, a multi-hop graph, and a named
DESTINATION ("funds trace to Demo Exchange").

Be honest with judges: this is a synthetic test case that stacks every signal to
demonstrate full-signal detection and end-to-end attribution.

Run:  python seed_demo.py
Then analyze:  TDemoFraudHub111111111111111111111
"""

import random
from datetime import datetime, timezone

from cache_to_db import init_db, save_transactions
from fraud_engine import DEMO_EXCHANGE          # the labeled cash-out address

DEMO = "TDemoFraudHub" + "1" * 21               # 34 chars, the suspect wallet
assert len(DEMO) == 34
BASE_MS = 1787000000000
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def demo_addr(tag):
    random.seed(tag)
    return "T" + "".join(random.choice(B58) for _ in range(33))


def row(txid, frm, to, amount, ts_ms):
    return {
        "txid": txid, "queried_wallet": DEMO,
        "from_address": frm, "to_address": to,
        "amount": float(amount), "token": "USDT",
        "timestamp_ms": ts_ms,
        "datetime_utc": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
        "tx_type": "TriggerSmartContract", "success": 1, "raw_json": "{}",
    }


def build_rows():
    rows = []
    mule1, mule2 = demo_addr("mule-1"), demo_addr("mule-2")

    # 25 senders pay INTO the seed within ~50 min (hub + rapid-fire; #0 is the 1.2M whale)
    for i in range(25):
        sender = demo_addr(f"sender-{i}")
        ts = BASE_MS + i * 120_000
        amount = 1_200_000 if i == 0 else random.randint(2_000, 40_000)
        rows.append(row(f"demo-in-{i}", sender, DEMO, amount, ts))

    # layering CHAIN the tracer will follow: seed -> mule1 -> mule2 -> exchange
    t1, t2, t3 = BASE_MS + 3_600_000, BASE_MS + 7_200_000, BASE_MS + 10_800_000
    rows.append(row("demo-chain-1", DEMO,  mule1, 1_150_000, t1))   # layering (in 24h)
    rows.append(row("demo-chain-2", mule1, mule2, 1_100_000, t2))
    rows.append(row("demo-chain-3", mule2, DEMO_EXCHANGE, 1_050_000, t3))  # -> cash-out

    # a few smaller fan-out from the seed (distribution pattern)
    for i in range(4):
        recip = demo_addr(f"recip-{i}")
        rows.append(row(f"demo-out-{i}", DEMO, recip, random.randint(5_000, 60_000),
                        t1 + (i + 1) * 300_000))
    return rows


def main():
    conn = init_db()
    # clean slate: remove old demo rows AND any cached result for these addresses
    conn.execute("DELETE FROM transactions WHERE txid LIKE 'demo-%'")
    try:
        conn.execute("DELETE FROM result_cache")   # table may not exist yet
    except Exception:
        pass
    conn.commit()

    added = save_transactions(conn, build_rows())
    conn.close()
    print(f"Seeded suspect wallet : {DEMO}")
    print(f"Cash-out exchange     : {DEMO_EXCHANGE}")
    print(f"Inserted {added} rows (chain: seed -> mule1 -> mule2 -> exchange).")
    print("Analyze the suspect in the app -> HIGH risk, RED card, named destination.")


if __name__ == "__main__":
    main()
