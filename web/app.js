/**
 * Orographic Arena — AI Options Trading Dashboard
 * No game loop. Direct AI recommendations → Tradier execution.
 */

const SNAPSHOT_SOURCE = "./data/latest_run.json";

// ── Formatting helpers ──────────────────────────────────────────────────────

function money(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "--";
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function pct(value, digits = 1) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "--";
  return `${(n * 100).toFixed(digits)}%`;
}

function integer(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "--";
  return n.toLocaleString("en-US");
}

function signed(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "--";
  const formatted = money(Math.abs(n));
  return n >= 0 ? `+${formatted}` : `-${formatted}`;
}

function formatTs(value) {
  if (!value) return "No timestamp";
  const d = new Date(value);
  if (isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
    timeZoneName: "short",
  }).format(d);
}

function timeAgo(date) {
  const seconds = Math.floor((new Date() - new Date(date)) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function toneClass(value) {
  return String(value).toLowerCase() === "call" ? "is-call" : "is-put";
}

function regimeToneClass(mode) {
  if (String(mode).toLowerCase() === "risk_on")  return "is-call";
  if (String(mode).toLowerCase() === "risk_off") return "is-put";
  return "is-neutral";
}

function sentenceList(notes, fallback) {
  if (Array.isArray(notes) && notes.length) return notes.join(". ");
  return fallback;
}

// ── Session & Auth ──────────────────────────────────────────────────────────

let SESSION = null;

async function loadSession() {
  try {
    const r = await fetch("/api/session", { cache: "no-store" });
    if (!r.ok) return { authenticated: false, session: null };
    return r.json();
  } catch {
    return { authenticated: false, session: null };
  }
}

function bindLogout() {
  const btn = document.getElementById("logout-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Signing out…";
    try {
      await fetch("/api/logout", { method: "POST", headers: { "content-type": "application/json" } });
      window.location.href = "/login";
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Log Out";
    }
  });
}

// ── Account / Broker ────────────────────────────────────────────────────────

let BROKER_STATE = {
  configured: false,
  mode: "offline",
  liveTradingEnabled: false,
  balances: null,
  positions: [],
  orders: [],
};

async function loadAccount() {
  try {
    const r = await fetch("/api/tradier/account", { cache: "no-store" });
    const data = await r.json();
    if (data.ok && data.broker) {
      BROKER_STATE = {
        ...BROKER_STATE,
        configured:         data.broker.configured,
        mode:               data.broker.mode || data.broker.environment || "offline",
        liveTradingEnabled: data.broker.liveTradingEnabled,
        balances:           data.balances || data.broker.balances || null,
        positions:          data.positions || data.broker.positions || [],
        orders:             data.orders || data.broker.orders || [],
        maxContracts:       data.broker.maxContracts || 3,
      };
    }
  } catch {
    // Leave defaults; ribbon will show --
  }
  renderRibbon();
  renderPositions();
  renderOrders();
  bindPositionsTable();
}

function renderRibbon() {
  const bal = BROKER_STATE.balances || {};
  setText("ribbon-equity",    money(bal.total_equity));
  setText("ribbon-obp",       money(bal.option_buying_power));
  setText("ribbon-cash",      money(bal.total_cash));
  const pl = bal.close_pl ?? bal.open_pl ?? null;
  const plEl = document.getElementById("ribbon-pl");
  if (plEl) {
    plEl.textContent = pl !== null ? signed(pl) : "--";
    plEl.className = "ribbon-stat-value" +
      (pl > 0 ? " is-positive" : pl < 0 ? " is-negative" : "");
  }
  setText("ribbon-positions", String(BROKER_STATE.positions.length));

  const modeEl = document.getElementById("ribbon-broker-mode");
  if (modeEl) {
    const m = BROKER_STATE.mode || "offline";
    const label = BROKER_STATE.configured
      ? (m === "live" ? "LIVE" : "SANDBOX")
      : "OFFLINE";
    modeEl.textContent = label;
    modeEl.className = "ribbon-mode-pill" +
      (m === "live" && BROKER_STATE.configured ? " is-live" :
       BROKER_STATE.configured ? " is-sandbox" : " is-offline");
  }
}

function renderPositions() {
  const tbody = document.getElementById("positions-tbody");
  if (!tbody) return;
  const rows = BROKER_STATE.positions || [];
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--text-muted);font-family:var(--font-data);font-size:.78rem;">No open positions found.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map((pos) => {
    const cv  = pos.current_value ?? null;
    const cb  = pos.cost_basis ?? null;
    const pl  = cv !== null && cb !== null ? cv - cb : null;
    const sym = String(pos.symbol || "");
    const isOpt = sym.length > 6;
    const tone = isOpt ? (sym.includes("C") ? "is-call-cell" : "is-put-cell") : "";
    const actionCell = isOpt
      ? `<button class="mini-action close-position-btn" type="button" data-contract="${sym}" data-qty="${pos.quantity}">Close</button>`
      : ``;
    return `<tr>
      <td class="${tone}">${sym}</td>
      <td>${integer(pos.quantity)}</td>
      <td class="is-num">${money(cb)}</td>
      <td class="is-num">${money(cv)}</td>
      <td class="is-num ${pl !== null ? (pl >= 0 ? "is-positive" : "is-negative") : ""}">${pl !== null ? signed(pl) : "--"}</td>
      <td>${pos.date_acquired ? pos.date_acquired.slice(0, 10) : "--"}</td>
      <td style="text-align:right;">${actionCell}</td>
    </tr>`;
  }).join("");
}

