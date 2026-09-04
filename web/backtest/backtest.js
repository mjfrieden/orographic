const MEGA_CAPS = "AAPL,MSFT,NVDA,AMZN,GOOGL,META,BRK.B,LLY,AVGO,JPM,V,TSM,WMT,XOM,UNH,MA,COST,ORCL,HD,PG";
const VARIANT_LABELS = { baseline_all_candidates: "All Forge Candidates", council_only: "Council Only", council_cost_cap: "Council + Cost Cap", council_cost_cap_symbol_priors: "Council + Cost Cap + Symbol Priors" };
const SHARED_MART_EVIDENCE_SOURCE = "/data/diagnostics/shared_mart_shadow_evidence_latest.json";

export function buildBacktestCommand(config) {
  const parts = [
    "PYTHONPATH=.",
    "./.venv/bin/python",
    "-m engine.backtest.runner",
    `--months ${Number(config.months)}`,
    `--base-budget-usd ${Number(config.budget)}`,
    `--hard-cost-ceiling-usd ${Number(config.ceiling)}`,
    `--expiry-policy ${config.expiry}`,
    `--entry-slippage-pct ${(Number(config.entrySlip) / 100).toFixed(3)}`,
    `--exit-slippage-pct ${(Number(config.exitSlip) / 100).toFixed(3)}`,
  ];
  if (config.strict) parts.push("--strict-options-data", `--min-real-coverage-pct ${(Number(config.coverage) / 100).toFixed(2)}`);
  if (config.symbols) parts.push(`--symbols ${config.symbols.replace(/\s+/g, "")}`);
  const stamp = String(config.runId || "research_run").replace(/[^a-zA-Z0-9_-]/g, "_");
  parts.push(`--output output/backtest_results_${stamp}.json`);
  return parts.join(" \\\n  ");
}

export function filterTrades(trades, filters = {}) {
  const query = String(filters.query || "").trim().toUpperCase();
  return (trades || []).filter((trade) => {
    if (query && !String(trade.symbol || "").toUpperCase().includes(query)) return false;
    if (filters.side && filters.side !== "all" && trade.option_type !== filters.side) return false;
    if (filters.outcome === "winner" && Number(trade.pnl) <= 0) return false;
    if (filters.outcome === "loser" && Number(trade.pnl) >= 0) return false;
    return true;
  });
}

export function summarizeTrades(trades) {
  const rows = trades || [];
  const pnl = rows.reduce((sum, row) => sum + Number(row.pnl || 0), 0);
  const deployed = rows.reduce((sum, row) => sum + Number(row.cost_basis || 0), 0);
  const winners = rows.filter((row) => Number(row.pnl) > 0).length;
  return { trades: rows.length, pnl, deployed, winRate: rows.length ? winners / rows.length : 0, returnPct: deployed ? pnl / deployed : 0 };
}

const money = (value) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(Number(value || 0));
const pct = (value, digits = 1) => `${(Number(value || 0) * 100).toFixed(digits)}%`;
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const byId = (id) => document.getElementById(id);

let artifacts = { walk: null, history: null };
let activeSource = "walk";
let visibleTrades = 30;

function readRunConfig() {
  const universe = byId("run-universe").value;
  const symbols = universe === "mega" ? MEGA_CAPS : universe === "custom" ? byId("run-symbols").value : "";
  return {
    months: byId("run-months").value,
    universe,
    symbols,
    budget: byId("run-budget").value,
    ceiling: byId("run-ceiling").value,
    expiry: byId("run-expiry").value,
    entrySlip: byId("run-entry-slip").value,
    exitSlip: byId("run-exit-slip").value,
    strict: byId("run-strict").checked,
    coverage: byId("run-coverage").value,
    runId: `lab_${new Date().toISOString().slice(0, 10)}_${byId("run-months").value}mo`,
  };
}

function updateCommand() {
  byId("run-coverage-value").textContent = `${byId("run-coverage").value}%`;
  byId("custom-symbols-wrap").hidden = byId("run-universe").value !== "custom";
  byId("run-command").textContent = buildBacktestCommand(readRunConfig());
}

async function copyCommand() {
  const command = byId("run-command").textContent;
  await navigator.clipboard.writeText(command);
  byId("run-status").textContent = "Command copied. Run it from the Orographic repository root.";
}

