import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../web/app.js", import.meta.url), "utf8");
const flow = source.slice(source.indexOf("let PENDING_ORDER = null;"), source.indexOf("function bindPositionsTable()"));
const envelope = {
  option_symbol: "AAPL261218C00200000", symbol: "AAPL", side: "buy_to_open",
  quantity: 2, type: "limit", duration: "day", price: "1.23",
};

function harness(fetch) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      textContent: "", innerHTML: "", disabled: false, hidden: true, style: {},
      classList: { add() {}, remove() {} }, handlers: {},
      addEventListener(name, fn) { this.handlers[name] = fn; },
      focus() {},
    });
    return elements.get(id);
  }
  const ctx = vm.createContext({
    document: { getElementById: element, body: { style: {} } },
    fetch, readBrokerJson: async (response) => response.json(),
    SESSION: { session: { role: "admin" } }, BROKER_STATE: { mode: "sandbox" },
    setText: (id, value) => { element(id).textContent = value; },
    summaryItemHtml: (name, value) => `${name}: ${value};`,
    money: (value) => `$${Number(value).toFixed(2)}`,
    escapeHtml: (value) => String(value).replaceAll("<", "&lt;"),
    clampQuantity: (qty, fallback) => Number(qty) || fallback,
    suggestedEntryQuantity: () => 1,
    estimateTradeValue: (_order, qty, price) => Number(qty) * Number(price) * 100,
    humanBrokerTicketError: (error, next) => `${error.message} ${next}`,
    loadAccount() {}, setTimeout() {},
  });
  vm.runInContext(`${flow}\n globalThis.ticket = { bindModal, openModal, closeModal, handlePreview, handleClosePosition, pending: () => PENDING_ORDER };`, ctx);
  ctx.ticket.bindModal();
  return { ...ctx.ticket, element, click: () => element("modal-execute-btn").handlers.click() };
}

const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

for (const closing of [false, true]) {
  test(`${closing ? "closing" : "entry"} preview displays and submits the server envelope even when the broker omits price/quantity`, async () => {
    const requests = [];
    const reviewed = { ...envelope, side: closing ? "sell_to_close" : "buy_to_open" };
    const ui = harness(async (_url, init) => {
      const body = JSON.parse(init.body);
      requests.push(body);
      return response(body.preview
        ? { ok: true, order: { result: true, price: 0, quantity: 0 }, envelope: reviewed, submission: { allowed: true, mode: "sandbox" } }
        : { ok: true, outcome: "accepted", order: { id: "ORDER-1" }, envelope: reviewed });
    });
    if (closing) await ui.handleClosePosition(envelope.option_symbol, 7);
    else await ui.handlePreview(envelope.option_symbol, "AAPL", "live", 0.99, 1, 7);
    assert.equal(ui.pending().price, "1.23");
    assert.equal(ui.pending().quantity, 2);
    assert.match(ui.element("modal-body").innerHTML, /Limit Price: \$1\.23/);
    assert.match(ui.element("modal-body").innerHTML, /Quantity: 2/);
    await ui.click();
    assert.equal(requests[1].price, "1.23");
    assert.equal(requests[1].quantity, 2);
    assert.equal(requests[1].side, reviewed.side);
  });
}

test("accepted order with unavailable status shows its ID and warning, and cannot resubmit", async () => {
  let calls = 0;
  const ui = harness(async () => {
    calls++;
    return response({ ok: true, outcome: "accepted", confirmation_status: "unavailable", order: { id: "ORDER-1", status: "submitted" }, envelope,
      warning: "Tradier accepted this order. Check this order ID in the broker; do not resubmit it." });
  });
  ui.openModal("Preview", "", true, envelope);
  await ui.click();
  assert.equal(ui.element("modal-title").textContent, "Order Submitted");
  assert.match(ui.element("modal-body").innerHTML, /ORDER-1/);
  assert.match(ui.element("modal-body").innerHTML, /Awaiting broker status/);
  assert.match(ui.element("modal-body").innerHTML, /do not resubmit/i);
  assert.equal(ui.element("modal-execute-btn").disabled, true);
  assert.equal(ui.pending(), null);
  await ui.click();
  assert.equal(calls, 1);
});

test("a rejected confirmation retains the order ID and disables the ticket", async () => {
  const ui = harness(async () => response({ ok: true, outcome: "rejected", confirmation_status: "confirmed",
    order: { id: "ORDER-1", status: "submitted" }, confirmation: { id: "ORDER-1", status: "rejected" }, envelope }));
  ui.openModal("Preview", "", true, envelope);
  await ui.click();
  assert.equal(ui.element("modal-title").textContent, "Order Rejected");
  assert.match(ui.element("modal-body").innerHTML, /ORDER-1/);
  assert.match(ui.element("modal-body").innerHTML, /rejected/);
  assert.equal(ui.element("modal-execute-btn").disabled, true);
});

for (const failure of ["network", "http", "session"]) {
  test(`${failure} submission failure cannot claim no order was sent or re-enable Execute`, async () => {
    let calls = 0;
    const ui = harness(async () => {
      calls++;
      if (failure === "network") throw new Error("Failed to fetch");
      return response({ ok: false, error: failure === "session" ? "Session expired" : "Broker unavailable" }, 502);
    });
    ui.openModal("Preview", "", true, envelope);
    await ui.click();
    assert.match(ui.element("modal-message").textContent, /Check Orders in Tradier/);
    assert.match(ui.element("modal-message").textContent, /Do not resubmit/);
    assert.doesNotMatch(ui.element("modal-message").textContent, /not sent|try execute|retry/i);
    assert.equal(ui.element("modal-execute-btn").disabled, true);
    assert.equal(ui.pending().executeEnabled, false);
    await ui.click();
    assert.equal(calls, 1);
  });
}

test("repeated clicks while the broker is pending send only one POST", async () => {
  let calls = 0;
  let settle;
  const ui = harness(() => { calls++; return new Promise((resolve) => { settle = resolve; }); });
  ui.openModal("Preview", "", true, envelope);
  const first = ui.click();
  await ui.click();
  assert.equal(calls, 1);
  settle(response({ ok: true, outcome: "accepted", order: { id: "ORDER-1" }, envelope }));
  await first;
  assert.equal(ui.element("modal-execute-btn").disabled, true);
});

test("a late response cannot overwrite a newer order ticket", async () => {
  let settle;
  const ui = harness(() => new Promise((resolve) => { settle = resolve; }));
  ui.openModal("Old preview", "", true, envelope);
  const first = ui.click();
  ui.closeModal();
  ui.openModal("New preview", "new details", true, { ...envelope, price: "2.00" });
  settle(response({ ok: true, outcome: "accepted", order: { id: "ORDER-1" }, envelope }));
  await first;
  assert.equal(ui.element("modal-title").textContent, "New preview");
  assert.equal(ui.pending().price, "2.00");
  assert.equal(ui.element("modal-execute-btn").disabled, false);
});
