import { createGainLossLoader } from './gainloss-history.js';
/** Read-only presentation of already loaded evidence. Never fetches or routes orders. */
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const words = (value) => String(value ?? "Unavailable").replaceAll("_", " ");
export function finite(value) {
  if (value === null || value === undefined || typeof value === "boolean" || (typeof value === "string" && !value.trim())) return null;
  if (typeof value !== "number" && typeof value !== "string") return null;
  return Number.isFinite(Number(value)) ? Number(value) : null;
}
const number = (value) => finite(value) === null ? "Unavailable" : finite(value).toLocaleString("en-US");
const money = (value) => finite(value) === null ? "Unavailable" : finite(value).toLocaleString("en-US", { style: "currency", currency: "USD" });
const percent = (value) => finite(value) === null ? "Unavailable" : `${(finite(value) * 100).toFixed(1)}%`;
const stamp = (value) => !value || !Number.isFinite(Date.parse(value)) ? "Time unavailable" : /^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? String(value) : new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(new Date(value));
const list = (value) => Array.isArray(value) ? value : [];
const metric = (label, value, detail = "") => `<div class="ew-metric"><dt>${escape(label)}</dt><dd>${escape(value)}</dd>${detail ? `<small>${escape(detail)}</small>` : ""}</div>`;
const notice = (title, text) => `<div class="ew-notice"><strong>${escape(title)}</strong><p>${escape(text)}</p></div>`;
const pill = (text, tone = "neutral") => `<span class="ew-pill is-${tone}">${escape(text)}</span>`;
const freshness = (value, now = Date.now()) => {
  const age = value ? now - Date.parse(value) : NaN;
  return !Number.isFinite(age) || age < -60000 ? "Freshness unknown" : age > 240 * 60000 ? "Older than 4 hours" : "Within 4 hours";
};

export function productionRows(ledger) {
  return list(ledger?.entries).flatMap((entry) => list(entry?.picks)
    .filter((pick) => pick && pick.lane === "live")
    .map((pick) => ({ ...pick, run_generated_at_utc: pick.run_generated_at_utc || entry.run_generated_at_utc })));
}
export function compareScans(ledger) {
  const byTime = new Map();
  for (const entry of list(ledger?.entries)) {
    const time = Date.parse(entry?.run_generated_at_utc);
    if (!Number.isFinite(time) || !Array.isArray(entry?.picks)) continue;
    // Repeated timestamps describe one scan; merge the bounded export safely.
    const prior = byTime.get(time) || { time, stamp: entry.run_generated_at_utc, picks: new Map(), truncated: false };
    prior.truncated ||= entry.comparison_truncated === true;
    for (const pick of entry.picks) {
      if (pick?.lane === "live" && typeof pick.contract_symbol === "string" && pick.contract_symbol) prior.picks.set(pick.contract_symbol, pick);
    }
    byTime.set(time, prior);
  }
  const scans = [...byTime.values()].sort((a, b) => b.time - a.time);
  if (scans.length < 2) return { available: false, scans: scans.length, rows: [] };
  const [current, previous] = scans;
  if (current.truncated || previous.truncated) return { available: false, scans: scans.length, rows: [], reason: "truncated" };
  const contracts = [...new Set([...current.picks.keys(), ...previous.picks.keys()])];
  return { available: true, scans: scans.length, current: current.stamp, previous: previous.stamp,
    rows: contracts.map((contract) => ({ contract, before: previous.picks.get(contract) || null, after: current.picks.get(contract) || null,
      status: !previous.picks.has(contract) ? "added" : !current.picks.has(contract) ? "removed" : "retained" })) };
}
export function quoteSummary(ledger, horizon) {
  const rows = productionRows(ledger).map((pick) => ({ pick, value: finite(pick?.outcomes?.fixed_exit_marks?.[horizon]?.pnl_pct_from_emission) }));
  const observed = rows.filter((row) => row.value !== null);
  return { rows, observed: observed.length, missing: rows.length - observed.length, total: rows.length,
    mean: observed.length ? observed.reduce((sum, row) => sum + row.value, 0) / observed.length : null };
}
export function brokerMetrics(broker) {
  const valid = broker?.configured && !broker?.loading && !broker?.lastError && broker?.balances;
  const balances = valid ? broker.balances : {};
  return { available: Boolean(valid), equity: finite(balances.total_equity), closed: finite(balances.close_pl), open: finite(balances.open_pl), cash: finite(balances.total_cash) };
}

