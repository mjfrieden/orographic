# Free-Data Backtest Improvements - April 17, 2026

## Summary

I added the first realism layer on top of the DoltHub + OnclickMedia strict-real backtest. The goal was to keep the provider-agnostic free-data stack, but stop treating every quoted option as equally executable.

The new controls are intentionally configurable and default-safe:

- Entry and exit slippage stress.
- Entry and exit bid/ask spread gates.
- Entry and exit open-interest and volume gates.
- Per-week symbol and sector concentration caps.
- Offline deterministic replay IV-rank and risk-free-rate assumptions, so historical runs no longer depend on live Yahoo calls.

## Code Changes

- `engine/backtest/pricer.py`
  - Added execution slippage to entry and exit fills.
  - Added entry/exit liquidity gates.
  - Added per-trade execution-quality fields, including raw prices, spread pct, OI, volume, and slippage.

- `engine/backtest/replay.py`
  - Made entry liquidity thresholds configurable.
  - Removed live IV-rank/risk-free-rate calls from historical replay.
  - Added deterministic offline IV-rank proxy for reproducible free-data backtests.

- `engine/backtest/risk_controls.py`
  - Added provider-agnostic concentration caps by symbol and sector.

- `engine/backtest/runner.py`
  - Added CLI flags for slippage, spread gates, liquidity gates, and concentration caps.

- `engine/backtest/alpha_experiment.py`
  - Added the same controls to walk-forward alpha experiments.

- `engine/backtest/results.py`
  - Added `execution_quality` metrics to result JSON.

## Stress Assumptions

The 12-month strict-real stress rerun used:

```bash
--strict-options-data
--min-real-coverage-pct 1.0
--options-data-dir engine/data/options/blended
--expiry-policy target_dte
--target-dte-min 7
--target-dte-max 14
--entry-slippage-pct 0.03
--exit-slippage-pct 0.03
--max-entry-spread-pct 0.20
--max-exit-spread-pct 0.25
--min-entry-open-interest 300
--min-entry-volume 50
--min-exit-open-interest 100
--min-exit-volume 10
```

The runner used:

```bash
--max-symbol-candidates-per-week 3
--max-sector-candidates-per-week 12
```

The alpha experiment used:

```bash
--max-symbol-candidates-per-week 2
--max-sector-candidates-per-week 4
```

## 12-Month Runner Comparison

| Test | Trades | Win Rate | P&L | Net Return | Sharpe | Max Drawdown | Real Entry | Real Exit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Blended strict-real baseline | 1,841 | 47.4% | $70,750.00 | 18.3% | 3.38 | -82.6% | 100.0% | 100.0% |
| Blended execution stress | 855 | 48.0% | $34,720.77 | 18.3% | 2.59 | -80.6% | 100.0% | 100.0% |

Execution-quality metrics from the stress run:

| Metric | Value |
|---|---:|
| Average entry spread | 6.9% |
| Average exit spread | 9.7% |
| Entry slippage stress | 3.0% |
| Exit slippage stress | 3.0% |
| Average exit open interest | 3,600 |
| Average exit volume | 2,056 |

Output:

- `output/backtest_results_2026-04-17_blended_target_dte_7_14_strict_real_execution_stress_12mo.json`

## 12-Month Alpha Experiment Comparison

Production-style variant: `council_cost_cap_symbol_priors`.

| Test | Trades | Win Rate | P&L | Net Return | Sharpe | Max Drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Blended strict-real baseline | 45 | 62.2% | $3,807.00 | 40.9% | 3.21 | -98.0% |
| Blended execution stress | 33 | 45.5% | $1,502.73 | 22.0% | 2.21 | -98.2% |

All-candidate research variant:

| Test | Trades | Win Rate | P&L | Net Return | Sharpe | Max Drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Blended strict-real baseline | 1,841 | 47.4% | $70,750.00 | 18.3% | 3.38 | -82.6% |
| Blended execution stress | 349 | 47.0% | $13,757.91 | 16.9% | 2.49 | -89.9% |

Output:

- `output/alpha_experiment_results_2026-04-17_blended_target_dte_7_14_strict_real_execution_stress_12mo.json`

## Interpretation

This is a good result, but the right read is not "ship it." The edge survives a first-pass execution realism stress, which is encouraging. The smaller trade count and lower P&L are healthier and more believable than the raw blended baseline.

The remaining red flag is drawdown. Even after tighter execution and concentration controls, the production-style alpha variant still has a very deep path drawdown. That means the next work should focus less on finding more trades and more on avoiding bad regimes and crowded exposures.

## Next Free-Data Steps

1. Add a daily free-data snapshot job that stores current OnclickMedia chains after market close into the same partitioned parquet store. This creates our own forward archive instead of relying only on historical free endpoints.

2. Add OCC volume/open-interest ingestion as a second liquidity-validation layer. OCC will not provide exact prices, but it can help flag contracts that should never be treated as executable.

3. Add an event overlay from SEC EDGAR filings and FinBERT sentiment. This should be a signal/regime filter only, not a pricing source.

4. Add regime-specific stress reporting. The current drawdown suggests we need to know whether losses cluster in risk-on reversals, risk-off rebounds, high-IV conditions, or specific sectors.

5. Add walk-forward parameter sweeps for the new execution controls. The current stress settings are reasonable first-pass assumptions, but we should measure sensitivity across slippage, spread, OI, volume, and concentration caps.
