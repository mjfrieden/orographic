import assert from "node:assert/strict";
import test from "node:test";

import {
  buildExecutionTelemetry,
  buildOrderProvenanceEvent,
  listOrderProvenance,
  recordOrderProvenance,
} from "../../functions/_lib/order_ledger.js";
import { buildSessionCookie } from "../../functions/_lib/auth.js";
import { enrichPositionsWithQuotes } from "../../functions/_lib/tradier.js";
import { onRequestPost as orderPost } from "../../functions/api/tradier/orders.js";

class FakeD1Statement {
  constructor(db, sql) {
    this.db = db;
    this.sql = sql;
    this.values = [];
  }

  bind(...values) {
    this.values = values;
    return this;
  }

  async run() {
    if (/INSERT INTO order_provenance_events/i.test(this.sql)) {
      const payload = JSON.parse(String(this.values[19] || "{}"));
      this.db.rows.push({
        id: this.values[0],
        created_at_utc: this.values[1],
        event_type: this.values[2],
        mode: this.values[3],
        username: this.values[4],
        user_role: this.values[5],
        run_generated_at_utc: this.values[6],
        lane: this.values[7],
        symbol: this.values[8],
        option_symbol: this.values[9],
        side: this.values[10],
        quantity: this.values[11],
        limit_price: this.values[12],
        broker_order_id: this.values[13],
        broker_status: this.values[14],
        exit_policy_action: this.values[15],
        payload_json: JSON.stringify(payload),
      });
    }
    return { success: true };
  }

  async all() {
    const limit = Number(this.values[0] || 50);
    return {
      results: [...this.db.rows]
        .sort((a, b) => String(b.created_at_utc).localeCompare(String(a.created_at_utc)))
        .slice(0, limit),
    };
  }
}

class FakeD1Database {
  constructor() {
    this.rows = [];
  }

  prepare(sql) {
    return new FakeD1Statement(this, sql);
  }

  async batch(statements) {
    return statements.map(() => ({ success: true }));
  }
}

function snapshot({ generatedAt = new Date().toISOString(), spread = false } = {}) {
  const live = {
    symbol: "AAPL",
    contract_symbol: "AAPL260522C00100000",
    option_type: "call",
    expiry: "2026-05-22",
    strike: 100,
    bid: 1.1,
    ask: 1.2,
    premium: 1.2,
    forge_score: 0.9,
    is_spread: spread,
    short_strike: spread ? 105 : null,
    short_bid: spread ? 0.35 : null,
    spread_cost: spread ? 0.85 : null,
  };
  const shadow = {
    symbol: "MSFT",
    contract_symbol: "MSFT260522C00200000",
    option_type: "call",
    expiry: "2026-05-22",
    strike: 200,
    bid: 1.0,
    ask: 1.1,
    premium: 1.1,
    forge_score: 0.82,
  };
  return {
    generated_at_utc: generatedAt,
    regime: { mode: "risk_on" },
    council: {
      live_board: [live],
      shadow_board: [shadow],
    },
    forge_candidates: [live, shadow],
  };
}

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function makeContext({
  role = "admin",
  body,
  envOverrides = {},
  snapshotPayload = snapshot(),
  db = new FakeD1Database(),
} = {}) {
  const env = {
    OROGRAPHIC_SESSION_SECRET: "test-secret",
    TRADIER_ACCESS_TOKEN: "token",
    TRADIER_ACCOUNT_ID: "acct",
    TRADIER_SANDBOX_MODE: "true",
    TRADIER_MAX_CONTRACTS: "3",
    POSITIONS_DB: db,
    ASSETS: {
      fetch: async () => jsonResponse(snapshotPayload),
    },
    ...envOverrides,
  };
  const cookie = await buildSessionCookie(env, {
    username: role === "admin" ? "admin@example.test" : "viewer@example.test",
    role,
  });
  const request = new Request("https://orographic.test/api/tradier/orders", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      cookie: cookie.split(";", 1)[0],
    },
    body: JSON.stringify(body),
  });
  return { request, env, db };
}