export function modelHtml(data, stage = "council") {
  const snapshot = data.snapshot;
  const governance = data.governance;
  if (!snapshot) return notice("Model evidence unavailable", "Refresh signal to load the current scan. A model label alone does not establish that it ran successfully.");
  const scout = snapshot.diagnostics?.scout || {};
  const council = snapshot.council || {};
  const capture = governance?.data_capture || {};
  const artifacts = Object.entries(snapshot.model_artifacts || {});
  const missing = artifacts.filter(([, value]) => value?.required === true && value?.present !== true);
  const liveKnown = Array.isArray(council.live_board);
  const live = list(council.live_board);
  const liveCount = finite(council.published_live_count) ?? (liveKnown ? live.length : null);
  const candidates = list(snapshot.forge_candidates);
  const weights = Object.entries(governance?.production_model?.score_weights || {});
  const stages = [
    ["scout", "01", "Scout", "Direction & abstention", number(scout.symbols_with_features), "symbols with features"],
    ["forge", "02", "Forge", "Contract ranking", number(snapshot.summary?.forge_candidate_count ?? (Array.isArray(snapshot.forge_candidates) ? candidates.length : null)), "published candidates"],
    ["council", "03", "Council", "Final selection", number(liveCount), "production recommendations"],
  ];
  let detail = "";
  if (stage === "scout") {
    const reasons = Object.entries(scout.rejection_counts || {});
    detail = `<h3>Which symbols made it through?</h3><p>Scout uses the published direction and abstention policy before contracts are ranked.</p><dl class="ew-metrics">${metric("Requested", number(scout.symbols_requested))}${metric("With price history", number(scout.symbols_with_history))}${metric("With model features", number(scout.symbols_with_features))}${metric("Model no-trade rejections", number(scout.active_side_model_no_trade_rejections))}</dl>
    ${reasons.length ? `<h4>Published rejection reasons</h4><ul class="ew-reasons">${reasons.map(([reason, count]) => `<li><span>${escape(words(reason))}</span><strong>${escape(number(count))}</strong></li>`).join("")}</ul>` : `<p class="ew-muted">No rejection breakdown was published for this scan.</p>`}`;
  } else if (stage === "forge") {
    detail = `<h3>What went into contract selection?</h3><p>Published ranking and execution checks. A score is a model rank, not a verified chance of profit.</p>
    ${candidates.length ? `<div class="ew-table-wrap"><table><caption>Up to 30 published contracts evaluated in this scan</caption><thead><tr><th scope="col">Contract</th><th scope="col">Rank score</th><th scope="col">Spread</th><th scope="col">Quoted cost</th><th scope="col">Published checks</th></tr></thead><tbody>${candidates.slice(0, 30).map((row) => `<tr><th scope="row">${escape(row.contract_symbol || row.symbol)}</th><td>${finite(row.forge_score) === null ? "Unavailable" : finite(row.forge_score).toFixed(3)}</td><td>${escape(percent(row.spread_pct))}</td><td>${escape(money(row.contract_cost))}</td><td>${escape([...list(row.execution_policy_reasons), ...list(row.tail_gate_reasons)].map(words).join(" · ") || (row.execution_policy_passed === true ? "Execution checks passed" : "No check detail published"))}</td></tr>`).join("")}</tbody></table></div>` : notice("No ranked contracts published", "This scan contains no Forge candidate rows. Check Scout coverage and the Council explanation.")}`;
  } else {
    const audit = council.summary?.abstain_audit || {};
    const reason = audit.primary_reason_label || (!liveKnown ? "This snapshot has no complete Council board. No decision can be established." : council.abstain === true ? "Council published a hold decision. No detailed reason was included." : live.length ? "Council selected the contracts below. Order preview still checks the current quote and account controls." : "No production recommendation was published. No detailed decision was included.");
    const rejects = Object.entries(audit).filter(([key, value]) => /_fail_count$|_rejections$/.test(key) && finite(value) > 0);
    detail = `<h3>${!liveKnown ? "Council decision unavailable" : live.length ? "Why this production board?" : "Why is the board on hold?"}</h3><p class="ew-decision">${escape(reason)}</p><dl class="ew-metrics">${metric("Candidates considered", number(audit.candidate_count ?? council.summary?.candidate_count))}${metric("Passed core filters", number(audit.core_filter_pass_count))}${metric("Production recommendations", number(liveCount))}</dl>
    ${liveCount > live.length ? `<p class="ew-muted">Showing ${live.length} of ${liveCount} published recommendations.</p>` : ""}${live.length ? `<ul class="ew-contracts">${live.map((row) => `<li><strong>${escape(row.contract_symbol || row.symbol)}</strong><span>${escape(list(row.notes).slice(0, 2).join(" · ") || "No candidate rationale published.")}</span></li>`).join("")}</ul>` : ""}
    ${rejects.length ? `<h4>Published blockers</h4><p class="ew-muted">Counts can overlap: one contract may fail more than one check.</p><ul class="ew-reasons">${rejects.map(([key, value]) => `<li><span>${escape(words(key.replace(/_fail_count$|_rejections$/, "")))}</span><strong>${number(value)}</strong></li>`).join("")}</ul>` : ""}`;
  }
  return `<div class="ew-panel-heading"><div><p class="ew-kicker">Current decision path</p><h3>${escape(words(snapshot.model_modes?.product_stack || "Model profile unavailable"))}</h3></div>${pill(freshness(snapshot.generated_at_utc), freshness(snapshot.generated_at_utc) === "Within 4 hours" ? "neutral" : "warning")}</div>
    <p class="ew-source">Scan ${escape(stamp(snapshot.generated_at_utc))}${data.boardState?.lastError ? " · Refresh failed; showing the last loaded scan" : ""}</p>
    <div class="ew-stage-grid" aria-label="Inspect model stages">${stages.map(([key, index, title, sub, count, label]) => `<button type="button" class="ew-stage ${stage === key ? "is-selected" : ""}" data-ew-stage="${key}" aria-pressed="${stage === key}"><span class="ew-stage-number">${index}</span><strong>${title}</strong><span>${sub}</span><b>${escape(count)}</b><small>${label}</small></button>`).join("")}</div>
    <section class="ew-stage-detail" aria-label="Selected model stage">${detail}</section>
    <div class="ew-health"><div><strong>Evidence health</strong><p>Data capture is separate from proof of profitable trading.</p></div><dl class="ew-metrics">${metric("Fresh marks / active contracts", `${number(capture.marks_written_last_run)} / ${number(capture.active_contracts_last_run)}`, `Stale quotes: ${number(capture.stale_quotes_last_run)} · Missing: ${number(capture.missing_quotes_last_run)}`)}${metric("Required artifacts", artifacts.length ? missing.length ? `${missing.length} missing` : "Present in snapshot" : "Unavailable", "Presence is not a validation result")}</dl></div>
    <p class="ew-source">Governance ${escape(stamp(governance?.generated_at_utc))} · ${escape(governance?.generated_at_utc ? freshness(governance.generated_at_utc) : "Governance artifact unavailable")}</p>
    <details class="ew-details" data-ew-key="model-identity"><summary>Inspect model identity, weights & policy</summary><div><p>${escape(governance?.live_authority?.summary || "Authority policy unavailable in the governance artifact.")}</p><p>${snapshot.scan_settings?.research_outputs_routable === false ? "Published policy: research outputs cannot route orders." : "Research routing policy is not established by this snapshot."}</p>
    ${weights.length ? `<h4>Published ranking weights</h4><dl class="ew-metrics">${weights.map(([key, value]) => metric(words(key), percent(value))).join("")}</dl>` : "<p>Ranking weights unavailable.</p>"}
    <div class="ew-table-wrap"><table><caption>Artifact identity from this scan</caption><thead><tr><th scope="col">Artifact</th><th scope="col">Status</th><th scope="col">SHA-256</th></tr></thead><tbody>${artifacts.map(([name, row]) => `<tr><th scope="row">${escape(words(name))}</th><td>${row?.present === true ? "Present" : "Missing"}${row?.required === true ? " · required" : ""}</td><td class="ew-hash">${escape(row?.sha256 || "Unavailable")}</td></tr>`).join("") || "<tr><td colspan=\"3\">No artifact identities published.</td></tr>"}</tbody></table></div><p class="ew-muted">${escape(Object.entries(snapshot.model_modes || {}).map(([key, value]) => `${words(key)}: ${words(value)}`).join(" · "))}</p></div></details>`;
}

