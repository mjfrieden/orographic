import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = (await readFile(new URL("../../web/app.js", import.meta.url), "utf8"))
  .replace(/^import .*\n/, "").replace(/main\(\);\s*$/, "");
const now = Date.parse("2026-10-09T01:00:00Z");
class Clock extends Date {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return now; }
}
class Element {
  constructor() {
    this.innerHTML = ""; this.textContent = ""; this.dataset = {}; this.attributes = {};
    this.handlers = {}; this.disabled = false; this.hidden = false; this.value = "all";
    this.className = ""; this.style = {};
    this.classList = { contains: () => false, add() {}, remove() {}, toggle() {} };
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, handler) { (this.handlers[type] ||= []).push(handler); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  focus() {}
  fire(type, event = {}) { for (const handler of this.handlers[type] || []) handler({ target: this, ...event }); }
}
function harness(ids = [], fetch = async () => { throw new Error("Unexpected network request"); }) {
  const nodes = new Map(ids.map((id) => [id, new Element()]));
  const selectors = new Map();
  const context = vm.createContext({
    Date: Clock, Intl, console, URL, setTimeout, clearTimeout, fetch,
    document: { getElementById: (id) => nodes.get(id) || null, querySelectorAll: (selector) => selectors.get(selector) || [] },
    readSessionJson: async (response) => { if (!response.ok) throw new Error(`Unavailable (${response.status})`); return response.json(); },
  });
  vm.runInContext(source, context);
  return { nodes, selectors, run: (code) => vm.runInContext(code, context), set: (name, value) => { context[name] = value; } };
}
const signalIds = ["cockpit-signal", "cockpit-signal-state", "cockpit-signal-note", "signal-selector", "signal-index", "signal-prev", "signal-next", "board-sync-status", "board-refresh-btn"];
const historyIds = ["recommendation-history", "recommendation-list", "recommendation-detail", "recommendation-date", "recommendation-symbol", "recommendation-status", "recommendation-clear", "recommendation-more", "recommendation-sync", "recommendation-results", "recommendation-count"];
const candidate = { symbol: "TEST", contract_symbol: "TEST261016C00064000", option_type: "call", ask: 1.5, bid: 1.4, allocation_weight: 1, notes: [] };
const snapshot = (ageMinutes = 0, live = [candidate]) => ({ generated_at_utc: new Date(now - ageMinutes * 60000).toISOString(), council: { live_board: live, summary: {} }, regime: {} });
const pick = (symbol, date, status = "pending", lane = "live", marks = {}) => ({ symbol, contract_symbol: `${symbol}261016C00064000`, run_generated_at_utc: date, lane, outcomes: { status, fixed_exit_marks: marks } });
const ledger = (picks) => ({ entries: [{ picks }], updated_at_utc: "2026-10-09T00:00:00Z", dashboard_summary: { picks: 4247, live: 44 }, outcome_policy: { required_fixed_exits: ["one_hour", "end_of_day"] } });
const response = (value, status = 200) => ({ ok: status < 400, status, json: async () => value });

for (const [name, payload, state, expected] of [
  ["initial loading", null, { loading: true }, "loading"],
  ["initial failure", null, { loading: false, lastError: "Service <unavailable>" }, "error"],
  ["absent snapshot", null, {}, "empty"],
  ["malformed board", { council: {} }, {}, "empty"],
  ["fresh hold", snapshot(0, []), {}, "hold"],
  ["fresh candidate", snapshot(0), {}, "ready"],
  ["stale candidate", snapshot(241), {}, "stale"],
  ["stale hold", snapshot(241, []), {}, "stale"],
  ["invalid timestamp", { ...snapshot(), generated_at_utc: "bad" }, {}, "stale"],
  ["legacy timestamp only", { ...snapshot(), generated_at_utc: null, timestamp: new Date(now).toISOString() }, {}, "stale"],
  ["refresh failure with saved data", snapshot(), { lastError: "Unavailable" }, "error"],
]) {
  test(`signal renders ${name} explicitly without a misleading ready label`, () => {
    const ui = harness(signalIds); ui.set("payload", payload); ui.set("state", state);
    ui.run("BOARD_STATE = { loading: false, lastError: null, ...state }; renderCockpitSignal(payload);");
    assert.equal(ui.nodes.get("cockpit-signal").dataset.state, expected);
    assert.doesNotMatch(ui.nodes.get("cockpit-signal").innerHTML, /Trade ready/);
    if (expected !== "ready") assert.doesNotMatch(ui.nodes.get("cockpit-signal").innerHTML, /signal-state is-ready/);
    if (name === "initial failure") assert.match(ui.nodes.get("cockpit-signal").innerHTML, /Service &lt;unavailable&gt;/);
  });
}

test("frontend age warning uses backend-provided window and preserves preview confirmation", () => {
  const ui = harness(signalIds); ui.set("payload", snapshot(31));
  ui.run("BROKER_STATE.maxSignalAgeMinutes = 30; renderCockpitSignal(payload);");
  assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "stale");
  assert.match(ui.nodes.get("cockpit-signal-note").textContent, /30-minute/);
  assert.match(ui.nodes.get("cockpit-signal").innerHTML, /Preview order/);
  assert.doesNotMatch(ui.nodes.get("cockpit-signal").innerHTML, /card-execute-btn/);
});