function manifest() {
  const config = readRunConfig();
  return { schema_version: 1, created_at: new Date().toISOString(), study: "orographic_backtest", config, command: buildBacktestCommand(config) };
}

function downloadManifest() {
  const payload = manifest();
  const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: "application/json" });
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(blob);
  anchor.download = `${payload.config.runId}.manifest.json`;
  anchor.click();
  URL.revokeObjectURL(anchor.href);
  byId("run-status").textContent = "Manifest exported with the exact engine command and assumptions.";
}

function renderMetrics(data) {
  const values = [
    ["metric-return", `${Number(data.net_return_pct) >= 0 ? "+" : ""}${pct(data.net_return_pct)}`],
    ["metric-pnl", `${Number(data.total_pnl) >= 0 ? "+" : ""}${money(data.total_pnl)}`],
    ["metric-win", pct(data.win_rate)],
    ["metric-sharpe", Number(data.sharpe_ratio || 0).toFixed(2)],
    ["metric-drawdown", pct(data.account_max_drawdown ?? data.max_drawdown)],
  ];
  values.forEach(([id, value]) => { byId(id).textContent = value; });
  byId("metric-win-note").textContent = `${Number(data.winners || 0).toLocaleString()} wins / ${Number(data.total_trades || 0).toLocaleString()} trades`;
}

function renderQuality(data) {
  const coverage = data.options_data_coverage || {};
  const policy = data.coverage_policy || {};
  const real = Number(coverage.fully_real_trade_pct || 0);
  const banner = byId("quality-banner");
  const passed = !policy.coverage_failed && real >= Number(policy.min_real_coverage_pct || 0);
  banner.classList.toggle("is-warning", !passed);
  const icon = banner.querySelector(".sigil");
  if (icon) {
    icon.classList.toggle("sigil-check", passed);
    icon.classList.toggle("sigil-warning", !passed);
  }
  banner.querySelector("strong").textContent = passed ? "Research integrity gate passed" : "Research integrity requires review";
  banner.querySelector("p").textContent = `${pct(real, 0)} of trades use real chains at both entry and exit · ${policy.strict_options_data ? "strict pricing enabled" : "mixed-source pricing"}.`;
}

function renderEquity(data) {
  const canvas = byId("equity-chart");
  const points = data.equity_curve || [];
  const box = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(box.width * dpr));
  canvas.height = Math.max(1, Math.round(box.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = box.width, height = box.height, pad = { top: 18, right: 18, bottom: 30, left: 58 };
  ctx.clearRect(0, 0, width, height);
  if (!points.length) return;
  const values = points.map((row) => Number(row.cumulative_pnl || 0));
  const min = Math.min(0, ...values), max = Math.max(0, ...values), span = max - min || 1;
  const x = (i) => pad.left + (i / Math.max(1, values.length - 1)) * (width - pad.left - pad.right);
  const y = (v) => pad.top + ((max - v) / span) * (height - pad.top - pad.bottom);
  ctx.font = "10px Inter"; ctx.fillStyle = "#778799"; ctx.strokeStyle = "rgba(154,181,203,.14)"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) { const value = min + (span * i) / 4; const py = y(value); ctx.beginPath(); ctx.moveTo(pad.left, py); ctx.lineTo(width - pad.right, py); ctx.stroke(); ctx.fillText(money(value), 4, py + 3); }
  const gradient = ctx.createLinearGradient(0, pad.top, 0, height - pad.bottom); gradient.addColorStop(0, "rgba(72,203,177,.24)"); gradient.addColorStop(1, "rgba(72,203,177,0)");
  ctx.beginPath(); values.forEach((value, index) => { if (index === 0) ctx.moveTo(x(index), y(value)); else ctx.lineTo(x(index), y(value)); }); ctx.lineTo(x(values.length - 1), height - pad.bottom); ctx.lineTo(x(0), height - pad.bottom); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
  ctx.beginPath(); values.forEach((value, index) => { if (index === 0) ctx.moveTo(x(index), y(value)); else ctx.lineTo(x(index), y(value)); }); ctx.strokeStyle = "#48cbb1"; ctx.lineWidth = 2; ctx.stroke();
  const labels = [0, Math.floor((points.length - 1) / 2), points.length - 1]; ctx.fillStyle = "#778799"; labels.forEach((index) => { const label = String(points[index]?.week || ""); ctx.fillText(label, Math.max(pad.left, Math.min(width - 80, x(index) - 28)), height - 8); });
}