export function changesHtml(data, filter = "all") {
  const diff = compareScans(data.ledger);
  if (!diff.available && diff.reason === "truncated") return notice("Scan comparison is incomplete", "At least one of the latest two boards exceeds the retained record limit or has incomplete pick data. Added/removed claims are withheld rather than inferred from a partial board.");
  if (!diff.available) return notice("Two published scans are needed", diff.scans ? "Only one dated scan is available in this recent export. Refresh signal after another scan is published." : "No dated recommendation scans are loaded. Refresh signal to retry the recent export.");
  const rows = diff.rows.filter((row) => filter === "all" || row.status === filter);
  const labels = { added: "Newly listed", removed: "No longer listed", retained: "Still listed" };
  return `<div class="ew-panel-heading"><div><p class="ew-kicker">Latest two loaded scans</p><h3>What changed on the board?</h3></div>${pill("Recommendation changes")}</div>
    <div class="ew-comparison"><div><small>Previous scan</small><strong>${escape(stamp(diff.previous))}</strong></div><span aria-hidden="true">→</span><div><small>Latest scan in export</small><strong>${escape(stamp(diff.current))}</strong></div></div>
    <p class="ew-muted">This compares production recommendations in the recent export. Listed or removed does not mean bought or sold. Model releases and policy changes cannot be established from these records.</p>
    ${data.prospectiveState?.lastError ? notice("History refresh failed", "This comparison uses the last loaded export. Refresh signal to retry.") : ""}
    <div class="ew-filter-row"><label for="ew-change-filter">Show changes<select id="ew-change-filter">${[["all", "All contracts"], ...Object.entries(labels)].map(([value, label]) => `<option value="${value}" ${value === filter ? "selected" : ""}>${label}${value === "all" ? "" : ` (${diff.rows.filter((row) => row.status === value).length})`}</option>`).join("")}</select></label><span>${rows.length} matching contract${rows.length === 1 ? "" : "s"}</span></div>
    ${rows.length ? `<div class="ew-change-list">${rows.map((row) => `<details class="ew-change" data-ew-key="${escape(row.contract)}"><summary><span>${pill(labels[row.status], row.status === "added" ? "teal" : "neutral")}<strong>${escape(row.contract)}</strong></span><span>Inspect</span></summary><div class="ew-change-detail"><dl class="ew-metrics">${metric("Previous quoted entry ask", money(row.before?.emission_quote?.ask), row.before ? "Published quote; not a fill" : "Not listed in previous scan")}${metric("Latest quoted entry ask", money(row.after?.emission_quote?.ask), row.after ? "Published quote; not a fill" : "Not listed in latest scan")}</dl><p>${row.status === "removed" ? "This contract is absent from the latest loaded scan. The export does not establish why it was removed or whether a position was closed." : row.status === "added" ? "This contract appears in the latest scan and was not in the preceding loaded scan. This is not evidence of a broker order." : "This contract is present in both scans. Repeated recommendations are not separate executed trades."}</p></div></details>`).join("")}</div>` : notice(diff.rows.length ? "No contracts match this filter" : "Both scans have no production recommendations", diff.rows.length ? "Choose All contracts to see the complete comparison." : "No recommendation was added or removed between these two exported scans.")}
    <p class="ew-source">${diff.scans} dated scans loaded · Export updated ${escape(stamp(data.ledger?.updated_at_utc))} · Bounded recent export, not full model history.</p>`;
}

