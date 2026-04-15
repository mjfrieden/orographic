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
      <article class="summary-item admin-card">
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
        <article class="summary-item admin-card">
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
    container.innerHTML = `<div class="admin-trend-empty">No hosted history yet.</div>`;
    return;
  }

  const chronological = [...items].reverse();
  const points = buildTrendPoints(chronological);
  const latest = items[0];
  const earliest = items[items.length - 1];
  const delta = latest.marketValue - earliest.marketValue;

  container.innerHTML = `
    <div class="admin-trend-chart">
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

async function initAdminHistory() {
  try {
    const snapshots = await loadHistory();
    const items = snapshots.map(summarizeSnapshot);
    renderOverview(items);
    renderTrend(items);
    renderHistoryTable(items);
  } catch (error) {
    const message = String(error?.message || error || "Unknown error");
    const overview = document.getElementById("admin-overview-grid");
    const trend = document.getElementById("admin-trend-panel");
    const tbody = document.getElementById("admin-history-tbody");
    if (overview) {
      overview.innerHTML = `
        <article class="summary-item admin-card">
          <span class="summary-label">Load Failed</span>
          <span class="summary-value">${escapeHtml(message)}</span>
        </article>
      `;
    }
    if (trend) {
      trend.innerHTML = `<div class="admin-trend-empty">${escapeHtml(message)}</div>`;
    }
    if (tbody) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="admin-history-loading">${escapeHtml(message)}</td>
        </tr>
      `;
    }
  }
}

document.addEventListener("DOMContentLoaded", initAdminHistory);