function renderOrders() {
  const tbody = document.getElementById("orders-tbody");
  if (!tbody) return;
  const rows = (BROKER_STATE.orders || []).slice(0, 10);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-muted);font-family:var(--font-data);font-size:.78rem;">No recent orders found.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map((o) => {
    const isBuy = String(o.side || "").includes("buy");
    return `<tr>
      <td><span style="font-family:var(--font-data);font-size:.65rem;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border-radius:99px;background:rgba(255,255,255,.04);border:1px solid var(--border)">${o.status || "open"}</span></td>
      <td style="font-family:var(--font-data);font-size:.72rem;word-break:break-all">${o.option_symbol || o.symbol || "--"}</td>
      <td class="${isBuy ? "is-call-cell" : "is-put-cell"}">${o.side || "--"}</td>
      <td class="is-num">${integer(o.quantity)}</td>
      <td class="is-num">${o.price ? money(o.price) : "--"}</td>
      <td class="is-num">${o.avg_fill_price ? money(o.avg_fill_price) : "--"}</td>
      <td style="font-family:var(--font-data);font-size:.7rem;color:var(--text-muted)">${o.create_date ? o.create_date.slice(0, 10) : "--"}</td>
    </tr>`;
  }).join("");
}

// ── Snapshot / Board ────────────────────────────────────────────────────────

let SNAPSHOT = null;
let LIVE_QUOTES = new Map();

async function loadSnapshot() {
  const r = await fetch(SNAPSHOT_SOURCE, { cache: "no-store" });
  SNAPSHOT = await r.json();
  return SNAPSHOT;
}

async function refreshQuotes(contractSymbols) {
  if (!contractSymbols.length) return;
  try {
    const url = `/api/tradier/quotes?symbols=${encodeURIComponent(contractSymbols.join(","))}`;
    const r = await fetch(url, { cache: "no-store" });
    const data = await r.json();
    if (data.ok && Array.isArray(data.quotes)) {
      data.quotes.forEach((q) => LIVE_QUOTES.set(q.symbol, q));
    }
  } catch {
    // Non-fatal; fall back to snapshot premium
  }
}

// ── AI Rationale ────────────────────────────────────────────────────────────

async function fetchRationale(candidate, regime) {
  try {
    const r = await fetch("/api/ai/explain", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ candidate, regime }),
    });
    const data = await r.json();
    return data.ok ? data.rationale : null;
  } catch {
    return null;
  }
}

// ── Card Rendering ──────────────────────────────────────────────────────────

function scoreBarWidth(score) {
  const s = Math.max(0, Math.min(1, Number(score || 0)));
  return `${Math.round(s * 100)}%`;
}

function buildTradeCard(candidate, regime, lane) {
  const role       = SESSION?.session?.role || "viewer";
  const isAdmin    = role === "admin";
  const isLive     = lane === "live";
  const tone       = toneClass(candidate.option_type);
  const dir        = candidate.option_type?.toUpperCase();
  const liveQuote  = LIVE_QUOTES.get(candidate.contract_symbol);
  const displayBid = liveQuote?.bid ?? candidate.bid;
  const displayAsk = liveQuote?.ask ?? candidate.ask ?? candidate.premium;
  const displayIv  = liveQuote?.greeks?.mid_iv
    ? Number(liveQuote.greeks.mid_iv * 100).toFixed(0) + "%"
    : candidate.implied_volatility
      ? Number(candidate.implied_volatility * 100).toFixed(0) + "%"
      : "--";

  const card = document.createElement("div");
  card.className = `trade-card ${tone}${!isLive ? " is-shadow" : ""}`;
  card.dataset.contractSymbol = candidate.contract_symbol;
  card.dataset.lane = lane;

  card.innerHTML = `
    <div class="card-art">
      <div class="card-art-glow"></div>
      <span class="card-symbol-giant">${candidate.symbol}</span>
      <div class="card-gem card-score-gem" title="Forge score">
        ${Number(candidate.forge_score || 0).toFixed(2)}
      </div>
      <div class="card-gem card-gem-direction" title="Direction">${dir}</div>
    </div>
    <div class="card-body">
      <div class="card-ticker-row">
        <span class="card-ticker">${candidate.symbol}</span>
        <span class="card-lane-badge ${isLive ? "is-live" : "is-shadow"}">${isLive ? "Live" : "Shadow"}</span>
      </div>
      <p class="card-contract">${candidate.contract_symbol}</p>

      <div class="card-score-bar-wrap">
        <div class="card-score-bar-label">
          <span>Conviction</span>
          <span>${Number(candidate.forge_score || 0).toFixed(2)}</span>
        </div>
        <div class="card-score-bar-track">
          <div class="card-score-bar-fill" style="width:${scoreBarWidth(candidate.forge_score)}"></div>
        </div>
      </div>

      <div class="card-stats">
        <div class="card-stat">
          <span class="card-stat-label">Strike</span>
          <span class="card-stat-value">$${Number(candidate.strike).toFixed(0)}</span>
        </div>
        <div class="card-stat">
          <span class="card-stat-label">Expiry</span>
          <span class="card-stat-value">${candidate.expiry}</span>
        </div>
        <div class="card-stat">
          <span class="card-stat-label">Ask</span>
          <span class="card-stat-value">${money(displayAsk)}</span>
        </div>
        <div class="card-stat">
          <span class="card-stat-label">Bid</span>
          <span class="card-stat-value">${money(displayBid)}</span>
        </div>
        <div class="card-stat">
          <span class="card-stat-label">Delta</span>
          <span class="card-stat-value">${candidate.delta ? Number(candidate.delta).toFixed(2) : "--"}</span>
        </div>
        <div class="card-stat">
          <span class="card-stat-label">IV</span>
          <span class="card-stat-value">${displayIv}</span>
        </div>
        <div class="card-stat">
          <span class="card-stat-label">Breakeven</span>
          <span class="card-stat-value">${pct(candidate.breakeven_move_pct)}</span>
        </div>
        <div class="card-stat">
          <span class="card-stat-label">Exp. Return</span>
          <span class="card-stat-value">${pct(candidate.expected_return_pct, 0)}</span>
        </div>
      </div>

      <div id="rationale-${candidate.contract_symbol.replace(/[^a-z0-9]/gi, "_")}" class="card-rationale is-loading">
        Asking the Council…
      </div>

      <div class="card-actions">
        <button
          class="primary-action ${tone} card-preview-btn"
          type="button"
          data-contract="${candidate.contract_symbol}"
          data-symbol="${candidate.symbol}"
          data-lane="${lane}"
          data-ask="${displayAsk || ""}"
          data-alloc="${candidate.allocation_weight || 1.0}"
          ${!isLive && BROKER_STATE.mode === "live" ? "disabled title='Live mode only accepts live-board contracts'" : ""}
        >Preview Trade</button>

        ${isAdmin ? `
        <button
          class="danger-action card-execute-btn"
          type="button"
          data-contract="${candidate.contract_symbol}"
          data-symbol="${candidate.symbol}"
          data-lane="${lane}"
          data-ask="${displayAsk || ""}"
          data-alloc="${candidate.allocation_weight || 1.0}"
          ${!isLive && BROKER_STATE.mode === "live" ? "disabled title='Live mode only'" : ""}
        >Execute Trade</button>
        ` : ""}
      </div>

      ${candidate.notes?.length ? `
        <p class="card-notes">${candidate.notes.join(" · ")}</p>
      ` : ""}
    </div>
  `;

  return card;
}