export function accountHtml(data) {
  const broker = data.broker || {};
  const value = brokerMetrics(broker);
  const environment = broker.mode === "live" ? "Live account" : broker.mode === "sandbox" ? "Sandbox account" : "Account environment unverified";
  return `<div class="ew-panel-heading"><div><p class="ew-kicker">Broker-reported snapshot</p><h3>What is the account doing?</h3></div>${pill(environment)}</div>
    ${!value.available ? notice(broker.loading ? "Refreshing account snapshot" : "Account values unavailable", broker.loading ? "Values will appear when the current account refresh completes." : "Use Refresh Tradier in Open Positions to reload account balances. Missing values are not zero.") : ""}
    <dl class="ew-metrics ew-account-metrics">${metric("Net liquidation", money(value.equity), "Current account value")}${metric("Closed P&L · current session", money(value.closed), "Broker-reported closed positions")}${metric("Open-position P&L", money(value.open), "Unrealized; current positions")}${metric("Cash balance", money(value.cash), "Not a performance measure")}</dl>
    <p class="ew-source">${value.available ? `Account retrieved ${escape(stamp(broker.lastLoadedAt))}` : "No current verified account snapshot"}</p>
    ${notice("Period returns are not available yet", "No cash-flow-adjusted return or equity curve is available. Closed-history records below reconcile separately from this snapshot. Never add their subtotal to current-session closed P&L.")}`;
}

