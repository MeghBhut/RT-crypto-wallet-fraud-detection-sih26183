"""
main.py  —  STEP 4: the FastAPI BACKEND. Implements API_CONTRACT.md.

It is thin ON PURPOSE: all the thinking lives in the engine + tracer we already
built and tested. This file just:
  1. receives an HTTP request,
  2. calls our existing functions,
  3. shapes the answer to match the contract,
  4. records history for the Risk Map.

Endpoints (see API_CONTRACT.md):
  GET  /api/health    -> is the server up?
  POST /api/analyze   -> verdict (score/level/confidence/threat/reasons/stats) + trace graph
  GET  /api/history   -> every analyzed wallet, for the risk x confidence scatter

Run it with:
    uvicorn main:app --reload
Then open http://localhost:8000/docs  (auto-generated, interactive API tester)
"""

import time
import json
import sqlite3

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# our own, already-tested building blocks
from cache_to_db import init_db, DB_FILE
from fraud_engine import analyze_wallet
from forward_trace import build_graph

app = FastAPI(title="TRON Fraud Detector", version="0.1.0")

# The frontend runs on a different port (e.g. 5500), so the browser needs CORS
# permission to call this API. For a hackathon we allow all origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# DB helper: open a connection with BOTH tables guaranteed to exist
# ---------------------------------------------------------------------------
def open_conn():
    conn = init_db()   # ensures the 'transactions' table exists, returns a connection
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_history (
            address      TEXT,
            risk_score   INTEGER,
            risk_level   TEXT,
            confidence   TEXT,
            threat       REAL,
            analyzed_at  INTEGER
        )
    """)
    # full-response cache so re-analyzing the same wallet (e.g. clicking a risk-map
    # dot) is INSTANT instead of re-fetching every hop from TronGrid.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS result_cache (
            address    TEXT PRIMARY KEY,
            payload    TEXT,
            cached_at  INTEGER
        )
    """)
    conn.commit()
    return conn


CACHE_TTL_MS = 15 * 60 * 1000   # a cached analysis is reused for 15 minutes


def valid_tron_address(addr):
    """TRON addresses are Base58, start with 'T', and are 34 characters long."""
    return isinstance(addr, str) and len(addr) == 34 and addr.startswith("T")


# ---------------------------------------------------------------------------
# request body shape for POST /api/analyze  (pydantic validates it for us)
# ---------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    address: str


# ---------------------------------------------------------------------------
# 1) GET /api/health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "tron-fraud-detector", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# 2) POST /api/analyze   (the main endpoint)
# ---------------------------------------------------------------------------
@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    address = req.address.strip()

    if not valid_tron_address(address):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_address",
                     "message": "Address must start with 'T' and be 34 characters."},
        )

    conn = open_conn()
    now = int(time.time() * 1000)
    try:
        # FAST PATH: return a recent cached result instead of re-tracing live.
        row = conn.execute(
            "SELECT payload, cached_at FROM result_cache WHERE address = ?", (address,)
        ).fetchone()
        if row and now - row[1] < CACHE_TTL_MS:
            return json.loads(row[0])

        # build_graph fetches live per hop + caches; if TronGrid is down it
        # degrades gracefully to whatever is already cached. Now also returns
        # 'destinations' — where the traced money lands (exit / cash-out points).
        g = build_graph(conn, address)

        # analyze reads the (now freshly cached) data -> verdict incl. threat
        res = analyze_wallet(conn, address)

        response = {
            "address": address,
            "risk_score": res["risk_score"],
            "risk_level": res["risk_level"],
            "confidence": res["confidence"],
            "confidence_reason": res["confidence_reason"],
            "threat": res["threat"],
            "reasons": res["reasons"],
            "stats": res["stats"],
            "destinations": g["destinations"],
            "graph": {"nodes": g["nodes"], "edges": g["edges"]},
        }

        # record for the Risk Map, and cache the full response for fast re-clicks
        conn.execute(
            "INSERT INTO analysis_history VALUES (?, ?, ?, ?, ?, ?)",
            (address, res["risk_score"], res["risk_level"],
             res["confidence"], res["threat"], now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO result_cache VALUES (?, ?, ?)",
            (address, json.dumps(response), now),
        )
        conn.commit()
        return response
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error",
                     "message": f"Something went wrong analyzing this wallet: {e}"},
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3) GET /api/history   (feeds the Risk Map)
# ---------------------------------------------------------------------------
@app.get("/api/history")
def history():
    conn = open_conn()
    # one row per address = its most recent analysis.
    # (SQLite returns the columns from the MAX(analyzed_at) row.)
    rows = conn.execute("""
        SELECT address, risk_score, risk_level, confidence, threat, MAX(analyzed_at)
        FROM analysis_history
        GROUP BY address
        ORDER BY MAX(analyzed_at) DESC
    """).fetchall()
    conn.close()

    items = [
        {"address": r[0], "risk_score": r[1], "risk_level": r[2],
         "confidence": r[3], "threat": r[4], "analyzed_at": r[5]}
        for r in rows
    ]
    return {"items": items}