function buildEmptyCard(title, body) {
  const card = document.createElement("div");
  card.className = "trade-card";
  card.style.cssText = "padding:32px;text-align:center;";
  card.innerHTML = `
    <p style="font-family:var(--font-ui);font-size:.85rem;color:var(--text-muted);margin-bottom:8px;">${title}</p>
    <p style="font-family:var(--font-data);font-size:.75rem;color:var(--text-muted);">${body}</p>
  `;
  return card;
}

// ── Board Rendering ─────────────────────────────────────────────────────────

function rowHtml(title, body, tone, slotLabel) {
  return `<div class="mini-row ${tone}"><span class="mini-slot">${slotLabel}</span><strong>${title}</strong><span class="muted">${body}</span></div>`;
}

function summaryItemHtml(label, value) {
  return `<div class="summary-item"><span class="summary-label">${label}</span><span class="summary-value">${value}</span></div>`;
}

function pctOrDash(value, digits = 1) {
  const num = Number(value);
  return Number.isFinite(num) ? `${(num * 100).toFixed(digits)}%` : "—";
}

function ratioOrDash(numerator, denominator, digits = 1) {
  const num = Number(numerator);
  const den = Number(denominator);
  if (!Number.isFinite(num) || !Number.isFinite(den) || den === 0) return "—";
  return `${((num / den) * 100).toFixed(digits)}%`;
}

function estimateTradeValue(order, fallbackQty, fallbackPrice) {
  const explicit = Number(order?.order_cost ?? order?.cost);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  const qty = Number(order?.quantity ?? fallbackQty);
  const price = Number(order?.price ?? fallbackPrice);
  if (!Number.isFinite(qty) || !Number.isFinite(price)) return null;
  return qty * price * 100;
}

function renderForgeDiagnostics(payload) {
  const waterfallEl = document.getElementById("forge-waterfall");
  const bottlenecksEl = document.getElementById("forge-bottlenecks");
  const forgeDiag = payload?.diagnostics?.forge || {};
  const waterfall = forgeDiag.waterfall || {};
  const perSymbol = Array.isArray(forgeDiag.per_symbol) ? forgeDiag.per_symbol : [];
  const passedSignals = perSymbol.filter((row) => Number(row.final_candidates) > 0).length;

  if (waterfallEl) {
    if (!Object.keys(waterfall).length) {
      waterfallEl.innerHTML = summaryItemHtml("Status", "No forge diagnostics yet");
    } else {
      waterfallEl.innerHTML = [
        summaryItemHtml("Signals", integer(waterfall.signals_considered)),
        summaryItemHtml("Chains", `${integer(waterfall.signals_with_chain)} / ${integer(waterfall.signals_with_expiry)}`),
        summaryItemHtml("Long-Leg Cap", `${integer(waterfall.rows_within_long_leg_cap)} rows`),
        summaryItemHtml("Spread Cap", `${integer(waterfall.rows_within_spread_cap)} rows`),
        summaryItemHtml("Liquidity", `${integer(waterfall.rows_passing_liquidity)} rows`),
        summaryItemHtml("Delta Band", `${integer(waterfall.rows_passing_delta)} rows`),
        summaryItemHtml("Net Debit", `${integer(waterfall.rows_passing_net_debit)} rows`),
        summaryItemHtml("Candidates", integer(waterfall.final_candidates)),
        summaryItemHtml("Pass Rate", ratioOrDash(passedSignals, waterfall.signals_considered)),
      ].join("");
    }
  }

  if (bottlenecksEl) {
    if (!perSymbol.length) {
      bottlenecksEl.innerHTML = summaryItemHtml("Status", "No symbol diagnostics yet");
    } else {
      const reasonCounts = perSymbol.reduce((acc, row) => {
        const reason = row.rejection_reason || (Number(row.final_candidates) > 0 ? "passed" : "unknown");
        if (reason === "passed") return acc;
        acc[reason] = (acc[reason] || 0) + 1;
        return acc;
      }, {});
      const topReasons = Object.entries(reasonCounts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4)
        .map(([reason, count]) => summaryItemHtml(reason.replaceAll("_", " "), `${count} symbol${count === 1 ? "" : "s"}`));
      const topPasses = perSymbol
        .filter((row) => Number(row.final_candidates) > 0)
        .sort((a, b) => Number(b.final_candidates || 0) - Number(a.final_candidates || 0))
        .slice(0, 2)
        .map((row) => summaryItemHtml(`${row.symbol} passed`, `${integer(row.final_candidates)} candidate${Number(row.final_candidates) === 1 ? "" : "s"}`));
      bottlenecksEl.innerHTML = [...topReasons, ...topPasses].join("");
    }
  }
}

