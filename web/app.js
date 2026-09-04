/**
 * Orographic Arena — AI Options Trading Dashboard
 * No game loop. Direct AI recommendations → Tradier execution.
 */

const SNAPSHOT_SOURCE = "/data/latest_run.json";
const PROSPECTIVE_LEDGER_SOURCE = "/data/diagnostics/prospective_dashboard_summary_latest.json";
const PROMOTION_COMPARISON_SOURCE = "/data/diagnostics/promotion_shadow_active_comparison_latest.json";
const RESEARCH_READINESS_SOURCE = "/data/diagnostics/research_readiness_health_latest.json";
const MODEL_GOVERNANCE_SOURCE = "/data/diagnostics/model_governance_summary_latest.json";
const BASE_BUDGET_USD = 300.0;
const HARD_COST_CEILING_USD = 600.0;
const PROSPECTIVE_RECENT_ROW_LIMIT = 24;

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
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
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

function laneLabel(lane) {
  if (lane === "live") return "Live";
  if (lane === "moonshot") return "Moonshot";
  if (lane === "manual_override") return "Historical Research";
  return "Shadow";
}

function laneClass(lane) {
  if (lane === "live") return "is-live";
  if (lane === "moonshot") return "is-moonshot";
  if (lane === "manual_override") return "is-shadow";
  return "is-shadow";
}

function regimeToneClass(mode) {
  if (String(mode).toLowerCase() === "risk_on") return "is-call";
  if (String(mode).toLowerCase() === "risk_off") return "is-put";
  return "is-neutral";
}

function sentenceList(notes, fallback) {
  if (Array.isArray(notes) && notes.length) return notes.join(". ");
  return fallback;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function domSafeId(value) {
  return String(value ?? "").replace(/[^a-z0-9]/gi, "_");
}

function signedNumber(value, digits = 2, suffix = "") {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}${suffix}`;
}

function evidenceStatus(value) {
  const normalized = String(value || "").toLowerCase();
  if (["pass", "green", "healthy"].includes(normalized)) return "pass";
  if (["pending", "collecting_evidence", "not_ready", "insufficient_data", "amber", "hold"].includes(normalized)) return "hold";
  return "fail";
}

function evidenceStatusLabel(value) {
  const status = evidenceStatus(value);
  return status === "pass" ? "Pass" : status === "hold" ? "Hold" : "Fail";
}

async function loadJsonArtifact(source) {
  const response = await fetch(source, { cache: "no-store" });
  if (!response.ok) throw new Error(`${source} returned ${response.status}`);
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("json")) {
    throw new Error(`${source} returned ${contentType || "non-JSON content"}`);
  }
  return response.json();
}

async function readBrokerJson(response, unavailableMessage = "Tradier is unavailable in this session.") {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("json")) throw new Error(unavailableMessage);
  return response.json();
}

let WORKBENCH_STATE = {
  comparison: null,
  readiness: null,
  governance: null,
  selectedWindow: "3_month",
};

function governanceMetricValue(metric) {
  const value = metric?.value;
  if (value === null || value === undefined) return "—";
  if (metric?.format === "integer") return integer(value);
  if (metric?.format === "decimal") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(3) : "—";
  }
  return String(value);
}

function renderModelGovernance() {
  const governance = WORKBENCH_STATE.governance || {};
  const capture = governance.data_capture || {};
  const authority = governance.live_authority || {};

  const captureTone = evidenceStatus(capture.status || "hold");
  const captureCard = document.getElementById("governance-capture-card");
  if (captureCard) captureCard.className = `governance-overview-card is-${captureTone}`;
  setText("governance-capture-status", captureTone === "pass" ? "Healthy" : captureTone === "fail" ? "Action needed" : "Awaiting capture");
  setText("governance-capture-note", capture.headline || "Trajectory health has not been published yet.");

  const modelCard = document.getElementById("governance-model-card");
  if (modelCard) modelCard.className = "governance-overview-card is-pass";
  setText("governance-model-status", "Production v2");
  setText("governance-model-note", "One volatility/contract rank with binding execution and after-cost controls.");

  const authorityCard = document.getElementById("governance-authority-card");
  if (authorityCard) authorityCard.className = "governance-overview-card is-pass";
  setText("governance-authority-status", "Protected");
  setText("governance-authority-note", "Only Council's production board can influence Tradier execution.");
  setText("governance-generated", governance.generated_at_utc ? `Updated ${formatTs(governance.generated_at_utc)}` : "Governance artifact unavailable");
}

function workbenchMetricRow(label, active, shadow, difference, check) {
  const status = evidenceStatus(check);
  return `
    <tr>
      <th scope="row">${escapeHtml(label)}</th>
      <td>${escapeHtml(active)}</td>
      <td>${escapeHtml(shadow)}</td>
      <td>${escapeHtml(difference)}</td>
      <td><span class="evidence-pill is-${status}">${evidenceStatusLabel(status)}</span></td>
    </tr>`;
}

function workbenchGateRow(title, summary, statusValue) {
  const status = evidenceStatus(statusValue);
  const icon = status === "pass" ? "check_circle" : status === "hold" ? "pause_circle" : "error";
  return `
    <div class="workbench-gate-row">
      <span class="material-symbols-outlined gate-icon is-${status}" aria-hidden="true">${icon}</span>
      <div>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(summary)}</span>
      </div>
      <span class="evidence-pill is-${status}">${evidenceStatusLabel(status)}</span>
    </div>`;
}

function drawWorkbenchComparison(windowRow) {
  const canvas = document.getElementById("workbench-comparison-chart");
  if (!canvas || !windowRow) return;
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(rect.width, 320);
  const height = 165;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const active = Number(windowRow.active?.net_pnl || 0);
  const shadow = Number(windowRow.shadow?.net_pnl || 0);
  const bound = Math.max(Math.abs(active), Math.abs(shadow), 1) * 1.18;
  const plotLeft = 96;
  const plotRight = width - 32;
  const zeroX = plotLeft + ((0 + bound) / (bound * 2)) * (plotRight - plotLeft);
  const scaleX = (value) => plotLeft + ((value + bound) / (bound * 2)) * (plotRight - plotLeft);

  ctx.strokeStyle = "rgba(232, 234, 240, 0.16)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(plotLeft, 22);
  ctx.lineTo(plotLeft, 130);
  ctx.moveTo(zeroX, 18);
  ctx.lineTo(zeroX, 137);
  ctx.stroke();

  const bars = [
    { label: "Active", value: active, y: 34, color: "#68b98a" },
    { label: "Shadow", value: shadow, y: 84, color: "#e8b44d" },
  ];
  ctx.font = "500 13px Inter, system-ui, sans-serif";
  ctx.textBaseline = "middle";
  for (const bar of bars) {
    ctx.fillStyle = "rgba(232, 234, 240, 0.7)";
    ctx.fillText(bar.label, 14, bar.y + 16);
    const endX = scaleX(bar.value);
    ctx.fillStyle = bar.color;
    ctx.fillRect(Math.min(zeroX, endX), bar.y, Math.max(Math.abs(endX - zeroX), 2), 32);
    ctx.fillStyle = "#e8eaf0";
    ctx.textAlign = bar.value >= 0 ? "left" : "right";
    ctx.fillText(signed(bar.value), bar.value >= 0 ? endX + 8 : endX - 8, bar.y + 16);
    ctx.textAlign = "left";
  }

  ctx.fillStyle = "rgba(232, 234, 240, 0.5)";
  ctx.font = "12px Inter, system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(money(-bound), plotLeft, 151);
  ctx.textAlign = "center";
  ctx.fillText("$0", zeroX, 151);
  ctx.textAlign = "right";
  ctx.fillText(money(bound), plotRight, 151);
}

function renderResearchWorkbench() {
  const comparison = WORKBENCH_STATE.comparison || {};
  const readiness = WORKBENCH_STATE.readiness || {};
  const windows = Array.isArray(comparison.windows) ? comparison.windows : [];
  const selected = windows.find((row) => row.window === WORKBENCH_STATE.selectedWindow) || windows[0];
  const overallStatus = readiness.status || comparison.decision || "not_ready";
  const overallTone = evidenceStatus(overallStatus);

  const overall = document.getElementById("workbench-overall-status");
  if (overall) {
    overall.textContent = overallTone === "pass" ? "Ready" : overallTone === "hold" ? "Hold" : "Blocked";
    overall.className = `workbench-status is-${overallTone}`;
  }
  setText("workbench-overall-note", readiness.headline || "Canonical promotion evidence is incomplete.");

  const banner = document.getElementById("research-claims-banner");
  if (banner) {
    banner.className = `research-claims-banner is-${overallTone}`;
    const icon = banner.querySelector(".material-symbols-outlined");
    if (icon) icon.textContent = overallTone === "pass" ? "verified" : overallTone === "hold" ? "pause_circle" : "gpp_bad";
    const title = banner.querySelector("strong");
    if (title) title.textContent = readiness.research_claims_allowed ? "Research claims allowed" : "Research claims blocked";
  }
  setText("research-claims-message", readiness.headline || "Promotion comparison is not ready.");

  const runStamp = comparison.generated_at_utc ? new Date(comparison.generated_at_utc) : null;
  const runId = runStamp && !Number.isNaN(runStamp.getTime())
    ? `PROMO-${runStamp.toISOString().slice(0, 10).replaceAll("-", "")}`
    : "Promotion artifact unavailable";
  setText("workbench-run-id", runId);
  setText("workbench-dataset", `${comparison.artifact || "promotion comparison"} · schema ${comparison.schema_version || "—"}`);
  setText("workbench-cutoff", comparison.as_of_utc ? `As of ${formatTs(comparison.as_of_utc)}` : "No reviewed cutoff");
  setText("workbench-generated", comparison.generated_at_utc ? formatTs(comparison.generated_at_utc) : "Artifact unavailable");

  const decision = document.getElementById("promotion-decision");
  if (decision) {
    decision.textContent = evidenceStatusLabel(comparison.decision || overallStatus);
    decision.className = `gate-summary-status is-${evidenceStatus(comparison.decision || overallStatus)}`;
  }

  const comparisonStatus = document.getElementById("comparison-status");
  if (comparisonStatus) {
    comparisonStatus.className = `comparison-status is-${evidenceStatus(selected?.status || comparison.decision)}`;
    comparisonStatus.textContent = selected
      ? `${String(selected.window || "").replace("_", " ")} · ${String(selected.status || "not ready").replaceAll("_", " ")} · ${selected.coverage_complete ? "coverage complete" : "coverage incomplete"}`
      : "No canonical comparison window is available.";
  }

  if (selected) {
    drawWorkbenchComparison(selected);
    const active = selected.active || {};
    const shadow = selected.shadow || {};
    const clusteredActive = selected.cluster_adjusted?.active || active;
    const clusteredShadow = selected.cluster_adjusted?.shadow || shadow;
    const inference = selected.paired_day_inference || {};
    const inferenceInterval = inference.confidence_interval_95 || {};
    const checks = selected.checks || {};
    const summary = document.getElementById("comparison-summary");
    if (summary) {
      summary.innerHTML = [
        summaryItemHtml("Active trades", integer(active.trades)),
        summaryItemHtml("Shadow trades", integer(shadow.trades)),
        summaryItemHtml("Net P&L lift", signed(Number(shadow.net_pnl || 0) - Number(active.net_pnl || 0))),
        summaryItemHtml("Spread drag", signed(-Number(shadow.spread_cost || 0))),
        summaryItemHtml("Paired market days", integer(inference.paired_days)),
      ].join("");
    }

    const gates = document.getElementById("workbench-gates");
    const readinessGates = Array.isArray(readiness.gates) ? readiness.gates : [];
    const completionGate = readinessGates.find((gate) => gate.code === "label_cohort_completion");
    const freshnessGate = readinessGates.find((gate) => gate.code === "freshness");
    if (gates) {
      gates.innerHTML = [
        workbenchGateRow("Minimum independent evidence", `${integer(selected.cluster_adjusted?.active?.trades)} active / ${integer(selected.cluster_adjusted?.shadow?.trades)} shadow daily-contract exposures`, checks.minimum_evidence ? "pass" : "hold"),
        workbenchGateRow("After-cost P&L lift", `Shadow ${signed(shadow.net_pnl)} vs active ${signed(active.net_pnl)}`, checks.after_cost_pnl_lift ? "pass" : "fail"),
        workbenchGateRow("Positive after costs", `Cluster-adjusted shadow return ${pct(clusteredShadow.net_return_pct)}`, checks.absolute_profitability ? "pass" : "fail"),
        workbenchGateRow("Uncertainty bound", `${integer(inference.paired_days)} paired days · 95% CI ${pct(inferenceInterval.lower)} to ${pct(inferenceInterval.upper)}`, checks.uncertainty_robustness ? "pass" : Number(inference.paired_days || 0) < 30 ? "hold" : "fail"),
        workbenchGateRow("Calibration non-worse", `Brier ${Number(shadow.calibration?.brier_score || 0).toFixed(3)} vs ${Number(active.calibration?.brier_score || 0).toFixed(3)}`, checks.calibration_non_worse ? "pass" : "fail"),
        workbenchGateRow("Sharpe non-worse", `${Number(shadow.sharpe_ratio || 0).toFixed(2)} vs ${Number(active.sharpe_ratio || 0).toFixed(2)}`, checks.sharpe_non_worse ? "pass" : "fail"),
        workbenchGateRow("Drawdown non-worse", `${pct(shadow.max_drawdown)} vs ${pct(active.max_drawdown)}`, checks.drawdown_non_worse ? "pass" : "fail"),
        workbenchGateRow("Repeated-scan robustness", "Daily contract clusters must preserve the challenger advantage.", checks.cluster_robustness ? "pass" : "fail"),
        workbenchGateRow("Outcome cohort complete", completionGate?.summary || "Completion evidence unavailable", completionGate?.status || "fail"),
        workbenchGateRow("Evidence fresh", freshnessGate?.summary || "Freshness evidence unavailable", freshnessGate?.status || "fail"),
      ].join("");
    }

    const body = document.getElementById("workbench-diagnostics-body");
    if (body) {
      body.innerHTML = [
        workbenchMetricRow("Net executable P&L", money(active.net_pnl), money(shadow.net_pnl), signed(Number(shadow.net_pnl || 0) - Number(active.net_pnl || 0)), checks.after_cost_pnl_lift ? "pass" : "fail"),
        workbenchMetricRow("Net return", pct(active.net_return_pct), pct(shadow.net_return_pct), signedNumber((Number(shadow.net_return_pct || 0) - Number(active.net_return_pct || 0)) * 100, 1, " pp"), checks.after_cost_pnl_lift ? "pass" : "fail"),
        workbenchMetricRow("Cluster-adjusted return", pct(clusteredActive.net_return_pct), pct(clusteredShadow.net_return_pct), signedNumber((Number(clusteredShadow.net_return_pct || 0) - Number(clusteredActive.net_return_pct || 0)) * 100, 1, " pp"), checks.absolute_profitability && checks.after_cost_pnl_lift ? "pass" : "fail"),
        workbenchMetricRow("Paired-day return lift", "Same-day baseline", pct(inference.mean_return_lift), `${pct(inferenceInterval.lower)} to ${pct(inferenceInterval.upper)} CI`, checks.uncertainty_robustness ? "pass" : Number(inference.paired_days || 0) < 30 ? "hold" : "fail"),
        workbenchMetricRow("Win rate", pct(active.win_rate), pct(shadow.win_rate), signedNumber((Number(shadow.win_rate || 0) - Number(active.win_rate || 0)) * 100, 1, " pp"), "hold"),
        workbenchMetricRow("Sharpe", Number(active.sharpe_ratio || 0).toFixed(2), Number(shadow.sharpe_ratio || 0).toFixed(2), signedNumber(Number(shadow.sharpe_ratio || 0) - Number(active.sharpe_ratio || 0)), checks.sharpe_non_worse ? "pass" : "fail"),
        workbenchMetricRow("Max drawdown", pct(active.max_drawdown), pct(shadow.max_drawdown), signedNumber((Number(shadow.max_drawdown || 0) - Number(active.max_drawdown || 0)) * 100, 1, " pp"), checks.drawdown_non_worse ? "pass" : "fail"),
        workbenchMetricRow("Brier score", Number(active.calibration?.brier_score || 0).toFixed(3), Number(shadow.calibration?.brier_score || 0).toFixed(3), signedNumber(Number(shadow.calibration?.brier_score || 0) - Number(active.calibration?.brier_score || 0), 3), checks.calibration_non_worse ? "pass" : "fail"),
        workbenchMetricRow("Calibration ECE", Number(active.calibration?.expected_calibration_error || 0).toFixed(3), Number(shadow.calibration?.expected_calibration_error || 0).toFixed(3), signedNumber(Number(shadow.calibration?.expected_calibration_error || 0) - Number(active.calibration?.expected_calibration_error || 0), 3), checks.calibration_non_worse ? "pass" : "fail"),
        workbenchMetricRow("Observed trades", integer(active.trades), integer(shadow.trades), signedNumber(Number(shadow.trades || 0) - Number(active.trades || 0), 0), selected.coverage_complete ? "pass" : "hold"),
      ].join("");
    }

    renderCockpitEvidence(selected);
  }

  const lineage = document.getElementById("workbench-lineage");
  if (lineage) {
    const artifactStatus = readiness.gates?.find((gate) => gate.code === "artifact_integrity")?.status || "fail";
    const promotionStatus = comparison.decision || "not_ready";
    lineage.innerHTML = [
      { label: "Artifact integrity", detail: readiness.gates?.find((gate) => gate.code === "artifact_integrity")?.summary || "Not verified", status: artifactStatus },
      { label: "Canonical replay", detail: comparison.generated_at_utc ? `Generated ${formatTs(comparison.generated_at_utc)}` : "Artifact missing", status: windows.length ? "pass" : "fail" },
      { label: "Evidence cutoff", detail: comparison.as_of_utc ? formatTs(comparison.as_of_utc) : "No cutoff", status: selected?.coverage_complete ? "pass" : "hold" },
      { label: "Current decision", detail: String(promotionStatus).replaceAll("_", " "), status: promotionStatus },
    ].map((item) => `
      <li class="is-${evidenceStatus(item.status)}">
        <span class="lineage-marker material-symbols-outlined" aria-hidden="true">${evidenceStatus(item.status) === "pass" ? "check_circle" : evidenceStatus(item.status) === "hold" ? "pause_circle" : "error"}</span>
        <div><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span></div>
      </li>`).join("");
  }
  renderModelGovernance();
}

function renderCockpitEvidence(windowRow) {
  if (!windowRow) return;
  const clustered = windowRow.cluster_adjusted || {};
  const shadow = clustered.shadow || windowRow.shadow || {};
  const independent = Number(clustered.independent_daily_contracts || 0);
  setText("evidence-independent", independent ? integer(independent) : "—");
  setText(
    "evidence-independent-note",
    independent
      ? `${integer(clustered.raw_recommendations || 0)} raw scans collapsed`
      : "Daily contract clusters",
  );
  setText("evidence-pnl", signed(shadow.net_pnl));
  setText(
    "evidence-calibration",
    shadow.calibration?.brier_score == null
      ? "—"
      : Number(shadow.calibration.brier_score).toFixed(3),
  );
  setText("evidence-drawdown", pct(shadow.max_drawdown));
}

async function loadResearchWorkbench() {
  const governanceResult = await Promise.allSettled([
    loadJsonArtifact(MODEL_GOVERNANCE_SOURCE),
  ]);
  WORKBENCH_STATE.governance = governanceResult[0].status === "fulfilled"
    ? governanceResult[0].value
    : {
        status: "hold",
        headline: "Production model-governance status is unavailable.",
        live_authority: {
          production_order_routing: false,
          summary: "Order routing is unavailable until governance status is restored.",
        },
      };
  renderModelGovernance();
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
      await fetch("/api/logout", {
        method: "POST",
        headers: { "content-type": "application/json" },
      });
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
  loading: false,
  lastLoadedAt: null,
  lastError: null,
  maxEntryCostBasisUsd: HARD_COST_CEILING_USD,
};

let POSITION_ADVICE = new Map();
let POSITION_ADVICE_EPOCH = 0;

function optionContractMeta(symbol) {
  const text = String(symbol || "")
    .trim()
    .toUpperCase();
  const rootMatch = text.match(/^([A-Z]{1,6})/);
  const sideMatch = text.match(/\d{6}([CP])\d{8}$/);
  return {
    symbol: text,
    root: rootMatch ? rootMatch[1] : text,
    side: sideMatch ? (sideMatch[1] === "C" ? "call" : "put") : null,
  };
}

function positionMarkMeta(position) {
  const source = String(position?.current_value_source || "").toLowerCase();
  const markSource = String(position?.mark_source || "").toLowerCase();
  if (source === "broker") {
    return {
      label: "Broker mark",
      toneClass: "is-neutral",
      detail: "Value supplied directly by Tradier.",
    };
  }
  if (markSource === "mid") {
    return {
      label: "Live mid",
      toneClass: "is-positive",
      detail: "Value derived from bid/ask midpoint.",
    };
  }
  if (markSource === "last") {
    return {
      label: "Last trade",
      toneClass: "is-neutral",
      detail: "Value derived from the most recent trade.",
    };
  }
  if (markSource === "close") {
    return {
      label: "Prev close",
      toneClass: "is-warning",
      detail: "Value derived from the previous close.",
    };
  }
  if (markSource === "bid") {
    return {
      label: "Bid mark",
      toneClass: "is-warning",
      detail: "Value derived from the live bid.",
    };
  }
  if (markSource === "ask") {
    return {
      label: "Ask mark",
      toneClass: "is-warning",
      detail: "Value derived from the live ask.",
    };
  }
  return {
    label: "Awaiting mark",
    toneClass: "is-muted",
    detail: "Refresh Tradier to pull the latest quote-backed value.",
  };
}

function adviceToneClass(action) {
  return String(action || "").toLowerCase() === "sell"
    ? "is-sell"
    : "is-hold";
}

function formatRiskFlags(flags) {
  if (!Array.isArray(flags) || !flags.length) return "";
  return flags
    .map((flag) =>
      String(flag || "")
        .replaceAll("_", " ")
        .trim(),
    )
    .filter(Boolean)
    .join(" · ");
}

function renderPositionAdviceHtml(symbol) {
  const advice = POSITION_ADVICE.get(symbol);
  if (!advice) {
    return `
      <div id="position-advice-${domSafeId(symbol)}" class="position-ai-brief is-loading">
        <div class="position-ai-head">
          <span class="position-ai-label">Exit AI</span>
          <span class="position-ai-pill is-loading">Loading…</span>
        </div>
        <p class="position-ai-text">Checking whether this live contract looks better held or sold.</p>
      </div>
    `;
  }

  const tone = adviceToneClass(advice.action);
  const confidence =
    Number.isFinite(Number(advice.confidence)) && Number(advice.confidence) > 0
      ? `${Math.round(Number(advice.confidence) * 100)}% confidence`
      : "Rule-based";
  const headline = advice.headline ? escapeHtml(advice.headline) : advice.action === "sell" ? "Sell bias" : "Hold bias";
  const rationale = advice.rationale
    ? escapeHtml(advice.rationale)
    : advice.action === "sell"
      ? "Conditions favor taking the exit."
      : "Conditions still support holding.";
  const flags = formatRiskFlags(advice.risk_flags);

  return `
    <div id="position-advice-${domSafeId(symbol)}" class="position-ai-brief ${tone}">
      <div class="position-ai-head">
        <span class="position-ai-label">Exit AI</span>
        <span class="position-ai-pill ${tone}">${escapeHtml(String(advice.action || "hold").toUpperCase())}</span>
      </div>
      <p class="position-ai-title">${headline}</p>
      <p class="position-ai-text">${rationale}</p>
      <div class="position-ai-meta">
        <span>${escapeHtml(confidence)}</span>
        ${flags ? `<span>${escapeHtml(flags)}</span>` : ""}
      </div>
    </div>
  `;
}

function renderPositionsMeta() {
  const syncEl = document.getElementById("positions-sync-status");
  const refreshBtn = document.getElementById("positions-refresh-btn");
  if (syncEl) {
    let text = "Waiting for Tradier account data.";
    let className = "sync-line positions-sync-status";
    if (BROKER_STATE.loading) {
      text = "Refreshing live Tradier account…";
      className += " is-loading";
    } else if (BROKER_STATE.lastError) {
      text = `Refresh failed: ${BROKER_STATE.lastError}`;
      className += " is-error";
    } else if (BROKER_STATE.lastLoadedAt) {
      text = `Synced ${formatTs(BROKER_STATE.lastLoadedAt)} · ${timeAgo(BROKER_STATE.lastLoadedAt)}`;
      className += " is-live";
    }
    syncEl.textContent = text;
    syncEl.className = className;
  }
  if (refreshBtn) {
    refreshBtn.disabled = BROKER_STATE.loading;
    refreshBtn.textContent = BROKER_STATE.loading
      ? "Refreshing…"
      : "Refresh Tradier";
  }
}

async function loadAccount() {
  POSITION_ADVICE_EPOCH += 1;
  POSITION_ADVICE = new Map();
  BROKER_STATE = {
    ...BROKER_STATE,
    loading: true,
  };
  renderPositionsMeta();
  try {
    const r = await fetch("/api/tradier/account", { cache: "no-store" });
    const contentType = r.headers.get("content-type") || "";
    if (!contentType.includes("json")) {
      throw new Error("Tradier account is unavailable in this session.");
    }
    const data = await readBrokerJson(
      r,
      "Tradier account is unavailable in this session.",
    );
    if (data.ok && data.broker) {
      BROKER_STATE = {
        ...BROKER_STATE,
        configured: data.broker.configured,
        mode: data.broker.mode || data.broker.environment || "offline",
        liveTradingEnabled: data.broker.liveTradingEnabled,
        balances: data.balances || data.broker.balances || null,
        positions: data.positions || data.broker.positions || [],
        orders: data.orders || data.broker.orders || [],
        maxContracts: data.broker.maxContracts || 3,
        maxEntryCostBasisUsd:
          Number(data.broker.maxEntryCostBasisUsd) || HARD_COST_CEILING_USD,
        loading: false,
        lastLoadedAt: new Date().toISOString(),
        lastError: null,
      };
    } else {
      BROKER_STATE = {
        ...BROKER_STATE,
        loading: false,
        lastError: data.error || "Tradier account request failed.",
      };
    }
  } catch (error) {
    BROKER_STATE = {
      ...BROKER_STATE,
      loading: false,
      lastError: String(error.message || error),
    };
  }
  renderRibbon();
  renderPositionsMeta();
  renderPositions();
  renderOrders();
  bindPositionsTable();
  hydratePositionAdvice(BROKER_STATE.positions, POSITION_ADVICE_EPOCH).catch(
    () => {},
  );
}

function renderRibbon() {
  const bal = BROKER_STATE.balances || {};
  setText("ribbon-equity", money(bal.total_equity));
  setText("ribbon-obp", money(bal.option_buying_power));
  setText("ribbon-cash", money(bal.total_cash));
  const pl = bal.close_pl ?? bal.open_pl ?? null;
  const plEl = document.getElementById("ribbon-pl");
  if (plEl) {
    plEl.textContent = pl !== null ? signed(pl) : "--";
    plEl.className =
      "ribbon-stat-value" +
      (pl > 0 ? " is-positive" : pl < 0 ? " is-negative" : "");
  }
  setText("ribbon-positions", String(BROKER_STATE.positions.length));

  const modeEl = document.getElementById("ribbon-broker-mode");
  if (modeEl) {
    const m = BROKER_STATE.mode || "offline";
    const label = BROKER_STATE.configured
      ? m === "live"
        ? "LIVE"
        : "SANDBOX"
      : "OFFLINE";
    modeEl.textContent = label;
    modeEl.className =
      "ribbon-mode-pill" +
      (m === "live" && BROKER_STATE.configured
        ? " is-live"
        : BROKER_STATE.configured
          ? " is-sandbox"
          : " is-offline");
  }
}

function renderPositions() {
  const tbody = document.getElementById("positions-tbody");
  if (!tbody) return;
  const rows = BROKER_STATE.positions || [];
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:24px;color:var(--text-muted);font-family:var(--font-data);font-size:.78rem;">No open positions found.</td></tr>`;
    renderCockpitPositions();
    return;
  }
  tbody.innerHTML = rows
    .map((pos) => {
      const cv = pos.current_value ?? null;
      const cb = pos.cost_basis ?? null;
      const pl = pos.open_pl ?? (cv !== null && cb !== null ? cv - cb : null);
      const plPct = pl !== null && cb ? pl / cb : null;
      const contract = optionContractMeta(pos.symbol);
      const sym = contract.symbol;
      const isOpt = Boolean(contract.side);
      const tone =
        contract.side === "call"
          ? "is-call-cell"
          : contract.side === "put"
            ? "is-put-cell"
            : "";
      const markMeta = positionMarkMeta(pos);
      const markPrice = Number.isFinite(Number(pos.mark_price))
        ? money(pos.mark_price)
        : null;
      const instrumentChip = contract.side
        ? `<span class="position-chip ${contract.side === "call" ? "is-call" : "is-put"}">${contract.side.toUpperCase()}</span>`
        : `<span class="position-chip is-neutral">EQUITY</span>`;
      const statusChip = `<span class="position-chip ${markMeta.toneClass}">${escapeHtml(markMeta.label)}</span>`;
      const adviceHtml = isOpt ? renderPositionAdviceHtml(sym) : "";
      const closeBtnClass = `mini-action close-position-btn${
        POSITION_ADVICE.get(sym)?.action === "sell" ? " is-advised-sell" : ""
      }`;
      const actionCell = isOpt
        ? `<button class="${closeBtnClass}" type="button" data-contract="${sym}" data-qty="${pos.quantity}">${POSITION_ADVICE.get(sym)?.action === "sell" ? "Close Suggested" : "Close"}</button>`
        : ``;
      return `<tr class="position-row ${cv === null ? "is-mark-pending" : "is-marked"}">
      <td data-label="Symbol" class="position-cell-symbol ${tone}">
        <div class="position-symbol-stack">
          <strong class="position-symbol">${escapeHtml(sym)}</strong>
          <span class="position-underlier">${escapeHtml(contract.root)}</span>
        </div>
      </td>
      <td data-label="Status" class="position-cell-status">
        <div class="position-status-stack">
          <div class="position-chip-row">${instrumentChip}${statusChip}</div>
          <span class="position-detail">${escapeHtml(markMeta.detail)}</span>
          ${adviceHtml}
        </div>
      </td>
      <td data-label="Qty">${integer(pos.quantity)}</td>
      <td data-label="Cost Basis" class="is-num">
        <div class="position-value-stack">
          <strong class="position-primary-value">${money(cb)}</strong>
          <span class="position-detail">${cb !== null ? "Capital committed" : "Waiting on basis"}</span>
        </div>
      </td>
      <td data-label="Market Value" class="is-num">
        <div class="position-value-stack">
          <strong class="position-primary-value">${money(cv)}</strong>
          <span class="position-detail">${markPrice ? `Mark ${markPrice}` : "No live mark yet"}</span>
        </div>
      </td>
      <td data-label="P&L" class="is-num ${pl !== null ? (pl >= 0 ? "is-positive" : "is-negative") : ""}">
        <div class="position-value-stack">
          <strong class="position-primary-value">${pl !== null ? signed(pl) : "--"}</strong>
          <span class="position-detail ${pl !== null ? (pl >= 0 ? "is-positive" : "is-negative") : ""}">${plPct !== null ? pct(plPct) : "Pending mark"}</span>
        </div>
      </td>
      <td data-label="Acquired">
        <div class="position-value-stack">
          <strong class="position-primary-value">${pos.date_acquired ? pos.date_acquired.slice(0, 10) : "--"}</strong>
          <span class="position-detail">Trade date</span>
        </div>
      </td>
      <td data-label="Action" class="position-cell-actions" style="text-align:right;">${actionCell}</td>
    </tr>`;
    })
    .join("");
  renderCockpitPositions();
}