export function historyRows(state, filters = {}) {
  return list(state?.pages).flatMap((page) => list(page.rows).map((row, index) => ({ ...row, page: page.pagination.page, rowIndex: index }))).filter((row) => {
    const date = row.close_date_calendar;
    const gain = finite(row.gain_loss);
    return (!filters.symbol || String(row.symbol || "").toLowerCase().includes(filters.symbol.toLowerCase())) &&
      (!filters.from || (date && date >= filters.from)) && (!filters.to || (date && date <= filters.to)) &&
      (!filters.result || filters.result === "all" || (filters.result === "gain" && gain !== null && gain > 0) ||
        (filters.result === "loss" && gain !== null && gain < 0) || (filters.result === "flat" && gain === 0) ||
        (filters.result === "missing" && gain === null));
  });
}
export function historyHtml(state = {}, filters = {}, session = null) {
  if (session && session.session?.role !== "admin") return notice("Closed history requires an admin session", "Your current session cannot read historical financial records. Current account snapshot access is unchanged.");
  const rows = historyRows(state, filters);
  const last = list(state.pages).at(-1);
  const valid = rows.filter((row) => finite(row.gain_loss) !== null);
  const subtotal = valid.length ? valid.reduce((sum, row) => sum + finite(row.gain_loss), 0) : null;
  const allCount = list(state.pages).reduce((sum, page) => sum + list(page.rows).length, 0);
  const capped = list(state.pages).length >= 10 || allCount >= 1000 || (last?.pagination?.next_page > 10);
  const next = !capped && Number.isInteger(last?.pagination?.next_page) && last.pagination.next_page > last.pagination.page ? last.pagination.next_page : null;
  return `<section class="ew-history" aria-labelledby="ew-history-title"><div class="ew-panel-heading"><div><p class="ew-kicker">Broker closed-position history</p><h3 id="ew-history-title">Inspect actual closed results</h3></div>${pill(state.loaded ? `${last?.environment === "live" ? "Live" : last?.environment === "sandbox" ? "Sandbox" : "Unknown environment"} · broker records` : "Load on request")}</div>
    <p>Tradier reconciles this history nightly. Today’s closures may not appear yet. Records are broker reported; fees and all-in net profit are not independently verified here.</p>
    <div class="ew-history-actions"><button class="quiet-button" type="button" id="ew-history-load" ${state.loading ? "disabled" : ""}>${state.loading ? "Loading closed results…" : state.loaded ? "Refresh closed results" : "Load closed results"}</button>${state.loaded && next ? `<button class="quiet-button" type="button" id="ew-history-older" data-page="${next}" ${state.loading ? "disabled" : ""}>Load older · page ${next}</button>` : ""}</div>
    <p id="ew-history-status" class="ew-source" role="status">${state.loading ? "Loading requested history page. Previously loaded records remain below." : state.error ? `History request failed: ${escape(state.error)}. ${state.loaded ? "Previously loaded records may be stale." : "No closed results loaded."}` : state.loaded ? `${allCount} rows loaded across ${list(state.pages).length} page(s). Retrieved ${escape(stamp(last?.fetched_at_utc))}.` : "No history request has been made. Loading this view reads records only."}</p>
    ${state.loaded ? `<div class="ew-filter-row ew-history-filters"><label for="ew-history-symbol">Contract or symbol<input id="ew-history-symbol" type="search" value="${escape(filters.symbol || "")}" placeholder="Filter loaded records" /></label><label for="ew-history-from">Closed from<input id="ew-history-from" type="date" value="${escape(filters.from || "")}" /></label><label for="ew-history-to">Closed through<input id="ew-history-to" type="date" value="${escape(filters.to || "")}" /></label><label for="ew-history-result">Result<select id="ew-history-result">${[["all", "All results"], ["gain", "Gains"], ["loss", "Losses"], ["flat", "Zero"], ["missing", "Unavailable"]].map(([value, label]) => `<option value="${value}" ${value === (filters.result || "all") ? "selected" : ""}>${label}</option>`).join("")}</select></label><button id="ew-history-clear" class="quiet-button" type="button">Clear filters</button></div>
    <dl class="ew-metrics">${metric("Loaded-record gain/loss subtotal", money(subtotal), `${valid.length} matching rows with gain/loss; ${rows.length - valid.length} unavailable`)}${metric("Matching / loaded records", `${rows.length} / ${allCount}`, "Filters apply only to downloaded pages")}</dl>
    ${capped ? notice("History load limit reached", "This view holds at most 10 pages / 1,000 broker records. Refresh closed results to start again.") : ""}
    <p class="ew-muted">Showing ${Math.min(rows.length, 100)} of ${rows.length} matching loaded rows. Narrow the filters to inspect a specific contract or date. The subtotal covers all matching loaded rows.</p>
    <p class="ew-muted">Historical coverage is incomplete or unverified. This subtotal is not lifetime P&L or a selected-period total. It is never combined with the current-session figure. Missing values are not zero.</p>
    ${list(last?.warnings).length ? `<p class="ew-muted">${escape(last.warnings.join(" · "))}</p>` : ""}
    <div class="ew-change-list">${rows.slice(0, 100).map((row) => `<details class="ew-change" data-ew-key="history-${row.page}-${row.rowIndex}"><summary><span><strong>${escape(row.symbol || "Symbol unavailable")}</strong><span>${escape(row.close_date_calendar || "Date unavailable")}</span></span><strong>${escape(money(row.gain_loss))}</strong></summary><div class="ew-change-detail"><dl class="ew-metrics">${metric("Broker-reported gain/loss", money(row.gain_loss))}${metric("Proceeds", money(row.proceeds))}${metric("Cost basis", money(row.cost))}${metric("Quantity", number(row.quantity))}${metric("Opened", row.open_date || "Unavailable")}${metric("Closed", row.close_date || "Unavailable")}</dl><p>Broker history record · page ${row.page}. No strategy or recommendation attribution has been inferred.${list(row.missing_fields).length ? ` Missing fields: ${escape(row.missing_fields.join(", "))}.` : ""}</p></div></details>`).join("") || notice(allCount ? "No loaded records match" : "No closed records in this response", allCount ? "Clear filters or load older pages to inspect more records." : "This response does not prove there have never been closed positions. History is reconciled nightly.")}</div>` : ""}
    </section>`;
}