async function renderBoard(payload) {
  if (!payload || !payload.council) {
    throw new Error("Invalid or missing council data in snapshot.");
  }

  const live   = payload.council.live_board || [];
  const shadow = payload.council.shadow_board || [];
  const summary = payload.council.summary || payload.summary || {};
  const generatedAt = payload.generated_at_utc || payload.timestamp;

  // Stale check (4 hours)
  const isStale = generatedAt && (Date.now() - new Date(generatedAt)) > (4 * 60 * 60 * 1000);
  const boardStatusEl = document.getElementById("board-status");
  if (boardStatusEl) {
    boardStatusEl.textContent = payload.council.abstain ? "Council abstained" : live.length ? "Harbor live" : "Live board quiet";
    if (isStale) {
      boardStatusEl.classList.add("is-stale-text");
      boardStatusEl.title = "Warning: This data is more than 4 hours old.";
    } else {
      boardStatusEl.classList.remove("is-stale-text");
      boardStatusEl.title = "";
    }
  }

  setText("board-status-note", sentenceList(summary.notes, "No council notes."));
  setText("live-count-hud",    integer(payload.council.summary?.live_count));
  setText("shadow-count-hud",  integer(payload.council.summary?.shadow_count));

  const regimePill = document.getElementById("regime-pill");
  if (regimePill) {
    regimePill.textContent = `${String(payload.regime.mode).replace("_", " ").toUpperCase()} · bias ${payload.regime.bias}`;
    regimePill.className = `hud-value ${regimeToneClass(payload.regime.mode) === "is-call" ? "" : regimeToneClass(payload.regime.mode)}`;
  }
  setText("regime-source",      payload.regime.source_symbol || "SPY");
  setText("regime-source-note", sentenceList(payload.regime.notes, `Watching ${payload.regime.source_symbol || "the market"}.`));

  const regimeTag = document.getElementById("regime-tag");
  if (regimeTag) {
    regimeTag.textContent = `Regime: ${String(payload.regime.mode).replace("_", " ").toUpperCase()}`;
  }
  const dispatchTag = document.getElementById("dispatch-tag");
  if (dispatchTag) {
    const ago = generatedAt ? ` (${timeAgo(generatedAt)})` : "";
    dispatchTag.textContent = `Last dispatch: ${formatTs(generatedAt)}${ago}`;
    if (isStale) {
      dispatchTag.style.color = "var(--amber)";
      dispatchTag.style.fontWeight = "600";
    }
  }

  // Prefetch live quotes for all contracts
  const allContracts = [...live, ...shadow].map((c) => c.contract_symbol).filter(Boolean);
  await refreshQuotes(allContracts);

  // Render live picks
  const liveGrid = document.getElementById("live-picks-grid");
  if (liveGrid) {
    liveGrid.innerHTML = "";
    if (!live.length) {
      liveGrid.appendChild(buildEmptyCard(
        "Council Abstained",
        sentenceList(payload.council.summary?.notes, "No contract cleared the live board threshold for this run.")
      ));
    } else {
      live.forEach((c) => liveGrid.appendChild(buildTradeCard(c, payload.regime, "live")));
    }
  }

  // Render shadow picks
  const shadowGrid = document.getElementById("shadow-picks-grid");
  if (shadowGrid) {
    shadowGrid.innerHTML = "";
    if (!shadow.length) {
      shadowGrid.appendChild(buildEmptyCard("Shadow Lane Quiet", "No shadow contracts available for this run."));
    } else {
      shadow.forEach((c) => shadowGrid.appendChild(buildTradeCard(c, payload.regime, "shadow")));
    }
  }

  // Scout / Forge / Council pipeline tables
  const scoutBoard = document.getElementById("scout-board");
  if (scoutBoard) {
    scoutBoard.innerHTML = (payload.scout_signals || []).slice(0, 5).map((row, i) =>
      rowHtml(
        `${row.symbol} ${String(row.direction).toUpperCase()} · ${row.scout_score}`,
        `m5 ${pct(row.momentum_5d)} · m20 ${pct(row.momentum_20d)} · RSI ${row.rsi_14}`,
        toneClass(row.direction),
        `Scout ${String(i + 1).padStart(2, "0")}`
      )
    ).join("") || `<div class="muted" style="padding:12px;font-family:var(--font-data);font-size:.75rem">No scout signals.</div>`;
  }

  const forgeBoard = document.getElementById("forge-board");
  if (forgeBoard) {
    forgeBoard.innerHTML = (payload.forge_candidates || []).slice(0, 5).map((row, i) =>
      rowHtml(
        `${row.symbol} ${String(row.option_type).toUpperCase()} · ${row.forge_score}`,
        `ask ${money(row.ask ?? row.premium)} · exp ${pct(row.expected_return_pct)} · OI ${integer(row.open_interest)}`,
        toneClass(row.option_type),
        `Forge ${String(i + 1).padStart(2, "0")}`
      )
    ).join("") || `<div class="muted" style="padding:12px;font-family:var(--font-data);font-size:.75rem">No forge candidates.</div>`;
  }

  const councilSummary = document.getElementById("council-summary");
  if (councilSummary) {
    councilSummary.innerHTML = [
      summaryItemHtml("Abstain",    payload.council.abstain ? "Yes" : "No"),
      summaryItemHtml("Live",       integer(payload.council.summary?.live_count)),
      summaryItemHtml("Shadow",     integer(payload.council.summary?.shadow_count)),
      summaryItemHtml("Candidates", integer(payload.council.summary?.candidate_count)),
      summaryItemHtml("Regime",     String(payload.regime.mode).replace("_", " ")),
      summaryItemHtml("Notes",      sentenceList(payload.council.summary?.notes, "No extra notes.")),
    ].join("");
  }

  renderForgeDiagnostics(payload);

  // Bind card buttons
  bindCardButtons();

  // Stream AI rationale for each card asynchronously
  const allCandidates = [
    ...live.map((c) => ({ candidate: c, lane: "live" })),
    ...shadow.map((c) => ({ candidate: c, lane: "shadow" })),
  ];
  for (const { candidate } of allCandidates) {
    loadCardRationale(candidate, payload.regime);
  }
}