async function fetchPositionAdvice(position) {
  try {
    const r = await fetch("/api/ai/position-advice", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ position }),
    });
    const data = await r.json();
    return data.ok ? data.advice || null : null;
  } catch {
    return null;
  }
}

function updatePositionAdviceDom(symbol, advice) {
  const container = document.getElementById(`position-advice-${domSafeId(symbol)}`);
  if (container) {
    container.outerHTML = renderPositionAdviceHtml(symbol);
  }

  const closeBtn = document.querySelector(
    `.close-position-btn[data-contract="${symbol}"]`,
  );
  if (closeBtn) {
    closeBtn.classList.toggle("is-advised-sell", advice?.action === "sell");
    closeBtn.textContent = advice?.action === "sell" ? "Close Suggested" : "Close";
  }
}

async function hydratePositionAdvice(positions, epoch) {
  const optionPositions = (positions || []).filter(
    (position) => Boolean(optionContractMeta(position.symbol).side),
  );

  const activeSymbols = new Set(
    optionPositions.map((position) => optionContractMeta(position.symbol).symbol),
  );
  POSITION_ADVICE = new Map(
    [...POSITION_ADVICE.entries()].filter(([symbol]) => activeSymbols.has(symbol)),
  );

  for (const position of optionPositions) {
    const symbol = optionContractMeta(position.symbol).symbol;
    POSITION_ADVICE.delete(symbol);
    updatePositionAdviceDom(symbol, null);
  }

  await Promise.all(
    optionPositions.map(async (position) => {
      const symbol = optionContractMeta(position.symbol).symbol;
      const advice = await fetchPositionAdvice(position);
      if (epoch !== POSITION_ADVICE_EPOCH) {
        return;
      }
      if (advice) {
        POSITION_ADVICE.set(symbol, advice);
        updatePositionAdviceDom(symbol, advice);
      }
    }),
  );
}

function renderOrders() {
  const tbody = document.getElementById("orders-tbody");
  if (!tbody) return;
  const rows = (BROKER_STATE.orders || []).slice(0, 10);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-muted);font-family:var(--font-data);font-size:.78rem;">No recent orders found.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map((o) => {
      const isBuy = String(o.side || "").includes("buy");
      const rejectionReason = orderIssueText(o);
      const contractMeta = rejectionReason
        ? `<div class="order-status-note" title="${escapeHtml(rejectionReason)}">${escapeHtml(rejectionReason)}</div>`
        : "";
      return `<tr>
      <td data-label="Status"><span style="font-family:var(--font-data);font-size:.65rem;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border-radius:99px;background:rgba(255,255,255,.04);border:1px solid var(--border)">${o.status || "open"}</span></td>
      <td data-label="Contract" style="font-family:var(--font-data);font-size:.72rem;word-break:break-all">${o.option_symbol || o.symbol || "--"}${contractMeta}</td>
      <td data-label="Side" class="${isBuy ? "is-call-cell" : "is-put-cell"}">${o.side || "--"}</td>
      <td data-label="Qty" class="is-num">${integer(o.quantity)}</td>
      <td data-label="Price" class="is-num">${o.price ? money(o.price) : "--"}</td>
      <td data-label="Fill" class="is-num">${o.avg_fill_price ? money(o.avg_fill_price) : "--"}</td>
      <td data-label="Date" style="font-family:var(--font-data);font-size:.7rem;color:var(--text-muted)">${o.create_date ? o.create_date.slice(0, 10) : "--"}</td>
    </tr>`;
    })
    .join("");
}

function bindPositionsControls() {
  const refreshBtn = document.getElementById("positions-refresh-btn");
  if (!refreshBtn || refreshBtn.dataset.bound === "true") return;
  refreshBtn.dataset.bound = "true";
  refreshBtn.addEventListener("click", () => {
    loadAccount().catch(() => {});
  });
}

function bindBoardControls() {
  const refreshBtn = document.getElementById("board-refresh-btn");
  if (!refreshBtn || refreshBtn.dataset.bound === "true") return;
  refreshBtn.dataset.bound = "true";
  refreshBtn.addEventListener("click", () => {
    refreshBoard().catch(() => {});
  });
}