export function quotesHtml(data, horizon) {
  const ledger = data.ledger;
  const horizons = [...new Set([...list(ledger?.outcome_policy?.required_fixed_exits).filter((x) => typeof x === "string"), ...productionRows(ledger).flatMap((pick) => Object.keys(pick?.outcomes?.fixed_exit_marks || {}))])];
  if (!ledger) return notice("Quoted outcomes unavailable", "Refresh signal to load the recent recommendation export. Quoted outcomes are separate from broker account performance.");
  if (!horizons.length) return notice("No outcome horizons published", "This export has no fixed-exit windows to compare. Missing marks are not zero returns.");
  const selected = horizons.includes(horizon) ? horizon : horizons[0];
  const summary = quoteSummary(ledger, selected);
  return `<div class="ew-panel-heading"><div><p class="ew-kicker">Published recommendation observations</p><h3>How did the quoted marks change?</h3></div>${pill("Quote proxy · not account P&L", "warning")}</div>
    <p>Mark-to-entry quoted change. Fills, fees, slippage and executable exit prices are not verified by this export. These values are not realized or after-cost returns.</p>
    <div class="ew-filter-row"><label for="ew-quote-horizon">Observation horizon<select id="ew-quote-horizon">${horizons.map((value) => `<option value="${escape(value)}" ${value === selected ? "selected" : ""}>${escape(words(value))}</option>`).join("")}</select></label><span>Production recommendations only</span></div>
    <dl class="ew-metrics">${metric("Observed / loaded records", `${summary.observed} / ${summary.total}`, `${summary.missing} without a usable mark at this horizon`)}${metric("Mean quoted mark change", percent(summary.mean), "Observed records only; not independent trades")}</dl>
    ${data.prospectiveState?.lastError ? notice("History refresh failed", "Showing the last loaded export; its marks may be stale.") : ""}
    <div class="ew-table-wrap"><table><caption>Latest 24 loaded recommendation observations · ${escape(words(selected))}</caption><thead><tr><th scope="col">Published</th><th scope="col">Contract</th><th scope="col">Quoted mark change</th></tr></thead><tbody>${[...summary.rows].sort((a, b) => (Date.parse(b.pick.run_generated_at_utc) || 0) - (Date.parse(a.pick.run_generated_at_utc) || 0)).slice(0, 24).map(({ pick, value }) => `<tr><td>${escape(stamp(pick.run_generated_at_utc))}</td><th scope="row">${escape(pick.contract_symbol || pick.symbol)}</th><td>${escape(percent(value))}</td></tr>`).join("") || '<tr><td colspan="3">No production records in this recent export.</td></tr>'}</tbody></table></div>
    <p class="ew-source">Export updated ${escape(stamp(ledger.updated_at_utc))} · Repeated scans of one contract are separate observations, not independent trades. Coverage is limited to the loaded recent export.</p>`;
}

