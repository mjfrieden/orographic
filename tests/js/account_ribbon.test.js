import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../web/app.js", import.meta.url), "utf8");
const money = source.slice(source.indexOf("function money("), source.indexOf("function pct("));
const ribbon = source.slice(source.indexOf("function renderRibbon()"), source.indexOf("function renderPositions()"));
function render(state) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {});
    return elements.get(id);
  };
  const ctx = vm.createContext({
    BROKER_STATE: { positions: [], ...state },
    document: { getElementById: element },
    setText: (id, text) => { element(id).textContent = text; },
    signed: String,
  });
  vm.runInContext(`${money}\n${ribbon}\nrenderRibbon();`, ctx);
  return element;
}

test("ribbon renders options buying power, not stock margin buying power", () => {
  const el = render({ balances: { buying_power: 6363.86, buying_power_label: "Options Buying Power", buying_power_source: "margin.option_buying_power", stock_buying_power: 12727.72 } });
  assert.equal(el("ribbon-obp").textContent, "$6,363.86");
  assert.equal(el("ribbon-obp-label").textContent, "Options Buying Power");
  assert.match(el("ribbon-obp").title, /margin.option_buying_power/);
});

test("ribbon labels cash accounts honestly and preserves genuine zero", () => {
  const el = render({ balances: { buying_power: 0, buying_power_label: "Cash Available", buying_power_source: "cash.cash_available" } });
  assert.equal(el("ribbon-obp").textContent, "$0.00");
  assert.equal(el("ribbon-obp-label").textContent, "Cash Available");
});

test("ribbon distinguishes unavailable and loading from zero", () => {
  assert.equal(render({ balances: { buying_power: null } })("ribbon-obp").textContent, "Unavailable");
  assert.equal(render({ loading: true })("ribbon-obp").textContent, "Loading…");
});

test("failed refresh cannot present previously loaded balances as current", () => {
  const el = render({ balances: { buying_power: 100, total_equity: 200 }, lastError: "Offline" });
  assert.equal(el("ribbon-obp").textContent, "Unavailable");
  assert.equal(el("ribbon-equity").textContent, "--");
  assert.match(el("ribbon-obp").title, /refresh failed/);
});

test("in-progress refresh labels retained snapshot; success displays fresh amount", () => {
  const el = render({ balances: { buying_power: 100 }, loading: true });
  assert.equal(el("ribbon-obp").textContent, "$100.00");
  assert.match(el("ribbon-obp").title, /previous account sync/);
  assert.equal(render({ balances: { buying_power: 75 } })("ribbon-obp").textContent, "$75.00");
});