// ── Snapshot / Board ────────────────────────────────────────────────────────

let SNAPSHOT = null;
let PROSPECTIVE_LEDGER = null;
let LIVE_QUOTES = new Map();
let BOARD_STATE = {
  loading: false,
  fetchedAt: null,
  snapshotGeneratedAt: null,
  lastError: null,
};
let PROSPECTIVE_STATE = {
  loading: false,
  updatedAt: null,
  lastError: null,
};
let COCKPIT_SIGNALS = [];
let COCKPIT_SIGNAL_INDEX = 0;

function cockpitCandidateThesis(candidate, payload) {
  const notes = Array.isArray(candidate?.notes) ? candidate.notes : [];
  const riskFlags = Array.isArray(candidate?.council_risk_flags)
    ? candidate.council_risk_flags.map((flag) => String(flag).replaceAll("_", " "))
    : [];
  const primary = notes.find((note) => !String(note).toLowerCase().includes("model"));
  const regime = String(payload?.regime?.mode || "unknown").replaceAll("_", " ");
  return sentenceList(
    [primary, riskFlags[0] ? `Risk flag: ${riskFlags[0]}` : null].filter(Boolean),
    `The candidate is being evaluated against the ${regime} regime and executable-cost gates.`,
  );
}

function renderCockpitSignalSelector() {
  const root = document.getElementById("signal-selector");
  if (!root) return;
  if (COCKPIT_SIGNALS.length <= 1) {
    root.innerHTML = "";
    root.hidden = true;
    return;
  }
  root.hidden = false;
  root.innerHTML = COCKPIT_SIGNALS.map(({ candidate, lane }, index) => {
    const active = index === COCKPIT_SIGNAL_INDEX;
    const label = candidate.symbol || optionContractMeta(candidate.contract_symbol).root || `Signal ${index + 1}`;
    return `<button class="signal-choice${active ? " is-active" : ""}" type="button" data-signal-index="${index}" aria-pressed="${active}">
      <span>${escapeHtml(label)}</span><small>${escapeHtml(lane)}</small>
    </button>`;
  }).join("");
  root.querySelectorAll(".signal-choice").forEach((button) => {
    button.addEventListener("click", () => {
      COCKPIT_SIGNAL_INDEX = Number(button.dataset.signalIndex) || 0;
      renderCockpitSignal(SNAPSHOT);
    });
  });
}

function noTradeFunnelHtml(payload) {
  const summary = payload?.summary || {};
  const councilSummary = payload?.council?.summary || {};
  const audit = councilSummary.abstain_audit || {};
  const researchObservations = Number(
    summary.counterfactual_signal_count
    || summary.scout_side_aware_no_trade_disagreements
    || 0,
  );
  const stages = [
    { label: "Universe", value: Number(summary.universe_size || 0), note: "symbols reviewed" },
    { label: "Scout", value: Number(summary.scout_signal_count || 0), note: "live-policy signals" },
    { label: "Forge", value: Number(summary.forge_candidate_count || 0), note: "executable contracts" },
    { label: "Council", value: Number(councilSummary.live_count || 0), note: "live approvals" },
  ];
  return `
    <details class="no-trade-funnel" open>
      <summary><span>Why no trade?</span><strong>${escapeHtml(audit.primary_reason_label || "No contract cleared every live gate.")}</strong></summary>
      <div class="no-trade-stages">
        ${stages.map((stage, index) => `
          <div class="no-trade-stage${index > 0 && stage.value === 0 ? " is-stop" : ""}">
            <span>${escapeHtml(stage.label)}</span><strong>${integer(stage.value)}</strong><small>${escapeHtml(stage.note)}</small>
          </div>`).join("")}
      </div>
      <p><span class="material-symbols-outlined" aria-hidden="true">visibility</span>${integer(researchObservations)} research-only no-trade observations were retained separately and had no live-policy effect.</p>
    </details>`;
}

function legendaryCardChrome({ ticker, gem, rarity, hold }) {
  return `
    <div class="card-foil" aria-hidden="true"></div>
    <div class="card-art" aria-hidden="true">
      <span class="card-art-glow"></span>
      <span class="card-rarity-gem${hold ? " is-sealed" : ""}"></span>
      <span class="card-symbol-giant">${ticker}</span>
      <span class="card-gem${hold ? "" : " card-gem-direction"}">${gem}</span>
      <span class="card-rarity">${rarity}</span>
    </div>`;
}

function renderCockpitSignal(payload) {
  const root = document.getElementById("cockpit-signal");
  if (!root) return;
  const live = payload?.council?.live_board || [];
  COCKPIT_SIGNALS = live.map((candidate) => ({ candidate, lane: "live" }));
  COCKPIT_SIGNAL_INDEX = Math.min(
    Math.max(COCKPIT_SIGNAL_INDEX, 0),
    Math.max(COCKPIT_SIGNALS.length - 1, 0),
  );

  if (!COCKPIT_SIGNALS.length) {
    root.innerHTML = `
      <div class="cockpit-signal-card trade-card is-hold is-legendary">
        ${legendaryCardChrome({
          ticker: "HOLD",
          gem: "—",
          rarity: "No legendary drawn",
          hold: true,
        })}
        <div class="card-body">
          <span class="signal-state is-hold">Hold</span>
          <p class="signal-lead">Council found no contract with sufficient after-cost edge and acceptable risk.</p>
          <div class="signal-contract"><span>Decision</span><h3>No trade today</h3><p>Abstention is an active portfolio decision. Refresh after the next model run.</p></div>
          ${noTradeFunnelHtml(payload)}
          <div class="signal-metrics">
            <div class="signal-metric"><span>Live candidates</span><strong>0</strong><small>No contract cleared Council.</small></div>
            <div class="signal-metric"><span>Regime</span><strong>${escapeHtml(String(payload?.regime?.mode || "—").replaceAll("_", " "))}</strong><small>${escapeHtml(payload?.regime?.source_symbol || "Market")}</small></div>
          </div>
          <button class="preview-order-button" type="button" disabled>No order to preview</button>
        </div>
      </div>`;
    setText("signal-index", "0 of 0");
    renderCockpitSignalSelector();
    syncCockpitSignalControls();
    return;
  }

  const { candidate, lane } = COCKPIT_SIGNALS[COCKPIT_SIGNAL_INDEX];
  const quote = LIVE_QUOTES.get(candidate.contract_symbol) || {};
  const ask = Number(quote.ask || candidate.ask || candidate.premium || 0.01);
  const confidence = Number(candidate.prob_positive_option_pnl);
  const afterCostEdge = Number(candidate.expected_edge_after_friction_pct);
  const maxLoss = Number(candidate.contract_cost || ask * 100);
  const generatedAt = payload.generated_at_utc || payload.timestamp;
  const stateLabel = "Trade ready";
  const stateClass = "is-ready";
  const previewLabel = "Preview order";
  const suggestedQty = suggestedEntryQuantity(
    ask,
    Number(candidate.allocation_weight || 1),
  );
  const estimatedDebit = suggestedQty * ask * 100;
  const riskFlags = (candidate.council_risk_flags || [])
    .map((flag) => String(flag).replaceAll("_", " "))
    .slice(0, 3);
  const optionType = String(candidate.option_type || "").toLowerCase();
  const tone = toneClass(optionType);
  const gemLabel = optionType === "put" ? "PUT" : optionType === "call" ? "CALL" : "LIVE";
  root.innerHTML = `
    <div class="cockpit-signal-card trade-card is-legendary ${tone}" data-ask="${ask}">
      ${legendaryCardChrome({
        ticker: escapeHtml(candidate.symbol || "—"),
        gem: gemLabel,
        rarity: "Legendary signal",
        hold: false,
      })}
      <div class="card-body">
      <span class="signal-state ${stateClass}">${stateLabel}</span>
      <p class="signal-lead">Model edge is positive after costs and the contract cleared Council risk controls.</p>
      <div class="signal-contract">
        <span>Contract thesis</span>
        <h3>${escapeHtml(candidate.contract_symbol || `${candidate.symbol} option`)}</h3>
        <p>${escapeHtml(cockpitCandidateThesis(candidate, payload))}</p>
      </div>
      <div class="signal-metrics">
        <div class="signal-metric"><span>Entry limit</span><strong>${money(ask)}</strong><small>Maximum one-contract premium ${money(maxLoss)}</small></div>
        <div class="signal-metric"><span>After-cost edge</span><strong>${pct(afterCostEdge)}</strong><small>Model estimate after friction</small></div>
        <div class="signal-metric"><span>Confidence</span><strong>${pct(confidence, 0)}</strong><small>Positive executable P&amp;L probability</small></div>
        <div class="signal-metric"><span>Freshness</span><strong>${generatedAt ? timeAgo(generatedAt) : "—"}</strong><small>${generatedAt ? formatTs(generatedAt) : "No timestamp"}</small></div>
      </div>
      <details class="signal-evidence-details">
        <summary>Why this signal?</summary>
        <div class="signal-evidence-grid">
          <div><span>Liquidity</span><strong>${pct(candidate.liquidity_score)}</strong><small>${integer(candidate.volume)} volume · ${integer(candidate.open_interest)} open interest</small></div>
          <div><span>Spread</span><strong>${pct(candidate.spread_pct)}</strong><small>Bid ${money(candidate.bid)} · ask ${money(ask)}</small></div>
          <div><span>Execution guard</span><strong>${candidate.execution_policy_passed === false ? "Blocked" : "Passed"}</strong><small>Conservative exit ${money(candidate.conservative_exit_bid ?? candidate.bid)} · round-trip drag ${pct(candidate.round_trip_spread_drag_pct)}</small></div>
          <div><span>Repeat entry</span><strong>${candidate.reentry_blocked ? "Blocked" : "Clear"}</strong><small>${candidate.prior_entry_ask != null ? `Prior ask ${money(candidate.prior_entry_ask)}` : "No recent matching live contract"}</small></div>
          <div><span>Projected move</span><strong>${pct(candidate.projected_move_pct)}</strong><small>Breakeven move ${pct(candidate.breakeven_move_pct)}</small></div>
          <div><span>Risk controls</span><strong>${riskFlags.length ? integer(riskFlags.length) : "0"}</strong><small>${escapeHtml(riskFlags.join(" · ") || "No flagged Council exceptions")}</small></div>
          ${candidate.payoff_shadow_return_q10 != null ? `<div><span>Research downside</span><strong>${pct(candidate.payoff_shadow_return_q10)}</strong><small>Observation-only q10 after-cost return</small></div>` : ""}
          ${candidate.payoff_shadow_return_q50 != null ? `<div><span>Research median</span><strong>${pct(candidate.payoff_shadow_return_q50)}</strong><small>Observation-only q50 after-cost return</small></div>` : ""}
          ${candidate.payoff_shadow_prob_fill_quality != null ? `<div><span>Research fill quality</span><strong>${pct(candidate.payoff_shadow_prob_fill_quality, 0)}</strong><small>Challenger estimate; no execution authority</small></div>` : ""}
          ${candidate.payoff_shadow_prob_target_before_stop != null ? `<div><span>Target before stop</span><strong>${pct(candidate.payoff_shadow_prob_target_before_stop, 0)}</strong><small>Observation-only path estimate</small></div>` : ""}
          ${candidate.path_hazard_target_probability != null ? `<div><span>Exit target incidence</span><strong>${pct(candidate.path_hazard_target_probability, 0)}</strong><small>Competing-risk research; advice only</small></div>` : ""}
          ${candidate.path_hazard_stop_probability != null ? `<div><span>Exit stop incidence</span><strong>${pct(candidate.path_hazard_stop_probability, 0)}</strong><small>${escapeHtml(String(candidate.path_exit_shadow_action || "hold_to_planned_exit").replaceAll("_", " "))}</small></div>` : ""}
        </div>
      </details>
      <div class="signal-order-row">
        <div class="signal-quantity-control stone-tablet">
          <span>Contracts</span>
          <div class="quantity-stepper">
            <button class="card-qty-step" type="button" data-step="-1" aria-label="Decrease contracts">−</button>
            <input class="card-qty-input" type="number" min="1" max="${brokerMaxContracts()}" value="${suggestedQty}" aria-label="Order quantity" />
            <button class="card-qty-step" type="button" data-step="1" aria-label="Increase contracts">+</button>
          </div>
          <small>Estimated debit <strong data-qty-cost>${money(estimatedDebit)}</strong></small>
        </div>
        <button class="preview-order-button card-preview-btn" type="button"
          data-contract="${escapeHtml(candidate.contract_symbol)}"
          data-symbol="${escapeHtml(candidate.symbol)}"
          data-lane="${escapeHtml(lane)}"
          data-ask="${ask}"
          data-alloc="${Number(candidate.allocation_weight || 1)}">
          ${previewLabel}<span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
        </button>
      </div>
      <p class="signal-safety"><span class="material-symbols-outlined" aria-hidden="true">lock</span>Preview first. Broker execution remains permissioned and separately confirmed.</p>
      </div>
    </div>`;
  setText("signal-index", `${COCKPIT_SIGNAL_INDEX + 1} of ${COCKPIT_SIGNALS.length}`);
  bindCardButtons();
  renderCockpitSignalSelector();
  syncCockpitSignalControls();
}

function renderMoonshotSidePick(payload) {
  const root = document.getElementById("moonshot-side-pick");
  if (!root) return;
  const lane = payload?.moonshot_lane || {};
  const pick = Array.isArray(lane.picks) ? lane.picks[0] : null;
  const policy = lane.policy || {};
  const tracking = policy.outcome_tracking || {};

  if (!pick) {
    root.className = "moonshot-side-pick is-quiet";
    root.innerHTML = `
      <strong>No side pick today</strong>
      <span>No contract cleared the Moonshot tail-upside gate. The primary Council decision is unchanged.</span>`;
    return;
  }

  const moonshot = pick.moonshot || {};
  const reasons = Array.isArray(moonshot.reasons) ? moonshot.reasons.slice(0, 2) : [];
  root.className = "moonshot-side-pick is-active";
  root.innerHTML = `
    <div class="moonshot-side-contract">
      <span>${escapeHtml(pick.symbol || "—")} · ${escapeHtml(String(pick.option_type || "option").toUpperCase())}</span>
      <strong>${escapeHtml(pick.contract_symbol || "Experimental contract")}</strong>
      <small>${escapeHtml(reasons.join(" · ") || "Tail-upside experiment cleared its dedicated gate.")}</small>
    </div>
    <div class="moonshot-side-metrics">
      <div><span>Tail score</span><strong>${Number(moonshot.tail_upside_score || 0).toFixed(2)}</strong></div>
      <div><span>Ask</span><strong>${money(pick.ask ?? pick.premium)}</strong></div>
      <div><span>Tracking</span><strong>${escapeHtml(tracking.dataset || "moonshot_outcomes")}</strong></div>
    </div>
    <p><span class="material-symbols-outlined" aria-hidden="true">lock</span>Visible experiment only. It does not affect the primary ensemble, Council, sizing, or Tradier routing.</p>`;
}

function syncCockpitSignalControls() {
  const previous = document.getElementById("signal-prev");
  const next = document.getElementById("signal-next");
  if (previous) previous.disabled = COCKPIT_SIGNAL_INDEX <= 0;
  if (next) next.disabled = COCKPIT_SIGNAL_INDEX >= COCKPIT_SIGNALS.length - 1;
}

function bindCockpitControls() {
  const previous = document.getElementById("signal-prev");
  const next = document.getElementById("signal-next");
  if (previous && previous.dataset.bound !== "true") {
    previous.dataset.bound = "true";
    previous.addEventListener("click", () => {
      COCKPIT_SIGNAL_INDEX -= 1;
      renderCockpitSignal(SNAPSHOT);
    });
  }
  if (next && next.dataset.bound !== "true") {
    next.dataset.bound = "true";
    next.addEventListener("click", () => {
      COCKPIT_SIGNAL_INDEX += 1;
      renderCockpitSignal(SNAPSHOT);
    });
  }

  const signalRegion = document.getElementById("signal");
  if (signalRegion && signalRegion.dataset.keyboardBound !== "true") {
    signalRegion.dataset.keyboardBound = "true";
    signalRegion.addEventListener("keydown", (event) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return;
      if (event.key === "ArrowLeft" && COCKPIT_SIGNAL_INDEX > 0) {
        COCKPIT_SIGNAL_INDEX -= 1;
        renderCockpitSignal(SNAPSHOT);
      }
      if (event.key === "ArrowRight" && COCKPIT_SIGNAL_INDEX < COCKPIT_SIGNALS.length - 1) {
        COCKPIT_SIGNAL_INDEX += 1;
        renderCockpitSignal(SNAPSHOT);
      }
    });
  }

  const drawer = document.getElementById("research-drawer");
  const toggle = document.getElementById("research-drawer-toggle");
  const close = document.getElementById("research-drawer-close");
  const setDrawer = (open) => {
    if (!drawer || !toggle) return;
    drawer.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      const windows = WORKBENCH_STATE.comparison?.windows || [];
      const selected = windows.find((row) => row.window === WORKBENCH_STATE.selectedWindow) || windows[0];
      if (selected) requestAnimationFrame(() => drawWorkbenchComparison(selected));
      drawer.scrollIntoView({ behavior: "smooth", block: "start" });
      close?.focus({ preventScroll: true });
    } else if (document.activeElement === close) {
      toggle.focus({ preventScroll: true });
    }
  };
  if (toggle && toggle.dataset.bound !== "true") {
    toggle.dataset.bound = "true";
    toggle.addEventListener("click", () => setDrawer(drawer?.hidden !== false));
  }
  if (close && close.dataset.bound !== "true") {
    close.dataset.bound = "true";
    close.addEventListener("click", () => setDrawer(false));
  }
  document.querySelectorAll(".evidence-metric-button").forEach((button) => {
    if (button.dataset.bound === "true") return;
    button.dataset.bound = "true";
    button.addEventListener("click", () => {
      setDrawer(true);
      const focus = button.dataset.researchFocus;
      const target = focus === "observations"
        ? document.getElementById("workbench-gates")
        : document.getElementById("workbench-diagnostics-body");
      target?.closest(".detail-table-wrap, article")?.classList.add("is-emphasized");
      setTimeout(() => target?.closest(".detail-table-wrap, article")?.classList.remove("is-emphasized"), 1400);
    });
  });
  if (drawer && drawer.dataset.escapeBound !== "true") {
    drawer.dataset.escapeBound = "true";
    drawer.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setDrawer(false);
    });
  }

  const bookTabs = [
    { button: document.getElementById("book-tab-positions"), panel: document.getElementById("book-positions-panel") },
    { button: document.getElementById("book-tab-orders"), panel: document.getElementById("book-orders-panel") },
  ];
  const selectBookTab = (selectedButton) => {
    bookTabs.forEach(({ button, panel }) => {
      const active = button === selectedButton;
      button?.classList.toggle("is-active", active);
      button?.setAttribute("aria-selected", String(active));
      if (panel) panel.hidden = !active;
    });
  };
  bookTabs.forEach(({ button }) => {
    if (!button || button.dataset.bound === "true") return;
    button.dataset.bound = "true";
    button.addEventListener("click", () => selectBookTab(button));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const other = bookTabs.find((entry) => entry.button !== button)?.button;
      if (other) {
        selectBookTab(other);
        other.focus();
      }
    });
  });
}

