"""
seed_demo.py  —  inject ONE crafted high-risk wallet into tron_cache.db so the
demo has a dramatic RED, animated result (and so you can SEE the theme shift).

Real fraud wallets that stack many signals are rare in a small cache, so for a
reliable demo we synthesize one that trips every rule:
  * HUB        — 25 distinct senders pay into it
  * RAPID-FIRE — those 25 arrive inside ~50 minutes (bot-like burst)
  * LAYERING   — a ~1.2M inflow leaves again (~1.15M) within 24h
  * WHALE      — total moved is well over 1,000,000 units
=> risk ~100, confidence HIGH, threat ~1.0  (full red)

This is HONEST for a demo as long as you TELL judges it's a synthetic test case:
"we seed a known-bad wallet to demonstrate a full-signal detection."

Run it with:
    python seed_demo.py
Then analyze this address in the app:
    TDemoFraudHub1111111111111111111111
"""

import json
import random
from datetime import datetime, timezone

from cache_to_db import init_db, save_transactions

DEMO = "TDemoFraudHub" + "1" * 21   # exactly 34 chars, starts with T (API requires len==34)
assert len(DEMO) == 34, f"demo address must be 34 chars, got {len(DEMO)}"
BASE_MS = 1787000000000                        # arbitrary fixed start time
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def demo_addr(tag):
    """Make a deterministic, valid-length (34-char) T-address for a fake party."""
    random.seed(tag)
    return "T" + "".join(random.choice(B58) for _ in range(33))


def row(txid, frm, to, amount, ts_ms):
    return {
        "txid": txid,
        "queried_wallet": DEMO,
        "from_address": frm,
        "to_address": to,
        "amount": float(amount),
        "token": "USDT",
        "timestamp_ms": ts_ms,
        "datetime_utc": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
        "tx_type": "TriggerSmartContract",
        "success": 1,
        "raw_json": "{}",
    }


def build_rows():
    rows = []

    # --- 25 senders pay INTO the demo wallet within ~50 minutes (hub + rapid-fire) ---
    for i in range(25):
        sender = demo_addr(f"sender-{i}")
        ts = BASE_MS + i * 120_000          # 2 minutes apart -> 25 in ~50 min
        amount = 1_200_000 if i == 0 else random.randint(2_000, 40_000)
        rows.append(row(f"demo-in-{i}", sender, DEMO, amount, ts))

    # --- the big 1.2M inflow arrived at BASE_MS (i==0). It LEAVES within 24h (layering) ---
    big_out_ts = BASE_MS + 3_600_000        # 1 hour later (well within 24h)
    rows.append(row("demo-out-big", DEMO, demo_addr("mule-0"), 1_150_000, big_out_ts))

    # --- 7 more outflows to different recipients (fan-out / distribution) ---
    for i in range(7):
        recip = demo_addr(f"recip-{i}")
        ts = big_out_ts + (i + 1) * 300_000
        rows.append(row(f"demo-out-{i}", DEMO, recip, random.randint(5_000, 60_000), ts))

    return rows


def main():
    conn = init_db()
    # clear any previous demo rows so re-seeding is always a clean slate
    conn.execute("DELETE FROM transactions WHERE txid LIKE 'demo-%'")
    conn.commit()
    rows = build_rows()
    added = save_transactions(conn, rows)
    total = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE queried_wallet = ?", (DEMO,)
    ).fetchone()[0]
    conn.close()

    print(f"Seeded demo wallet: {DEMO}")
    print(f"Inserted {added} new rows ({total} total for this wallet).")
    print("Now analyze that address in the app — expect HIGH risk, HIGH confidence, RED card.")


if __name__ == "__main__":
    main()