async function loadCardRationale(candidate, regime) {
  const id  = `rationale-${candidate.contract_symbol.replace(/[^a-z0-9]/gi, "_")}`;
  const el  = document.getElementById(id);
  if (!el) return;
  const rationale = await fetchRationale(candidate, regime);
  if (el) {
    el.classList.remove("is-loading");
    el.textContent = rationale || sentenceList(
      candidate.notes,
      `${candidate.symbol} ${candidate.option_type} — Forge score ${Number(candidate.forge_score || 0).toFixed(2)}.`
    );
  }
}

// ── Order Flow (Preview → Execute) ─────────────────────────────────────────

let PENDING_ORDER = null;

function openModal(title, bodyHtml, executeEnabled, orderData) {
  setText("modal-title", title);
  const body = document.getElementById("modal-body");
  if (body) body.innerHTML = bodyHtml;
  const execBtn = document.getElementById("modal-execute-btn");
  if (execBtn) execBtn.disabled = !executeEnabled;
  const msg = document.getElementById("modal-message");
  if (msg) msg.textContent = "";
  PENDING_ORDER = orderData || null;
  const modal = document.getElementById("preview-modal");
  if (modal) modal.hidden = false;
  document.body.style.overflow = "hidden";
}

function closeModal() {
  const modal = document.getElementById("preview-modal");
  if (modal) modal.hidden = true;
  document.body.style.overflow = "";
  PENDING_ORDER = null;
}

function bindModal() {
  document.getElementById("modal-close-btn")?.addEventListener("click", closeModal);
  document.getElementById("modal-cancel-btn")?.addEventListener("click", closeModal);
  document.getElementById("preview-modal")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
  });

  document.getElementById("modal-execute-btn")?.addEventListener("click", async () => {
    if (!PENDING_ORDER) return;
    const btn = document.getElementById("modal-execute-btn");
    const msg = document.getElementById("modal-message");
    btn.disabled = true;
    btn.textContent = "Submitting…";
    if (msg) msg.textContent = "";

    try {
      const r = await fetch("/api/tradier/orders", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...PENDING_ORDER, preview: false, confirm_live: true }),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.error || `Order failed (${r.status})`);

      const order = data.order || {};
      if (msg) {
        msg.textContent = "";
        msg.style.color = "var(--teal)";
      }
      openModal(
        "Order Submitted",
        `<div class="summary-box">
          ${summaryItemHtml("Status",  order.status || "submitted")}
          ${summaryItemHtml("Order ID", order.id || "--")}
          ${summaryItemHtml("Contract", data.envelope?.option_symbol || PENDING_ORDER.option_symbol)}
          ${summaryItemHtml("Qty",      order.quantity || PENDING_ORDER.quantity)}
          ${summaryItemHtml("Price",    money(order.price || PENDING_ORDER.price))}
        </div>`,
        false,
        null
      );
      // Refresh account after a brief delay
      setTimeout(loadAccount, 1800);
    } catch (err) {
      const msg = document.getElementById("modal-message");
      if (msg) {
        msg.textContent = String(err.message || err);
        msg.style.color = "var(--crimson)";
      }
      btn.disabled = false;
      btn.textContent = "Execute Trade";
    }
  });
}

async function handlePreview(contractSymbol, underlyingSymbol, lane, ask, allocWeight) {
  const msg = document.getElementById("modal-message");
  openModal("Requesting Preview…", `<div style="padding:24px;text-align:center;font-family:var(--font-data);font-size:.8rem;color:var(--text-muted)">Fetching Tradier preview…</div>`, false, null);

  try {
    const price = Number(ask) || 0.01;
    // Volatility-Scaled position sizing (max $500 per position)
    const weight = Number(allocWeight) || 1.0;
    const scaledBudget = 500.0 * weight;
    const qty = Math.max(1, Math.floor(scaledBudget / (price * 100.0)));

    const r = await fetch("/api/tradier/orders", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        preview: true,
        option_symbol: contractSymbol,
        symbol: underlyingSymbol,
        side: "buy_to_open",
        quantity: qty,
        type: "limit",
        duration: "day",
        price,
      }),
    });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || `Preview failed (${r.status})`);

    const order = data.order || {};
    const elig  = data.eligibility || {};
    const isAdmin = SESSION?.session?.role === "admin";
    const warned = (elig.warnings || []).length;
    const canExec = isAdmin && !warned;
    const estCost = estimateTradeValue(order, qty, price);
    const hasCommission = order.commission !== null
      && order.commission !== undefined
      && Number.isFinite(Number(order.commission));
    const commissionText = hasCommission
      ? money(Number(order.commission))
      : "Pending broker preview";

    const warningHtml = (elig.warnings || []).map((w) =>
      `<div style="font-family:var(--font-data);font-size:.7rem;color:var(--amber);margin-top:4px;">⚠ ${w}</div>`
    ).join("");

    const bodyHtml = `
      <div class="summary-box">
        ${summaryItemHtml("Contract",    data.envelope?.option_symbol || contractSymbol)}
        ${summaryItemHtml("Side",        "Buy to Open · Limit")}
        ${summaryItemHtml("Vol Scaling", weight.toFixed(2) + "x")}
        ${summaryItemHtml("Quantity",    order.quantity || qty)}
        ${summaryItemHtml("Limit Price", money(order.price || price))}
        ${summaryItemHtml("Est. Cost",   estCost !== null ? money(estCost) : "—")}
        ${summaryItemHtml("Commission",  commissionText)}
        ${summaryItemHtml("Mode",        BROKER_STATE.mode?.toUpperCase() || "--")}
        ${summaryItemHtml("Lane",        lane)}
      </div>
      ${warningHtml}
      ${!isAdmin ? `<p style="font-family:var(--font-data);font-size:.72rem;color:var(--text-muted);margin-top:12px;">Admin session required to execute.</p>` : ""}
    `;

    // Store the pending order so Execute can fire it
    const pendingOrder = {
      option_symbol: contractSymbol,
      symbol: underlyingSymbol,
      side: "buy_to_open",
      quantity: qty,
      type: "limit",
      duration: "day",
      price,
    };

    openModal("Order Preview", bodyHtml, canExec, pendingOrder);
  } catch (err) {
    openModal(
      "Preview Failed",
      `<p style="font-family:var(--font-data);font-size:.8rem;color:var(--crimson);padding:16px">${err.message || err}</p>`,
      false,
      null
    );
  }
}