function renderCockpitPositions() {
  const root = document.getElementById("cockpit-positions-list");
  if (!root) return;
  const positions = BROKER_STATE.positions || [];
  setText("book-positions-count", String(positions.length));
  setText("book-orders-count", String((BROKER_STATE.orders || []).length));
  if (!positions.length) {
    root.innerHTML = `<div class="position-loading">No open positions. The book is clear.</div>`;
    return;
  }
  root.innerHTML = positions.slice(0, 6).map((position) => {
    const symbol = String(position.symbol || "—");
    const value = Number(position.current_value);
    const basis = Number(position.cost_basis);
    const pnl = Number.isFinite(Number(position.open_pl))
      ? Number(position.open_pl)
      : Number.isFinite(value) && Number.isFinite(basis)
        ? value - basis
        : null;
    const pnlPct = pnl != null && basis ? pnl / basis : null;
    const advice = POSITION_ADVICE.get(symbol);
    const action = advice?.action === "sell" ? "Reduce risk" : pnl != null && pnl > 0 ? "Monitor gain" : "Monitor";
    const warning = advice?.action === "sell" || (pnl != null && pnl < 0);
    const vitality = Math.max(6, Math.min(100, 50 + (Number(pnlPct) || 0) * 100));
    return `<div class="cockpit-position-row${pnl != null && pnl < 0 ? " is-negative" : ""}">
      <div class="position-name"><strong>${escapeHtml(symbol)}</strong><span>${escapeHtml(optionContractMeta(symbol).root)}</span></div>
      <div class="position-size"><strong>${integer(position.quantity)}</strong><span>contracts</span></div>
      <div class="position-pnl ${pnl != null && pnl < 0 ? "is-negative" : ""}">${pnl == null ? "--" : signed(pnl)}<span>${pnlPct == null ? "" : pct(pnlPct)}</span></div>
      <div class="position-action ${warning ? "is-warning" : ""}"><strong>${action}</strong><span>${escapeHtml(positionMarkMeta(position).label)}</span></div>
      <button class="position-row-action" type="button" aria-label="View ${escapeHtml(symbol)} details"><span class="material-symbols-outlined" aria-hidden="true">chevron_right</span></button>
      <div class="position-vitality" aria-hidden="true"><span style="width:${vitality.toFixed(0)}%"></span></div>
    </div>`;
  }).join("");
  root.querySelectorAll(".position-row-action").forEach((button) => {
    button.addEventListener("click", () => {
      const details = document.querySelector(".book-details");
      if (details) {
        details.open = true;
        details.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });
}

function renderBoardMeta() {
  const syncEl = document.getElementById("board-sync-status");
  const refreshBtn = document.getElementById("board-refresh-btn");
  if (syncEl) {
    let text = "Waiting for latest board snapshot.";
    let className = "sync-line positions-sync-status";
    if (BOARD_STATE.loading) {
      text = "Refreshing latest AI board…";
      className += " is-loading";
    } else if (BOARD_STATE.lastError) {
      text = `Board refresh failed: ${BOARD_STATE.lastError}`;
      className += " is-error";
    } else if (BOARD_STATE.snapshotGeneratedAt) {
      const snapshotAge = timeAgo(BOARD_STATE.snapshotGeneratedAt);
      const fetchedNote = BOARD_STATE.fetchedAt
        ? ` · checked ${timeAgo(BOARD_STATE.fetchedAt)}`
        : "";
      text = `Snapshot ${formatTs(BOARD_STATE.snapshotGeneratedAt)} · ${snapshotAge}${fetchedNote}`;
      const isStale =
        Date.now() - new Date(BOARD_STATE.snapshotGeneratedAt) >
        4 * 60 * 60 * 1000;
      className += isStale ? " is-warning" : " is-live";
    } else if (BOARD_STATE.fetchedAt) {
      text = `Board checked ${formatTs(BOARD_STATE.fetchedAt)} · ${timeAgo(BOARD_STATE.fetchedAt)}`;
      className += " is-live";
    }
    syncEl.textContent = text;
    syncEl.className = className;
  }
  if (refreshBtn) {
    refreshBtn.disabled = BOARD_STATE.loading;
    if (refreshBtn.classList.contains("icon-button")) {
      refreshBtn.setAttribute(
        "aria-label",
        BOARD_STATE.loading ? "Refreshing signal" : "Refresh signal",
      );
    } else {
      refreshBtn.textContent = BOARD_STATE.loading
        ? "Refreshing…"
        : "Refresh Live Board";
    }
  }
}

async function loadSnapshot() {
  const r = await fetch(SNAPSHOT_SOURCE, { cache: "no-store" });
  SNAPSHOT = await r.json();
  return SNAPSHOT;
}

async function loadProspectiveLedger() {
  const r = await fetch(PROSPECTIVE_LEDGER_SOURCE, { cache: "no-store" });
  if (!r.ok) {
    throw new Error(`Prospective ledger unavailable (${r.status})`);
  }
  PROSPECTIVE_LEDGER = await r.json();
  return PROSPECTIVE_LEDGER;
}

async function refreshBoard() {
  BOARD_STATE = {
    ...BOARD_STATE,
    loading: true,
  };
  PROSPECTIVE_STATE = {
    ...PROSPECTIVE_STATE,
    loading: true,
  };
  renderBoardMeta();
  renderProspectiveMeta();
  try {
    const [payload, ledgerResult] = await Promise.all([
      loadSnapshot(),
      loadProspectiveLedger()
        .then((ledger) => ({ ok: true, ledger }))
        .catch((error) => ({ ok: false, error })),
    ]);
    await renderBoard(payload);
    if (ledgerResult.ok) {
      PROSPECTIVE_STATE = {
        loading: false,
        updatedAt: ledgerResult.ledger?.updated_at_utc || null,
        lastError: null,
      };
      renderProspectiveScoreboard(ledgerResult.ledger);
    } else {
      PROSPECTIVE_STATE = {
        loading: false,
        updatedAt: null,
        lastError: String(ledgerResult.error?.message || ledgerResult.error),
      };
      renderProspectiveScoreboard(null);
    }
    BOARD_STATE = {
      loading: false,
      fetchedAt: new Date().toISOString(),
      snapshotGeneratedAt: payload?.generated_at_utc || payload?.timestamp || null,
      lastError: null,
    };
    renderBoardMeta();
    renderProspectiveMeta();
    return payload;
  } catch (error) {
    BOARD_STATE = {
      ...BOARD_STATE,
      loading: false,
      lastError: String(error.message || error),
    };
    renderBoardMeta();
    PROSPECTIVE_STATE = {
      ...PROSPECTIVE_STATE,
      loading: false,
    };
    renderProspectiveMeta();
    throw error;
  }
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

function renderProspectiveMeta() {
  const el = document.getElementById("prospective-sync-status");
  if (!el) return;
  let text = "Waiting for prospective ledger.";
  let className = "positions-sync-status";
  if (PROSPECTIVE_STATE.loading) {
    text = "Refreshing forward outcome ledger…";
    className += " is-loading";
  } else if (PROSPECTIVE_STATE.lastError) {
    text = PROSPECTIVE_STATE.lastError;
    className += " is-warning";
  } else if (PROSPECTIVE_STATE.updatedAt) {
    text = `Updated ${formatTs(PROSPECTIVE_STATE.updatedAt)} · ${timeAgo(PROSPECTIVE_STATE.updatedAt)}`;
    className += " is-live";
  }
  el.textContent = text;
  el.className = className;
}

function entryMid(pick) {
  const quote = pick?.emission_quote || {};
  const mid = Number(quote.mid);
  if (Number.isFinite(mid) && mid > 0) return mid;
  const bid = Number(quote.bid);
  const ask = Number(quote.ask);
  if (Number.isFinite(bid) && Number.isFinite(ask) && bid > 0 && ask > 0) {
    return (bid + ask) / 2;
  }
  return Number(quote.last || quote.ask || quote.bid || NaN);
}

function pickOutcomeReturns(pick) {
  const marks = pick?.outcomes?.fixed_exit_marks || {};
  return Object.entries(marks)
    .map(([window, mark]) => ({
      window,
      value: Number(mark?.pnl_pct_from_emission),
    }))
    .filter((row) => Number.isFinite(row.value));
}

function allProspectivePicks(ledger) {
  const entries = Array.isArray(ledger?.entries) ? ledger.entries : [];
  return entries.flatMap((entry) =>
    (Array.isArray(entry.picks) ? entry.picks : []).map((pick) => ({
      ...pick,
      run_generated_at_utc:
        pick.run_generated_at_utc || entry.run_generated_at_utc,
    })),
  );
}

function latestProspectivePicks(ledger, limit = PROSPECTIVE_RECENT_ROW_LIMIT) {
  return allProspectivePicks(ledger).reverse().slice(0, limit);
}

function recentProspectivePicks(ledger, entryLimit = 8) {
  const entries = Array.isArray(ledger?.entries) ? ledger.entries : [];
  return entries.slice(-entryLimit).flatMap((entry) =>
    (Array.isArray(entry.picks) ? entry.picks : []).map((pick) => ({
      ...pick,
      run_generated_at_utc:
        pick.run_generated_at_utc || entry.run_generated_at_utc,
    })),
  );
}

function summarizeProspectivePicks(picks) {
  const withMarks = picks.filter((pick) => pickOutcomeReturns(pick).length > 0);
  const complete = picks.filter((pick) => pick?.outcomes?.status === "complete");
  const bestReturns = withMarks.map((pick) =>
    Math.max(...pickOutcomeReturns(pick).map((row) => row.value)),
  );
  const worstReturns = withMarks.map((pick) =>
    Math.min(...pickOutcomeReturns(pick).map((row) => row.value)),
  );
  const takeProfitHits = withMarks.filter(
    (pick) =>
      pick?.outcomes?.path_rules?.take_profit_40_pct_before_stop_50_pct ===
      true,
  ).length;
  const stopHits = withMarks.filter((pick) => {
    const firstHit = pick?.outcomes?.path_rules?.first_hit || {};
    return String(firstHit.rule || "").includes("stop_50");
  }).length;
  const latestRun = picks
    .map((pick) => pick.run_generated_at_utc)
    .filter(Boolean)
    .sort()
    .at(-1);
  return {
    picks: picks.length,
    marked: withMarks.length,
    complete: complete.length,
    pending: picks.filter((pick) => (pick?.outcomes?.status || "pending") === "pending").length,
    takeProfitHits,
    stopHits,
    latestRun,
    avgBest:
      bestReturns.length
        ? bestReturns.reduce((sum, value) => sum + value, 0) /
          bestReturns.length
        : null,
    avgWorst:
      worstReturns.length
        ? worstReturns.reduce((sum, value) => sum + value, 0) /
          worstReturns.length
        : null,
  };
}

function summarizeProspectiveLedger(ledger) {
  const published = ledger?.dashboard_summary;
  if (published && typeof published === "object") {
    return {
      runs: Number(published.runs || 0),
      picks: Number(published.picks || 0),
      marked: Number(published.marked || 0),
      complete: Number(published.complete || 0),
      pending: Number(published.pending || 0),
      live: Number(published.live || 0),
      shadow: Number(published.shadow || 0),
      takeProfitHits: Number(published.take_profit_hits || 0),
      stopHits: Number(published.stop_hits || 0),
      latestRun: published.latest_run || null,
      avgBest: published.avg_best ?? null,
      avgWorst: published.avg_worst ?? null,
    };
  }
  const entries = Array.isArray(ledger?.entries) ? ledger.entries : [];
  const picks = allProspectivePicks(ledger);
  const live = picks.filter((pick) => pick.lane === "live");
  const shadow = picks.filter((pick) => pick.lane === "shadow");
  return {
    runs: entries.length,
    live: live.length,
    shadow: shadow.length,
    ...summarizeProspectivePicks(picks),
  };
}

function renderRecentForwardPerformance(ledger) {
  const el = document.getElementById("bt-recent-forward-performance");
  if (!el) return;
  if (!ledger || !Array.isArray(ledger.entries)) {
    el.innerHTML = summaryItemHtml("Status", "No recent forward ledger yet");
    return;
  }
  const summary = summarizeProspectivePicks(recentProspectivePicks(ledger, 8));
  el.innerHTML = [
    summaryItemHtml("Latest Scan", summary.latestRun ? formatTs(summary.latestRun) : "—"),
    summaryItemHtml("Recent Contracts", integer(summary.picks)),
    summaryItemHtml("Marked", `${integer(summary.marked)} / ${integer(summary.picks)}`),
    summaryItemHtml("Fully Marked", integer(summary.complete)),
    summaryItemHtml("Pending", integer(summary.pending)),
    summaryItemHtml("+40% Hits", integer(summary.takeProfitHits)),
    summaryItemHtml("-50% Stops", integer(summary.stopHits)),
    summaryItemHtml("Avg Best Mark", pct(summary.avgBest)),
    summaryItemHtml("Avg Worst Mark", pct(summary.avgWorst)),
  ].join("");
}

function renderProspectiveExplanation(ledger, summary) {
  const el = document.getElementById("prospective-performance-explain");
  if (!el) return;
  if (!ledger || !Array.isArray(ledger.entries)) {
    el.innerHTML =
      "No automatically updated forward ledger is available yet. Each scheduled scan appends picks here and later marks their real quote path.";
    return;
  }

  const aggregate = ledger.aggregate || {};
  const updated = ledger.updated_at_utc
    ? `Updated ${formatTs(ledger.updated_at_utc)}.`
    : "";
  const markedShare =
    summary.picks > 0 ? summary.marked / summary.picks : null;
  const completeShare =
    summary.picks > 0 ? summary.complete / summary.picks : null;
  const takeProfitShare =
    summary.marked > 0 ? summary.takeProfitHits / summary.marked : null;
  const stopShare = summary.marked > 0 ? summary.stopHits / summary.marked : null;
  const purpose =
    ledger.outcome_policy?.purpose ||
    "Judge every emitted contract recommendation, whether traded or not.";
  const markSummary = ledger.last_mark_summary || {};
  const missingQuotes = Number(markSummary.quotes_missing || 0);
  const missingQuoteText = missingQuotes > 0
    ? `${integer(missingQuotes)} marks are still waiting on quote availability.`
    : "No quote gaps were reported in the latest marking pass.";

  el.innerHTML = [
    `<strong>True forward evidence.</strong> ${escapeHtml(purpose)} ${updated}`,
    `The ledger now covers ${integer(aggregate.runs ?? summary.runs)} scans and ${integer(summary.picks)} emitted contracts.`,
    `${integer(summary.marked)} contracts have at least one real forward mark (${pct(markedShare)}), and ${integer(summary.complete)} are fully marked (${pct(completeShare)}).`,
    `Among marked contracts, ${integer(summary.takeProfitHits)} hit the +40% path rule (${pct(takeProfitShare)}) and ${integer(summary.stopHits)} hit the -50% stop path first (${pct(stopShare)}).`,
    `Average best mark is ${pct(summary.avgBest)} and average worst mark is ${pct(summary.avgWorst)}.`,
    missingQuoteText,
  ]
    .filter(Boolean)
    .join(" ");
}

function renderProspectiveScoreboard(ledger) {
  const grid = document.getElementById("prospective-overview-grid");
  const tbody = document.getElementById("prospective-tbody");
  if (!grid && !tbody) return;

  if (!ledger || !Array.isArray(ledger.entries)) {
    renderProspectiveExplanation(null, null);
    renderRecentForwardPerformance(null);
    if (grid) {
      grid.innerHTML = summaryItemHtml("Prospective Ledger", "Not available yet");
    }
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-muted);font-family:var(--font-data);font-size:.78rem;">No prospective pick ledger found.</td></tr>`;
    }
    return;
  }

  const summary = summarizeProspectiveLedger(ledger);
  renderProspectiveExplanation(ledger, summary);
  renderRecentForwardPerformance(ledger);
  if (grid) {
    grid.innerHTML = [
      summaryItemHtml("Runs", integer(summary.runs)),
      summaryItemHtml("Contracts Judged", integer(summary.picks)),
      summaryItemHtml("Live / Shadow", `${integer(summary.live)} / ${integer(summary.shadow)}`),
      summaryItemHtml("Marked Outcomes", integer(summary.marked)),
      summaryItemHtml("Fully Marked", integer(summary.complete)),
      summaryItemHtml("Pending", integer(summary.pending)),
      summaryItemHtml("+40% Hits", integer(summary.takeProfitHits)),
      summaryItemHtml("-50% Stops", integer(summary.stopHits)),
      summaryItemHtml("Avg Best Mark", pct(summary.avgBest)),
      summaryItemHtml("Avg Worst Mark", pct(summary.avgWorst)),
    ].join("");
  }

  if (!tbody) return;
  const rows = latestProspectivePicks(ledger);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-muted);font-family:var(--font-data);font-size:.78rem;">Prospective ledger has no picks yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map((pick) => {
      const returns = pickOutcomeReturns(pick);
      const best = returns.length
        ? Math.max(...returns.map((row) => row.value))
        : null;
      const worst = returns.length
        ? Math.min(...returns.map((row) => row.value))
        : null;
      const firstHit = pick?.outcomes?.path_rules?.first_hit;
      const ruleHit = firstHit?.rule
        ? `${String(firstHit.rule).replaceAll("_", " ")} · ${firstHit.window || ""}`
        : returns.length
          ? "No threshold hit"
          : "Pending marks";
      const lane = String(pick.lane || "unknown");
      return `<tr>
        <td data-label="Run" style="font-family:var(--font-data);font-size:.7rem;color:var(--text-muted)">${formatTs(pick.run_generated_at_utc)}</td>
        <td data-label="Lane"><span class="position-chip ${lane === "live" ? "is-positive" : lane === "shadow" ? "is-neutral" : "is-warning"}">${escapeHtml(lane.replaceAll("_", " "))}</span></td>
        <td data-label="Contract" style="font-family:var(--font-data);font-size:.72rem;word-break:break-all">${escapeHtml(pick.contract_symbol || "--")}</td>
        <td data-label="Entry Mid" class="is-num">${money(entryMid(pick))}</td>
        <td data-label="Best Mark" class="is-num ${best !== null && best >= 0 ? "is-positive" : best !== null ? "is-negative" : ""}">${best !== null ? pct(best) : "--"}</td>
        <td data-label="Worst Mark" class="is-num ${worst !== null && worst >= 0 ? "is-positive" : worst !== null ? "is-negative" : ""}">${worst !== null ? pct(worst) : "--"}</td>
        <td data-label="Rule Hit">${escapeHtml(ruleHit)}</td>
      </tr>`;
    })
    .join("");
}