test("late broker config updates state without replacing entered quantity or detail markup", () => {
  const ui = harness(signalIds); ui.set("payload", snapshot(31));
  ui.run("renderCockpitSignal(payload)");
  const before = ui.nodes.get("cockpit-signal").innerHTML;
  ui.run("BROKER_STATE.maxSignalAgeMinutes = 30; updateCockpitSignalState(payload)");
  assert.equal(ui.nodes.get("cockpit-signal").innerHTML, before);
  assert.equal(ui.nodes.get("cockpit-signal-state").textContent, "Stale snapshot");
});

for (const invalid of [null, "bad"]) test(`meta does not mark ${invalid} timestamp as live`, () => {
  const ui = harness(signalIds); ui.set("stamp", invalid);
  ui.run("BOARD_STATE = { loading: false, fetchedAt: new Date().toISOString(), snapshotGeneratedAt: stamp }; renderBoardMeta();");
  assert.match(ui.nodes.get("board-sync-status").className, /is-warning/);
  assert.match(ui.nodes.get("board-sync-status").textContent, /unknown/);
});

test("snapshot failure still settles independent history and shows error in visible card", async () => {
  const ui = harness([...signalIds, ...historyIds], async (url) => url.includes("latest_run") ? response(null, 503) : response(ledger([])));
  await assert.rejects(ui.run("refreshBoard()"), /503/);
  assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "error");
  assert.match(ui.nodes.get("recommendation-sync").textContent, /0 production recommendations/);
  assert.equal(ui.nodes.get("recommendation-list").attributes["aria-busy"], "false");
});

test("invalid snapshot cannot replace last valid board", async () => {
  const ui = harness(signalIds, async () => response({ council: {} })); ui.set("saved", snapshot());
  ui.run("SNAPSHOT = saved");
  await assert.rejects(ui.run("loadSnapshot()"), /no valid Council board/);
  assert.equal(ui.run("SNAPSHOT === saved"), true);
});

test("repeated refreshes are coalesced while loading and settle visibly", async () => {
  let release; const gate = new Promise((resolve) => { release = resolve; }); const calls = [];
  const ui = harness([...signalIds, ...historyIds], async (url) => { calls.push(url); await gate; return response(url.includes("latest_run") ? snapshot() : ledger([])); });
  ui.run("renderBoard = async (payload) => renderCockpitSignal(payload)");
  const first = ui.run("refreshBoard()"); await ui.run("refreshBoard()");
  assert.equal(calls.length, 2); release(); await first;
  assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "ready");
  assert.equal(ui.nodes.get("board-refresh-btn").disabled, false);
});

test("history includes production rows only and uses loaded count instead of aggregate", () => {
  const ui = harness(historyIds); ui.set("data", ledger([pick("LIVE", "2026-10-08T14:00:00Z"), pick("SHADOW", "2026-10-08T14:00:00Z", "complete", "shadow")]));
  ui.run("PROSPECTIVE_LEDGER = data; renderRecommendationHistory()");
  assert.match(ui.nodes.get("recommendation-sync").textContent, /1 production recommendation/);
  assert.doesNotMatch(ui.nodes.get("recommendation-list").innerHTML, /SHADOW|4247/);
  assert.match(ui.nodes.get("recommendation-sync").textContent, /not the full ledger/);
});

test("Chicago date filters include day-boundary and daylight-saving dates without inventing missing dates", () => {
  const ui = harness();
  assert.equal(ui.run('recommendationDate("2026-10-09T01:00:00Z")'), "2026-10-08");
  assert.equal(ui.run('recommendationDate("2026-11-02T05:30:00Z")'), "2026-11-01");
  assert.equal(ui.run('recommendationDate("bad")'), "unknown");
  assert.equal(ui.run('recommendationDate(null)'), "unknown");
});