function renderVariants(data) {
  const summaries = data.variant_summaries || {};
  const entries = Object.entries(summaries);
  if (!entries.length) {
    byId("variant-list").innerHTML = `<div class="variant-row"><div><strong>Historical Forge surface</strong><small>Single benchmark artifact</small></div><span>${Number(data.total_trades || 0)} trades</span><strong class="${Number(data.total_pnl) >= 0 ? "positive" : "negative"}">${money(data.total_pnl)}</strong><strong>${pct(data.win_rate)}</strong></div>`;
    return;
  }
  byId("variant-list").innerHTML = entries.map(([key, row]) => `<div class="variant-row ${key === data.variant_key ? "is-selected" : ""}"><div><strong>${escapeHtml(row.label || VARIANT_LABELS[key] || key.replaceAll("_", " "))}</strong><small>${key === data.variant_key ? "Active validation target" : "Challenger"}</small></div><span>${Number(row.total_trades || row.trades || 0)} trades</span><strong class="${Number(row.total_pnl) >= 0 ? "positive" : "negative"}">${money(row.total_pnl)}</strong><strong>${pct(row.win_rate)}</strong></div>`).join("");
}

function renderCoverage(data) {
  const coverage = data.options_data_coverage || {};
  const execution = data.execution_quality || {};
  const values = [
    ["Real entry quotes", coverage.entry_real_trade_pct, true],
    ["Real exit quotes", coverage.exit_real_trade_pct, true],
    ["Fully real trades", coverage.fully_real_trade_pct, true],
    ["Avg entry spread", execution.avg_entry_spread_pct, false],
  ];
  byId("coverage-grid").innerHTML = values.map(([label, value, bar]) => `<article><span>${label}</span><strong>${pct(value)}</strong>${bar ? `<div class="coverage-bar"><i style="width:${Math.max(0, Math.min(100, Number(value || 0) * 100))}%"></i></div>` : ""}</article>`).join("");
  byId("coverage-gate").textContent = data.coverage_policy?.coverage_failed ? "Gate failed" : "Gate passed";
}

function currentFilters() { return { query: byId("trade-search").value, side: byId("trade-side").value, outcome: byId("trade-outcome").value }; }

function renderTrades(data) {
  const filtered = filterTrades(data.all_trades, currentFilters());
  const summary = summarizeTrades(filtered);
  byId("filtered-summary").textContent = `${summary.trades.toLocaleString()} observations · ${pct(summary.winRate)} win rate · ${summary.pnl >= 0 ? "+" : ""}${money(summary.pnl)} executable P&L · ${pct(summary.returnPct)} on deployed capital`;
  const shown = [...filtered].reverse().slice(0, visibleTrades);
  byId("trade-rows").innerHTML = shown.map((trade) => {
    const positive = Number(trade.pnl) >= 0;
    return `<tr><td>${escapeHtml(trade.entry_date)}</td><td><strong>${escapeHtml(trade.symbol)}</strong><span class="pill">${escapeHtml(trade.option_type)}</span></td><td>${escapeHtml(trade.expiry)} · $${Number(trade.strike).toFixed(2)}</td><td>$${Number(trade.entry_price).toFixed(2)} → $${Number(trade.exit_price).toFixed(2)}</td><td>${pct(trade.options_data_coverage_pct, 0)}</td><td class="${positive ? "positive" : "negative"}">${positive ? "+" : ""}${money(trade.pnl)}</td><td class="${positive ? "positive" : "negative"}">${pct(trade.pnl_pct, 0)}</td></tr>`;
  }).join("");
  byId("trade-count").textContent = `Showing ${shown.length} of ${filtered.length} observations`;
  byId("show-more").hidden = shown.length >= filtered.length;
}

function renderArtifact() {
  const data = artifacts[activeSource];
  if (!data) return;
  visibleTrades = 30;
  byId("artifact-meta").textContent = `${data.study_label || "Validation study"} · ${data.backtest_start} to ${data.backtest_end} · generated ${data.generated_at} · ${data.variant_label || "Forge candidate surface"}`;
  renderMetrics(data); renderQuality(data); renderVariants(data); renderCoverage(data); renderTrades(data); requestAnimationFrame(() => renderEquity(data));
}

