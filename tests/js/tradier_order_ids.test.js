import assert from "node:assert/strict";
import test from "node:test";
import { buildSessionCookie } from "../../functions/_lib/auth.js";
import { normalizeOrderId, parseOrderJson } from "../../functions/_lib/order_ids.js";
import { buildOrderProvenanceEvent, listOrderProvenance } from "../../functions/_lib/order_ledger.js";
import { previewOrPlaceOrder, summarizeOrders, tradierRequest } from "../../functions/_lib/tradier.js";
import { onRequestPost } from "../../functions/api/tradier/orders.js";
import { onRequestGet as accountGet } from "../../functions/api/tradier/account.js";
import { onRequestGet as statusGet } from "../../functions/api/tradier/status.js";

const contract = "AAPL261218C00200000";
const env = {
  OROGRAPHIC_SESSION_SECRET: "mock-session-secret",
  TRADIER_ACCESS_TOKEN: "mock-token",
  TRADIER_ACCOUNT_ID: "mock-account",
  TRADIER_SANDBOX_MODE: "true",
};
const envelope = {
  class: "option", symbol: "AAPL", option_symbol: contract, side: "buy_to_open",
  quantity: 1, type: "limit", duration: "day", price: "1.20",
};
const cases = [
  ["152158800", "152158800"],
  ["2147483648", "2147483648"],
  ["4294967296", "4294967296"],
  ["91759371479", "91759371479"],
  ["9007199254740991", "9007199254740991"],
  ["9007199254740993", "9007199254740993"],
  ["9223372036854775807", "9223372036854775807"],
  ['"00091759371479"', "00091759371479"],
  [JSON.stringify("ORD/2026?x# part"), "ORD/2026?x# part"],
];

function mockBroker(t, literal, { confirmationLiteral = literal, detailFailure = false, rejected = false } = {}) {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    const path = new URL(url).pathname;
    assert.equal(new URL(url).origin, "https://sandbox.tradier.com");
    assert.equal(init.redirect, "manual");
    calls.push({ path, method: init.method });
    if (path.endsWith("/markets/quotes")) return Response.json({ quotes: { quote: { symbol: contract, bid: 1.1, ask: 1.2 } } });
    if (path.endsWith("/orders") && init.method === "POST") {
      return new Response(`{"order":{"id":${literal},"status":"${rejected ? "rejected" : "submitted"}","result":${!rejected},"quantity":1,"price":1.2,"leg":[{"id":9223372036854775806}]}}`);
    }
    if (path.includes("/orders/")) {
      if (detailFailure) throw new Error("mock status unavailable");
      return new Response(`{"order":{"id":${confirmationLiteral},"status":"open","quantity":1,"price":1.2}}`);
    }
    if (path.endsWith("/orders")) return new Response(`{"orders":{"order":[{"id":${literal},"status":"open","quantity":1,"price":1.2}]}}`);
    if (/\/(profile|positions|balances)$/.test(path)) return Response.json({});
    throw new Error(`Unexpected mocked request: ${url}`);
  });
  return calls;
}

async function context() {
  const rows = [];
  const inserts = [];
  const db = {
    batch: async () => [],
    prepare(sql) {
      return {
        bind(...values) {
          return {
            run: async () => {
              assert.match(sql, /INSERT INTO order_provenance_events/);
              inserts.push(values);
              rows.push({ id: values[0], broker_order_id: values[13], payload_json: values[19] });
              return { success: true };
            },
            all: async () => ({ results: rows }),
          };
        },
      };
    },
  };
  const settings = { ...env, POSITIONS_DB: db, ASSETS: { fetch: async () => Response.json({
    generated_at_utc: new Date().toISOString(),
    council: { live_board: [{ symbol: "AAPL", contract_symbol: contract }] },
  }) } };
  const cookie = await buildSessionCookie(settings, { username: "admin@example.test", role: "admin" });
  const headers = { cookie: cookie.split(";", 1)[0], "content-type": "application/json" };
  return {
    env: settings, rows, inserts, headers,
    request: new Request("https://orographic.test/api/tradier/orders", {
      method: "POST", headers, body: JSON.stringify({ ...envelope, preview: false }),
    }),
  };
}

for (const [literal, expected] of cases) {
  test(`order ${literal} stays exact through raw broker JSON, lookup, API, D1 and account lists`, async (t) => {
    const calls = mockBroker(t, literal, { confirmationLiteral: JSON.stringify(expected) });
    const ctx = await context();
    const response = await onRequestPost(ctx);
    assert.equal(response.status, 200);
    const data = await response.json();
    assert.equal(data.order.id, expected);
    assert.equal(data.order.raw.id, expected);
    assert.equal(data.order.raw.leg[0].id, "9223372036854775806");
    assert.equal(data.confirmation.id, expected);
    assert.equal(data.confirmation_status, "confirmed");
    assert.equal(data.order.quantity, 1);
    assert.equal(data.order.price, 1.2);
    assert.equal(calls.filter(call => call.method === "POST").length, 1);
    assert.ok(calls.some(call => call.path === `/v1/accounts/mock-account/orders/${encodeURIComponent(expected)}`));
    assert.equal(ctx.inserts[0][13], expected, "D1 TEXT binding");
    assert.equal(JSON.parse(ctx.inserts[0][18]).id, expected, "order_json");
    assert.equal(JSON.parse(ctx.inserts[0][19]).confirmation.id, expected, "payload_json");
    const ledger = await listOrderProvenance(ctx.env);
    assert.equal(ledger[0].broker_order_id, expected);
    assert.equal(ledger[0].payload.order.id, expected);
    for (const handler of [accountGet, statusGet]) {
      const read = await handler({ env: ctx.env, request: new Request("https://orographic.test/api/tradier/account", { headers: ctx.headers }) });
      assert.equal(read.status, 200);
      assert.equal((await read.json()).broker.orders[0].id, expected);
    }
  });
}

