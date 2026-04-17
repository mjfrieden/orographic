# Single-Leg Calls/Puts Backtest vs Prior Spread-Enabled Workflow

Generated: 2026-04-16  
Updated commit tested: `052c2e8` (`Enforce single-leg option execution`)  
Prior comparison commit: `2391dc7` (`chore: refresh Orographic snapshot`)  
Backtest end date: 2026-04-15, the last completed trading session before the 2026-04-16 deployment

## Executive Summary

The single-leg calls/puts-only workflow backtested better than the prior spread-enabled workflow over the 6- and 12-month windows, especially in the production-style `council_cost_cap_symbol_priors` variant. The 3-month window favored the prior spread-enabled workflow, but that result is based on only about 20 production-style trades and is less stable.

The important realism caveat is unchanged: the modeled walk-forward results use synthetic/model-derived option chains for almost all historical fills. A strict real-options-data audit produced zero tradable SPY trades because the local OptionsDX store only has sparse SPY chain coverage (`2020-03-06`, `2026-01-05`, `2026-01-09`) and no broad 2025-2026 historical option-chain coverage across the universe.

## Test Matrix

Modeled walk-forward experiment:

- Current single-leg code: `python -m engine.backtest.alpha_experiment --months {3,6,12} --end-date 2026-04-15`
- Prior spread-enabled code: same command from detached worktree at `2391dc7`
- Universe: default 100-name Orographic universe
- Variants reviewed: all four alpha experiment variants, with emphasis on `council_cost_cap_symbol_priors`
- Sizing: default `$300` base budget, `$600` hard cost ceiling

Strict real-data audit:

- Current and prior code: `python -m engine.backtest.runner --symbols SPY --strict-options-data --min-real-coverage-pct 1.0`
- Windows: 6 months and 12 months
- Result: zero trades in both code versions and both windows

## Production-Style Variant

Variant: `council_cost_cap_symbol_priors`

| Window | Version | Trades | Win Rate | Total P&L | Net Return | Sharpe | Max DD |
|---:|---|---:|---:|---:|---:|---:|---:|
| 3 mo | Single-leg | 21 | 57.1% | $1,677.67 | 34.5% | 2.75 | -64.2% |
| 3 mo | Spread-enabled | 20 | 75.0% | $2,267.11 | 45.1% | 4.04 | -63.3% |
| 6 mo | Single-leg | 33 | 60.6% | $5,962.44 | 68.7% | 3.67 | -100.0% |
| 6 mo | Spread-enabled | 38 | 63.2% | $2,472.83 | 25.0% | 2.93 | -100.0% |
| 12 mo | Single-leg | 58 | 62.1% | $15,971.06 | 96.8% | 4.79 | -100.0% |
| 12 mo | Spread-enabled | 63 | 63.5% | $7,243.91 | 39.9% | 2.96 | -100.0% |

Delta, single-leg minus spread-enabled:

| Window | Trade Delta | P&L Delta | Net Return Delta | Sharpe Delta |
|---:|---:|---:|---:|---:|
| 3 mo | +1 | -$589.44 | -10.7 pts | -1.29 |
| 6 mo | -5 | +$3,489.61 | +43.7 pts | +0.74 |
| 12 mo | -5 | +$8,727.15 | +56.9 pts | +1.83 |

Interpretation:

- The single-leg update reduced trade count slightly in the production-style variant over 6 and 12 months, but improved total P&L, net return, and Sharpe materially.
- The 3-month spread-enabled edge appears short-window and low-sample-size sensitive.
- The single-leg path keeps execution intent clean: every candidate is a direct long call or direct long put, with no hidden short leg.

## Side Mix

Production-style `council_cost_cap_symbol_priors` side breakdown:

