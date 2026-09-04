function money(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "--";
  }
  return amount.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function signedMoney(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "--";
  }
  return `${amount >= 0 ? "+" : "-"}${money(Math.abs(amount))}`;
}

function integer(value) {
  const amount = Number(value);
  return Number.isFinite(amount) ? String(Math.round(amount)) : "--";
}

function percent(value) {
  const amount = Number(value);
  return Number.isFinite(amount) ? `${(amount * 100).toFixed(1)}%` : "--";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    switch (char) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      case "'":
        return "&#39;";
      default:
        return char;
    }
  });
}

function formatDateTime(value) {
  if (!value) {
    return "--";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function totalFromPositions(positions, field) {
  return (positions || []).reduce((sum, position) => {
    const value = Number(position?.[field]);
    return Number.isFinite(value) ? sum + value : sum;
  }, 0);
}

function summarizeSnapshot(entry) {
  const snapshot = entry?.snapshot || {};
  const positions = Array.isArray(snapshot.positions) ? snapshot.positions : [];
  const costBasis = totalFromPositions(positions, "cost_basis");
  const marketValue = totalFromPositions(positions, "current_value");
  const openPl = totalFromPositions(positions, "open_pl");
  return {
    entry,
    snapshot,
    positions,
    costBasis,
    marketValue,
    openPl,
  };
}

function buildTrendPoints(items) {
  const values = items.map((item) => item.marketValue).filter((value) => Number.isFinite(value));
  if (!values.length) {
    return "";
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 100;
  const height = 36;
  return items
    .map((item, index) => {
      const x = items.length === 1 ? width / 2 : (index / (items.length - 1)) * width;
      const y = height - (((item.marketValue || 0) - min) / span) * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function renderOverview(items) {
  const container = document.getElementById("admin-overview-grid");
  if (!container) return;
  if (!items.length) {
    container.innerHTML = `
        <article class="summary-item admin-card inventory-slot">
        <span class="summary-label">No History</span>
        <span class="summary-value">No hosted captures found yet.</span>
      </article>
    `;
    return;
  }

  const latest = items[0];
  const earliest = items[items.length - 1];
  const avgMarketValue =
    items.reduce((sum, item) => sum + item.marketValue, 0) / items.length;
  const drift = latest.marketValue - earliest.marketValue;

  const cards = [
    {
      label: "Latest Capture",
      value: formatDateTime(latest.entry.captured_at_utc),
    },
    {
      label: "Current Marked Value",
      value: money(latest.marketValue),
      className: "",
    },
    {
      label: "Open P&L",
      value: signedMoney(latest.openPl),
      className: latest.openPl >= 0 ? "is-positive" : "is-negative",
    },
    {
      label: "Net Drift",
      value: signedMoney(drift),
      className: drift >= 0 ? "is-positive" : "is-negative",
    },
    {
      label: "Avg Marked Value",
      value: money(avgMarketValue),
    },
    {
      label: "Snapshots Loaded",
      value: integer(items.length),
    },
  ];

  container.innerHTML = cards
    .map(
      (card) => `
        <article class="summary-item admin-card inventory-slot">
          <span class="summary-label">${escapeHtml(card.label)}</span>
          <span class="summary-value ${card.className || ""}">${escapeHtml(card.value)}</span>
        </article>
      `,
    )
    .join("");
}

function renderTrend(items) {
  const container = document.getElementById("admin-trend-panel");
  if (!container) return;
  if (!items.length) {
    container.innerHTML = `<div class="admin-trend-empty map-empty">The parchment is still blank. No hosted history yet.</div>`;
    return;
  }

  const chronological = [...items].reverse();
  const points = buildTrendPoints(chronological);
  const latest = items[0];
  const earliest = items[items.length - 1];
  const delta = latest.marketValue - earliest.marketValue;

  container.innerHTML = `
    <div class="admin-trend-chart map-parchment">
      <span class="map-compass" aria-hidden="true"></span>
      <p class="map-cartouche">Marked value</p>
      <svg viewBox="0 0 100 36" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="history-line" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="rgba(78, 205, 196, 0.9)"></stop>
            <stop offset="100%" stop-color="rgba(232, 197, 109, 0.9)"></stop>
          </linearGradient>
        </defs>
        <polyline points="${points}" class="admin-trend-line"></polyline>
      </svg>
    </div>
    <div class="admin-trend-meta">
      <div class="admin-trend-stat">
        <span class="summary-label">Start</span>
        <span class="summary-value">${escapeHtml(money(earliest.marketValue))}</span>
      </div>
      <div class="admin-trend-stat">
        <span class="summary-label">Latest</span>
        <span class="summary-value">${escapeHtml(money(latest.marketValue))}</span>
      </div>
      <div class="admin-trend-stat">
        <span class="summary-label">Delta</span>
        <span class="summary-value ${delta >= 0 ? "is-positive" : "is-negative"}">${escapeHtml(signedMoney(delta))}</span>
      </div>
    </div>
  `;
}

function contractTone(symbol) {
  return String(symbol || "").includes("P") ? "is-put" : "is-call";
}

function positionChip(position) {
  const symbol = String(position?.symbol || "--");
  const value = money(position?.current_value);
  const pl = Number(position?.open_pl);
  const plClass = Number.isFinite(pl) ? (pl >= 0 ? "is-positive" : "is-negative") : "";
  return `
    <div class="mini-row ${contractTone(symbol)} admin-contract-row">
      <strong title="${escapeHtml(symbol)}">${escapeHtml(symbol)}</strong>
      <span class="muted">${escapeHtml(value)}</span>
      <span class="${plClass}">${escapeHtml(signedMoney(pl))}</span>
    </div>
  `;
}

function renderHistoryTable(items) {
  const tbody = document.getElementById("admin-history-tbody");
  if (!tbody) return;
  if (!items.length) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" class="admin-history-loading">No captured history found.</td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = items
    .map((item) => {
      const contracts = item.positions.length
        ? `<div class="mini-table admin-contract-table">${item.positions.map(positionChip).join("")}</div>`
        : `<span class="muted">No open positions captured.</span>`;
      return `
        <tr>
          <td>
            <div class="admin-time-stack">
              <strong>${escapeHtml(formatDateTime(item.entry.captured_at_utc))}</strong>
              <span class="muted">Run ${escapeHtml(formatDateTime(item.entry.run_generated_at_utc))}</span>
            </div>
          </td>
          <td>${escapeHtml(item.entry.source || "--")}</td>
          <td class="is-num">${escapeHtml(integer(item.positions.length))}</td>
          <td class="is-num">${escapeHtml(money(item.costBasis))}</td>
          <td class="is-num">${escapeHtml(money(item.marketValue))}</td>
          <td class="is-num ${item.openPl >= 0 ? "is-positive" : "is-negative"}">${escapeHtml(signedMoney(item.openPl))}</td>
          <td>${contracts}</td>
        </tr>
      `;
    })
    .join("");
}

async function loadHistory() {
  const response = await fetch("/api/admin/positions-history?limit=24", {
    cache: "no-store",
  });
  const payload = await response.json();
  if (!payload.ok) {
    throw new Error(payload.error || "Unable to load position history.");
  }
  return Array.isArray(payload.snapshots) ? payload.snapshots : [];
}

async function loadOrderLedger() {
  const response = await fetch("/api/admin/order-ledger?limit=50", {
    cache: "no-store",
  });
  const payload = await response.json();
  if (!payload.ok) {
    throw new Error(payload.error || "Unable to load order provenance.");
  }
  return Array.isArray(payload.events) ? payload.events : [];
}

async function loadMartAudit() {
  const response = await fetch("/data/diagnostics/shared_mart_shadow_evidence_latest.json", {
    cache: "no-store",
  });
  if (!response.ok) throw new Error("Shared-mart audit artifact is unavailable.");
  renderMartAudit(await response.json());
}

function renderMartAudit(payload) {
  const bundle = payload.consumer_bundle || {};
  const views = bundle.views || {};
  const gates = payload.shadow_entry_gates || {};
  const execution = payload.execution_quality || {};
  const status = document.getElementById("admin-mart-status");
  if (status) {
    status.textContent = bundle.status === "ready" ? "Validated · observation only" : "Audit required";
    status.classList.toggle("is-ready", bundle.status === "ready");
  }
  const summary = document.getElementById("admin-mart-summary");
  if (summary) {
    const pairedDates = gates.paired_market_dates || {};
    const coverage = Number(execution.executable_recommendations || 0) / Math.max(Number(execution.recommendations || 0), 1);
    summary.innerHTML = [
      ["Mart ID", payload.mart_id || "--"],
      ["Source systems", (bundle.source_systems || []).join(" + ") || "--"],
      ["Consumer schema", payload.consumer_schema_version || "--"],
      ["Generated", formatDateTime(payload.generated_at_utc)],
      ["Paired-date gate", `${integer(pairedDates.actual)} / ${integer(pairedDates.required)}`],
      ["Executable coverage", percent(coverage)],
    ].map(([label, value]) => `<article class="summary-item admin-card inventory-slot"><span class="summary-label">${escapeHtml(label)}</span><span class="summary-value">${escapeHtml(value)}</span></article>`).join("");
  }
  const tbody = document.getElementById("admin-mart-views-tbody");
  if (tbody) {
    const rows = Object.entries(views);
    tbody.innerHTML = rows.length ? rows.map(([name, view]) => `
      <tr>
        <td><strong>${escapeHtml(name)}</strong></td>
        <td class="is-num">${escapeHtml(Number(view.rows || 0).toLocaleString())}</td>
        <td>${escapeHtml((view.primary_key || []).join(" + ") || "--")}</td>
        <td class="admin-mart-hash">${escapeHtml(view.sha256 || "--")}</td>
      </tr>`).join("") : `<tr><td colspan="4" class="admin-history-loading">No validated consumer views found.</td></tr>`;
  }
}

function renderMartAuditError(error) {
  const message = escapeHtml(String(error?.message || error || "Unable to load mart audit."));
  const status = document.getElementById("admin-mart-status");
  const tbody = document.getElementById("admin-mart-views-tbody");
  if (status) status.textContent = "Unavailable";
  if (tbody) tbody.innerHTML = `<tr><td colspan="4" class="admin-history-loading">${message}</td></tr>`;
}

function renderOrderLedger(events) {
  const tbody = document.getElementById("admin-order-ledger-tbody");
  if (!tbody) return;
  if (!events.length) {
    tbody.innerHTML = `
      <tr>
        <td colspan="8" class="admin-history-loading">No order provenance events found.</td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = events
    .map((event) => {
      const exitPolicy = event.exit_policy_action
        ? `<div class="order-status-note">${escapeHtml(event.exit_policy_action.replaceAll("_", " "))}</div>`
        : "";
      const runTime = event.run_generated_at_utc
        ? `<span class="muted">Run ${escapeHtml(formatDateTime(event.run_generated_at_utc))}</span>`
        : "";
      const execution = event.payload?.execution || {};
      const executionDetails = [
        Number.isFinite(Number(execution.broker_round_trip_ms))
          ? `${Math.round(Number(execution.broker_round_trip_ms))} ms`
          : "",
        Number.isFinite(Number(execution.signed_adverse_slippage_usd))
          ? `slip ${signedMoney(Number(execution.signed_adverse_slippage_usd))}`
          : "",
        Number.isFinite(Number(execution.fill_delay_seconds))
          ? `fill ${Number(execution.fill_delay_seconds).toFixed(1)}s`
          : "",
      ].filter(Boolean).join(" · ");
      const executionNote = executionDetails
        ? `<div class="order-status-note">${escapeHtml(executionDetails)}</div>`
        : "";
      return `
        <tr>
          <td>
            <div class="admin-time-stack">
              <strong>${escapeHtml(formatDateTime(event.created_at_utc))}</strong>
              ${runTime}
            </div>
          </td>
          <td>${escapeHtml(event.event_type || "--")}</td>
          <td>${escapeHtml(event.lane || "unknown")}</td>
          <td style="font-family:var(--font-data);font-size:.72rem;word-break:break-all">${escapeHtml(event.option_symbol || "--")}${exitPolicy}</td>
          <td>${escapeHtml(event.side || "--")}</td>
          <td class="is-num">${escapeHtml(integer(event.quantity))}</td>
          <td class="is-num">${escapeHtml(money(event.limit_price))}</td>
          <td>${escapeHtml(event.broker_status || event.broker_order_id || "--")}${executionNote}</td>
        </tr>
      `;
    })
    .join("");
}

async function initAdminHistory() {
  loadMartAudit().catch(renderMartAuditError);
  try {
    const [snapshots, orderEvents] = await Promise.all([
      loadHistory(),
      loadOrderLedger().catch((error) => ({ error })),
    ]);
    const items = snapshots.map(summarizeSnapshot);
    renderOverview(items);
    renderTrend(items);
    renderHistoryTable(items);
    if (Array.isArray(orderEvents)) {
      renderOrderLedger(orderEvents);
    } else {
      const tbody = document.getElementById("admin-order-ledger-tbody");
      if (tbody) {
        tbody.innerHTML = `
          <tr>
            <td colspan="8" class="admin-history-loading">${escapeHtml(String(orderEvents.error?.message || orderEvents.error || "Unable to load order provenance."))}</td>
          </tr>
        `;
      }
    }
  } catch (error) {
    const message = String(error?.message || error || "Unknown error");
    const overview = document.getElementById("admin-overview-grid");
    const trend = document.getElementById("admin-trend-panel");
    const tbody = document.getElementById("admin-history-tbody");
    const orderTbody = document.getElementById("admin-order-ledger-tbody");
    if (overview) {
      overview.innerHTML = `
        <article class="summary-item admin-card inventory-slot">
          <span class="summary-label">Load Failed</span>
          <span class="summary-value">${escapeHtml(message)}</span>
        </article>
      `;
    }
    if (trend) {
      trend.innerHTML = `<div class="admin-trend-empty map-empty">${escapeHtml(message)}</div>`;
    }
    if (tbody) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="admin-history-loading">${escapeHtml(message)}</td>
        </tr>
      `;
    }
    if (orderTbody) {
      orderTbody.innerHTML = `
        <tr>
          <td colspan="8" class="admin-history-loading">${escapeHtml(message)}</td>
        </tr>
      `;
    }
  }
}

document.addEventListener("DOMContentLoaded", initAdminHistory);