test("parser handles escaped keys, nested IDs and quoted text without changing financial numbers", () => {
  const value = parseOrderJson(String.raw`{"order":{"\u0069d":9007199254740993,"message":"say \"id\": 9007199254740993","quantity":3,"price":1.25,"fees":1e-3,"leg":{"id":91759371479}}}`);
  assert.equal(value.order.id, "9007199254740993");
  assert.equal(value.order.leg.id, "91759371479");
  assert.equal(value.order.message, 'say "id": 9007199254740993');
  assert.deepEqual([value.order.quantity, value.order.price, value.order.fees], [3, 1.25, 0.001]);
  assert.equal(parseOrderJson('{"id":null}').id, null);
  assert.deepEqual(parseOrderJson('{"order":{}}'), { order: {} });
});

test("malformed JSON and malformed numeric IDs are not repaired into valid payloads", () => {
  for (const text of ['{"id":01}', '{"id":+123}', '{"id":1.}', '{"id":1e}', '{"id":123true}', '{"id":123,}', '{"id":123']) {
    assert.throws(() => parseOrderJson(text), SyntaxError, text);
  }
});

test("normalization preserves opaque text and rejects already-lossy or invalid ID values", () => {
  assert.equal(normalizeOrderId(91759371479), "91759371479");
  assert.equal(normalizeOrderId("000123"), "000123");
  assert.equal(normalizeOrderId(" ORD-A "), " ORD-A ");
  for (const value of [9007199254740992, -1, 1.5, NaN, Infinity, null, undefined, "", "  ", true, {}, []]) {
    assert.equal(normalizeOrderId(value), null);
  }
  assert.equal(summarizeOrders({ orders: { order: { id: 9007199254740992 } } })[0].id, null);
  assert.equal(buildOrderProvenanceEvent({ result: { order: { id: 9007199254740992 } } }).broker_order_id, "");
});

test("two IDs that round to the same Number cannot confirm one another", async (t) => {
  const calls = mockBroker(t, "9007199254740992", { confirmationLiteral: "9007199254740993" });
  const result = await previewOrPlaceOrder(env, envelope, { preview: false });
  assert.equal(result.order.id, "9007199254740992");
  assert.equal(result.confirmation, null);
  assert.equal(result.confirmationStatus, "unavailable");
  assert.match(result.warning, /do not resubmit/i);
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
});

for (const rejected of [false, true]) {
  test(`large recorded ID survives ${rejected ? "broker rejection" : "unavailable confirmation"} without resubmission`, async (t) => {
    const calls = mockBroker(t, "9223372036854775807", { rejected, detailFailure: true });
    const result = await previewOrPlaceOrder(env, envelope, { preview: false });
    assert.equal(result.order.id, "9223372036854775807");
    assert.equal(result.outcome, rejected ? "rejected" : "accepted");
    assert.equal(result.confirmationStatus, rejected ? "confirmed" : "unavailable");
    assert.equal(calls.filter(call => call.method === "POST").length, 1);
  });
}

for (const literal of ["null", "true", "-1", "-0", "1.5", "1.0", "1e3", "9.223372036854776e18", "9007199254740990.9", "1.00000000000000001", "1e-9999", "-1e-9999"]) {
  test(`invalid identifier ${literal} does not trigger an order lookup or automatic retry`, async (t) => {
    const calls = mockBroker(t, literal);
    const result = await previewOrPlaceOrder(env, envelope, { preview: false });
    assert.equal(result.order.id, null);
    assert.equal(result.outcome, "unknown");
    assert.match(result.warning, /do not resubmit/i);
    assert.equal(calls.length, 1);
  });
}

test("preview with no broker ID remains a preview", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ order: { result: true } }));
  const result = await previewOrPlaceOrder(env, envelope, { preview: true });
  assert.equal(result.order.id, null);
  assert.equal(result.outcome, "preview");
});

for (const id of [".", ".."]) {
  test(`opaque dot-segment ${id} is retained without looking up a different resource`, async (t) => {
    const calls = mockBroker(t, JSON.stringify(id));
    const result = await previewOrPlaceOrder(env, envelope, { preview: false });
    assert.equal(result.order.id, id);
    assert.equal(result.confirmationStatus, "unavailable");
    assert.equal(calls.length, 1);
  });
}

test("non-order endpoints keep their existing JSON types", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response('{"positions":{"position":{"id":123,"quantity":2}}}'));
  const response = await tradierRequest(env, { path: "/accounts/mock-account/positions" });
  assert.equal(response.data.positions.position.id, 123);
});

test("legacy ledger numeric JSON is read losslessly, while existing TEXT identifiers stay untouched", async () => {
  const ctx = await context();
  ctx.rows.push({ id: "event-1", broker_order_id: "000123", payload_json: '{"broker_order_id":9007199254740993,"order":{"id":9223372036854775807}}' });
  const [row] = await listOrderProvenance(ctx.env);
  assert.equal(row.broker_order_id, "000123");
  assert.equal(row.payload.broker_order_id, "9007199254740993");
  assert.equal(row.payload.order.id, "9223372036854775807");
});
