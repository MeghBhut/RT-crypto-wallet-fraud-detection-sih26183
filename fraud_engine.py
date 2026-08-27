"""
fraud_engine.py  —  STEP 3d: the BRAIN. Turn signals into a risk SCORE + REASONS.

For a given wallet, we read its cached transactions and run a set of transparent
rules. Each rule that fires ADDS points and APPENDS a plain-English reason.
The output is never a black box -- every point has an explanation.

We report THREE things (this is the important design idea):
  * risk_score  (0-100)   -> HOW suspicious the wallet is
  * risk_level  (LOW/MED/HIGH)
  * confidence  (LOW/MED/HIGH) -> HOW SURE we are, based on how much data we have

A high score on very little data is flagged LOW confidence, so we never overclaim.

Run it with:
    python fraud_engine.py                # analyzes the biggest sender in cache
    python fraud_engine.py TXyz...addr    # analyze any wallet
"""

import sys
import sqlite3

# ---------------------------------------------------------------------------
# THRESHOLDS  (kept here, named, so we can DEFEND every number to a judge)
# ---------------------------------------------------------------------------
FANIN_MIN        = 5          # >= this many distinct senders looks hub-like
FANOUT_MIN       = 5          # >= this many distinct recipients looks like spraying
RAPID_COUNT      = 20         # >= this many tx inside...
RAPID_WINDOW_MS  = 3600_000   # ...a 1-hour window = rapid-fire (bot-like)
LAYER_MIN_AMOUNT = 1000.0     # a "big" transfer worth tracking for pass-through
LAYER_MATCH_PCT  = 0.15       # in-amount vs out-amount within 15% = "same money"
LAYER_WINDOW_MS  = 24 * 3600_000   # ...and it left within 24h of arriving
WHALE_TOTAL      = 1_000_000  # total moved above this = whale-level scrutiny

# score -> level bands
MED_BAND, HIGH_BAND = 40, 70

# confidence bands, based on how many transactions we actually have for the wallet
CONF_MED_TX, CONF_HIGH_TX = 10, 30

DB_FILE = "tron_cache.db"


# ---------------------------------------------------------------------------
# small helper: max number of transactions inside any sliding time window
# ---------------------------------------------------------------------------
def max_in_window(timestamps_ms, window_ms):
    ts = sorted(t for t in timestamps_ms if t)
    best, left = 0, 0
    for right in range(len(ts)):
        while ts[right] - ts[left] > window_ms:
            left += 1
        best = max(best, right - left + 1)
    return best