function withMockTradier(run) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input);
    if (url.includes("/markets/quotes")) {
      const symbol = new URL(url).searchParams.get("symbols") || "AAPL260522C00100000";
      return jsonResponse({
        quotes: {
          quote: {
            symbol,
            bid: 1.11,
            ask: 1.23,
            last: 1.18,
          },
        },
      });
    }
    if (url.includes("/accounts/acct/orders/ORDER1")) {
      return jsonResponse({
        order: {
          id: "ORDER1",
          status: "filled",
          option_symbol: "AAPL260522C00100000",
          side: "buy_to_open",
          quantity: 1,
          price: 1.23,
          avg_fill_price: 1.25,
          transaction_date: new Date().toISOString(),
        },
      });
    }
    if (url.includes("/accounts/acct/orders") && init.method === "POST") {
      const body = new URLSearchParams(String(init.body || ""));
      return jsonResponse({
        order: {
          id: body.get("preview") === "true" ? null : "ORDER1",
          status: body.get("preview") === "true" ? "ok" : "submitted",
          result: true,
          option_symbol: body.get("option_symbol"),
          side: body.get("side"),
          quantity: Number(body.get("quantity") || 1),
          price: Number(body.get("price") || 0),
        },
      });
    }
    return originalFetch(input, init);
  };
  return Promise.resolve()
    .then(run)
    .finally(() => {
      globalThis.fetch = originalFetch;
    });
}

const buyLiveBody = {
  preview: false,
  option_symbol: "AAPL260522C00100000",
  symbol: "AAPL",
  side: "buy_to_open",
  quantity: 1,
  type: "limit",
  duration: "day",
  price: 1.2,
};

test("recordOrderProvenance and listOrderProvenance round trip event payloads", async () => {
  const db = new FakeD1Database();
  const event = buildOrderProvenanceEvent({
    eventType: "preview",
    config: { mode: "sandbox" },
    session: { username: "admin@example.test", role: "admin" },
    snapshot: { generated_at_utc: "2026-05-15T14:00:00Z" },
    snapshotInfo: { age_minutes: 3, is_fresh: true },
    lane: "live",
    candidate: {
      symbol: "AAPL",
      contract_symbol: "AAPL260522C00100000",
      forge_score: 0.91,
    },
    quote: { bid: 1.1, ask: 1.2 },
    envelope: {
      symbol: "AAPL",
      option_symbol: "AAPL260522C00100000",
      side: "buy_to_open",
      quantity: 1,
      price: "1.20",
    },
    result: { order: { status: "ok" } },
  });

  await recordOrderProvenance({ POSITIONS_DB: db }, event);
  const rows = await listOrderProvenance({ POSITIONS_DB: db });

  assert.equal(rows.length, 1);
  assert.equal(rows[0].event_type, "preview");
  assert.equal(rows[0].lane, "live");
  assert.equal(
    rows[0].payload.recommendation_id,
    "2026-05-15T14:00:00Z|AAPL260522C00100000|live",
  );
  assert.equal(rows[0].payload.candidate.forge_score, 0.91);
});

test("execution telemetry measures quote age, latency, fill delay, and adverse slippage", () => {
  const telemetry = buildExecutionTelemetry({
    envelope: {
      side: "buy_to_open",
      quantity: 2,
      price: "1.22",
    },
    quote: {
      bid: 1.1,
      ask: 1.2,
      askdate: "2026-05-15T13:59:58Z",
    },
    result: {
      confirmation: {
        avg_fill_price: 1.25,
        commission: 0.5,
        fees: 0.15,
        raw: { transaction_date: "2026-05-15T14:00:02Z" },
      },
    },
    timing: {
      quote_captured_at_utc: "2026-05-15T13:59:59Z",
      broker_requested_at_utc: "2026-05-15T14:00:00Z",
      broker_response_at_utc: "2026-05-15T14:00:00.250Z",
    },
  });

  assert.equal(telemetry.quote_age_seconds, 1);
  assert.equal(telemetry.broker_round_trip_ms, 250);
  assert.equal(telemetry.fill_delay_seconds, 2);
  assert.ok(Math.abs(telemetry.signed_adverse_slippage_per_contract - 0.05) < 1e-9);
  assert.ok(Math.abs(telemetry.signed_adverse_slippage_usd - 10) < 1e-9);
  assert.equal(telemetry.fill_telemetry_complete, true);
});