async function handleDirectExecute(contractSymbol, underlyingSymbol, lane, ask, allocWeight) {
  // Direct execute: still shows the modal with pre-confirmed execute button
  await handlePreview(contractSymbol, underlyingSymbol, lane, ask, allocWeight);
  // Auto-enable execute if not already blocked
  const execBtn = document.getElementById("modal-execute-btn");
  if (execBtn && !execBtn.disabled) {
    execBtn.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

async function handleClosePosition(contractSymbol, qty) {
  const match = contractSymbol.match(/^[A-Z]+/);
  const underlyingSymbol = match ? match[0] : contractSymbol;

  const msg = document.getElementById("modal-message");
  openModal("Closing Position…", `<div style="padding:24px;text-align:center;font-family:var(--font-data);font-size:.8rem;color:var(--text-muted)">Fetching Tradier preview…</div>`, false, null);

  try {
    const price = 0.01; // Will be resolved to bid price on backend
    const r = await fetch("/api/tradier/orders", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        preview: true,
        option_symbol: contractSymbol,
        symbol: underlyingSymbol,
        side: "sell_to_close",
        quantity: Number(qty) || 1,
        type: "limit",
        duration: "day",
        price,
      }),
    });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || `Preview failed (${r.status})`);

    const order = data.order || {};
    const elig  = data.eligibility || {};
    const isAdmin = SESSION?.session?.role === "admin";
    const warned = (elig.warnings || []).length;
    const canExec = isAdmin && !warned;
    const estProceeds = estimateTradeValue(order, qty, price);
    const hasCommission = order.commission !== null
      && order.commission !== undefined
      && Number.isFinite(Number(order.commission));
    const commissionText = hasCommission
      ? money(Number(order.commission))
      : "Pending broker preview";

    const warningHtml = (elig.warnings || []).map((w) =>
      `<div style="font-family:var(--font-data);font-size:.7rem;color:var(--amber);margin-top:4px;">⚠ ${w}</div>`
    ).join("");

    const bodyHtml = `
      <div class="summary-box">
        ${summaryItemHtml("Contract",    data.envelope?.option_symbol || contractSymbol)}
        ${summaryItemHtml("Side",        "Sell to Close · Limit")}
        ${summaryItemHtml("Quantity",    order.quantity || qty)}
        ${summaryItemHtml("Limit Price", money(order.price || price))}
        ${summaryItemHtml("Est. Proceeds", estProceeds !== null ? money(Math.abs(estProceeds)) : "—")}
        ${summaryItemHtml("Commission",  commissionText)}
        ${summaryItemHtml("Mode",        BROKER_STATE.mode?.toUpperCase() || "--")}
      </div>
      ${warningHtml}
      ${!isAdmin ? `<p style="font-family:var(--font-data);font-size:.72rem;color:var(--text-muted);margin-top:12px;">Admin session required to execute.</p>` : ""}
    `;

    const pendingOrder = {
      option_symbol: contractSymbol,
      symbol: underlyingSymbol,
      side: "sell_to_close",
      quantity: Number(qty) || 1,
      type: "limit",
      duration: "day",
      price: order.price || price,
    };

    openModal("Close Position Preview", bodyHtml, canExec, pendingOrder);
  } catch (err) {
    openModal(
      "Preview Failed",
      `<p style="font-family:var(--font-data);font-size:.8rem;color:var(--crimson);padding:16px">${err.message || err}</p>`,
      false,
      null
    );
  }
}

function bindPositionsTable() {
  document.querySelectorAll(".close-position-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      handleClosePosition(btn.dataset.contract, btn.dataset.qty);
    });
  });
}

function bindCardButtons() {
  document.querySelectorAll(".card-preview-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      handlePreview(
        btn.dataset.contract,
        btn.dataset.symbol,
        btn.dataset.lane,
        btn.dataset.ask,
        btn.dataset.alloc
      );
    });
  });

  document.querySelectorAll(".card-execute-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      handleDirectExecute(
        btn.dataset.contract,
        btn.dataset.symbol,
        btn.dataset.lane,
        btn.dataset.ask,
        btn.dataset.alloc
      );
    });
  });
}

// ── Utility ─────────────────────────────────────────────────────────────────

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

// ── Backtest ─────────────────────────────────────────────────────────────────