// ── Explanation-only AI Rationale ───────────────────────────────────────────

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

function policyScore(candidate) {
  const value =
    candidate?.policy_score ??
    candidate?.risk_adjusted_score ??
    candidate?.learned_rank_score ??
    candidate?.final_candidate_score ??
    candidate?.forge_score;
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function sentinelNoTradePressure(candidate) {
  if (candidate?.sentinel_no_trade_pressure != null) {
    const n = Number(candidate.sentinel_no_trade_pressure);
    return Number.isFinite(n) ? n : null;
  }
  const relevance = Number(candidate?.sentinel_no_trade_relevance);
  const confidence = Number(candidate?.sentinel_confidence);
  if (!Number.isFinite(relevance) && !Number.isFinite(confidence)) return null;
  return Math.max(Number.isFinite(relevance) ? relevance : 0, 0) * Math.max(Number.isFinite(confidence) ? confidence : 0, 0.35);
}

function noTradePressure(candidate) {
  if (candidate?.no_trade_pressure != null) {
    const n = Number(candidate.no_trade_pressure);
    return Number.isFinite(n) ? n : null;
  }
  const values = [
    Number(candidate?.prob_no_trade),
    Number(candidate?.scout_no_trade_prob),
    Number(sentinelNoTradePressure(candidate)),
  ].filter((value) => Number.isFinite(value));
  if (!values.length) return null;
  return Math.max(...values);
}

function brokerMaxContracts() {
  return Math.max(1, Number(BROKER_STATE.maxContracts) || 3);
}

function clampQuantity(
  value,
  fallback = 1,
  maxContracts = brokerMaxContracts(),
) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) {
    return Math.min(Math.max(fallback, 1), maxContracts);
  }
  return Math.min(Math.max(parsed, 1), maxContracts);
}

function isSpreadPick(candidate) {
  return Boolean(candidate?.is_spread);
}

function spreadPickLabel(candidate) {
  const optionType = String(candidate?.option_type || "").toUpperCase();
  const longStrike = Number(candidate?.strike);
  const shortStrike = Number(candidate?.short_strike);
  const netDebit = Number(candidate?.spread_cost);
  const longText = Number.isFinite(longStrike) ? longStrike.toFixed(0) : "--";
  const shortText = Number.isFinite(shortStrike) ? shortStrike.toFixed(0) : "--";
  const debitText = Number.isFinite(netDebit) ? ` · Net debit ${money(netDebit)}` : "";
  return `${optionType} debit spread ${longText}/${shortText}${debitText}`;
}

function orderIssueText(order) {
  return String(
    order?.broker_issue?.message ||
      order?.reason_description ||
      order?.message ||
      "",
  ).trim();
}

function suggestedEntryQuantity(price, allocWeight) {
  const contractPrice = Number(price);
  if (!Number.isFinite(contractPrice) || contractPrice <= 0) {
    return 1;
  }
  const weight = Number(allocWeight) || 1.0;
  const scaledBudget = Math.min(
    BASE_BUDGET_USD * weight,
    Number(BROKER_STATE.maxEntryCostBasisUsd) || HARD_COST_CEILING_USD,
  );
  const rawQty = Math.floor(scaledBudget / (contractPrice * 100.0));
  return clampQuantity(rawQty || 1, 1);
}

