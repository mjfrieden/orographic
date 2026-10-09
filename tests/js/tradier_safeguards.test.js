import assert from "node:assert/strict";
import test from "node:test";
import { buildSessionCookie } from "../../functions/_lib/auth.js";
import {
  buildOrderEnvelope,
  getTradierSettings,
  previewOrPlaceOrder,
  tradierRequest,
} from "../../functions/_lib/tradier.js";
import { onRequestPost } from "../../functions/api/tradier/orders.js";

const contract = "AAPL261218C00200000";
const candidate = { symbol: "AAPL", contract_symbol: contract, ask: 1.2, bid: 1.1, premium: 1.2 };
const quote = { symbol: contract, ask: 1.23, bid: 1.11 };
const env = {
  OROGRAPHIC_SESSION_SECRET: "mock-session-secret",
  TRADIER_ACCESS_TOKEN: "mock-token",
  TRADIER_ACCOUNT_ID: "mock-account",
  TRADIER_SANDBOX_MODE: "true",
};
const body = {
  preview: false, option_symbol: contract, symbol: "AAPL", side: "buy_to_open",
  quantity: 1, type: "limit", duration: "day", price: "1.20",
};
const json = (payload, status = 200) => new Response(JSON.stringify(payload), { status });

async function context(requestBody = body, overrides = {}, withCandidate = true) {
  const events = [];
  const db = {
    batch: async () => [],
    prepare(sql) {
      return {
        bind(...values) {
          return { run: async () => {
            if (sql.includes("INSERT INTO order_provenance_events")) events.push(JSON.parse(values[19]));
            return { success: true };
          } };
        },
      };
    },
  };
  const settings = {
    ...env,
    POSITIONS_DB: db,
    ASSETS: { fetch: async () => json({
      generated_at_utc: new Date().toISOString(),
      council: { live_board: withCandidate ? [candidate] : [] },
    }) },
    ...overrides,
  };
  const cookie = await buildSessionCookie(settings, { username: "admin@example.test", role: "admin" });
  return {
    env: settings, events,
    request: new Request("https://orographic.test/api/tradier/orders", {
      method: "POST", headers: { cookie: cookie.split(";", 1)[0], "content-type": "application/json" },
      body: JSON.stringify(requestBody),
    }),
  };
}

function mockBroker(t, { currentQuote = quote, quoteFailure = false, detail = "filled", post = { id: "ORDER-1", status: "submitted", result: true } } = {}) {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    calls.push({ url: String(url), ...init });
    assert.equal(new URL(url).origin, "https://sandbox.tradier.com", "tests may only call the mocked sandbox endpoint");
    if (String(url).includes("/markets/quotes")) {
      if (quoteFailure) throw new Error("mock quote unavailable");
      return json({ quotes: { quote: currentQuote } });
    }
    if (init.method === "POST") return json({ order: post });
    if (String(url).endsWith("/orders/ORDER-1")) {
      if (detail === "network") throw new Error("mock network failure");
      if (detail === "http") return json({ error: "mock timeout" }, 504);
      if (detail === "malformed") return json({});
      if (detail === "wrong_id") return json({ order: { id: "OTHER", status: "filled" } });
      return json({ order: { id: "ORDER-1", status: detail } });
    }
    throw new Error(`Unexpected mocked request: ${url}`);
  });
  return calls;
}

test("mode and endpoint conflicts fail closed before any broker request", async (t) => {
  const configurations = [
    { TRADIER_BASE_URL: "https://api.tradier.com/v1" },
    { TRADIER_TRADING_MODE: "live" },
    { TRADIER_SANDBOX_MODE: "false", TRADIER_TRADING_MODE: "live", TRADIER_BASE_URL: "https://sandbox.tradier.com/v1" },
    { TRADIER_BASE_URL: "https://sandbox.tradier.com.attacker.test/v1" },
    { TRADIER_BASE_URL: "https://attacker.test/sandbox.tradier.com" },
    { TRADIER_BASE_URL: "https://api.tradier.com/v1?sandbox.tradier.com" },
    { TRADIER_BASE_URL: "http://sandbox.tradier.com/v1" },
    { TRADIER_BASE_URL: "https://user:pass@sandbox.tradier.com/v1" },
    { TRADIER_BASE_URL: "https://sandbox.tradier.com:444/v1" },
    { TRADIER_BASE_URL: "https://sandbox.tradier.com/v1#fragment" },
    { TRADIER_TRADING_MODE: "sandobx" },
    { TRADIER_TRADING_MODE: "disabled" },
  ];
  let fetches = 0;
  t.mock.method(globalThis, "fetch", async () => { fetches++; throw new Error("No broker request is allowed"); });
  for (const overrides of configurations) {
    const settings = getTradierSettings({ ...env, ...overrides });
    assert.equal(settings.configured, false, JSON.stringify(overrides));
    assert.equal(settings.enabled, false);
    await assert.rejects(tradierRequest(settings, "/markets/quotes"));
    const response = await onRequestPost(await context(body, overrides));
    assert.equal(response.status, 503);
  }
  assert.equal(fetches, 0);
});