test("preview order records provenance", async () => {
  await withMockTradier(async () => {
    const context = await makeContext({
      role: "viewer",
      body: { ...buyLiveBody, preview: true },
    });
    const response = await orderPost(context);
    const payload = await response.json();
    const rows = await listOrderProvenance({ POSITIONS_DB: context.db });

    assert.equal(response.status, 200);
    assert.equal(payload.provenance.ok, true);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].event_type, "preview");
    assert.equal(rows[0].user_role, "viewer");
    assert.equal(rows[0].payload.execution.quote_ask, 1.23);
    assert.ok(rows[0].payload.execution.broker_round_trip_ms >= 0);
  });
});

test("submitted order records provenance", async () => {
  await withMockTradier(async () => {
    const context = await makeContext({ body: buyLiveBody });
    const response = await orderPost(context);
    const payload = await response.json();
    const rows = await listOrderProvenance({ POSITIONS_DB: context.db });

    assert.equal(response.status, 200);
    assert.equal(payload.provenance.ok, true);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].event_type, "submit");
    assert.equal(rows[0].broker_order_id, "ORDER1");
    assert.equal(rows[0].payload.execution.broker_avg_fill_price, 1.25);
    assert.ok(rows[0].payload.execution.signed_adverse_slippage_usd > 0);
  });
});

test("blocked stale, non-admin, live-disabled, shadow, and spread attempts are recorded", async () => {
  await withMockTradier(async () => {
    const cases = [
      {
        name: "stale",
        expectedStatus: 409,
        expectedType: "blocked_validation",
        context: () =>
          makeContext({
            body: buyLiveBody,
            snapshotPayload: snapshot({ generatedAt: "2020-01-01T14:00:00Z" }),
          }),
      },
      {
        name: "non-admin",
        expectedStatus: 403,
        expectedType: "blocked_validation",
        context: () => makeContext({ role: "viewer", body: buyLiveBody }),
      },
      {
        name: "live disabled",
        expectedStatus: 412,
        expectedType: "blocked_live_disabled",
        context: () =>
          makeContext({
            body: { ...buyLiveBody, confirm_live: true },
            envOverrides: {
              TRADIER_SANDBOX_MODE: "false",
              TRADIER_TRADING_MODE: "live",
              TRADIER_LIVE_TRADING_ENABLED: "false",
            },
          }),
      },
      {
        name: "shadow",
        expectedStatus: 409,
        expectedType: "blocked_validation",
        context: () =>
          makeContext({
            body: {
              ...buyLiveBody,
              option_symbol: "MSFT260522C00200000",
              symbol: "MSFT",
            },
          }),
      },
      {
        name: "manual",
        expectedStatus: 409,
        expectedType: "blocked_validation",
        context: () =>
          makeContext({
            body: {
              ...buyLiveBody,
              option_symbol: "TSLA260522C00300000",
              symbol: "TSLA",
            },
          }),
      },
      {
        name: "spread",
        expectedStatus: 409,
        expectedType: "blocked_spread",
        context: () =>
          makeContext({
            body: { ...buyLiveBody, preview: true },
            snapshotPayload: snapshot({ spread: true }),
          }),
      },
    ];

    for (const item of cases) {
      const context = await item.context();
      const response = await orderPost(context);
      const payload = await response.json();
      const rows = await listOrderProvenance({ POSITIONS_DB: context.db });

      assert.equal(response.status, item.expectedStatus, item.name);
      assert.equal(payload.provenance.ok, true, item.name);
      assert.equal(rows.length, 1, item.name);
      assert.equal(rows[0].event_type, item.expectedType, item.name);
      assert.ok(rows[0].broker_status, item.name);
    }
  });
});

test("enrichPositionsWithQuotes annotates 50 percent stop-loss exits", () => {
  const [position] = enrichPositionsWithQuotes(
    [
      {
        symbol: "SHOP260515C00103000",
        quantity: 1,
        cost_basis: 138,
        current_value: 60,
      },
    ],
    [],
  );

  assert.equal(position.open_pl_pct, -0.5652);
  assert.equal(position.exit_policy.action, "sell_to_close");
  assert.equal(position.exit_policy.required, true);
});