function buildTradeCard(candidate, regime, lane) {
  const role = SESSION?.session?.role || "viewer";
  const isAdmin = role === "admin";
  const isLive = lane === "live";
  const isMoonshot = lane === "moonshot";
  const tone = toneClass(candidate.option_type);
  const dir = candidate.option_type?.toUpperCase();
  const liveQuote = LIVE_QUOTES.get(candidate.contract_symbol);
  const displayBid = liveQuote?.bid ?? candidate.bid;
  const displayAsk = liveQuote?.ask ?? candidate.ask ?? candidate.premium;
  const isSpread = isSpreadPick(candidate);
  const spreadNotice = isSpread
    ? "Manual spread order required: Orographic will not transmit only the long leg."
    : "";
  const displayIv = liveQuote?.greeks?.mid_iv
    ? Number(liveQuote.greeks.mid_iv * 100).toFixed(0) + "%"
    : candidate.implied_volatility
      ? Number(candidate.implied_volatility * 100).toFixed(0) + "%"
      : "--";
  const maxContracts = brokerMaxContracts();
  const suggestedQty = suggestedEntryQuantity(
    displayAsk,
    candidate.allocation_weight || 1.0,
  );
  const displayedPolicyScore = policyScore(candidate);
  const displayedNoTradePressure = noTradePressure(candidate);
  const displayedSentinelPressure = sentinelNoTradePressure(candidate);

  const card = document.createElement("div");
  card.className = `trade-card ${tone}${isMoonshot ? " is-moonshot" : !isLive ? " is-shadow" : ""}`;
  card.dataset.contractSymbol = candidate.contract_symbol;
  card.dataset.lane = lane;

  card.innerHTML = `
    <div class="card-art">
      <div class="card-art-glow"></div>
      <span class="card-symbol-giant">${candidate.symbol}</span>
      <div class="card-gem card-score-gem" title="Council policy score">
        ${displayedPolicyScore.toFixed(2)}
      </div>
      <div class="card-gem card-gem-direction" title="Direction">${dir}</div>
    </div>
    <div class="card-body">
      <div class="card-ticker-row">
        <span class="card-ticker">${candidate.symbol}</span>
        <span class="card-lane-badge ${laneClass(lane)}">${laneLabel(lane)}</span>
      </div>
      <p class="card-contract">${candidate.contract_symbol}</p>

      ${
        candidate.moonshot
          ? `
      <div class="moonshot-card-strip">
        <span>Tail score</span>
        <strong>${Number(candidate.moonshot.tail_upside_score || 0).toFixed(2)}</strong>
      </div>
      `
          : ""
      }

      <div class="card-score-bar-wrap">
        <div class="card-score-bar-label">
          <span>Policy Score</span>
          <span>${displayedPolicyScore.toFixed(2)}</span>
        </div>
        <div class="card-score-bar-track">
          <div class="card-score-bar-fill" style="width:${scoreBarWidth(displayedPolicyScore)}"></div>
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
        ${
          candidate.risk_adjusted_score != null
            ? `
        <div class="card-stat">
          <span class="card-stat-label">Council Policy</span>
          <span class="card-stat-value">${Number(candidate.risk_adjusted_score).toFixed(2)}</span>
        </div>
        `
            : ""
        }
        ${
          candidate.payoff_edge_score != null
            ? `
        <div class="card-stat">
          <span class="card-stat-label">Payoff Edge</span>
          <span class="card-stat-value">${pct(candidate.payoff_edge_score, 0)}</span>
        </div>
        `
            : ""
        }
        ${
          candidate.expected_edge_after_friction_pct != null
            ? `
        <div class="card-stat">
          <span class="card-stat-label">Edge After Friction</span>
          <span class="card-stat-value">${pct(candidate.expected_edge_after_friction_pct, 0)}</span>
        </div>
        `
            : ""
        }
        ${
          candidate.prob_fill_quality_ok != null
            ? `
        <div class="card-stat">
          <span class="card-stat-label">Fill Quality</span>
          <span class="card-stat-value">${pct(candidate.prob_fill_quality_ok, 0)}</span>
        </div>
        `
            : ""
        }
        ${
          displayedNoTradePressure != null
            ? `
        <div class="card-stat">
          <span class="card-stat-label">No-Trade Pressure</span>
          <span class="card-stat-value">${pct(displayedNoTradePressure, 0)}</span>
        </div>
        `
            : ""
        }
        ${
          displayedSentinelPressure != null
            ? `
        <div class="card-stat">
          <span class="card-stat-label">Sentinel Pressure</span>
          <span class="card-stat-value">${pct(displayedSentinelPressure, 0)}</span>
        </div>
        `
            : ""
        }
        ${
          candidate.learned_rank_score != null
            ? `
        <div class="card-stat">
          <span class="card-stat-label">${candidate.ranker_mode === "active" ? "Ranker" : "Shadow Rank"}</span>
          <span class="card-stat-value">${Number(candidate.learned_rank_score).toFixed(2)}</span>
        </div>
        `
            : ""
        }
        ${
          candidate.sector
            ? `
        <div class="card-stat">
          <span class="card-stat-label">Sector</span>
          <span class="card-stat-value">${String(candidate.sector).replaceAll("_", " ")}</span>
        </div>
        `
            : ""
        }
        ${
          candidate.moonshot?.tail_upside_score != null
            ? `
        <div class="card-stat">
          <span class="card-stat-label">Tail Score</span>
          <span class="card-stat-value">${Number(candidate.moonshot.tail_upside_score).toFixed(2)}</span>
        </div>
        `
            : ""
        }
        ${
          candidate.contract_cost != null
            ? `
        <div class="card-stat">
          <span class="card-stat-label">Cost</span>
          <span class="card-stat-value">${money(candidate.contract_cost)}</span>
        </div>
        `
            : ""
        }
        ${
          isSpread
            ? `
        <div class="card-stat">
          <span class="card-stat-label">Strategy</span>
          <span class="card-stat-value">${spreadPickLabel(candidate)}</span>
        </div>
        `
            : ""
        }
      </div>

      <div id="rationale-${domSafeId(candidate.contract_symbol)}" class="card-rationale is-loading">
        Loading explanation-only note…
      </div>

      <div class="card-order-config">
        <div class="card-order-copy">
          <span class="card-stat-label">Order Qty</span>
          <span class="card-order-note">Cap ${integer(maxContracts)} contract${maxContracts === 1 ? "" : "s"}</span>
        </div>
        <div class="card-qty-control">
          <button class="mini-action card-qty-step" type="button" data-step="-1" aria-label="Decrease quantity">−</button>
          <input
            class="card-qty-input"
            type="number"
            inputmode="numeric"
            min="1"
            max="${maxContracts}"
            step="1"
            value="${suggestedQty}"
            aria-label="Order quantity"
          />
          <button class="mini-action card-qty-step" type="button" data-step="1" aria-label="Increase quantity">+</button>
        </div>
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
          ${
            !isLive
              ? "disabled title='Research telemetry has no broker authority'"
              : isSpread
                ? "disabled title='Debit spread picks require manual multi-leg entry in Tradier'"
              : ""
          }
        >${!isLive ? "Research Only" : isSpread ? "Manual Spread Required" : "Preview Trade"}</button>

        ${
          isAdmin
            ? `
        <button
          class="danger-action card-execute-btn"
          type="button"
          data-contract="${candidate.contract_symbol}"
          data-symbol="${candidate.symbol}"
          data-lane="${lane}"
          data-ask="${displayAsk || ""}"
          data-alloc="${candidate.allocation_weight || 1.0}"
          ${
            !isLive
              ? "disabled title='Research telemetry has no broker authority'"
              : isSpread
                ? "disabled title='Debit spread picks require manual multi-leg entry in Tradier'"
              : ""
          }
        >${!isLive ? "Research Only" : isSpread ? "Manual Spread Required" : "Execute Trade"}</button>
        `
            : ""
        }
      </div>

      ${
        spreadNotice
          ? `
        <p class="card-notes">${spreadNotice}</p>
      `
          : ""
      }

      ${
        candidate.moonshot?.reasons?.length
          ? `
        <p class="card-notes">Moonshot: ${candidate.moonshot.reasons.join(" · ")}</p>
      `
          : ""
      }
      ${
        candidate.notes?.length
          ? `
        <p class="card-notes">${candidate.notes.join(" · ")}</p>
      `
          : ""
      }
      ${
        candidate.council_risk_flags?.length
          ? `
        <p class="card-notes">Risk flags: ${candidate.council_risk_flags.join(" · ")}</p>
      `
          : ""
      }
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
  const observabilityEl = document.getElementById("model-observability");
  const forgeDiag = payload?.diagnostics?.forge || {};
  const scoutDiag = payload?.diagnostics?.scout || {};
  const waterfall = forgeDiag.waterfall || {};
  const learnedRanker = forgeDiag.learned_ranker || {};
  const perSymbol = Array.isArray(forgeDiag.per_symbol)
    ? forgeDiag.per_symbol
    : [];
  const passedSignals = perSymbol.filter(
    (row) => Number(row.final_candidates) > 0,
  ).length;

  if (waterfallEl) {
    if (!Object.keys(waterfall).length) {
      waterfallEl.innerHTML = summaryItemHtml(
        "Status",
        "No forge diagnostics yet",
      );
    } else {
      waterfallEl.innerHTML = [
        summaryItemHtml("Signals", integer(waterfall.signals_considered)),
        summaryItemHtml(
          "Chains",
          `${integer(waterfall.signals_with_chain)} / ${integer(waterfall.signals_with_expiry)}`,
        ),
        summaryItemHtml(
          "Long-Leg Cap",
          `${integer(waterfall.rows_within_long_leg_cap)} rows`,
        ),
        summaryItemHtml(
          "Spread Cap",
          `${integer(waterfall.rows_within_spread_cap)} rows`,
        ),
        summaryItemHtml(
          "Liquidity",
          `${integer(waterfall.rows_passing_liquidity)} rows`,
        ),
        summaryItemHtml(
          "Delta Band",
          `${integer(waterfall.rows_passing_delta)} rows`,
        ),
        summaryItemHtml(
          "Net Debit",
          `${integer(waterfall.rows_passing_net_debit)} rows`,
        ),
        summaryItemHtml("Candidates", integer(waterfall.final_candidates)),
        summaryItemHtml(
          "ML Ranker",
          learnedRanker.scored_candidates
            ? `${integer(learnedRanker.scored_candidates)} scored · ${Object.keys(learnedRanker.mode_counts || {}).join(", ") || "shadow"}`
            : "No artifact",
        ),
        summaryItemHtml(
          "Pass Rate",
          ratioOrDash(passedSignals, waterfall.signals_considered),
        ),
      ].join("");
    }
  }

  if (bottlenecksEl) {
    if (!perSymbol.length) {
      bottlenecksEl.innerHTML = summaryItemHtml(
        "Status",
        "No symbol diagnostics yet",
      );
    } else {
      const reasonCounts = perSymbol.reduce((acc, row) => {
        const reason =
          row.rejection_reason ||
          (Number(row.final_candidates) > 0 ? "passed" : "unknown");
        if (reason === "passed") return acc;
        acc[reason] = (acc[reason] || 0) + 1;
        return acc;
      }, {});
      const topReasons = Object.entries(reasonCounts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4)
        .map(([reason, count]) =>
          summaryItemHtml(
            reason.replaceAll("_", " "),
            `${count} symbol${count === 1 ? "" : "s"}`,
          ),
        );
      const topPasses = perSymbol
        .filter((row) => Number(row.final_candidates) > 0)
        .sort(
          (a, b) =>
            Number(b.final_candidates || 0) - Number(a.final_candidates || 0),
        )
        .slice(0, 2)
        .map((row) =>
          summaryItemHtml(
            `${row.symbol} passed`,
            `${integer(row.final_candidates)} candidate${Number(row.final_candidates) === 1 ? "" : "s"}`,
          ),
        );
      bottlenecksEl.innerHTML = [...topReasons, ...topPasses].join("");
    }
  }

  if (observabilityEl) {
    const sentinelModes = (scoutDiag.sentinel_scores || []).reduce(
      (acc, row) => {
        const mode = row.mode || "shadow";
        acc[mode] = (acc[mode] || 0) + 1;
        return acc;
      },
      {},
    );
    observabilityEl.innerHTML = [
      summaryItemHtml(
        "Scout Side View",
        `${integer(scoutDiag.side_aware_scores?.length)} symbols`,
      ),
      summaryItemHtml(
        "Hierarchical Research",
        `${integer(scoutDiag.hierarchical_challenger_observations)} observed · ${integer(scoutDiag.hierarchical_challenger_abstentions)} abstain · ${integer(scoutDiag.hierarchical_challenger_directional_disagreements)} side conflicts`,
      ),
      summaryItemHtml(
        "Sentinel",
        Object.entries(sentinelModes)
          .map(([mode, count]) => `${mode} ${count}`)
          .join(" · ") || "neutral",
      ),
      summaryItemHtml(
        "Ranker Mode",
        Object.entries(learnedRanker.mode_counts || {})
          .map(([mode, count]) => `${mode} ${count}`)
          .join(" · ") || "heuristic",
      ),
      summaryItemHtml(
        "No-Trade Rule",
        payload?.council?.summary?.no_trade_discipline
          ? `score ${Number(payload.council.summary.no_trade_discipline.effective_minimum_live_score).toFixed(2)} · no-trade ${pct(payload.council.summary.no_trade_discipline.max_live_no_trade_prob, 0)}`
          : "—",
      ),
    ].join("");
  }
}

function attributionCandidateSummary(row) {
  if (!row || typeof row !== "object") return "—";
  const parts = [];
  if (row.symbol) parts.push(`${row.symbol} ${String(row.option_type || "").toUpperCase()}`.trim());
  if (Number.isFinite(Number(row.policy_score))) parts.push(`policy ${Number(row.policy_score).toFixed(2)}`);
  if (Number.isFinite(Number(row.forge_score))) parts.push(`forge ${Number(row.forge_score).toFixed(2)}`);
  if (Number.isFinite(Number(row.expected_edge_after_friction_pct))) {
    parts.push(`post-friction ${pct(row.expected_edge_after_friction_pct, 0)}`);
  }
  if (Number.isFinite(Number(row.prob_fill_quality_ok))) {
    parts.push(`fill ${pct(row.prob_fill_quality_ok, 0)}`);
  }
  if (Number.isFinite(Number(row.no_trade_pressure))) {
    parts.push(`no-trade ${pct(row.no_trade_pressure, 0)}`);
  }
  const flags = Array.isArray(row.council_risk_flags) ? row.council_risk_flags.filter(Boolean) : [];
  if (flags.length) parts.push(flags.slice(0, 2).join(" · ").replaceAll("_", " "));
  const notes = Array.isArray(row.notes) ? row.notes.filter(Boolean) : [];
  if (notes.length) parts.push(notes[0]);
  return parts.join(" · ") || "—";
}

function attributionFrictionSummary(row) {
  if (!row || typeof row !== "object") return "—";
  const parts = [];
  if (row.symbol) parts.push(row.symbol);
  if (row.contract_symbol) parts.push(String(row.contract_symbol).slice(0, 18));
  if (Number.isFinite(Number(row.expected_edge_after_friction_pct))) {
    parts.push(`edge ${pct(row.expected_edge_after_friction_pct, 0)}`);
  }
  if (Number.isFinite(Number(row.friction_buffer_pct))) {
    parts.push(`buffer ${pct(row.friction_buffer_pct, 0)}`);
  }
  return parts.join(" · ") || "—";
}

function blockedSymbolsSummary(blocked) {
  if (!blocked || typeof blocked !== "object") return "—";
  const parts = [];
  const scoreOnly = Array.isArray(blocked.score_only) ? blocked.score_only : [];
  const extrinsicOnly = Array.isArray(blocked.extrinsic_only) ? blocked.extrinsic_only : [];
  const scoreAndExtrinsic = Array.isArray(blocked.score_and_extrinsic)
    ? blocked.score_and_extrinsic
    : [];
  const modelNoTrade = Array.isArray(blocked.model_no_trade) ? blocked.model_no_trade : [];
  const fillQuality = Array.isArray(blocked.fill_quality) ? blocked.fill_quality : [];
  const sentinelPressure = Array.isArray(blocked.sentinel_no_trade_pressure)
    ? blocked.sentinel_no_trade_pressure
    : [];
  if (scoreOnly.length) parts.push(`score ${scoreOnly.join(", ")}`);
  if (extrinsicOnly.length) parts.push(`extrinsic ${extrinsicOnly.join(", ")}`);
  if (scoreAndExtrinsic.length) {
    parts.push(`score+extrinsic ${scoreAndExtrinsic.join(", ")}`);
  }
  if (modelNoTrade.length) parts.push(`no-trade ${modelNoTrade.join(", ")}`);
  if (fillQuality.length) parts.push(`fill ${fillQuality.join(", ")}`);
  if (sentinelPressure.length) parts.push(`sentinel ${sentinelPressure.join(", ")}`);
  return parts.join(" · ") || "No blocked-symbol sample";
}

function coreFilterAuditSummary(audit) {
  if (!audit || typeof audit !== "object") return "—";
  return [
    `${integer(audit.core_filter_pass_count)} pass`,
    `${integer(audit.score_only_fail_count)} score`,
    `${integer(audit.extrinsic_only_fail_count)} extrinsic`,
    `${integer(audit.score_and_extrinsic_fail_count)} both`,
    `${integer(audit.model_no_trade_fail_count)} no-trade`,
    `${integer(audit.fill_quality_fail_count)} fill`,
    `${integer(audit.execution_policy_fail_count)} execution`,
  ].join(" · ");
}

function renderLiveShadowAttribution(payload) {
  const attributionEl = document.getElementById("live-shadow-attribution");
  if (!attributionEl) return;

  const attribution = payload?.attribution || {};
  const summary = attribution.summary || {};
  const abstainAudit =
    payload?.council?.summary?.abstain_audit ||
    attribution?.layer_breakdown?.council?.abstain_audit ||
    {};
  const holdouts = Array.isArray(attribution.council_holdouts) ? attribution.council_holdouts : [];
  const frictionVetoes = Array.isArray(attribution.friction_vetoes) ? attribution.friction_vetoes : [];

  if (!Object.keys(summary).length) {
    attributionEl.innerHTML = summaryItemHtml("Status", "No attribution artifact in this scan");
    return;
  }

  attributionEl.innerHTML = [
    summaryItemHtml("Live Mix", compactCounts(summary.live_side_mix)),
    summaryItemHtml("Shadow Mix", compactCounts(summary.shadow_side_mix)),
    summaryItemHtml("Friction Vetoes", integer(summary.friction_veto_count)),
    summaryItemHtml("Dedupe Removed", integer(summary.dedupe_removed_count)),
    summaryItemHtml("Holdouts", integer(summary.council_holdout_count)),
    summaryItemHtml(
      "Live Avg Edge",
      pctOrDash(summary.live_avg_edge_after_friction_pct),
    ),
    summaryItemHtml(
      "Shadow Avg Edge",
      pctOrDash(summary.shadow_avg_edge_after_friction_pct),
    ),
    summaryItemHtml(
      "Abstain Driver",
      abstainAudit.primary_reason_label ||
        (summary.abstain ? "Council abstained without a structured audit." : "Live board available"),
    ),
    summaryItemHtml("Core Filter Audit", coreFilterAuditSummary(abstainAudit)),
    summaryItemHtml("Blocked Symbols", blockedSymbolsSummary(abstainAudit.blocked_symbols)),
    summaryItemHtml(
      "Top Holdout",
      holdouts.length ? attributionCandidateSummary(holdouts[0]) : "No unselected Forge holdouts",
    ),
    summaryItemHtml(
      "Top Friction Veto",
      frictionVetoes.length ? attributionFrictionSummary(frictionVetoes[0]) : "No friction vetoes",
    ),
  ].join("");
}

function promotionStatusClass(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized.includes("production") || normalized === "pass") return "is-pass";
  if (normalized.includes("shadow")) return "is-pending";
  if (normalized.includes("collect") || normalized.includes("pending") || normalized === "not_ready") return "is-pending";
  if (normalized.includes("observe")) return "is-observe";
  if (normalized.includes("fail") || normalized.includes("reject")) return "is-fail";
  return "is-neutral";
}

function compactCounts(counts) {
  if (!counts || typeof counts !== "object") return "—";
  const entries = Object.entries(counts).filter(([, value]) => Number(value) > 0);
  if (!entries.length) return "—";
  return entries
    .map(([key, value]) => `${String(key).replaceAll("_", " ")} ${integer(value)}`)
    .join(" · ");
}

function renderPromotionModelCard(model) {
  const statusClass = promotionStatusClass(model.status);
  const observations = Number(model.observations);
  const metricRows = [];
  if (Number.isFinite(observations)) {
    metricRows.push(summaryItemHtml("Observations", integer(observations)));
  }
  if (model.disagreements !== undefined) {
    metricRows.push(summaryItemHtml("Disagreements", integer(model.disagreements)));
  }
  if (model.non_neutral_events !== undefined) {
    metricRows.push(summaryItemHtml("Event Signals", integer(model.non_neutral_events)));
  }
  if (model.avg_learned_rank_score !== undefined && model.avg_learned_rank_score !== null) {
    metricRows.push(summaryItemHtml("Avg Rank", Number(model.avg_learned_rank_score).toFixed(2)));
  }
  if (model.avg_pairwise_correlation !== undefined && model.avg_pairwise_correlation !== null) {
    metricRows.push(summaryItemHtml("Correlation", Number(model.avg_pairwise_correlation).toFixed(2)));
  }
  if (model.shadow_window_runs !== undefined) {
    metricRows.push(summaryItemHtml("Shadow Runs", integer(model.shadow_window_runs)));
  }
  if (model.shadow_would_veto_observations !== undefined) {
    metricRows.push(summaryItemHtml("Would Veto", integer(model.shadow_would_veto_observations)));
  }
  if (model.counterfactual_candidates !== undefined) {
    metricRows.push(summaryItemHtml("Research Contracts", integer(model.counterfactual_candidates)));
  }
  if (model.veto_evidence_decision) {
    metricRows.push(summaryItemHtml("Veto Evidence", String(model.veto_evidence_decision).replaceAll("_", " ")));
  }
  if (model.veto_resolved_current_rule !== undefined) {
    metricRows.push(summaryItemHtml("Resolved Vetoes", `${integer(model.veto_resolved_current_rule)}/100`));
  }
  if (model.veto_independent_trading_days !== undefined) {
    metricRows.push(summaryItemHtml("Veto Days", `${integer(model.veto_independent_trading_days)}/30`));
  }
  if (model.veto_mean_avoided_net_return !== undefined && model.veto_mean_avoided_net_return !== null) {
    metricRows.push(summaryItemHtml("Avoided Return", pctOrDash(model.veto_mean_avoided_net_return)));
  }
  if (model.execution_effect) {
    metricRows.push(summaryItemHtml("Authority", String(model.execution_effect).replaceAll("_", " ")));
  }
  if (model.live_policy_effect) {
    metricRows.push(summaryItemHtml("Live Policy", String(model.live_policy_effect).replaceAll("_", " ")));
  }
  if (model.tracked_live_recommendations !== undefined) {
    metricRows.push(summaryItemHtml("Tracked Live", integer(model.tracked_live_recommendations)));
  }
  if (model.live_realized_pnl !== undefined && model.live_realized_pnl !== null) {
    metricRows.push(summaryItemHtml("Realized P&L", money(model.live_realized_pnl)));
  }
  if (model.live_friday_close_avg_pnl_pct !== undefined && model.live_friday_close_avg_pnl_pct !== null) {
    metricRows.push(summaryItemHtml("Live Friday Avg", pctOrDash(model.live_friday_close_avg_pnl_pct)));
  }
  if (model.holdout_friday_close_avg_pnl_pct !== undefined && model.holdout_friday_close_avg_pnl_pct !== null) {
    metricRows.push(summaryItemHtml("Holdout Friday Avg", pctOrDash(model.holdout_friday_close_avg_pnl_pct)));
  }
  if (model.friction_veto_friday_close_avg_pnl_pct !== undefined && model.friction_veto_friday_close_avg_pnl_pct !== null) {
    metricRows.push(summaryItemHtml("Veto Friday Avg", pctOrDash(model.friction_veto_friday_close_avg_pnl_pct)));
  }
  if (model.side_mix) {
    metricRows.push(summaryItemHtml("Side Mix", compactCounts(model.side_mix)));
  }
  if (model.mode_counts) {
    metricRows.push(summaryItemHtml("Mode Counts", compactCounts(model.mode_counts)));
  } else if (model.model_modes) {
    metricRows.push(summaryItemHtml("Model Modes", compactCounts(model.model_modes)));
  }
  if (model.live_sector_counts) {
    metricRows.push(summaryItemHtml("Live Sectors", compactCounts(model.live_sector_counts)));
  }

  return `
    <article class="promotion-card ${statusClass}">
      <div class="promotion-card-head">
        <div>
          <span class="meta-label">${escapeHtml(model.mode || "shadow")}</span>
          <h3>${escapeHtml(model.name || "Model")}</h3>
        </div>
        <span class="promotion-step">${escapeHtml(model.promotion_step || "shadow")}</span>
      </div>
      <p class="promotion-role">${escapeHtml(model.role || "")}</p>
      <div class="summary-box promotion-card-metrics">
        ${metricRows.join("") || summaryItemHtml("Status", "No scan observations")}
      </div>
      <p class="promotion-recommendation">${escapeHtml(model.recommendation || "Keep monitoring.")}</p>
    </article>
  `;
}

function renderPromotionReadiness(payload) {
  const readiness = payload?.promotion_readiness || {};
  const decisionEl = document.getElementById("promotion-decision");
  const modelGrid = document.getElementById("promotion-model-grid");
  const gatesEl = document.getElementById("promotion-gates");
  const policyEl = document.getElementById("promotion-policy");

  if (decisionEl) {
    decisionEl.textContent = readiness.decision_label || "Production-active";
    decisionEl.className = `promotion-decision ${promotionStatusClass(readiness.promotion_gate_decision || readiness.decision || "pending")}`;
  }

  if (modelGrid) {
    const models = Array.isArray(readiness.models) ? readiness.models : [];
    modelGrid.innerHTML = models.length
      ? models.map(renderPromotionModelCard).join("")
      : `<div class="card-placeholder muted" style="padding:24px;text-align:center;font-family:var(--font-data);font-size:.8rem;">No promotion readiness artifact in this scan.</div>`;
  }

  if (gatesEl) {
    const gates = Array.isArray(readiness.gates) ? readiness.gates : [];
    gatesEl.innerHTML = gates.length
      ? gates
          .map((gate) =>
            summaryItemHtml(
              `${gate.name || "Gate"} · ${String(gate.status || "pending").replaceAll("_", " ")}`,
              [gate.target || "—", gate.progress || ""].filter(Boolean).join(" "),
            ),
          )
          .join("")
      : summaryItemHtml("Status", "No acceptance gates logged");
  }

  if (policyEl) {
    const policy = readiness.policy || {};
    const profitability = readiness.profitability_summary || {};
    policyEl.innerHTML = [
      summaryItemHtml("Path", Array.isArray(readiness.promotion_path) ? readiness.promotion_path.join(" -> ") : "—"),
      summaryItemHtml("Evidence Window", `${integer(policy.minimum_shadow_trading_days)} min · ${integer(policy.preferred_shadow_trading_days)} preferred`),
      summaryItemHtml("Disagreements", `${integer(policy.minimum_disagreement_trades)} minimum`),
      summaryItemHtml("P&L Lift", pctOrDash(policy.minimum_pnl_lift_pct)),
      summaryItemHtml("Windows", Array.isArray(policy.required_windows) ? policy.required_windows.join(" · ").replaceAll("_", " ") : "—"),
      summaryItemHtml("Tracked Recs", integer(profitability.tracked_recommendations)),
      summaryItemHtml("Quote Coverage", pctOrDash(profitability.quote_coverage_pct)),
      summaryItemHtml("Live Realized P&L", profitability.live_realized_pnl !== undefined && profitability.live_realized_pnl !== null ? money(profitability.live_realized_pnl) : "—"),
      summaryItemHtml("Live Friday Avg", pctOrDash(profitability.live_friday_close_avg_pnl_pct)),
      summaryItemHtml("Shadow Friday Avg", pctOrDash(profitability.shadow_friday_close_avg_pnl_pct)),
      summaryItemHtml("Holdout Friday Avg", pctOrDash(profitability.holdout_friday_close_avg_pnl_pct)),
      summaryItemHtml("3/6/12 Replay", String(profitability.promotion_comparison_decision || "not run").replaceAll("_", " ")),
      summaryItemHtml(
        "Payoff Challenger",
        `${String(profitability.payoff_challenger_decision || "not run").replaceAll("_", " ")} · ${integer(profitability.payoff_challenger_scored)} scored · ${integer(profitability.payoff_challenger_resolved)}/100 resolved · ${integer(profitability.payoff_challenger_disagreements)}/30 disagreements · ${integer(profitability.payoff_challenger_replay_runs)}/30 replay runs`,
      ),
      summaryItemHtml("Challenger Next Step", profitability.payoff_challenger_next_action || "Collect prospective evidence."),
      summaryItemHtml(
        "Scout Veto Evidence",
        `${String(profitability.counterfactual_veto_decision || "not run").replaceAll("_", " ")} · ${integer(profitability.counterfactual_veto_resolved)}/100 resolved current-rule vetoes`,
      ),
      summaryItemHtml(
        "Outcome Capture",
        `${integer(profitability.capture_policy_v2_picks)} strict picks · ${integer(profitability.capture_windows_valid)} valid · ${integer(profitability.capture_windows_quote_missing)} retrying · ${integer(profitability.capture_windows_stale_quote)} stale · ${integer(profitability.capture_windows_missed)} missed`,
      ),
      summaryItemHtml("Rule", policy.promotion_rule || "—"),
    ].join("");
  }
}

async function renderBoard(payload) {
  if (!payload || !payload.council) {
    throw new Error("Invalid or missing council data in snapshot.");
  }

  const live = payload.council.live_board || [];
  const shadow = payload.council.shadow_board || [];
  const hasMoonshotLane = Boolean(payload.moonshot_lane);
  const moonshot = payload.moonshot_lane || {};
  const moonshotPicks = Array.isArray(moonshot.picks) ? moonshot.picks : [];
  const moonshotSummary = moonshot.summary || {};
  const moonshotPolicy = moonshot.policy || {};
  const summary = payload.council.summary || payload.summary || {};
  const abstainAudit = summary.abstain_audit || {};
  const generatedAt = payload.generated_at_utc || payload.timestamp;
  renderMoonshotSidePick(payload);

  // Stale check (4 hours)
  const isStale =
    generatedAt && Date.now() - new Date(generatedAt) > 4 * 60 * 60 * 1000;
  const boardStatusEl = document.getElementById("board-status");
  if (boardStatusEl) {
    boardStatusEl.textContent = payload.council.abstain
      ? "Council abstained"
      : live.length
        ? "Harbor live"
        : "Live board quiet";
    if (isStale) {
      boardStatusEl.classList.add("is-stale-text");
      boardStatusEl.title = "Warning: This data is more than 4 hours old.";
    } else {
      boardStatusEl.classList.remove("is-stale-text");
      boardStatusEl.title = "";
    }
  }

  setText(
    "board-status-note",
    sentenceList(summary.notes, "No council notes."),
  );
  setText("live-count-hud", integer(payload.council.summary?.live_count));
  setText("shadow-count-hud", integer(payload.council.summary?.shadow_count));

  const regimePill = document.getElementById("regime-pill");
  if (regimePill) {
    regimePill.textContent = `${String(payload.regime.mode).replace("_", " ").toUpperCase()} · bias ${payload.regime.bias}`;
    regimePill.className = `hud-value ${regimeToneClass(payload.regime.mode) === "is-call" ? "" : regimeToneClass(payload.regime.mode)}`;
  }
  setText("regime-source", payload.regime.source_symbol || "SPY");
  setText(
    "regime-source-note",
    sentenceList(
      payload.regime.notes,
      `Watching ${payload.regime.source_symbol || "the market"}.`,
    ),
  );

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
    } else {
      dispatchTag.style.color = "";
      dispatchTag.style.fontWeight = "";
    }
  }

  // Prefetch live quotes for all contracts
  const allContracts = [...live]
    .map((c) => c.contract_symbol)
    .filter(Boolean);
  await refreshQuotes(allContracts);

  // Render live picks
  const liveGrid = document.getElementById("live-picks-grid");
  if (liveGrid) {
    liveGrid.innerHTML = "";
    if (!live.length) {
      liveGrid.appendChild(
        buildEmptyCard(
          "Council Abstained",
          sentenceList(
            payload.council.summary?.notes,
            "No contract cleared the live board threshold for this run.",
          ),
        ),
      );
    } else {
      live.forEach((c) =>
        liveGrid.appendChild(buildTradeCard(c, payload.regime, "live")),
      );
    }
  }

  const moonshotPolicyStatus = document.getElementById("moonshot-policy-status");
  if (moonshotPolicyStatus) {
    if (!hasMoonshotLane) {
      moonshotPolicyStatus.textContent = "Waiting for next scan";
      moonshotPolicyStatus.className = "positions-sync-status is-warning";
    } else {
      const picked = integer(moonshotSummary.pick_count);
      const eligible = integer(moonshotSummary.eligible_count);
      moonshotPolicyStatus.textContent = `${picked} picked · ${eligible} eligible`;
      moonshotPolicyStatus.className = `positions-sync-status ${moonshotPicks.length ? "is-live" : "is-warning"}`;
    }
  }

  const moonshotSummaryGrid = document.getElementById("moonshot-summary-grid");
  if (moonshotSummaryGrid) {
    moonshotSummaryGrid.innerHTML = [
      summaryItemHtml("Picked", integer(moonshotSummary.pick_count)),
      summaryItemHtml("Eligible", integer(moonshotSummary.eligible_count)),
      summaryItemHtml("Top Tail Score", moonshotSummary.top_score == null ? "—" : Number(moonshotSummary.top_score).toFixed(2)),
      summaryItemHtml("Threshold", moonshotSummary.threshold == null ? "—" : Number(moonshotSummary.threshold).toFixed(2)),
      summaryItemHtml("Cost Cap", money(moonshotSummary.max_cost_basis)),
      summaryItemHtml("Mode", moonshotPolicy.capital_mode || "research_telemetry_non_routable"),
    ].join("");
  }

  const moonshotGrid = document.getElementById("moonshot-picks-grid");
  if (moonshotGrid) {
    moonshotGrid.innerHTML = "";
    if (!hasMoonshotLane) {
      moonshotGrid.appendChild(
        buildEmptyCard(
          "Moonshot Payload Pending",
          "The next Orographic scan will write dedicated moonshot candidates into this slot.",
        ),
      );
    } else if (!moonshotPicks.length) {
      moonshotGrid.appendChild(
        buildEmptyCard(
          "Moonshot Lane Quiet",
          "No contract cleared the dedicated tail-upside slot for this run.",
        ),
      );
    } else {
      moonshotPicks.forEach((c) =>
        moonshotGrid.appendChild(buildTradeCard(c, payload.regime, "moonshot")),
      );
    }
  }

  // Render shadow picks
  const shadowGrid = document.getElementById("shadow-picks-grid");
  if (shadowGrid) {
    shadowGrid.innerHTML = "";
    if (!shadow.length) {
      shadowGrid.appendChild(
        buildEmptyCard(
          "Shadow Lane Quiet",
          "No shadow contracts available for this run.",
        ),
      );
    } else {
      shadow.forEach((c) =>
        shadowGrid.appendChild(buildTradeCard(c, payload.regime, "shadow")),
      );
    }
  }

  // Scout / Forge / Council pipeline tables
  const scoutBoard = document.getElementById("scout-board");
  if (scoutBoard) {
    scoutBoard.innerHTML =
      (payload.scout_signals || [])
        .slice(0, 5)
        .map((row, i) =>
          rowHtml(
            `${row.symbol} ${String(row.direction).toUpperCase()} · ${row.scout_score}`,
            `call edge ${pct(row.call_edge_prob, 0)} · put edge ${pct(row.put_edge_prob, 0)} · no-trade edge ${pct(row.no_trade_prob, 0)}`,
            toneClass(row.direction),
            `Scout ${String(i + 1).padStart(2, "0")}`,
          ),
        )
        .join("") ||
      `<div class="muted" style="padding:12px;font-family:var(--font-data);font-size:.75rem">No scout signals.</div>`;
  }

  const forgeBoard = document.getElementById("forge-board");
  if (forgeBoard) {
    forgeBoard.innerHTML =
      (payload.forge_candidates || [])
        .slice(0, 5)
        .map((row, i) =>
          rowHtml(
            `${row.symbol} ${String(row.option_type).toUpperCase()} · ${row.forge_score}`,
            `ask ${money(row.ask ?? row.premium)} · edge ${pct(row.payoff_edge_score ?? row.prob_positive_option_pnl, 0)} · execution ${row.execution_policy_passed === false ? "blocked" : "pass"} · policy ${Number(policyScore(row)).toFixed(2)}`,
            toneClass(row.option_type),
            `Forge ${String(i + 1).padStart(2, "0")}`,
          ),
        )
        .join("") ||
      `<div class="muted" style="padding:12px;font-family:var(--font-data);font-size:.75rem">No forge candidates.</div>`;
  }

  const councilSummary = document.getElementById("council-summary");
  if (councilSummary) {
    councilSummary.innerHTML = [
      summaryItemHtml("Abstain", payload.council.abstain ? "Yes" : "No"),
      summaryItemHtml("Live", integer(payload.council.summary?.live_count)),
      summaryItemHtml("Shadow", integer(payload.council.summary?.shadow_count)),
      summaryItemHtml(
        "Candidates",
        integer(payload.council.summary?.candidate_count),
      ),
      summaryItemHtml(
        "Correlation",
        payload.council.summary?.avg_pairwise_correlation ?? "—",
      ),
      summaryItemHtml(
        "Abstain Driver",
        abstainAudit.primary_reason_label ||
          (payload.council.abstain ? "Unspecified" : "Live board available"),
      ),
      summaryItemHtml("Core Pass", `${integer(abstainAudit.core_filter_pass_count)} / ${integer(abstainAudit.candidate_count)}`),
      summaryItemHtml(
        "Live Sectors",
        Object.entries(payload.council.summary?.live_sector_counts || {})
          .map(([sector, count]) => `${sector.replaceAll("_", " ")} ${count}`)
          .join(" · ") || "—",
      ),
      summaryItemHtml("Regime", String(payload.regime.mode).replace("_", " ")),
      summaryItemHtml(
        "Notes",
        sentenceList(payload.council.summary?.notes, "No extra notes."),
      ),
    ].join("");
  }

  renderForgeDiagnostics(payload);
  renderLiveShadowAttribution(payload);

  // Bind card buttons
  bindCardButtons();

  // Stream explanation-only AI rationale for each card asynchronously.
  // This text never participates in Scout, Forge, Council, or broker gating.
  const allCandidates = live.map((c) => ({ candidate: c, lane: "live" }));
  for (const { candidate } of allCandidates) {
    loadCardRationale(candidate, payload.regime);
  }
  renderCockpitSignal(payload);
}

async function loadCardRationale(candidate, regime) {
  const id = `rationale-${domSafeId(candidate.contract_symbol)}`;
  const el = document.getElementById(id);
  if (!el) return;
  const rationale = await fetchRationale(candidate, regime);
  if (el) {
    el.classList.remove("is-loading");
    el.textContent =
      rationale
        ? `Explanation only: ${rationale}`
        : sentenceList(
            candidate.notes,
            `${candidate.symbol} ${candidate.option_type} — policy score ${policyScore(candidate).toFixed(2)}.`,
          );
  }
}

// ── Order Flow (Preview → Execute) ─────────────────────────────────────────

let PENDING_ORDER = null;

function selectedCardQuantity(button) {
  const card = button.closest(".trade-card");
  const input = card?.querySelector(".card-qty-input");
  if (!input) {
    return 1;
  }
  const qty = clampQuantity(input.value, input.value || 1);
  input.value = String(qty);
  return qty;
}

function syncTradeCardQuantity(card) {
  const input = card?.querySelector(".card-qty-input");
  const cost = card?.querySelector("[data-qty-cost]");
  if (!input || !cost) return;
  const qty = clampQuantity(input.value, input.value || 1);
  input.value = String(qty);
  cost.textContent = money(qty * (Number(card.dataset.ask) || 0) * 100);
}

function syncModalExecuteState() {
  const execBtn = document.getElementById("modal-execute-btn");
  if (!execBtn) return;
  execBtn.disabled = !Boolean(PENDING_ORDER?.executeEnabled);
}

function executionNotice(submission, isAdmin) {
  if (!isAdmin) {
    return "Admin session required to execute broker orders.";
  }
  if (submission?.reason) {
    return submission.reason;
  }
  if (submission?.allowed && submission?.mode === "live") {
    return "Live order will be transmitted immediately.";
  }
  return null;
}

function submissionDetailHtml(submission, isAdmin) {
  const note = executionNotice(submission, isAdmin);
  if (!note) return "";
  const tone =
    submission?.allowed && isAdmin ? "var(--teal)" : "var(--text-muted)";
  return `<p style="font-family:var(--font-data);font-size:.72rem;color:${tone};margin-top:12px;">${escapeHtml(note)}</p>`;
}

function openModal(title, bodyHtml, executeEnabled, orderData, options = {}) {
  setText("modal-title", title);
  const body = document.getElementById("modal-body");
  if (body) body.innerHTML = bodyHtml;
  const execBtn = document.getElementById("modal-execute-btn");
  if (execBtn) {
    execBtn.textContent = options.executeLabel || "Execute Trade";
  }
  const msg = document.getElementById("modal-message");
  if (msg) msg.textContent = "";
  PENDING_ORDER = orderData
    ? {
        ...orderData,
        executeEnabled,
        isLiveOrder: Boolean(options.isLiveOrder),
      }
    : null;
  syncModalExecuteState();
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
  document
    .getElementById("modal-close-btn")
    ?.addEventListener("click", closeModal);
  document
    .getElementById("modal-cancel-btn")
    ?.addEventListener("click", closeModal);
  document.getElementById("preview-modal")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
  });

  document
    .getElementById("modal-execute-btn")
    ?.addEventListener("click", async () => {
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
          body: JSON.stringify({
            option_symbol: PENDING_ORDER.option_symbol,
            symbol: PENDING_ORDER.symbol,
            side: PENDING_ORDER.side,
            quantity: PENDING_ORDER.quantity,
            type: PENDING_ORDER.type,
            duration: PENDING_ORDER.duration,
            price: PENDING_ORDER.price,
            preview: false,
            confirm_live: PENDING_ORDER.isLiveOrder ? true : undefined,
          }),
        });
        const data = await r.json();
        if (!r.ok || !data.ok)
          throw new Error(data.error || `Order failed (${r.status})`);

        const order = data.order || {};
        if (msg) {
          msg.textContent = "";
          msg.style.color = "var(--teal)";
        }
        openModal(
          "Order Submitted",
          `<div class="summary-box">
          ${summaryItemHtml("Status", order.status || "submitted")}
          ${summaryItemHtml("Order ID", order.id || "--")}
          ${summaryItemHtml("Contract", data.envelope?.option_symbol || PENDING_ORDER.option_symbol)}
          ${summaryItemHtml("Qty", order.quantity || PENDING_ORDER.quantity)}
          ${summaryItemHtml("Price", money(order.price || PENDING_ORDER.price))}
        </div>`,
          false,
          null,
          { executeLabel: "Execute Trade" },
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
        btn.textContent = PENDING_ORDER?.isLiveOrder
          ? "Transmit Live Order"
          : PENDING_ORDER?.side === "sell_to_close"
            ? "Close Position"
            : "Execute Trade";
        syncModalExecuteState();
      }
    });
}

async function handlePreview(
  contractSymbol,
  underlyingSymbol,
  lane,
  ask,
  allocWeight,
  requestedQty,
) {
  openModal(
    "Requesting Preview…",
    `<div style="padding:24px;text-align:center;font-family:var(--font-data);font-size:.8rem;color:var(--text-muted)">Fetching Tradier preview…</div>`,
    false,
    null,
  );

  try {
    const price = Number(ask) || 0.01;
    const weight = Number(allocWeight) || 1.0;
    const qty = clampQuantity(
      requestedQty,
      suggestedEntryQuantity(price, weight),
    );

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
    const data = await readBrokerJson(
      r,
      "Tradier order preview is unavailable in this session.",
    );
    if (!r.ok || !data.ok)
      throw new Error(data.error || `Preview failed (${r.status})`);

    const order = data.order || {};
    const elig = data.eligibility || {};
    const submission = data.submission || {};
    const isAdmin = SESSION?.session?.role === "admin";
    const canExec = Boolean(isAdmin && submission.allowed);
    const estCost = estimateTradeValue(order, qty, price);
    const hasCommission =
      order.commission !== null &&
      order.commission !== undefined &&
      Number.isFinite(Number(order.commission));
    const commissionText = hasCommission
      ? money(Number(order.commission))
      : "Pending broker preview";

    const warningHtml = (elig.warnings || [])
      .map(
        (w) =>
          `<div style="font-family:var(--font-data);font-size:.7rem;color:var(--amber);margin-top:4px;">⚠ ${w}</div>`,
      )
      .join("");

    const bodyHtml = `
      <div class="summary-box">
        ${summaryItemHtml("Contract", data.envelope?.option_symbol || contractSymbol)}
        ${summaryItemHtml("Side", "Buy to Open · Limit")}
        ${summaryItemHtml("Vol Scaling", weight.toFixed(2) + "x")}
        ${summaryItemHtml("Quantity", order.quantity || qty)}
        ${summaryItemHtml("Limit Price", money(order.price || price))}
        ${summaryItemHtml("Est. Cost", estCost !== null ? money(estCost) : "—")}
        ${summaryItemHtml("Commission", commissionText)}
        ${summaryItemHtml("Mode", BROKER_STATE.mode?.toUpperCase() || "--")}
        ${summaryItemHtml("Production Board", elig.lane === "live" ? "Council" : "Ineligible")}
      </div>
      ${warningHtml}
      ${submissionDetailHtml(submission, isAdmin)}
    `;

    // Store the pending order so Execute can fire it
    const pendingOrder = {
      option_symbol: contractSymbol,
      symbol: underlyingSymbol,
      side: "buy_to_open",
      quantity: qty,
      type: "limit",
      duration: "day",
      price: order.price || price,
    };

    openModal("Order Preview", bodyHtml, canExec, pendingOrder, {
      executeLabel:
        submission.mode === "live" ? "Transmit Live Order" : "Execute Trade",
      isLiveOrder: submission.mode === "live",
    });
  } catch (err) {
    openModal(
      "Preview Failed",
      `<p style="font-family:var(--font-data);font-size:.8rem;color:var(--crimson);padding:16px">${escapeHtml(err.message || err)}</p>`,
      false,
      null,
      { executeLabel: "Execute Trade" },
    );
  }
}

async function handleDirectExecute(
  contractSymbol,
  underlyingSymbol,
  lane,
  ask,
  allocWeight,
  requestedQty,
) {
  // Direct execute: still shows the modal with pre-confirmed execute button
  await handlePreview(
    contractSymbol,
    underlyingSymbol,
    lane,
    ask,
    allocWeight,
    requestedQty,
  );
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
  openModal(
    "Closing Position…",
    `<div style="padding:24px;text-align:center;font-family:var(--font-data);font-size:.8rem;color:var(--text-muted)">Fetching Tradier preview…</div>`,
    false,
    null,
  );

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
    const data = await readBrokerJson(
      r,
      "Tradier order preview is unavailable in this session.",
    );
    if (!r.ok || !data.ok)
      throw new Error(data.error || `Preview failed (${r.status})`);

    const order = data.order || {};
    const elig = data.eligibility || {};
    const submission = data.submission || {};
    const isAdmin = SESSION?.session?.role === "admin";
    const canExec = Boolean(isAdmin && submission.allowed);
    const estProceeds = estimateTradeValue(order, qty, price);
    const hasCommission =
      order.commission !== null &&
      order.commission !== undefined &&
      Number.isFinite(Number(order.commission));
    const commissionText = hasCommission
      ? money(Number(order.commission))
      : "Pending broker preview";

    const warningHtml = (elig.warnings || [])
      .map(
        (w) =>
          `<div style="font-family:var(--font-data);font-size:.7rem;color:var(--amber);margin-top:4px;">⚠ ${w}</div>`,
      )
      .join("");

    const bodyHtml = `
      <div class="summary-box">
        ${summaryItemHtml("Contract", data.envelope?.option_symbol || contractSymbol)}
        ${summaryItemHtml("Side", "Sell to Close · Limit")}
        ${summaryItemHtml("Quantity", order.quantity || qty)}
        ${summaryItemHtml("Limit Price", money(order.price || price))}
        ${summaryItemHtml("Est. Proceeds", estProceeds !== null ? money(Math.abs(estProceeds)) : "—")}
        ${summaryItemHtml("Commission", commissionText)}
        ${summaryItemHtml("Mode", BROKER_STATE.mode?.toUpperCase() || "--")}
      </div>
      ${warningHtml}
      ${submissionDetailHtml(submission, isAdmin)}
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

    openModal("Close Position Preview", bodyHtml, canExec, pendingOrder, {
      executeLabel:
        submission.mode === "live" ? "Transmit Live Order" : "Close Position",
      isLiveOrder: submission.mode === "live",
    });
  } catch (err) {
    openModal(
      "Preview Failed",
      `<p style="font-family:var(--font-data);font-size:.8rem;color:var(--crimson);padding:16px">${escapeHtml(err.message || err)}</p>`,
      false,
      null,
      { executeLabel: "Close Position" },
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
  document.querySelectorAll(".card-qty-step").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const card = btn.closest(".trade-card");
      const input = card?.querySelector(".card-qty-input");
      if (!input) return;
      const current = Number.parseInt(String(input.value || "1"), 10) || 1;
      const step = Number(btn.dataset.step) || 0;
      input.value = String(clampQuantity(current + step, current));
      syncTradeCardQuantity(card);
    });
  });

  document.querySelectorAll(".card-qty-input").forEach((input) => {
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("change", () => {
      input.value = String(clampQuantity(input.value, input.value || 1));
      syncTradeCardQuantity(input.closest(".trade-card"));
    });
  });

  document.querySelectorAll(".card-preview-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      handlePreview(
        btn.dataset.contract,
        btn.dataset.symbol,
        btn.dataset.lane,
        btn.dataset.ask,
        btn.dataset.alloc,
        selectedCardQuantity(btn),
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
        btn.dataset.alloc,
        selectedCardQuantity(btn),
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
    if (data.ok && data.backtest) {
      return {
        ...data.backtest,
        study_kind: data.kind || data.backtest.study_type || "backtest",
      };
    }
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
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const W = rect.width;
  const H = rect.height;
  const PAD = { top: 16, right: 24, bottom: 32, left: 52 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const values = curve.map((pt) => pt.cumulative_pnl);
  const minVal = Math.min(0, ...values);
  const maxVal = Math.max(0, ...values);
  const range = maxVal - minVal || 1;

  function xOf(i) {
    return PAD.left + (i / (values.length - 1)) * plotW;
  }
  function yOf(val) {
    return PAD.top + plotH - ((val - minVal) / range) * plotH;
  }

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
    grad.addColorStop(0, "rgba(74,216,162,0.35)");
    grad.addColorStop(1, "rgba(74,216,162,0.02)");
  } else {
    grad.addColorStop(0, "rgba(220,53,69,0.02)");
    grad.addColorStop(1, "rgba(220,53,69,0.35)");
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
    i === 0
      ? ctx.moveTo(xOf(i), yOf(values[i]))
      : ctx.lineTo(xOf(i), yOf(values[i]));
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
    const y = yOf(val);
    ctx.fillText(
      `$${val >= 0 ? "+" : ""}${val.toFixed(0)}`,
      PAD.left - 6,
      y + 4,
    );
  }

  // X-axis dates (show first and last only)
  ctx.textAlign = "center";
  ctx.fillStyle = "rgba(255,255,255,0.35)";
  if (curve.length > 0) {
    ctx.fillText(curve[0].week, xOf(0), H - 6);
    ctx.fillText(curve[curve.length - 1].week, xOf(curve.length - 1), H - 6);
  }
}