test("matching sandbox and live settings retain their exact official endpoint", () => {
  for (const mode of ["sandbox", "live"]) {
    const baseUrl = `https://${mode === "sandbox" ? "sandbox" : "api"}.tradier.com/v1`;
    const settings = getTradierSettings({ ...env, TRADIER_SANDBOX_MODE: "false", TRADIER_TRADING_MODE: mode, TRADIER_BASE_URL: `${baseUrl}/` });
    assert.equal(settings.configured, true);
    assert.equal(settings.mode, mode);
    assert.equal(settings.sandboxMode, mode === "sandbox");
    assert.equal(settings.baseUrl, baseUrl);
  }
});

for (const failure of ["network", "http", "malformed", "wrong_id"]) {
  test(`accepted POST plus ${failure} status lookup retains ID, outcome and ledger without a second POST`, async (t) => {
    const calls = mockBroker(t, { detail: failure });
    const ctx = await context();
    const response = await onRequestPost(ctx);
    const result = await response.json();
    assert.equal(response.status, 200);
    assert.equal(result.ok, true);
    assert.equal(result.order.id, "ORDER-1");
    assert.equal(result.outcome, "accepted");
    assert.equal(result.confirmation_status, "unavailable");
    assert.equal(result.confirmation, null);
    assert.match(result.warning, /do not resubmit/i);
    assert.equal(ctx.events[0].broker_order_id, "ORDER-1");
    assert.equal(ctx.events[0].submission_outcome, "accepted");
    assert.equal(ctx.events[0].confirmation_status, "unavailable");
    assert.equal(calls.filter(c => c.method === "POST").length, 1);
    assert.ok(calls.every(c => c.redirect === "manual"));
  });
}

test("rejected status confirmation is retained with its broker ID", async (t) => {
  mockBroker(t, { detail: "rejected" });
  const ctx = await context();
  const result = await (await onRequestPost(ctx)).json();
  assert.equal(result.ok, true);
  assert.equal(result.outcome, "rejected");
  assert.equal(result.confirmation.id, "ORDER-1");
  assert.equal(result.confirmation.status, "rejected");
  assert.equal(ctx.events[0].broker_order_id, "ORDER-1");
  assert.equal(ctx.events[0].broker_status, "rejected");
});

test("POST rejection with an ID retains the recorded order without a status lookup", async (t) => {
  const calls = mockBroker(t, { post: { id: "ORDER-1", status: "rejected", result: false } });
  const result = await previewOrPlaceOrder(env, body, { preview: false });
  assert.equal(result.outcome, "rejected");
  assert.equal(result.order.id, "ORDER-1");
  assert.equal(calls.length, 1);
});

test("successful POST without an ID is reported as unknown, never automatically retried", async (t) => {
  const calls = mockBroker(t, { post: { result: true } });
  const result = await previewOrPlaceOrder(env, body, { preview: false });
  assert.equal(result.outcome, "unknown");
  assert.equal(result.confirmationStatus, "unavailable");
  assert.match(result.warning, /do not resubmit/i);
  assert.equal(calls.length, 1);
});

