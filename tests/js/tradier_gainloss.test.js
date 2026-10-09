import assert from "node:assert/strict";
import test from "node:test";
import { webcrypto } from "node:crypto";
import { buildSessionCookie } from "../../functions/_lib/auth.js";
import { normalizeGainloss, parseGainlossPage } from "../../functions/_lib/gainloss.js";
import { onRequestGet } from "../../functions/api/tradier/gainloss.js";
if (!globalThis.crypto) globalThis.crypto = webcrypto;
const env = { OROGRAPHIC_SESSION_SECRET: "test-secret", TRADIER_ACCESS_TOKEN: "test-token", TRADIER_ACCOUNT_ID: "test-account", TRADIER_TRADING_MODE: "live" };
async function context(query = "", role = "admin", overrides = {}) {
  const config = { ...env, ...overrides };
  const cookie = role ? await buildSessionCookie(config, { username: "test", role }) : "";
  return { request: new Request(`https://app.example/api/tradier/gainloss${query}`, { headers: { cookie } }), env: config };
}
const row = { symbol: "SPY260101C00600000", cost: 25.05, proceeds: 0, gain_loss: -25.05, gain_loss_percent: -100, quantity: 1, term: 3, open_date: "2026-01-01T00:00:00.000Z", close_date: "2026-01-04T00:00:00.000Z" };
const payload = { gainloss: { closed_position: [row] } };
test("page input is strictly bounded and cannot override account, endpoint, or sort", () => {
  assert.equal(parseGainlossPage("https://app.example/"), 1);
  assert.equal(parseGainlossPage("https://app.example/?page=10000"), 10000);
  for (const query of ["page=0", "page=-1", "page=01", "page=1.5", "page=1e2", "page=10001", "page=", "page=1&page=2", "account=another", "limit=500", "url=https://evil.example", "sort=asc"]) assert.equal(parseGainlossPage(`https://app.example/?${query}`), null);
});
test("projection preserves monetary totals, duplicates, percent and broker calendar date", () => {
  const data = normalizeGainloss({ gainloss: { closed_position: [row, row] } }, 1);
  assert.equal(data.rows.length, 2);
  assert.equal(data.rows[0].cost, 25.05);
  assert.equal(data.rows[0].gain_loss_percent, -100);
  assert.equal(data.rows[0].close_date_calendar, "2026-01-04");
  assert.deepEqual(data.rows[0].missing_fields, []);
  assert.equal(data.pagination.completeness, "unknown");
  assert.equal(data.pagination.has_more, null);
  assert.equal(data.pagination.next_page, 2);
});
test("nulls, blanks, booleans and nonfinite numeric fields never become zero", () => {
  for (const invalid of [null, "", " ", true, [], {}, Infinity, NaN, "Infinity", "0x20"]) {
    const result = normalizeGainloss({ gainloss: { closed_position: { ...row, cost: invalid } } }, 1);
    assert.equal(result.rows[0].cost, null);
    assert.ok(result.rows[0].missing_fields.includes("cost"));
  }
  const result = normalizeGainloss({ gainloss: { closed_position: { symbol: "XYZ", gain_loss: "0", close_date: "2026-02-30" } } }, 1);
  assert.equal(result.rows[0].gain_loss, 0);
  assert.equal(result.rows[0].close_date_calendar, null);
  assert.ok(result.rows[0].missing_fields.includes("proceeds"));
});
test("date projection rejects malformed timestamps without changing the broker calendar day", () => {
  for (const date of ["2026-01-04Tgarbage", "2026-01-04T99:00:00Z", "2026-02-30T00:00:00Z", "2026-13-01", "no date"]) {
    assert.equal(normalizeGainloss({ gainloss: { closed_position: { ...row, close_date: date } } }, 1).rows[0].close_date_calendar, null);
  }
  assert.equal(normalizeGainloss({ gainloss: { closed_position: { ...row, close_date: "2026-01-04T23:30:00-07:00" } } }, 1).rows[0].close_date_calendar, "2026-01-04");
});
test("valid empty and malformed are different, incomplete pagination never certifies history", () => {
  for (const empty of [{ gainloss: null }, { gainloss: { closed_position: null } }, { gainloss: { closed_position: [] } }]) {
    const result = normalizeGainloss(empty, 2);
    assert.deepEqual(result.rows, []);
    assert.equal(result.pagination.has_more, false);
    assert.equal(result.pagination.next_page, null);
    assert.equal(result.pagination.completeness, "unknown");
  }
  for (const malformed of [null, {}, [], { gainloss: {} }, { gainloss: "null" }, { gainloss: { closed_position: "" } }, { gainloss: { closed_position: [null] } }, { gainloss: { closed_position: [{}] } }, { gainloss: { closed_position: Array(101).fill(row) } }]) assert.throws(() => normalizeGainloss(malformed, 1));
  assert.equal(normalizeGainloss({ gainloss: { closed_position: [row], total: 1, has_more: false } }, 1).pagination.completeness, "unknown");
});
test("anonymous, viewer, invalid query and invalid settings never call broker", async (t) => {
  t.mock.method(globalThis, "fetch", () => { throw new Error("Unexpected broker access"); });
  for (const [ctx, status] of [[await context("", null), 401], [await context("", "viewer"), 403], [await context("?page=0"), 400], [await context("?account_id=other"), 400], [await context("", "admin", { TRADIER_BASE_URL: "https://evil.example/v1" }), 503]]) {
    const response = await onRequestGet(ctx);
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
  }
  assert.equal(fetch.mock.callCount(), 0);
});
test("one explicit GET uses strict helper and only server configured account", async (t) => {
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, "https://api.tradier.com/v1/accounts/test-account/gainloss?page=2&limit=100&sortBy=closeDate&sort=desc");
    assert.equal(init.method, "GET");
    assert.equal(init.redirect, "manual");
    assert.equal(init.body, undefined);
    return Response.json(payload, { headers: { "X-Ratelimit-Available": "17" } });
  });
  const response = await onRequestGet(await context("?page=2"));
  const data = await response.json();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.equal(data.rows[0].gain_loss, -25.05);
  assert.equal(data.rate_limits.available, 17);
  assert.equal(data.broker_as_of, null);
  assert.ok(Number.isFinite(Date.parse(data.fetched_at_utc)));
  assert.equal(fetch.mock.callCount(), 1);
  assert.ok(!JSON.stringify(data).includes("test-token"));
  assert.ok(!JSON.stringify(data).includes("test-account"));
});
test("errors, 429 and redirects are sanitized with no retries or follows", async (t) => {
  for (const status of [302, 401, 403, 429, 500]) await t.test(String(status), async (st) => {
    st.mock.method(globalThis, "fetch", async () => Response.json({ error: "test-token test-account" }, { status, headers: { Location: "https://evil.example", "X-Ratelimit-Expiry": "1791554400000" } }));
    const response = await onRequestGet(await context());
    const data = await response.json();
    assert.equal(response.status, status === 429 ? 429 : 502);
    assert.ok(!JSON.stringify(data).includes("test-token"));
    assert.ok(!JSON.stringify(data).includes("test-account"));
    if (status === 429) assert.equal(data.rate_limits.expiry, 1791554400000);
    assert.equal(fetch.mock.callCount(), 1);
  });
});
test("malformed successful broker payload is an error, sandbox caveat explicit", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({}));
  assert.equal((await onRequestGet(await context())).status, 502);
  t.mock.method(globalThis, "fetch", async () => Response.json(payload));
  const response = await onRequestGet(await context("", "admin", { TRADIER_TRADING_MODE: "sandbox" }));
  const data = await response.json();
  assert.equal(data.environment, "sandbox");
  assert.ok(data.warnings.some((warning) => warning.includes("unverified")));
});