function performanceToneText(value, goodAt, poorAt, goodText, mixedText, poorText) {
  const n = Number(value);
  if (!Number.isFinite(n)) return mixedText;
  if (n >= goodAt) return goodText;
  if (n <= poorAt) return poorText;
  return mixedText;
}

function renderPerformanceExplanation(bt) {
  const explanationEl = document.getElementById("bt-performance-explain");
  if (!explanationEl) return;
  if (!bt) {
    explanationEl.innerHTML =
      "No walk-forward performance artifact is available yet. The scheduled scan will publish one after validation artifacts are synced.";
    return;
  }

  const studyKind = String(bt.study_kind || bt.study_type || "backtest");
  const isWalkForward = studyKind === "walk_forward";
  const totalPnl = Number(bt.total_pnl || 0);
  const netReturn = Number(bt.net_return_pct || 0);
  const winRate = Number(bt.win_rate || 0);
  const sharpe = Number(bt.sharpe_ratio || 0);
  const maxDD = Number(bt.account_max_drawdown ?? bt.max_drawdown ?? 0);
  const avgWin = Number(bt.avg_winner_pct || 0);
  const avgLoss = Number(bt.avg_loser_pct || 0);
  const trades = Number(bt.total_trades || 0);
  const optionsCoverage = bt.options_data_coverage || {};
  const entryReal = Number(optionsCoverage.entry_real_trade_pct);
  const exitReal = Number(optionsCoverage.exit_real_trade_pct);
  const hasRealCoverage =
    Number.isFinite(entryReal) &&
    Number.isFinite(exitReal) &&
    entryReal >= 0.99 &&
    exitReal >= 0.99;
  const generated = bt.generated_at ? `Generated ${escapeHtml(bt.generated_at)}.` : "";
  const sourceText = isWalkForward
    ? "This is the automatically refreshed walk-forward validation artifact."
    : "Walk-forward data was unavailable, so this is the fallback historical backtest artifact.";
  const resultText =
    totalPnl >= 0
      ? `The tested strategy made ${money(totalPnl)} on ${integer(trades)} trades, a ${pct(netReturn)} net return.`
      : `The tested strategy lost ${money(Math.abs(totalPnl))} on ${integer(trades)} trades, a ${pct(netReturn)} net return.`;
  const hitRateText = `It won ${pct(winRate)} of trades; average winners were ${pct(avgWin)} and average losers were ${pct(avgLoss)}.`;
  const riskText = `${performanceToneText(
    sharpe,
    1.5,
    0.5,
    "Risk-adjusted performance is strong",
    "Risk-adjusted performance is mixed",
    "Risk-adjusted performance is weak",
  )} with a ${sharpe.toFixed(2)} Sharpe, but the worst drawdown was ${pct(maxDD)}, so losses can still be sharp.`;
  const coverageText = hasRealCoverage
    ? "Entries and exits used real option-chain quotes."
    : "Some entry or exit marks may not be fully quote-backed, so read the result with extra caution.";

  explanationEl.innerHTML = [
    `<strong>${sourceText}</strong> ${generated}`,
    resultText,
    hitRateText,
    riskText,
    coverageText,
  ]
    .filter(Boolean)
    .join(" ");
}