| Window | Version | Calls | Call P&L | Puts | Put P&L |
|---:|---|---:|---:|---:|---:|
| 3 mo | Single-leg | 12 | $1,483.67 | 9 | $194.00 |
| 3 mo | Spread-enabled | 13 | $2,252.74 | 7 | $14.37 |
| 6 mo | Single-leg | 22 | $3,709.38 | 11 | $2,253.06 |
| 6 mo | Spread-enabled | 27 | $2,234.96 | 11 | $237.87 |
| 12 mo | Single-leg | 47 | $13,796.15 | 11 | $2,174.91 |
| 12 mo | Spread-enabled | 52 | $6,813.99 | 11 | $429.92 |

The updated workflow still recommends puts. In the production-style variant, put trade count was identical to the spread-enabled baseline over the 6- and 12-month windows, but put P&L improved substantially after removing spreads.

## All-Candidate Variant

Variant: `baseline_all_candidates`

| Window | Version | Trades | Win Rate | Total P&L | Net Return | Sharpe | Max DD |
|---:|---|---:|---:|---:|---:|---:|---:|
| 3 mo | Single-leg | 560 | 49.5% | $117,662.15 | 101.3% | 5.71 | -90.7% |
| 3 mo | Spread-enabled | 821 | 51.2% | $79,854.21 | 51.4% | 4.82 | -81.3% |
| 6 mo | Single-leg | 1,179 | 44.6% | $175,024.83 | 67.8% | 4.93 | -90.7% |
| 6 mo | Spread-enabled | 1,717 | 47.7% | $124,756.69 | 36.2% | 4.09 | -81.3% |
| 12 mo | Single-leg | 2,291 | 48.7% | $495,655.40 | 92.4% | 6.07 | -90.7% |
| 12 mo | Spread-enabled | 3,264 | 51.3% | $377,716.82 | 53.8% | 5.62 | -81.3% |

Interpretation:

- Removing spreads reduced broad candidate count substantially.
- Despite fewer all-candidate trades, single-leg candidates generated higher total P&L and higher net return in every modeled window.
- Spread-enabled candidates had slightly higher win rates, but lower payoff quality.

## Strict Real-Options-Data Audit

| Window | Version | Symbol Scope | Strict Real Trades | Coverage Failed |
|---:|---|---|---:|---|
| 6 mo | Single-leg | SPY | 0 | Yes |
| 6 mo | Spread-enabled | SPY | 0 | Yes |
| 12 mo | Single-leg | SPY | 0 | Yes |
| 12 mo | Spread-enabled | SPY | 0 | Yes |

The strict audit did not validate profitability because there is not enough local real options coverage to form a realistic trade set. This is a data limitation, not a strategy pass/fail. The current local OptionsDX coverage contains real data for only one symbol (`SPY`) and only three quote dates.

## Files Produced

- `output/alpha_experiment_results_2026-04-16_single_leg_3mo.json`
- `output/alpha_experiment_results_2026-04-16_single_leg_6mo.json`
- `output/alpha_experiment_results_2026-04-16_single_leg_12mo.json`
- `output/alpha_experiment_results_2026-04-16_spread_enabled_3mo.json`
- `output/alpha_experiment_results_2026-04-16_spread_enabled_6mo.json`
- `output/alpha_experiment_results_2026-04-16_spread_enabled_12mo.json`
- `output/backtest_results_2026-04-16_single_leg_6mo_spy_strict_real.json`
- `output/backtest_results_2026-04-16_single_leg_12mo_spy_strict_real.json`
- `output/backtest_results_2026-04-16_spread_enabled_6mo_spy_strict_real.json`
- `output/backtest_results_2026-04-16_spread_enabled_12mo_spy_strict_real.json`

## Recommendation

Keep the single-leg calls/puts-only workflow live. It is simpler to execute, better aligned with the stated trading intent, and outperformed the spread-enabled workflow over the more meaningful 6- and 12-month modeled windows.

Before increasing reliance on these results, the next highest-value improvement is historical options data coverage. The modeled results are directionally useful for comparing code paths, but they are not a substitute for a real option-chain backtest with bid/ask, expiries, liquidity, and exit marks available across the full universe.