async function loadArtifacts() {
  const [walk, history] = await Promise.all([
    fetch("/data/walk_forward_results.json", { cache: "no-store" }),
    fetch("/data/backtest_results.json", { cache: "no-store" }),
  ]);
  if (!walk.ok || !history.ok) throw new Error("Validation artifacts are not available.");
  artifacts = { walk: await walk.json(), history: await history.json() };
  renderArtifact();
}

function renderSharedMartEvidence(data) {
  const gates = data.shadow_entry_gates || {};
  const dates = gates.paired_market_dates || {};
  const outcomes = gates.paired_executable_outcomes || {};
  const execution = data.execution_quality || {};
  const exits = data.exit_replay || {};
  const training = data.training_evidence || {};
  const ready = data.status === "shadow_evidence_ready";
  const status = byId("mart-research-status");
  status.textContent = ready ? "Shadow ready" : "Collecting evidence";
  status.classList.toggle("is-ready", ready);
  byId("mart-research-summary").innerHTML = [
    ["Paired market dates", `${Number(dates.actual || 0).toLocaleString()} / ${Number(dates.required || 0).toLocaleString()}`, "Independent comparison dates"],
    ["Paired outcomes", `${Number(outcomes.actual || 0).toLocaleString()} / ${Number(outcomes.required || 0).toLocaleString()}`, "Both systems executable"],
    ["Training rows", Number(training.training_rows || 0).toLocaleString(), "Pre-decision features + labels"],
    ["Executable coverage", pct(Number(execution.executable_recommendations || 0) / Math.max(Number(execution.recommendations || 0), 1)), `${Number(execution.executable_recommendations || 0).toLocaleString()} of ${Number(execution.recommendations || 0).toLocaleString()} recommendations`],
    ["Executable return", pct(execution.avg_executable_return), `${pct(execution.executable_win_rate)} win rate`],
    ["Exit-path signal", pct(exits.touched_25_rate), `Touched +25%; ${pct(exits.touched_negative_15_rate)} touched −15%`],
  ].map(([label, value, note]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`).join("");
  byId("mart-research-next").textContent = `${data.next_action || "Continue evidence collection."} Mart ${String(data.mart_id || "unknown").slice(0, 12)}…`;
}

async function loadSharedMartEvidence() {
  const response = await fetch(SHARED_MART_EVIDENCE_SOURCE, { cache: "no-store" });
  if (!response.ok) throw new Error("Shared-mart evidence is unavailable.");
  renderSharedMartEvidence(await response.json());
}

function bindEvents() {
  byId("run-form").addEventListener("input", updateCommand);
  byId("run-form").addEventListener("submit", async (event) => { event.preventDefault(); try { await copyCommand(); byId("run-status").textContent = "Run prepared and copied. Execute it locally, then run scripts/sync_dashboard_artifacts.py to publish the result."; } catch { byId("run-status").textContent = "Run prepared. Copy the command above and execute it from the repository root."; } });
  byId("copy-command").addEventListener("click", copyCommand);
  byId("export-manifest").addEventListener("click", downloadManifest);
  document.querySelectorAll("[data-source]").forEach((button) => button.addEventListener("click", () => { activeSource = button.dataset.source; document.querySelectorAll("[data-source]").forEach((item) => item.classList.toggle("is-active", item === button)); renderArtifact(); }));
  ["trade-search", "trade-side", "trade-outcome"].forEach((id) => byId(id).addEventListener("input", () => { visibleTrades = 30; renderTrades(artifacts[activeSource]); }));
  byId("show-more").addEventListener("click", () => { visibleTrades += 30; renderTrades(artifacts[activeSource]); });
  let resizeTimer; window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => artifacts[activeSource] && renderEquity(artifacts[activeSource]), 100); });
}

async function main() {
  updateCommand(); bindEvents();
  try { await loadArtifacts(); } catch (error) { byId("artifact-meta").textContent = error.message; byId("quality-banner").classList.add("is-warning"); byId("quality-banner").querySelector("strong").textContent = "No validated artifact loaded"; }
  try { await loadSharedMartEvidence(); } catch (error) { byId("mart-research-status").textContent = "Unavailable"; byId("mart-research-next").textContent = error.message; }
}

if (typeof document !== "undefined") main();
