# API_CONTRACT.md — the spine between frontend and backend

> This is the agreement. Once both sides build to this, frontend and backend can
> be developed independently and still fit together. Change this file first if
> the shape needs to change — never let code and contract drift apart.

**Base URL (local dev):** `http://localhost:8000`
**Format:** all requests/responses are JSON (`Content-Type: application/json`).
**Auth:** none for the hackathon (single local user).

---

## What the UI needs, and which endpoint feeds it

| UI piece | Fed by |
|---|---|
| Result card (score / level / confidence / reasons) | `POST /api/analyze` |
| Animated trace graph (nodes + edges, revealed hop-by-hop) | `POST /api/analyze` → `graph` |
| Risk Map scatter (all analyzed wallets, risk × confidence) | `GET /api/history` |
| Health check (is the server up?) | `GET /api/health` |

---

## 1) `GET /api/health`

Liveness check. Frontend can ping this on load.

**Response 200**
```json
{ "status": "ok", "service": "tron-fraud-detector", "version": "0.1.0" }
```

---

## 2) `POST /api/analyze`  ← the main endpoint

Analyze one wallet: returns the verdict AND the trace graph for animation.

**Request**
```json
{ "address": "TNeorv8DUs4rX3oF4rCpcmBXJ95Pqm9umQ" }
```

**Field rules**
- `address` (string, required): a TRON address. Starts with `T`, 34 chars.

**Response 200**
```json
{
  "address": "TNeorv8DUs4rX3oF4rCpcmBXJ95Pqm9umQ",
  "risk_score": 25,
  "risk_level": "LOW",
  "confidence": "MEDIUM",
  "confidence_reason": "based on 12 transactions seen for this wallet",
  "threat": 0.19,
  "reasons": [
    { "points": 25, "text": "~200,000 units passed straight through within 24h (layering)" }
  ],
  "stats": {
    "tx_count": 12,
    "fan_in": 9,
    "fan_out": 3,
    "total_in": 0.0,
    "total_out": 380125.06
  },
  "destinations": [
    { "address": "TExchangeCashOut111111111111111111", "label": "TExcha…1111", "kind": "exchange", "note": "Demo Exchange (cash-out)", "received": 1050000.0, "hop": 3 }
  ],
  "graph": {
    "nodes": [
      { "id": "TNeorv8DUs4rX3oF4rCpcmBXJ95Pqm9umQ", "label": "TNeorv8…9umQ", "hop": 0, "is_seed": true,  "risk_level": "LOW",  "fan_in": 9, "kind": "seed",   "note": null, "terminal": false },
      { "id": "TQwJnszBVfgKBoYvnxQvu8xiQuHZRD6sjc", "label": "TQwJns…6sjc", "hop": 1, "is_seed": false, "risk_level": "LOW",  "fan_in": 4, "kind": "wallet", "note": null, "terminal": true }
    ],
    "edges": [
      { "from": "TNeorv8DUs4rX3oF4rCpcmBXJ95Pqm9umQ", "to": "TQwJnszBVfgKBoYvnxQvu8xiQuHZRD6sjc", "amount": 200000.0, "token": "USDT", "timestamp_ms": 1787851965000 }
    ]
  }
}
```

**Field meanings (so the frontend knows what to draw)**
- `risk_level`: `"LOW" | "MEDIUM" | "HIGH"` → paint card GREEN / YELLOW / RED.
- `confidence`: `"LOW" | "MEDIUM" | "HIGH"` → show as a small badge; low = "limited data".
- `threat`: **float 0.0–1.0** → drives the cyan→red theme blend (see "Theme mapping"
  below). This is the ONLY color-driving number; the backend never sends hex colors.
- `reasons[]`: list, each `{points, text}` → render as bullet list ("why").
- `destinations[]`: **where the traced money lands** (exit / cash-out points), ranked
  exchanges-first then by amount. Each: `{address, label, kind, note, received, hop}`.
  `kind` = `"exchange" | "likely_exchange" | "wallet"`; `note` is the human label
  (e.g. "Demo Exchange (cash-out)") or null. Render as a panel; highlight exchanges.