async function loadBacktest() {
  try {
    const r = await fetch("/api/backtest/summary", { cache: "no-store" });
    if (!r.ok) throw new Error("not found");
    const data = await r.json();
    if (data.ok && data.backtest) return data.backtest;
  } catch {
    /* silently degrade — show "no data" placeholder */
  }
  return null;
}

function renderEquityCurve(canvas, curve) {
  const ctx = canvas.getContext("2d");
  if (!ctx || !curve || curve.length === 0) return;

  // Retina / high-DPI
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width  = rect.width  * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const W = rect.width;
  const H = rect.height;
  const PAD = { top: 16, right: 24, bottom: 32, left: 52 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top  - PAD.bottom;

  const values = curve.map(pt => pt.cumulative_pnl);
  const minVal = Math.min(0, ...values);
  const maxVal = Math.max(0, ...values);
  const range  = maxVal - minVal || 1;

  function xOf(i)   { return PAD.left + (i / (values.length - 1)) * plotW; }
  function yOf(val) { return PAD.top  + plotH - ((val - minVal) / range) * plotH; }

  // Zero line
  const zeroY = yOf(0);
  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(PAD.left, zeroY);
  ctx.lineTo(W - PAD.right, zeroY);
  ctx.stroke();
  ctx.setLineDash([]);

  // Gradient fill
  const lastVal = values[values.length - 1];
  const grad = ctx.createLinearGradient(0, PAD.top, 0, H - PAD.bottom);
  const positive = lastVal >= 0;
  if (positive) {
    grad.addColorStop(0,   "rgba(74,216,162,0.35)");
    grad.addColorStop(1,   "rgba(74,216,162,0.02)");
  } else {
    grad.addColorStop(0,   "rgba(220,53,69,0.02)");
    grad.addColorStop(1,   "rgba(220,53,69,0.35)");
  }

  ctx.beginPath();
  ctx.moveTo(xOf(0), yOf(values[0]));
  for (let i = 1; i < values.length; i++) ctx.lineTo(xOf(i), yOf(values[i]));
  ctx.lineTo(xOf(values.length - 1), H - PAD.bottom);
  ctx.lineTo(PAD.left, H - PAD.bottom);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Line
  ctx.beginPath();
  ctx.lineWidth = 2;
  ctx.strokeStyle = positive ? "#4ad8a2" : "#dc3545";
  ctx.lineJoin = "round";
  for (let i = 0; i < values.length; i++) {
    i === 0 ? ctx.moveTo(xOf(i), yOf(values[i])) : ctx.lineTo(xOf(i), yOf(values[i]));
  }
  ctx.stroke();

  // Dots at each data point
  ctx.fillStyle = positive ? "#4ad8a2" : "#dc3545";
  for (let i = 0; i < values.length; i++) {
    ctx.beginPath();
    ctx.arc(xOf(i), yOf(values[i]), 3, 0, Math.PI * 2);
    ctx.fill();
  }

  // Y-axis labels
  ctx.font = "11px var(--font-data, monospace)";
  ctx.fillStyle = "rgba(255,255,255,0.45)";
  ctx.textAlign = "right";
  const steps = 4;
  for (let s = 0; s <= steps; s++) {
    const val = minVal + (range / steps) * s;
    const y   = yOf(val);
    ctx.fillText(`$${val >= 0 ? "+" : ""}${val.toFixed(0)}`, PAD.left - 6, y + 4);
  }

  // X-axis dates (show first and last only)
  ctx.textAlign = "center";
  ctx.fillStyle = "rgba(255,255,255,0.35)";
  if (curve.length > 0) {
    ctx.fillText(curve[0].week, xOf(0), H - 6);
    ctx.fillText(curve[curve.length - 1].week, xOf(curve.length - 1), H - 6);
  }
}

function renderBacktest(bt) {
  if (!bt) {
    const noData = document.getElementById("bt-no-data");
    if (noData) noData.hidden = false;
    const sizingPolicy = document.getElementById("bt-sizing-policy");
    const researchNotes = document.getElementById("bt-research-notes");
    if (sizingPolicy) sizingPolicy.innerHTML = summaryItemHtml("Status", "No backtest sizing data");
    if (researchNotes) researchNotes.innerHTML = summaryItemHtml("Status", "No backtest methodology data");
    return;
  }

  // Stats ribbon
  const setVal = (id, text, positive) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (positive === true)  el.classList.add("positive");
    if (positive === false) el.classList.add("negative");
  };

  const totalPnl     = Number(bt.total_pnl || 0);
  const netReturn    = Number(bt.net_return_pct || 0);
  const sharpe       = Number(bt.sharpe_ratio || 0);
  const maxDD        = Number(bt.max_drawdown || 0);
  const winRate      = Number(bt.win_rate || 0);
  const avgWin       = Number(bt.avg_winner_pct || 0);
  const avgLoss      = Number(bt.avg_loser_pct || 0);
  const trades       = Number(bt.total_trades || 0);
  const sizingPolicy = bt.sizing_policy || {};
  const sizingPolicyEl = document.getElementById("bt-sizing-policy");
  const researchNotesEl = document.getElementById("bt-research-notes");
  const subtitleEl = document.getElementById("bt-section-sub");

  if (subtitleEl) {
    subtitleEl.textContent = [
      "3-month backtest",
      "All Forge candidates",
      `base $${Number(bt.budget_per_trade_usd || 0).toFixed(0)} / trade`,
      sizingPolicy.skip_when_underfunded ? "underfunded trades skipped" : "forced minimum 1 contract",
    ].join(" · ");
  }

  setVal("bt-win-rate",    `${(winRate * 100).toFixed(1)}%`,      winRate >= 0.5);
  setVal("bt-total-pnl",  `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`, totalPnl >= 0);
  setVal("bt-sharpe",     sharpe.toFixed(2),                      sharpe >= 1.0);
  setVal("bt-drawdown",   `${(maxDD * 100).toFixed(1)}%`,         maxDD >= -0.10);
  setVal("bt-avg-win",    `+${(avgWin * 100).toFixed(1)}%`,       true);
  setVal("bt-avg-loss",   `${(avgLoss * 100).toFixed(1)}%`,       false);
  setVal("bt-trades",     trades.toLocaleString(),                null);
  setVal("bt-net-return", `${netReturn >= 0 ? "+" : ""}${(netReturn * 100).toFixed(1)}%`, netReturn >= 0);

  if (sizingPolicyEl) {
    sizingPolicyEl.innerHTML = [
      summaryItemHtml("Base Budget", money(bt.budget_per_trade_usd || 0)),
      summaryItemHtml("Allocation Weight", Array.isArray(sizingPolicy.allocation_weight_range) ? `${sizingPolicy.allocation_weight_range[0]}x to ${sizingPolicy.allocation_weight_range[1]}x` : "—"),
      summaryItemHtml("Confidence Scale", Array.isArray(sizingPolicy.confidence_scale_range) ? `${sizingPolicy.confidence_scale_range[0]}x to ${sizingPolicy.confidence_scale_range[1]}x` : "—"),
      summaryItemHtml("Underfunded Trade", sizingPolicy.skip_when_underfunded ? "Skip" : "Force 1 contract"),
      summaryItemHtml("Max Observed Cost", money(sizingPolicy.max_observed_cost_basis_usd || 0)),
    ].join("");
  }

  if (researchNotesEl) {
    researchNotesEl.innerHTML = [
      summaryItemHtml("Window", `${bt.backtest_start} to ${bt.backtest_end}`),
      summaryItemHtml("Trades", integer(bt.total_trades)),
      summaryItemHtml("Win Rate", pctOrDash(bt.win_rate)),
      summaryItemHtml("Sharpe", Number.isFinite(sharpe) ? sharpe.toFixed(2) : "—"),
      summaryItemHtml("Drawdown", pctOrDash(bt.max_drawdown)),
    ].join("");
  }

  // Equity curve
  const canvas = document.getElementById("equity-curve-chart");
  const noData = document.getElementById("bt-no-data");
  if (canvas && bt.equity_curve && bt.equity_curve.length > 0) {
    if (noData) noData.hidden = true;
    // Wait one frame so the canvas has been laid out
    requestAnimationFrame(() => renderEquityCurve(canvas, bt.equity_curve));
  } else {
    if (noData) noData.hidden = false;
  }

  // Symbol breakdown
  if (bt.symbol_breakdown && bt.symbol_breakdown.length > 0) {
    const wrap = document.getElementById("bt-symbols");
    const grid = document.getElementById("bt-symbol-grid");
    if (wrap && grid) {
      wrap.hidden = false;
      grid.innerHTML = bt.symbol_breakdown.map(row => {
        const pnlPos = row.total_pnl >= 0;
        return `
          <div class="bt-sym-card">
            <span class="bt-sym-label">${row.symbol}</span>
            <span class="bt-sym-meta">${row.trades} trades · ${(row.win_rate * 100).toFixed(0)}% win</span>
            <span class="bt-sym-meta ${pnlPos ? "positive" : "negative"}" style="color:${pnlPos ? "var(--green)" : "var(--red)"}">
              ${pnlPos ? "+" : ""}$${row.total_pnl.toFixed(2)}
            </span>
          </div>`;
      }).join("");
    }
  }

  // Trade log (last 20)
  if (bt.all_trades && bt.all_trades.length > 0) {
    const wrap = document.getElementById("bt-trades-wrap");
    const tbody = document.getElementById("bt-trade-rows");
    if (wrap && tbody) {
      wrap.hidden = false;
      const shown = [...bt.all_trades].reverse().slice(0, 30);
      tbody.innerHTML = shown.map(t => {
        const pnlPos = t.pnl >= 0;
        return `
          <tr>
            <td>${t.entry_date}</td>
            <td>${t.symbol}</td>
            <td class="${t.option_type === "call" ? "is-call" : "is-put"}">${t.option_type.toUpperCase()}</td>
            <td>$${t.strike}</td>
            <td>$${t.entry_price.toFixed(2)}</td>
            <td>$${t.exit_price.toFixed(2)}</td>
            <td style="color:${pnlPos ? "var(--green)" : "var(--red)"}">${pnlPos ? "+" : ""}$${t.pnl.toFixed(2)}</td>
            <td style="color:${pnlPos ? "var(--green)" : "var(--red)"}">${(t.pnl_pct * 100).toFixed(0)}%</td>
          </tr>`;
      }).join("");
    }
  }
}

// ── Boot ────────────────────────────────────────────────────────────────────

async function main() {
  // Auth
  const sessionPayload = await loadSession();
  SESSION = sessionPayload;
  const userLabel = document.getElementById("session-user");
  if (userLabel) {
    userLabel.textContent = sessionPayload.authenticated && sessionPayload.session
      ? `${sessionPayload.session.username} · ${String(sessionPayload.session.role).toUpperCase()}`
      : "Local preview";
  }
  bindLogout();
  bindModal();

  // Load account (non-blocking so board renders even if Tradier is offline)
  loadAccount().catch(() => {});

  // Load snapshot and render board
  try {
    const payload = await loadSnapshot();
    await renderBoard(payload);
  } catch (err) {
    const liveGrid = document.getElementById("live-picks-grid");
    if (liveGrid) {
      liveGrid.innerHTML = `<div style="padding:32px;font-family:var(--font-data);font-size:.8rem;color:var(--crimson)">Failed to load snapshot: ${err.message || err}</div>`;
    }
  }

  // Load backtest results (non-blocking — shows placeholder if not yet generated)
  loadBacktest().then(bt => renderBacktest(bt)).catch(() => {});
}

main();