for (const side of ["buy_to_open", "sell_to_close"]) {
  test(`${side} submission preserves the reviewed limit and duration after the quote moves`, async (t) => {
    const calls = mockBroker(t, { currentQuote: { ...quote, bid: 4, ask: 5 } });
    const ctx = await context({ ...body, side, price: "1.20", duration: "gtc" }, {}, side !== "sell_to_close");
    const result = await (await onRequestPost(ctx)).json();
    assert.equal(result.ok, true);
    assert.equal(result.envelope.price, "1.20");
    assert.equal(result.envelope.duration, "gtc");
    const submitted = new URLSearchParams(calls.find(c => c.method === "POST").body);
    assert.equal(submitted.get("price"), "1.20");
    assert.equal(submitted.get("duration"), "gtc");
    assert.equal(ctx.events[0].limit_price, 1.2);
  });
}

test("previews use the valid quote, while a reviewed submission cannot exceed the risk budget", async (t) => {
  const calls = mockBroker(t);
  const preview = await (await onRequestPost(await context({ ...body, preview: true }))).json();
  assert.equal(preview.envelope.price, "1.23");
  const submitted = await onRequestPost(await context({ ...body, price: 7 }));
  assert.equal(submitted.status, 409);
  assert.equal(calls.filter(c => c.method === "POST").length, 1, "only the preview was transmitted");
});

test("invalid quote books cannot fall back to candidate or penny prices", async (t) => {
  const invalidQuotes = [null, {}, { ...quote, symbol: "OTHER" }, { ...quote, bid: 0 },
    { ...quote, ask: -1 }, { ...quote, bid: -1 }, { ...quote, ask: "Infinity" },
    { ...quote, ask: "NaN" }, { ...quote, bid: null }, { ...quote, ask: true },
    { ...quote, bid: true }, { ...quote, bid: 2, ask: 1 }];
  for (const currentQuote of invalidQuotes) {
    for (const preview of [true, false]) {
      for (const side of ["buy_to_open", "sell_to_close"]) {
        await t.test(`${preview ? "preview" : "submit"} ${side} ${JSON.stringify(currentQuote)}`, async (st) => {
          const calls = mockBroker(st, { currentQuote });
          const ctx = await context({ ...body, preview, side }, {}, side !== "sell_to_close");
          const response = await onRequestPost(ctx);
          assert.equal(response.status, 409);
          assert.equal(ctx.events[0].event_type, "blocked_quote");
          assert.equal(calls.filter(c => c.method === "POST").length, 0);
        });
      }
    }
  }
});

test("a missing ask blocks entry but a valid matching bid can still close a position", async (t) => {
  for (const ask of [undefined, null, "", 0, "0"]) {
    for (const preview of [true, false]) {
      await t.test(`${preview ? "preview" : "submit"} ask=${ask}`, async (st) => {
        const calls = mockBroker(st, { currentQuote: { ...quote, ask } });
        assert.equal((await onRequestPost(await context({ ...body, preview }))).status, 409);
        const result = await (await onRequestPost(await context({ ...body, preview, side: "sell_to_close" }, {}, false))).json();
        assert.equal(result.ok, true);
        assert.equal(result.envelope.price, preview ? "1.11" : "1.20");
        assert.equal(calls.filter(c => c.method === "POST").length, 1);
      });
    }
  }
});

test("a quote network error blocks a closing order absent from the board", async (t) => {
  const calls = mockBroker(t, { quoteFailure: true });
  const response = await onRequestPost(await context({ ...body, side: "sell_to_close" }, {}, false));
  assert.equal(response.status, 409);
  assert.equal(calls.filter(c => c.method === "POST").length, 0);
});

test("nonfinite, nonnumeric and sub-cent limit prices and missing preview mode are blocked before fetching", async (t) => {
  const calls = mockBroker(t);
  for (const price of ["Infinity", "NaN", "text", null, true, [], 0, -1, 0.001, 1.235]) {
    const response = await onRequestPost(await context({ ...body, price }));
    assert.equal(response.status, 400, String(price));
  }
  const { preview, ...missingPreview } = body;
  assert.equal((await onRequestPost(await context(missingPreview))).status, 400);
  assert.equal(calls.length, 0);
});

test("envelope rejects a fabricated supplied configuration or invalid quote price", async (t) => {
  const calls = mockBroker(t);
  await assert.rejects(tradierRequest({ ...getTradierSettings(env), baseUrl: "https://api.tradier.com/v1" }, "/markets/quotes"));
  assert.throws(() => buildOrderEnvelope(candidate, 1, getTradierSettings(env), { ...quote, ask: NaN }));
  assert.equal(calls.length, 0);
});
