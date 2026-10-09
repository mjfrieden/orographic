import assert from "node:assert/strict";
import test from "node:test";
import { fetchBrokerStatus, normalizeBalances, summarizeAccount } from "../../functions/_lib/tradier.js";

const example = (account_type, amounts) => ({ balances: {
  account_type, total_equity: 17798.36, total_cash: 6363.86,
  close_pl: -4813, open_pl: 546.9, [account_type]: amounts,
} });

for (const type of ["margin", "pdt"]) {
  test(`${type} uses the documented type-specific option and stock amounts`, () => {
    const payload = example(type, { option_buying_power: "6363.86", stock_buying_power: 12727.72 });
    const result = normalizeBalances(payload);
    assert.equal(result.option_buying_power, 6363.86);
    assert.equal(result.stock_buying_power, 12727.72);
    assert.equal(result.buying_power, 6363.86);
    assert.equal(result.buying_power_label, "Options Buying Power");
    assert.equal(result.buying_power_source, `${type}.option_buying_power`);
    assert.equal(result.total_equity, 17798.36);
    assert.equal(result.open_pl, 546.9);
    assert.equal(summarizeAccount(payload, { accountIdMasked: "mock" }).option_buying_power, 6363.86);
  });
}

test("cash shows broker-reported cash available, never total cash or unsettled funds as buying power", () => {
  const result = normalizeBalances(example("cash", { cash_available: 4343.38, unsettled_funds: 1310 }));
  assert.equal(result.buying_power, 4343.38);
  assert.equal(result.cash_available, 4343.38);
  assert.equal(result.buying_power_label, "Cash Available");
  assert.equal(result.option_buying_power, null);
  assert.equal(result.stock_buying_power, null);
  assert.equal(result.total_cash, 6363.86);
  assert.equal(normalizeBalances(example("cash", { unsettled_funds: 1310 })).buying_power, null);
});

test("reference sibling layout and legacy flat balances remain supported", () => {
  assert.equal(normalizeBalances({ balances: { account_type: "margin" }, margin: { option_buying_power: 123 } }).buying_power, 123);
  assert.equal(normalizeBalances({ balances: { option_buying_power: 123, stock_buying_power: 246 } }).buying_power, 123);
});

test("type-specific options take precedence; stock buying power cannot stand in for options", () => {
  assert.equal(normalizeBalances({ balances: { account_type: "margin", option_buying_power: 99, margin: { option_buying_power: 0, stock_buying_power: 500 } } }).buying_power, 0);
  assert.equal(normalizeBalances(example("margin", { stock_buying_power: 500 })).buying_power, null);
  assert.equal(normalizeBalances({ balances: { account_type: "margin", option_buying_power: 99, margin: { option_buying_power: null } } }).buying_power, null);
  assert.equal(normalizeBalances({ balances: { account_type: "cash", margin: { option_buying_power: 500 } } }).buying_power, null);
});

for (const value of [null, undefined, "", "   ", "invalid", NaN, Infinity, true, false, [], {}]) {
  test(`missing/invalid balance ${String(value)} remains unavailable, never zero`, () => {
    const result = normalizeBalances({ balances: {
      account_type: "margin", total_equity: value, total_cash: value,
      close_pl: value, open_pl: value, margin: { option_buying_power: value },
    } });
    for (const field of ["total_equity", "total_cash", "close_pl", "open_pl", "buying_power", "option_buying_power", "buying_power_source"]) {
      assert.equal(result[field], null, field);
    }
  });
}

test("genuine zero and negative values are preserved", () => {
  assert.equal(normalizeBalances(example("margin", { option_buying_power: 0 })).buying_power, 0);
  assert.equal(normalizeBalances(example("pdt", { option_buying_power: -12.34 })).buying_power, -12.34);
  assert.equal(normalizeBalances(example("cash", { cash_available: "0" })).buying_power, 0);
});

for (const sandbox of [true, false]) {
  test(`${sandbox ? "sandbox" : "live"} mock account reads deliver normalized amounts without any order request`, async (t) => {
    const calls = [];
    t.mock.method(globalThis, "fetch", async (url, init) => {
      const parsed = new URL(url);
      assert.equal(parsed.origin, sandbox ? "https://sandbox.tradier.com" : "https://api.tradier.com");
      assert.equal(init.method, "GET");
      assert.equal(init.redirect, "manual");
      calls.push(parsed.pathname);
      return Response.json(parsed.pathname.endsWith("/balances")
        ? example("margin", { option_buying_power: 6363.86, stock_buying_power: 12727.72 }) : {});
    });
    const result = await fetchBrokerStatus({ TRADIER_ACCESS_TOKEN: "mock-token", TRADIER_ACCOUNT_ID: "mock-account", TRADIER_SANDBOX_MODE: String(sandbox) });
    assert.equal(result.balances.buying_power, 6363.86);
    assert.equal(result.mode, sandbox ? "sandbox" : "live");
    assert.equal(calls.length, 4);
  });
}