test("date, symbol, status filters combine and clear without network requests", () => {
  const ui = harness(historyIds); ui.set("data", ledger([pick("AAA", "2026-10-08T14:00:00Z"), pick("BBB", "2026-10-08T15:00:00Z"), pick("AAA", "2026-10-07T15:00:00Z")]));
  ui.run("PROSPECTIVE_LEDGER = data; bindRecommendationHistory(); renderRecommendationHistory()");
  ui.nodes.get("recommendation-symbol").value = "AAA"; ui.nodes.get("recommendation-symbol").fire("change");
  ui.nodes.get("recommendation-date").value = "2026-10-08"; ui.nodes.get("recommendation-date").fire("change");
  assert.match(ui.nodes.get("recommendation-results").textContent, /1 of 1/);
  ui.nodes.get("recommendation-status").value = "complete"; ui.nodes.get("recommendation-status").fire("change");
  assert.match(ui.nodes.get("recommendation-list").innerHTML, /No recommendations match/);
  ui.nodes.get("recommendation-clear").fire("click");
  assert.equal(ui.nodes.get("recommendation-status").value, "all");
  assert.match(ui.nodes.get("recommendation-results").textContent, /3 of 3/);
});

test("history selection keeps the clicked native button and exposes escaped, non-actionable quote details", () => {
  const ui = harness(historyIds); ui.set("data", ledger([pick('<img onerror="bad">', "2026-10-08T14:00:00Z", "partial", "live", { one_hour: { pnl_pct_from_emission: 0.1 } })]));
  ui.run("PROSPECTIVE_LEDGER = data; bindRecommendationHistory(); renderRecommendationHistory()");
  const button = new Element(); button.dataset.recommendationKey = ui.run("liveRecommendationRows(PROSPECTIVE_LEDGER)[0].key");
  button.closest = () => button; ui.selectors.set("[data-recommendation-key]", [button]);
  const listBefore = ui.nodes.get("recommendation-list").innerHTML;
  ui.nodes.get("recommendation-list").fire("click", { target: button });
  assert.equal(ui.nodes.get("recommendation-list").innerHTML, listBefore);
  assert.equal(button.attributes["aria-pressed"], "true");
  const html = ui.nodes.get("recommendation-detail").innerHTML;
  assert.match(html, /&lt;img/); assert.doesNotMatch(html, /<img|card-preview-btn|card-execute-btn/);
  assert.match(html, /Quote proxy/); assert.match(html, /Unavailable/); assert.match(html, /not filled orders/);
});

test("null, blank, nonfinite, and absent marks are unavailable, while zero is a real quote mark", () => {
  const ui = harness();
  for (const value of [null, undefined, "", "bad", Infinity, true, " "]) {
    ui.set("mark", { pnl_pct_from_emission: value });
    assert.equal(ui.run("recommendationMarkValue(mark)"), null, `invalid ${value}`);
  }
  assert.equal(ui.run("recommendationMarkValue({ pnl_pct_from_emission: 0 })"), 0);
  assert.equal(ui.run("recommendationStatus({ outcomes: {status: 'complete', fixed_exit_marks: {one_hour: {pnl_pct_from_emission: null}}} })"), "missing");
});

test("failed history refresh labels retained export; no-data and zero-live states remain distinct", () => {
  const ui = harness(historyIds); ui.run("renderRecommendationHistory()");
  assert.match(ui.nodes.get("recommendation-count").textContent, /Unavailable/);
  ui.set("data", ledger([])); ui.run("PROSPECTIVE_LEDGER = data; renderRecommendationHistory()");
  assert.match(ui.nodes.get("recommendation-list").innerHTML, /No production recommendations/);
  ui.run("PROSPECTIVE_STATE.lastError = 'network'; renderRecommendationHistory()");
  assert.match(ui.nodes.get("recommendation-sync").textContent, /last loaded export/);
});

test("show-more is bounded and filter changes reset selection and page size", () => {
  const ui = harness(historyIds); ui.set("data", ledger(Array.from({ length: 30 }, (_, i) => pick(`T${i}`, "2026-10-08T14:00:00Z"))));
  ui.run("PROSPECTIVE_LEDGER = data; bindRecommendationHistory(); renderRecommendationHistory()");
  assert.match(ui.nodes.get("recommendation-results").textContent, /24 of 30/);
  ui.nodes.get("recommendation-more").fire("click");
  assert.match(ui.nodes.get("recommendation-results").textContent, /30 of 30/);
  assert.equal(ui.nodes.get("recommendation-more").hidden, true);
  ui.nodes.get("recommendation-clear").fire("click");
  assert.equal(ui.run("RECOMMENDATION_HISTORY.limit"), 24);
});

