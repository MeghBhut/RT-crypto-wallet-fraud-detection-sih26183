/* =========================================================================
   app.js — TronTrace frontend logic
   Talks to the FastAPI backend (API_CONTRACT.md), renders the verdict card,
   the animated trace graph, and the risk map.
   ========================================================================= */

// Use 127.0.0.1, NOT localhost: on Windows 'localhost' can add a ~2s DNS delay
// (it tries IPv6 ::1 first and times out). 127.0.0.1 is instant.
const API = "http://127.0.0.1:8000";   // where the FastAPI backend runs

// ---- element refs ----
const $ = (id) => document.getElementById(id);
const form = $("searchForm"), input = $("addrInput"), btn = $("analyzeBtn");
const statusEl = $("status"), results = $("results");

// ---- small helpers ----
const short = (a) => (a && a.length > 14 ? `${a.slice(0, 8)}…${a.slice(-6)}` : a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function setStatus(msg, kind) {
  statusEl.textContent = msg || "";
  statusEl.className = "status" + (kind ? " " + kind : "");
}

// ---- main flow ----
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const address = input.value.trim();
  if (!address) return;

  btn.disabled = true;
  setStatus("Fetching transactions and tracing funds…", "busy");
  try {
    const res = await fetch(`${API}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address }),
    });
    const data = await res.json();

    if (!res.ok) {
      setStatus(data.message || "Analysis failed.", "err");
      return;
    }
    setStatus("");
    renderVerdict(data);
    renderDestinations(data.destinations || []);
    renderGraph(data.graph);
    results.hidden = false;
    loadHistory();                    // refresh the risk map
  } catch (err) {
    setStatus("Cannot reach the backend. Is it running on :8000?", "err");
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// VERDICT CARD
// ---------------------------------------------------------------------------
// blend cyan -> amber -> red from the LIVE theme tokens, driven by threat (0..1)
function hexToRgb(h) {
  h = h.replace("#", "").trim();
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function mix(a, b, t) { return a.map((v, i) => Math.round(v + (b[i] - v) * t)); }
function threatColor(t, el) {
  const cs = getComputedStyle(el);
  const cyan = hexToRgb(cs.getPropertyValue("--cyan") || "#0A8FA5");
  const warn = hexToRgb(cs.getPropertyValue("--warn") || "#C9821C");
  const danger = hexToRgb(cs.getPropertyValue("--danger") || "#C43D2E");
  const rgb = t <= 0.5 ? mix(cyan, warn, t / 0.5) : mix(warn, danger, (t - 0.5) / 0.5);
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

function renderVerdict(d) {
  // drive ONLY the verdict card's cyan->red shift from this wallet's threat
  const card = $("verdictCard");
  card.style.setProperty("--threat", d.threat);                    // for reference/debug
  card.style.setProperty("--threat-color", threatColor(d.threat, card));

  $("levelPill").textContent = d.risk_level;
  $("confBadge").textContent = `confidence: ${d.confidence.toLowerCase()}`;
  $("verdictAddr").textContent = d.address;

  // stats
  const s = d.stats;
  $("stats").innerHTML = [
    ["transactions", s.tx_count],
    ["fan-in", s.fan_in],
    ["fan-out", s.fan_out],
    ["in", fmt(s.total_in)],
    ["out", fmt(s.total_out)],
    ["threat", d.threat],
  ].map(([k, v]) => `<div class="stat"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");

  // reasons
  $("reasons").innerHTML = d.reasons.map((r) => {
    const clean = r.points === 0 ? " clean" : "";
    return `<li class="${clean}"><span class="pts">+${r.points}</span><span>${r.text}</span></li>`;
  }).join("");

  countUp($("scoreNum"), d.risk_score);
}

function fmt(n) {
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n;
}

function countUp(el, target) {
  const dur = 650, start = performance.now();
  function step(now) {
    const t = Math.min((now - start) / dur, 1);
    el.textContent = Math.round(target * (1 - Math.pow(1 - t, 3)));  // ease-out
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
  // fallback: guarantee the final value even if rAF was paused (tab backgrounded)
  setTimeout(() => { el.textContent = target; }, dur + 80);
}

// ---------------------------------------------------------------------------
// DESTINATIONS — where the traced money lands (exit / cash-out points)
// ---------------------------------------------------------------------------
function renderDestinations(dests) {
  const box = $("dests");
  if (!dests.length) {
    box.innerHTML = `<span class="text-dim">No exit point identified in the traced hops.</span>`;
    return;
  }
  box.innerHTML = dests.map((d) => {
    const isExch = d.kind === "exchange" || d.kind === "likely_exchange";
    const tag = d.kind === "exchange" ? "EXCHANGE"
      : d.kind === "likely_exchange" ? "LIKELY EXCHANGE" : "WALLET";
    const label = d.note ? d.note : "terminal wallet";
    return `<div class="dest ${isExch ? "exch" : ""}">
        <span class="dest-tag">${tag}</span>
        <span class="dest-body">
          <span class="mono dest-addr">${d.address}</span>
          <span class="text-dim dest-note">${label} · received ~${abbr(d.received)} · hop ${d.hop}</span>
        </span>
      </div>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// TRACE GRAPH (custom SVG, revealed hop-by-hop)
// ---------------------------------------------------------------------------
const SVGNS = "http://www.w3.org/2000/svg";
const el = (name, attrs = {}) => {
  const n = document.createElementNS(SVGNS, name);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
};

async function renderGraph(graph) {
  const svg = $("graph");
  svg.innerHTML = "";
  const empty = $("graphEmpty");

  const nodes = graph.nodes || [], edges = graph.edges || [];
  // if the only node is the seed with no edges, show the empty note
  empty.classList.toggle("show", edges.length === 0);

  const W = 760, H = 430, padX = 70, padY = 40;
  const maxHop = Math.max(...nodes.map((n) => n.hop), 0);
  const colGap = maxHop ? (W - 2 * padX) / maxHop : 0;

  // group nodes by hop, compute positions
  const byHop = {};
  nodes.forEach((n) => (byHop[n.hop] = byHop[n.hop] || []).push(n));
  const pos = {};
  Object.keys(byHop).forEach((h) => {
    const col = byHop[h], x = padX + h * colGap;
    col.forEach((n, i) => {
      const y = padY + (H - 2 * padY) * ((i + 1) / (col.length + 1));
      pos[n.id] = { x, y, node: n };
    });
  });

  const radius = (n) => Math.max(15, Math.min(34, 15 + n.fan_in * 1.6));

  // arrow marker
  const defs = el("defs");
  const marker = el("marker", { id: "arw", viewBox: "0 0 10 10", refX: "9", refY: "5",
    markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
  marker.appendChild(el("path", { d: "M0,0 L10,5 L0,10 z", fill: "var(--ink-dim)" }));
  defs.appendChild(marker); svg.appendChild(defs);

  // draw edges first (so nodes sit on top), grouped so we can reveal by target hop
  const edgeGroups = {};  // hop -> [g]
  edges.forEach((e) => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    const g = el("g", { class: "gedge" });
    const mx = (a.x + b.x) / 2;
    const path = el("path", {
      d: `M ${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x - radius(b.node) - 6} ${b.y}`,
      "marker-end": "url(#arw)",
    });
    g.appendChild(path);
    const amt = el("text", { class: "amt", x: mx, y: (a.y + b.y) / 2 - 5, "text-anchor": "middle" });
    amt.textContent = `${abbr(e.amount)} ${e.token}`;
    g.appendChild(amt);
    svg.appendChild(g);
    const hop = b.node.hop;
    (edgeGroups[hop] = edgeGroups[hop] || []).push(g);
  });

  // draw nodes, grouped by hop
  const nodeGroups = {};
  nodes.forEach((n) => {
    const p = pos[n.id];
    const isExch = n.kind === "exchange" || n.kind === "likely_exchange";
    const g = el("g", { class: `gnode node-${n.risk_level} kind-${n.kind}${n.is_seed ? " seed" : ""}` });
    g.appendChild(el("circle", { cx: p.x, cy: p.y, r: radius(n) }));
    const lbl = el("text", { class: "lbl", x: p.x, y: p.y + radius(n) + 14 });
    lbl.textContent = n.label;
    g.appendChild(lbl);
    const tagText = n.is_seed ? "SEED" : isExch ? "EXIT" : null;
    if (tagText) {
      const tag = el("text", { class: "seedtag", x: p.x, y: p.y - radius(n) - 6 });
      tag.textContent = tagText;
      g.appendChild(tag);
    }
    svg.appendChild(g);
    (nodeGroups[n.hop] = nodeGroups[n.hop] || []).push(g);
  });

  // ANIMATE: reveal hop 0, then its edges+nodes, then hop 1, ... (the "discovery" effect)
  for (let h = 0; h <= maxHop; h++) {
    (nodeGroups[h] || []).forEach((g) => g.classList.add("show"));
    (edgeGroups[h] || []).forEach((g) => g.classList.add("show"));
    await sleep(480);
  }
}

function abbr(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

// ---------------------------------------------------------------------------
// RISK MAP (scatter: x = risk, y = confidence)
// ---------------------------------------------------------------------------
async function loadHistory() {
  try {
    const res = await fetch(`${API}/api/history`);
    const data = await res.json();
    renderMap(data.items || []);
  } catch (e) { /* backend down — leave map as-is */ }
}

function renderMap(items) {
  const card = $("mapCard"), map = $("map");
  card.hidden = false;
  map.innerHTML = "";

  // horizontal bands for LOW / MEDIUM / HIGH confidence
  const rows = { HIGH: 0.18, MEDIUM: 0.5, LOW: 0.82 };
  ["HIGH", "MEDIUM", "LOW"].forEach((c) => {
    const band = document.createElement("div");
    band.className = "band";
    band.style.top = `${rows[c] * 100}%`;
    band.innerHTML = `<span>${c.toLowerCase()}</span>`;
    map.appendChild(band);
  });
  const xaxis = document.createElement("div");
  xaxis.className = "xaxis";
  xaxis.innerHTML = "<span>0</span><span>risk score</span><span>100</span>";
  map.appendChild(xaxis);

  if (!items.length) {
    const e = document.createElement("p");
    e.className = "map-empty"; e.textContent = "No wallets analyzed yet.";
    map.appendChild(e); return;
  }

  items.forEach((it) => {
    const dot = document.createElement("div");
    const cls = it.risk_level === "HIGH" ? "high" : it.risk_level === "MEDIUM" ? "med" : "low";
    dot.className = `mdot ${cls}`;
    dot.style.left = `${it.risk_score}%`;
    dot.style.bottom = `${(1 - rows[it.confidence]) * 100}%`;
    dot.title = `${short(it.address)}\nrisk ${it.risk_score} · ${it.confidence.toLowerCase()} confidence · threat ${it.threat}`;
    dot.addEventListener("click", () => { input.value = it.address; form.requestSubmit(); });
    map.appendChild(dot);
  });
}

// load the map on first paint (may be empty)
loadHistory();