- `graph.nodes[].kind`: `"seed" | "exchange" | "likely_exchange" | "wallet"` → style the
  node (seed highlighted, exchange gold ring). `terminal`: true if money stops there.
- `graph.nodes[]`:
  - `id`: the address (unique key).
  - `label`: short display form (frontend may also shorten itself).
  - `hop`: distance from the seed (0 = searched wallet). **Animate by hop: reveal hop 0, then 1, then 2…**
  - `is_seed`: true for the searched wallet → draw it bigger/highlighted.
  - `risk_level`: color the node.
  - `fan_in`: size the node (bigger = more senders = more hub-like).
- `graph.edges[]`: `from`/`to` are node ids; `amount` + `token` label the arrow; `timestamp_ms` for ordering.

**Errors**
```json
// 400 — malformed address
{ "error": "invalid_address", "message": "Address must start with 'T' and be 34 characters." }
```
```json
// 502 — TronGrid unreachable / rate-limited (we tried to fetch and failed)
{ "error": "upstream_unavailable", "message": "Could not reach TronGrid. Showing cached data only." }
```
```json
// 500 — anything unexpected
{ "error": "internal_error", "message": "Something went wrong analyzing this wallet." }
```

---

## 3) `GET /api/history`  ← feeds the Risk Map

Every wallet analyzed so far, as points for the risk × confidence scatter.

**Response 200**
```json
{
  "items": [
    { "address": "TNeorv8DUs4rX3oF4rCpcmBXJ95Pqm9umQ", "risk_score": 25, "risk_level": "LOW",  "confidence": "MEDIUM", "threat": 0.19, "analyzed_at": 1787851969284 },
    { "address": "TQwJnszBVfgKBoYvnxQvu8xiQuHZRD6sjc", "risk_score": 25, "risk_level": "LOW",  "confidence": "LOW",    "threat": 0.13, "analyzed_at": 1787851970111 }
  ]
}
```

- Plot each item: **x = risk_score (0–100), y = confidence (LOW/MED/HIGH)**.
- Color by `risk_level`. Top-right = high risk + high confidence = the "red zone".

---

## Theme mapping — how `threat` drives cyan → red (contract-level agreement)

The backend sends a single number; the frontend turns it into color. Both sides
agree on this so the theme stays "real" and consistent.

**Backend computes `threat` like this:**
```
risk_norm   = risk_score / 100                          # 0.0 – 1.0
conf_weight = { "LOW": 0.5, "MEDIUM": 0.75, "HIGH": 1.0 }[confidence]
threat      = round(risk_norm * conf_weight, 2)         # 0.0 (safe) – 1.0 (danger)
```
Rationale: risk sets how bad; confidence scales how far we commit to red. A
high-risk / low-confidence wallet stays orange, not full red — we don't scream
red on thin data.

**Frontend maps `threat` to color** (design lives in CSS, not here):
- Sets a CSS variable `--threat: <value>` (0–1) on the result container.
- A derived token blends the theme's safe color and a new danger token, e.g.
  `--threat-color: color-mix(in oklab, var(--cyan), var(--danger), calc(var(--threat) * 100%))`.
- Used on: result-card border/glow, the risk score number, and seed node in the graph.
- `threat` bands (suggested): `< 0.34` cyan · `0.34–0.66` amber · `> 0.66` red.

The backend NEVER sends hex colors. `threat` is the only presentation-driving
number, and it is derived purely from `risk_score` + `confidence`.

## Notes / decisions locked here
- **No geographic map** — blockchain addresses have no location; the "map" is the
  risk × confidence scatter (`/api/history`) plus the trace graph. This is honest data.
- **Graph, not clustering** — the trace is shown as a node-link graph; nodes may be
  sized by `fan_in` and colored by risk, but we do NOT claim entity clustering.
  True address clustering is deferred to the separate Bitcoin engine/version.
- **One call, everything** — `/api/analyze` returns verdict + graph together so a
  single search populates both the card and the animated graph.

---

## Status codes summary
| Code | Meaning |
|---|---|
| 200 | success |
| 400 | bad address format |
| 422 | request body not valid JSON / missing `address` |
| 502 | TronGrid fetch failed (may still return cached partial) |
| 500 | unexpected server error |