test("rebinding dynamic order controls cannot double quantity changes or preview calls", () => {
  const ui = harness(); const button = new Element(); ui.selectors.set(".card-preview-btn", [button]);
  ui.run("bindCardButtons(); bindCardButtons()");
  assert.equal(button.handlers.click.length, 1);
});

test("signal arrow shortcuts never intercept book tabs or history/filter controls", () => {
  const ui = harness(["signal"]); ui.run("bindCockpitControls(); COCKPIT_SIGNALS = [{}, {}]");
  ui.nodes.get("signal").fire("keydown", { key: "ArrowRight", target: { closest: () => null } });
  assert.equal(ui.run("COCKPIT_SIGNAL_INDEX"), 0);
});

test("disclosure, filters and selection remain native keyboard controls with responsive touch targets", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const css = await readFile(new URL("../../web/cockpit.css", import.meta.url), "utf8");
  assert.match(html, /<details id="recommendation-history"/);
  assert.match(html, /<label>Date \(Chicago\)<select id="recommendation-date"/);
  assert.match(html, /id="recommendation-results" role="status"/);
  assert.match(source, /<button type="button" class="recommendation-choice/);
  assert.match(css, /\.recommendation-history button\s*\{[^}]*min-height: 44px/s);
  assert.match(css, /@media \(max-width: 720px\)\s*\{\s*\.recommendation-history-grid,\s*\.recommendation-filters \{ grid-template-columns: 1fr;/);
});

test("complete status cannot mask a missing required outcome window", () => {
  const ui = harness(); ui.set("data", ledger([pick("AAA", "2026-10-08T14:00:00Z", "complete", "live", { one_hour: { pnl_pct_from_emission: 0.1 } })]));
  assert.equal(ui.run("liveRecommendationRows(data)[0].status"), "partial");
});

test("single or absent candidates hide the otherwise empty pager", () => {
  const ui = harness(["signal-pagination"]);
  ui.run("COCKPIT_SIGNALS = []; syncCockpitSignalControls()");
  assert.equal(ui.nodes.get("signal-pagination").hidden, true);
  ui.run("COCKPIT_SIGNALS = [{}]; syncCockpitSignalControls()");
  assert.equal(ui.nodes.get("signal-pagination").hidden, true);
  ui.run("COCKPIT_SIGNALS = [{}, {}]; syncCockpitSignalControls()");
  assert.equal(ui.nodes.get("signal-pagination").hidden, false);
});

test("a failed refresh can retry and clear the error state", async () => {
  let fail = true;
  const ui = harness([...signalIds, ...historyIds], async (url) => url.includes("latest_run") ? response(snapshot(), fail ? 503 : 200) : response(ledger([])));
  ui.run("renderBoard = async (payload) => renderCockpitSignal(payload)");
  await assert.rejects(ui.run("refreshBoard()"));
  assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "error");
  fail = false; await ui.run("refreshBoard()");
  assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "ready");
  assert.equal(ui.run("BOARD_STATE.lastError"), null);
});


test("history timestamps visibly match the Chicago calendar-date filter", () => {
  const ui = harness();
  assert.match(ui.run('formatRecommendationTs("2026-10-09T01:00:00Z")'), /Oct 8, 2026/);
  assert.match(ui.run('formatRecommendationTs("2026-10-09T01:00:00Z")'), /CDT/);
});

for (const invalid of [{ entries: [null] }, { entries: [{ picks: [null] }] }, { entries: [{ picks: "bad" }] }]) {
  test(`malformed optional history ${JSON.stringify(invalid)} cannot strand the primary board`, async () => {
    const ui = harness([...signalIds, ...historyIds], async (url) => response(url.includes("latest_run") ? snapshot() : invalid));
    ui.run("renderBoard = async (payload) => renderCockpitSignal(payload)");
    await ui.run("refreshBoard()");
    assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "ready");
    assert.equal(ui.nodes.get("board-refresh-btn").disabled, false);
    assert.equal(ui.run("BOARD_STATE.loading"), false);
    assert.equal(ui.run("PROSPECTIVE_LEDGER"), null);
    assert.match(ui.nodes.get("recommendation-sync").textContent, /could not be loaded/);
  });
}