export function researchHtml(data) {
  const bt = data.backtest;
  if (!bt) return notice("Historical study unavailable", "No historical backtest summary is loaded. Open the archived study view for available studies and their methodology.") + '<a class="quiet-button" href="/backtest/">Open archived study →</a>';
  return `<div class="ew-panel-heading"><div><p class="ew-kicker">Historical study</p><h3>Research is a separate evidence set</h3></div>${pill("Simulation · not account P&L", "warning")}</div>
    <p>This is an archived model study. Its results do not establish the performance of today’s deployed model. Check the study’s model identity, dates, quote coverage and cost assumptions before comparing results.</p>
    <dl class="ew-metrics">${metric("Study", words(bt.study_kind || bt.study_type || "backtest"))}${metric("Reported simulated trades", number(bt.total_trades))}${metric("Variant", bt.variant_label || "Unavailable")}${metric("Study generated", stamp(bt.generated_at_utc || bt.generated_at))}${metric("Study window", `${bt.backtest_start || "Unavailable"} to ${bt.backtest_end || "Unavailable"}`)}</dl><dl class="ew-metrics">${metric("Entry slippage assumption", percent(bt.execution_quality?.avg_entry_slippage_pct), "Historical study assumption")}${metric("Exit slippage assumption", percent(bt.execution_quality?.avg_exit_slippage_pct), "Historical study assumption")}</dl><p class="ew-source">Entry quote sources: ${escape(Object.entries(bt.options_data_coverage?.entry_source_counts || {}).map(([key, value]) => `${words(key)}: ${number(value)}`).join(" · ") || "Unavailable")} · Exit quote sources: ${escape(Object.entries(bt.options_data_coverage?.exit_source_counts || {}).map(([key, value]) => `${words(key)}: ${number(value)}`).join(" · ") || "Unavailable")}</p>
    ${notice("No blended performance total", "Broker balances, quoted recommendation marks and historical simulations answer different questions. This view never adds them together or claims a current live edge from an archived study.")}
    <a class="quiet-button" href="/backtest/">Inspect archived study details →</a>`;
}