# ---------------------------------------------------------------------------
# THE ENGINE: analyze one wallet -> a result dictionary
# ---------------------------------------------------------------------------
def analyze_wallet(conn, wallet):
    # pull this wallet's incoming and outgoing transfers from the cache
    incoming = conn.execute("""
        SELECT from_address, amount, timestamp_ms FROM transactions
        WHERE to_address = ? AND success = 1
    """, (wallet,)).fetchall()
    outgoing = conn.execute("""
        SELECT to_address, amount, timestamp_ms FROM transactions
        WHERE from_address = ? AND success = 1
    """, (wallet,)).fetchall()

    tx_count = len(incoming) + len(outgoing)
    fan_in  = len({r[0] for r in incoming})
    fan_out = len({r[0] for r in outgoing})
    total_in  = sum(r[1] for r in incoming)
    total_out = sum(r[1] for r in outgoing)

    score = 0
    reasons = []   # each item: (points, human_text)

    # --- RULE 1: hub / high fan-in ---
    if fan_in >= FANIN_MIN:
        pts = min(30, fan_in * 2)
        score += pts
        reasons.append((pts, f"Receives from {fan_in} distinct senders (hub-like collector)"))

    # --- RULE 2: distribution / high fan-out ---
    if fan_out >= FANOUT_MIN:
        pts = min(20, fan_out * 2)
        score += pts
        reasons.append((pts, f"Sends to {fan_out} distinct recipients (distribution pattern)"))

    # --- RULE 3: rapid-fire velocity ---
    all_ts = [r[2] for r in incoming] + [r[2] for r in outgoing]
    burst = max_in_window(all_ts, RAPID_WINDOW_MS)
    if burst >= RAPID_COUNT:
        score += 25
        reasons.append((25, f"{burst} transactions within one hour (automated / bot-like)"))

    # --- RULE 4: pass-through / layering (big money in, ~same out within 24h) ---
    layered = None
    for _, in_amt, in_ts in incoming:
        if in_amt < LAYER_MIN_AMOUNT or not in_ts:
            continue
        for _, out_amt, out_ts in outgoing:
            if not out_ts:
                continue
            same_money = abs(out_amt - in_amt) <= in_amt * LAYER_MATCH_PCT
            in_window  = in_ts <= out_ts <= in_ts + LAYER_WINDOW_MS
            if same_money and in_window:
                layered = in_amt
                break
        if layered:
            break
    if layered:
        score += 25
        reasons.append((25, f"~{layered:,.0f} units passed straight through within 24h (layering)"))

    # --- RULE 5: whale flow ---
    moved = max(total_in, total_out)
    if moved >= WHALE_TOTAL:
        score += 10
        reasons.append((10, f"Large volume moved (~{moved:,.0f} units) — elevated scrutiny"))

    # cap and classify
    score = min(score, 100)
    level = "HIGH" if score >= HIGH_BAND else "MEDIUM" if score >= MED_BAND else "LOW"

    # confidence: how much data backs this judgment?
    if tx_count >= CONF_HIGH_TX:
        confidence = "HIGH"
    elif tx_count >= CONF_MED_TX:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    conf_reason = f"based on {tx_count} transactions seen for this wallet"

    if not reasons:
        reasons.append((0, "No suspicious patterns detected in available data"))

    return {
        "wallet": wallet,
        "risk_score": score,
        "risk_level": level,
        "confidence": confidence,
        "confidence_reason": conf_reason,
        "reasons": [{"points": p, "text": t} for p, t in reasons],
        "stats": {
            "tx_count": tx_count, "fan_in": fan_in, "fan_out": fan_out,
            "total_in": round(total_in, 2), "total_out": round(total_out, 2),
        },
    }


# ---------------------------------------------------------------------------
# pretty printer (so we can eyeball the result in the terminal)
# ---------------------------------------------------------------------------
def print_report(res):
    light = {"LOW": "GREEN", "MEDIUM": "YELLOW", "HIGH": "RED"}[res["risk_level"]]
    print("=" * 64)
    print(f"WALLET     : {res['wallet']}")
    print(f"RISK SCORE : {res['risk_score']}/100   ->  {res['risk_level']} ({light})")
    print(f"CONFIDENCE : {res['confidence']}  ({res['confidence_reason']})")
    s = res["stats"]
    print(f"STATS      : {s['tx_count']} tx | fan-in {s['fan_in']} | fan-out {s['fan_out']} "
          f"| in {s['total_in']:,} | out {s['total_out']:,}")
    print("-" * 64)
    print("WHY (each reason = points added):")
    for r in res["reasons"]:
        print(f"   +{r['points']:>3}  {r['text']}")
    print("=" * 64)


def pick_default_seed(conn):
    row = conn.execute("""
        SELECT from_address, SUM(amount) s FROM transactions
        WHERE from_address IS NOT NULL AND success = 1
        GROUP BY from_address ORDER BY s DESC LIMIT 1
    """).fetchone()
    return row[0] if row else None


def main():
    conn = sqlite3.connect(DB_FILE)
    wallet = sys.argv[1] if len(sys.argv) > 1 else pick_default_seed(conn)
    if not wallet:
        print("No wallet to analyze. Run cache_to_db.py first.")
        return
    print_report(analyze_wallet(conn, wallet))
    conn.close()


if __name__ == "__main__":
    main()