test("malformed history preserves a previously valid export with a refresh warning", async () => {
  const ui = harness([...signalIds, ...historyIds], async (url) => response(url.includes("latest_run") ? snapshot() : { entries: [null] }));
  ui.set("saved", ledger([pick("AAA", "2026-10-08T14:00:00Z")]));
  ui.run("PROSPECTIVE_LEDGER = saved; renderBoard = async (payload) => renderCockpitSignal(payload)");
  await ui.run("refreshBoard()");
  assert.equal(ui.run("PROSPECTIVE_LEDGER === saved"), true);
  assert.match(ui.nodes.get("recommendation-sync").textContent, /last loaded export/);
  assert.equal(ui.nodes.get("board-refresh-btn").disabled, false);
});

test("optional history renderer failures cannot block a successful primary snapshot", async () => {
  const ui = harness([...signalIds, ...historyIds], async (url) => response(url.includes("latest_run") ? snapshot() : ledger([])));
  ui.run("renderBoard = async (payload) => renderCockpitSignal(payload); renderProspectiveScoreboard = () => { throw new Error('render failed'); }");
  await ui.run("refreshBoard()");
  assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "ready");
  assert.equal(ui.nodes.get("board-refresh-btn").disabled, false);
  assert.match(ui.nodes.get("recommendation-sync").textContent, /could not be displayed/);
  assert.equal(ui.nodes.get("recommendation-list").attributes["aria-busy"], "false");
});


test("text-only age updates keep the visible freshness metric current", () => {
  const ui = harness([...signalIds, "cockpit-signal-age"]); ui.set("payload", snapshot(241));
  ui.run("renderCockpitSignal(payload)");
  const before = ui.nodes.get("cockpit-signal").innerHTML;
  ui.run("timeAgo = () => '5h ago'; updateCockpitSignalState(payload)");
  assert.equal(ui.nodes.get("cockpit-signal-age").textContent, "5h ago");
  assert.equal(ui.nodes.get("cockpit-signal").innerHTML, before);
  assert.equal(ui.nodes.get("cockpit-signal-state").textContent, "Stale snapshot");
});


test("initial history renderer failure cannot strand refresh or its next retry", async () => {
  let requests = 0;
  const ui = harness([...signalIds, ...historyIds], async (url) => {
    requests++;
    return response(url.includes("latest_run") ? snapshot() : ledger([]));
  });
  ui.run("renderBoard = async (payload) => renderCockpitSignal(payload); renderRecommendationHistory = () => { throw new Error('history failed'); }");
  for (let retry = 0; retry < 2; retry++) {
    await ui.run("refreshBoard()");
    assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "ready");
    assert.equal(ui.nodes.get("board-refresh-btn").disabled, false);
    assert.match(ui.nodes.get("recommendation-sync").textContent, /could not be displayed/);
  }
  assert.equal(requests, 4);
});


test("invalid outcome window labels cannot break history selection or become an accepted export", async () => {
  const data = ledger([pick("AAA", "2026-10-08T14:00:00Z")]);
  data.outcome_policy.required_fixed_exits = [null];
  const ui = harness(historyIds, async () => response(data)); ui.set("data", data);
  await assert.rejects(ui.run("loadProspectiveLedger()"), /unavailable in this export/);
  // Also tolerate already-retained malformed data at the presentation boundary.
  ui.run("PROSPECTIVE_LEDGER = data; RECOMMENDATION_HISTORY.selected = liveRecommendationRows(data)[0].key; renderRecommendationHistory()");
  assert.match(ui.nodes.get("recommendation-detail").innerHTML, /Outcome marks are pending/);
});


test("null candidate rows cannot replace the previous valid snapshot or strand the visible error state", async () => {
  const invalid = snapshot(0, [null]);
  const ui = harness([...signalIds, ...historyIds], async (url) => response(url.includes("latest_run") ? invalid : ledger([])));
  ui.set("saved", snapshot()); ui.run("SNAPSHOT = saved");
  await assert.rejects(ui.run("refreshBoard()"), /no valid Council board/);
  assert.equal(ui.run("SNAPSHOT === saved"), true);
  assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "error");
  assert.equal(ui.nodes.get("board-refresh-btn").disabled, false);
  ui.set("invalid", invalid); ui.run("BOARD_STATE.lastError = null; renderCockpitSignal(invalid)");
  assert.equal(ui.nodes.get("cockpit-signal").dataset.state, "empty");
});