export function createWorkbench(root, options = {}) {
  const mode = options.mode || root.dataset.ewMode || "full";
  let data = {};
  let tab = mode === "trading" ? "performance" : "model";
  let stage = "council";
  let changeFilter = "all";
  let performance = "account";
  let horizon = "";
  let identity = null;
  let stopped = false;
  let history = { pages: [], loading: false, error: null, loaded: false };
  let historyFilters = { symbol: "", from: "", to: "", result: "all" };
  const historyLoader = mode === "research" ? null : createGainLossLoader((next) => { history = next; if (tab === "performance" && performance === "account") render(); });
  const panels = new Map((mode === "trading" ? ["performance"] : ["model", "changes", "performance"]).map((key) => [key, root.querySelector(`#ew-panel-${key}`)]));
  function render() {
    const active = root.ownerDocument?.activeElement;
    const focus = active && root.contains(active) ? {
      id: active.id,
      tab: active.dataset?.ewTab,
      stage: active.dataset?.ewStage,
      performance: active.dataset?.ewPerformance,
      selectionStart: active.selectionStart,
      selectionEnd: active.selectionEnd,
      detail: active.tagName === "SUMMARY" ? active.parentElement?.dataset?.ewKey : null,
    } : null;
    const openDetails = new Set([...root.querySelectorAll("details[data-ew-key][open]")].map((node) => node.dataset.ewKey));
    root.querySelectorAll("[data-ew-tab]").forEach((button) => {
      const selected = button.dataset.ewTab === tab;
      button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1;
    });
    for (const [key, panel] of panels) if (panel) panel.hidden = key !== tab;
    const panel = panels.get(tab);
    if (!panel) return;
    if (tab === "model") panel.innerHTML = modelHtml(data, stage);
    if (tab === "changes") panel.innerHTML = changesHtml(data, changeFilter);
    if (tab === "performance" && mode === "research") panel.innerHTML = researchHtml(data);
    else if (tab === "performance") {
      panel.innerHTML = `<div class="ew-segments" role="group" aria-label="Performance evidence source">${(mode === "trading" ? [["account", "Account"], ["quotes", "Quoted recommendation outcomes"]] : [["account", "Account"], ["quotes", "Quoted outcomes"], ["research", "Research"]]).map(([key, label]) => `<button class="quiet-button" type="button" data-ew-performance="${key}" aria-pressed="${performance === key}">${label}</button>`).join("")}</div><section aria-label="Selected performance evidence">${performance === "account" ? accountHtml(data) + historyHtml(history, historyFilters, data.session) : performance === "quotes" ? quotesHtml(data, horizon) : researchHtml(data)}</section>`;
    }
    root.querySelectorAll("details[data-ew-key]").forEach((node) => { node.open = openDetails.has(node.dataset.ewKey); });
    if (focus) {
      const target = [...root.querySelectorAll("[id], [data-ew-tab], [data-ew-stage], [data-ew-performance], summary")].find((node) =>
        (focus.id && node.id === focus.id) ||
        (focus.tab && node.dataset?.ewTab === focus.tab) ||
        (focus.stage && node.dataset?.ewStage === focus.stage) ||
        (focus.performance && node.dataset?.ewPerformance === focus.performance) ||
        (focus.detail && node.tagName === "SUMMARY" && node.parentElement?.dataset?.ewKey === focus.detail));
      target?.focus({ preventScroll: true });
      if (target && Number.isInteger(focus.selectionStart) && typeof target.setSelectionRange === "function") {
        try { target.setSelectionRange(focus.selectionStart, focus.selectionEnd); } catch { /* Date fields do not expose text selection. */ }
      }
    }
  }
  root.addEventListener("click", (event) => {
    const action = event.target.closest("#ew-history-load, #ew-history-older, #ew-history-clear");
    if (action && root.contains(action)) {
      if (stopped || data.session?.session?.role !== "admin") return;
      if (action.id === "ew-history-clear") { historyFilters = { symbol: "", from: "", to: "", result: "all" }; render(); root.querySelector("#ew-history-clear")?.focus(); }
      else historyLoader?.load(action.id === "ew-history-older" ? Number(action.dataset.page) : 1, action.id === "ew-history-load");
      return;
    }
    const button = event.target.closest("[data-ew-tab], [data-ew-stage], [data-ew-performance]");
    if (!button || !root.contains(button)) return;
    let selector;
    if (button.dataset.ewTab) { tab = button.dataset.ewTab; selector = `[data-ew-tab="${tab}"]`; }
    if (button.dataset.ewStage) { stage = button.dataset.ewStage; selector = `[data-ew-stage="${stage}"]`; }
    if (button.dataset.ewPerformance) { performance = button.dataset.ewPerformance; selector = `[data-ew-performance="${performance}"]`; }
    render(); root.querySelector(selector)?.focus();
  });
  root.addEventListener("input", (event) => {
    if (["ew-history-symbol", "ew-history-from", "ew-history-to"].includes(event.target.id)) {
      historyFilters = { ...historyFilters, [event.target.id.slice("ew-history-".length)]: event.target.value };
    }
  });
  root.addEventListener("change", (event) => {
    if (event.target.id === "ew-change-filter") changeFilter = event.target.value;
    else if (event.target.id === "ew-quote-horizon") horizon = event.target.value;
    else if (event.target.id.startsWith("ew-history-")) {
      const key = event.target.id.slice("ew-history-".length);
      if (!["symbol", "from", "to", "result"].includes(key)) return;
      historyFilters = { ...historyFilters, [key]: event.target.value };
    } else return;
    const id = event.target.id; render(); root.querySelector(`#${id}`)?.focus();
  });
  root.addEventListener("keydown", (event) => {
    if (!event.target.matches("[data-ew-tab]") || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); const keys = [...panels.keys()];
    tab = event.key === "Home" ? keys[0] : event.key === "End" ? keys.at(-1) : keys[(keys.indexOf(tab) + (event.key === "ArrowRight" ? 1 : keys.length - 1)) % keys.length];
    render(); root.querySelector(`[data-ew-tab="${tab}"]`)?.focus();
  });
  render();
  return {
    selectTab(next) { if (panels.has(next)) { tab = next; render(); } },
    update(next) {
      if (stopped) return;
      const merged = { ...data, ...next };
      const nextIdentity = JSON.stringify([merged.session?.authenticated ?? null, merged.session?.session?.username ?? null, merged.session?.session?.role ?? null, merged.broker?.accountId ?? null, merged.broker?.mode ?? null]);
      const changedIdentity = identity !== null && identity !== nextIdentity;
      identity = nextIdentity; data = merged;
      if (changedIdentity) {
        if (panels.get("performance")) panels.get("performance").innerHTML = "";
        historyLoader?.reset(); historyFilters = { symbol: "", from: "", to: "", result: "all" };
      }
      render();
    },
    clearSensitiveData() {
      stopped = true; data = { ...data, broker: null, session: { authenticated: false } };
      if (panels.get("performance")) panels.get("performance").innerHTML = "";
      historyLoader?.reset(); historyFilters = { symbol: "", from: "", to: "", result: "all" }; render();
    },
  };
}
if (typeof document !== "undefined") {
  const root = document.getElementById("evidence-workbench");
  if (root) {
    globalThis.OrographicEvidence = createWorkbench(root);
    globalThis.addEventListener?.("pagehide", () => globalThis.OrographicEvidence.clearSensitiveData());
  }
}