function renderBacktest(bt) {
  if (!bt) {
    renderPerformanceExplanation(null);
    renderRecentForwardPerformance(PROSPECTIVE_LEDGER);
    const noData = document.getElementById("bt-no-data");
    if (noData) noData.hidden = false;
    const sizingPolicy = document.getElementById("bt-sizing-policy");
    const researchNotes = document.getElementById("bt-research-notes");
    const regimeWrap = document.getElementById("bt-regimes");
    const symbolWrap = document.getElementById("bt-symbols");
    const tradesWrap = document.getElementById("bt-trades-wrap");
    if (sizingPolicy)
      sizingPolicy.innerHTML = summaryItemHtml(
        "Status",
        "No validation sizing data",
      );
    if (researchNotes)
      researchNotes.innerHTML = summaryItemHtml(
        "Status",
        "No validation methodology data",
      );
    if (regimeWrap) regimeWrap.hidden = true;
    if (symbolWrap) symbolWrap.hidden = true;
    if (tradesWrap) tradesWrap.hidden = true;
    return;
  }

  renderPerformanceExplanation(bt);
  renderRecentForwardPerformance(PROSPECTIVE_LEDGER);

  // Stats ribbon
  const setVal = (id, text, positive) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (positive === true) el.classList.add("positive");
    if (positive === false) el.classList.add("negative");
  };

  const totalPnl = Number(bt.total_pnl || 0);
  const netReturn = Number(bt.net_return_pct || 0);
  const sharpe = Number(bt.sharpe_ratio || 0);
  const maxDD = Number(bt.account_max_drawdown ?? bt.max_drawdown ?? 0);
  const winRate = Number(bt.win_rate || 0);
  const avgWin = Number(bt.avg_winner_pct || 0);
  const avgLoss = Number(bt.avg_loser_pct || 0);
  const trades = Number(bt.total_trades || 0);
  const sizingPolicy = bt.sizing_policy || {};
  const coveragePolicy = bt.coverage_policy || {};
  const optionsCoverage = bt.options_data_coverage || {};
  const sizingPolicyEl = document.getElementById("bt-sizing-policy");
  const researchNotesEl = document.getElementById("bt-research-notes");
  const subtitleEl = document.getElementById("bt-section-sub");
  const sectionTitleEl = document.getElementById("bt-section-title");
  const studyKind = String(bt.study_kind || bt.study_type || "backtest");
  const isWalkForward = studyKind === "walk_forward";
  const config = bt.config || {};
  const variantLabel = String(bt.variant_label || "").trim();
  const formatRegimeLabel = (value) =>
    String(value || "unclassified")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (char) => char.toUpperCase());

  if (sectionTitleEl) {
    sectionTitleEl.textContent = isWalkForward
      ? "Walk-Forward Validation"
      : "Historical Performance";
  }

  if (subtitleEl) {
    subtitleEl.textContent = isWalkForward
      ? [
          bt.months ? `${bt.months}-month walk-forward` : "Walk-forward study",
          variantLabel || "Deployable council variant",
          `base $${Number(bt.budget_per_trade_usd || config.budget_per_trade_usd || 0).toFixed(0)} / trade`,
          (bt.hard_cost_ceiling_usd || config.hard_cost_ceiling_usd || config.cost_cap_usd)
            ? `hard cap $${Number(bt.hard_cost_ceiling_usd || config.hard_cost_ceiling_usd || config.cost_cap_usd).toFixed(0)}`
            : "hard cap disabled",
          sizingPolicy.skip_when_underfunded
            ? "underfunded trades skipped"
            : "forced minimum 1 contract",
        ].join(" · ")
      : [
          "3-month backtest",
          "All Forge candidates",
          `base $${Number(bt.budget_per_trade_usd || 0).toFixed(0)} / trade`,
          bt.hard_cost_ceiling_usd
            ? `hard cap $${Number(bt.hard_cost_ceiling_usd).toFixed(0)}`
            : "hard cap disabled",
          sizingPolicy.skip_when_underfunded
            ? "underfunded trades skipped"
            : "forced minimum 1 contract",
        ].join(" · ");
  }

  setVal("bt-win-rate", `${(winRate * 100).toFixed(1)}%`, winRate >= 0.5);
  setVal(
    "bt-total-pnl",
    `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`,
    totalPnl >= 0,
  );
  setVal("bt-sharpe", sharpe.toFixed(2), sharpe >= 1.0);
  setVal("bt-drawdown", `${(maxDD * 100).toFixed(1)}%`, maxDD >= -0.1);
  setVal("bt-avg-win", `+${(avgWin * 100).toFixed(1)}%`, true);
  setVal("bt-avg-loss", `${(avgLoss * 100).toFixed(1)}%`, false);
  setVal("bt-trades", trades.toLocaleString(), null);
  setVal(
    "bt-net-return",
    `${netReturn >= 0 ? "+" : ""}${(netReturn * 100).toFixed(1)}%`,
    netReturn >= 0,
  );

  if (sizingPolicyEl) {
    sizingPolicyEl.innerHTML = [
      summaryItemHtml("Base Budget", money(bt.budget_per_trade_usd || 0)),
      summaryItemHtml(
        "Hard Ceiling",
        bt.hard_cost_ceiling_usd ? money(bt.hard_cost_ceiling_usd) : "Disabled",
      ),
      summaryItemHtml(
        "Allocation Weight",
        Array.isArray(sizingPolicy.allocation_weight_range)
          ? `${sizingPolicy.allocation_weight_range[0]}x to ${sizingPolicy.allocation_weight_range[1]}x`
          : "—",
      ),
      summaryItemHtml(
        "Confidence Scale",
        Array.isArray(sizingPolicy.confidence_scale_range)
          ? `${sizingPolicy.confidence_scale_range[0]}x to ${sizingPolicy.confidence_scale_range[1]}x`
          : "—",
      ),
      summaryItemHtml(
        "Underfunded Trade",
        sizingPolicy.skip_when_underfunded ? "Skip" : "Force 1 contract",
      ),
      summaryItemHtml(
        "Max Observed Cost",
        money(sizingPolicy.max_observed_cost_basis_usd || 0),
      ),
    ].join("");
  }

  if (researchNotesEl) {
    researchNotesEl.innerHTML = [
      summaryItemHtml("Window", `${bt.backtest_start} to ${bt.backtest_end}`),
      summaryItemHtml("Trades", integer(bt.total_trades)),
      summaryItemHtml("Win Rate", pctOrDash(bt.win_rate)),
      summaryItemHtml(
        "Sharpe",
        Number.isFinite(sharpe) ? sharpe.toFixed(2) : "—",
      ),
      summaryItemHtml(
        bt.account_max_drawdown != null ? "Account Drawdown" : "Drawdown",
        pctOrDash(bt.account_max_drawdown ?? bt.max_drawdown),
      ),
      summaryItemHtml(
        "Coverage Gate",
        coveragePolicy.coverage_failed ? "Failed" : "Passed",
      ),
      summaryItemHtml(
        "Entry Real",
        pctOrDash(optionsCoverage.entry_real_trade_pct),
      ),
      summaryItemHtml(
        "Exit Real",
        pctOrDash(optionsCoverage.exit_real_trade_pct),
      ),
      isWalkForward
        ? summaryItemHtml(
            "Prior Lookback",
            config.rolling_prior_lookback_weeks
              ? `${config.rolling_prior_lookback_weeks} weeks`
              : "—",
          )
        : "",
      isWalkForward
        ? summaryItemHtml(
            "Universe",
            bt.symbols_count ? `${integer(bt.symbols_count)} symbols` : "—",
          )
        : "",
    ]
      .filter(Boolean)
      .join("");
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
      grid.innerHTML = bt.symbol_breakdown
        .map((row) => {
          const pnlPos = row.total_pnl >= 0;
          return `
          <div class="bt-sym-card">
            <span class="bt-sym-label">${row.symbol}</span>
            <span class="bt-sym-meta">${row.trades} trades · ${(row.win_rate * 100).toFixed(0)}% win</span>
            <span class="bt-sym-meta ${pnlPos ? "positive" : "negative"}" style="color:${pnlPos ? "var(--green)" : "var(--red)"}">
              ${pnlPos ? "+" : ""}$${row.total_pnl.toFixed(2)}
            </span>
          </div>`;
        })
        .join("");
    }
  } else {
    const wrap = document.getElementById("bt-symbols");
    if (wrap) wrap.hidden = true;
  }

  if (bt.regime_breakdown && bt.regime_breakdown.length > 0) {
    const wrap = document.getElementById("bt-regimes");
    const grid = document.getElementById("bt-regime-grid");
    if (wrap && grid) {
      wrap.hidden = false;
      grid.innerHTML = bt.regime_breakdown
        .map((row) => {
          const pnlPos = Number(row.total_pnl) >= 0;
          const bias = Number(row.avg_regime_bias);
          const hasBias = Number.isFinite(bias);
          return `
          <div class="bt-sym-card">
            <span class="bt-sym-label">${formatRegimeLabel(row.regime_mode)}</span>
            <span class="bt-sym-meta">${integer(row.trades)} trades · ${(Number(row.win_rate || 0) * 100).toFixed(0)}% win</span>
            <span class="bt-sym-meta">${hasBias ? `avg bias ${bias >= 0 ? "+" : ""}${bias.toFixed(2)}` : "avg bias —"}</span>
            <span class="bt-sym-meta ${pnlPos ? "positive" : "negative"}" style="color:${pnlPos ? "var(--green)" : "var(--red)"}">
              ${pnlPos ? "+" : ""}$${Number(row.total_pnl || 0).toFixed(2)}
            </span>
          </div>`;
        })
        .join("");
    }
  } else {
    const wrap = document.getElementById("bt-regimes");
    if (wrap) wrap.hidden = true;
  }

  // Trade log (last 20)
  if (bt.all_trades && bt.all_trades.length > 0) {
    const wrap = document.getElementById("bt-trades-wrap");
    const tbody = document.getElementById("bt-trade-rows");
    if (wrap && tbody) {
      wrap.hidden = false;
      const shown = [...bt.all_trades].reverse().slice(0, 30);
      tbody.innerHTML = shown
        .map((t) => {
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
        })
        .join("");
    }
  } else {
    const wrap = document.getElementById("bt-trades-wrap");
    if (wrap) wrap.hidden = true;
  }
}

// ── Boot ────────────────────────────────────────────────────────────────────

async function main() {
  // Auth
  const sessionPayload = await loadSession();
  SESSION = sessionPayload;
  const userLabel = document.getElementById("session-user");
  if (userLabel) {
    userLabel.textContent =
      sessionPayload.authenticated && sessionPayload.session
        ? `${sessionPayload.session.username} · ${String(sessionPayload.session.role).toUpperCase()}`
        : "Local preview";
  }
  bindLogout();
  bindModal();
  bindPositionsControls();
  bindBoardControls();
  bindCockpitControls();

  // Load scientific evidence independently from Tradier and the live board.
  // Missing evidence fails closed in the workbench without blocking broker access.
  loadResearchWorkbench().catch(() => {});

  // Load account (non-blocking so board renders even if Tradier is offline)
  loadAccount().catch(() => {});

  // Load snapshot and render board
  try {
    await refreshBoard();
  } catch (err) {
    const liveGrid = document.getElementById("live-picks-grid");
    if (liveGrid) {
      liveGrid.innerHTML = `<div style="padding:32px;font-family:var(--font-data);font-size:.8rem;color:var(--crimson)">Failed to load snapshot: ${err.message || err}</div>`;
    }
  }

  // Load backtest results (non-blocking — shows placeholder if not yet generated)
  loadBacktest()
    .then((bt) => renderBacktest(bt))
    .catch(() => {});
}

main();
