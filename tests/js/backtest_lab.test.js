import test from "node:test";
import assert from "node:assert/strict";
import { buildBacktestCommand, filterTrades, summarizeTrades } from "../../web/backtest/backtest.js";

test("buildBacktestCommand preserves reproducible research controls", () => {
  const command = buildBacktestCommand({ months: 6, budget: 300, ceiling: 600, expiry: "target_dte", entrySlip: 3, exitSlip: 2, strict: true, coverage: 90, symbols: "AAPL, MSFT", runId: "trial-1" });
  assert.match(command, /--months 6/);
  assert.match(command, /--strict-options-data/);
  assert.match(command, /--min-real-coverage-pct 0\.90/);
  assert.match(command, /--entry-slippage-pct 0\.030/);
  assert.match(command, /--symbols AAPL,MSFT/);
  assert.match(command, /backtest_results_trial-1\.json/);
});

test("filterTrades combines symbol, side, and outcome filters", () => {
  const trades = [
    { symbol: "AAPL", option_type: "call", pnl: 10 },
    { symbol: "AAPL", option_type: "put", pnl: -5 },
    { symbol: "MSFT", option_type: "call", pnl: 7 },
  ];
  assert.deepEqual(filterTrades(trades, { query: "aap", side: "call", outcome: "winner" }), [trades[0]]);
});

test("summarizeTrades recalculates the filtered observation set", () => {
  const summary = summarizeTrades([{ pnl: 50, cost_basis: 100 }, { pnl: -10, cost_basis: 100 }]);
  assert.equal(summary.trades, 2);
  assert.equal(summary.pnl, 40);
  assert.equal(summary.winRate, 0.5);
  assert.equal(summary.returnPct, 0.2);
});
