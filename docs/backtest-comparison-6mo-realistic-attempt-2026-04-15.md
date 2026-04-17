# Six-Month Backtest Comparison With Realistic-Coverage Attempt

Date: 2026-04-15

## Scope

Requested comparison:

- Updated soft-regime code vs old hard-veto code
- Six-month window
- Prefer realistic option-chain backtesting over synthetic chains

Window used:

- Start: `2025-10-17`
- End: `2026-04-15`

## Realistic Strict-Chain Attempt

The local OptionsDX store is too sparse for a meaningful strict real-chain six-month backtest.

Available local real-chain coverage:

- `SPY` on `2020-03-06`
- `SPY` on `2026-01-05`
- `SPY` on `2026-01-09`

The realistic test was therefore constrained to:

```bash
python -m engine.backtest.runner \
  --months 6 \
  --end-date 2026-04-15 \
  --symbols SPY \
  --strict-options-data \
  --min-real-coverage-pct 1.0
```

Artifacts:

- Updated soft-regime strict-real: `output/backtest_results_2026-04-15_soft_regime_6mo_spy_real.json`
- Old hard-veto strict-real: `output/backtest_results_2026-04-15_baseline_hard_veto_6mo_spy_real.json`

Result:

| Run | Trades | P&L | Real entry coverage | Real exit coverage | Coverage policy |
| --- | ---: | ---: | ---: | ---: | --- |
| Old hard veto | 0 | $0.00 | 0.0% | 0.0% | failed |
| Updated soft regime | 0 | $0.00 | 0.0% | 0.0% | failed |

Why this produced no trades:

- Only one relevant 2026 entry/exit pair exists locally: `SPY` `2026-01-05` entry and `2026-01-09` exit.
- On `2026-01-05`, replay SPY spot was about `687.72`.
- The real sample chain only contains strikes `540`, `550`, and `555`.
- Those strikes are far from the live moneyness band and do not produce Forge candidates.

Conclusion: the strict-real result is not a useful alpha comparison. It is a useful data-quality finding: local OptionsDX coverage is not sufficient yet for realistic six-month validation.

## Modeled-Chain Six-Month Fallback

Because strict-real coverage produced zero trades, I also compared old vs updated code on the six-month modeled-chain walk-forward experiment. This is not realistic execution evidence, but it is useful for signal-behavior comparison.

Artifacts:

- Updated soft-regime walk-forward: `docs/alpha_experiment_results_2026-04-15_soft_regime.json`
- Old hard-veto walk-forward: `output/alpha_experiment_results_2026-04-15_baseline_hard_veto_6mo.json`

## Headline Variant Comparison

| Variant | Old trades | New trades | Old P&L | New P&L | P&L change | Old Sharpe | New Sharpe |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline all candidates | 1,633 | 1,717 | $100,713.73 | $124,756.69 | +$24,042.96 | 3.6629 | 4.0908 |
| Council only | 16 | 19 | $1,428.85 | $2,018.07 | +$589.22 | 1.9574 | 2.3347 |
| Council + cost cap | 29 | 31 | $2,243.29 | $2,904.83 | +$661.54 | 3.6696 | 4.1184 |
| Council + cost cap + priors | 35 | 38 | $1,623.65 | $2,472.83 | +$849.18 | 2.5721 | 2.9348 |

## Production-Style Variant

The closest deployed/research variant is `council_cost_cap_symbol_priors`.

| Metric | Old hard veto | Updated soft regime | Change |
| --- | ---: | ---: | ---: |
| Trades | 35 | 38 | +3 |
| Win rate | 60.00% | 63.16% | +3.16 pp |
| Total P&L | $1,623.65 | $2,472.83 | +$849.18 |
| Net return | 18.06% | 25.00% | +6.94 pp |
| Sharpe | 2.5721 | 2.9348 | +0.3627 |
| Max drawdown | -100.00% | -100.00% | unchanged |

Side breakdown for `council_cost_cap_symbol_priors`:

| Side | Old trades | New trades | Old win rate | New win rate | Old P&L | New P&L |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Calls | 25 | 27 | 68.00% | 70.37% | $1,573.42 | $2,234.96 |
| Puts | 10 | 11 | 40.00% | 45.45% | $50.23 | $237.87 |

## Behavioral Finding

The soft-regime gate did what it was designed to do:

- It preserved the dominant regime direction.
- It allowed strong counter-regime candidates to survive instead of being deleted.
- The largest behavioral difference appeared in hard-veto weeks.

Examples:

| Week | Regime | Old signals/candidates | New signals/candidates |
| --- | --- | ---: | ---: |
| `2025-10-27` | risk_on | 2 / 4 | 25 / 34 |
| `2026-03-30` | risk_off | 11 / 14 | 78 / 124 |
| `2026-04-13` | risk_on | 43 / 68 | 65 / 100 |

The most dramatic case was `2026-03-30`, where the updated model allowed counter-regime calls to survive in a risk-off regime:

- Old: `11` signals, `14` raw candidates
- Updated: `78` signals, `124` raw candidates
- Production-style week P&L improved from `-$72.32` to `+$293.17`

## Interpretation

On a modeled-chain basis, the update improved every six-month walk-forward variant.

The strongest conclusion we can make:

- The soft-regime gate improves signal/candidate availability during regime transitions.
- It improved P&L, win rate, net return, and Sharpe in this six-month replay.
- It also increased put participation slightly in the production-style variant.

The strongest conclusion we cannot make yet:

- We cannot claim realistic execution improvement until the OptionsDX store contains enough real chains across the six-month window.

## Next Data Requirement

To make this truly realistic, we need materially broader real-chain coverage:

- At least every Monday entry date and Friday exit date in the test window.
- At least the symbols in the selected board, not just `SPY`.
- Enough strikes around current spot to pass moneyness, delta, liquidity, and spread filters.

With the current local OptionsDX sample, strict-real backtesting is data-starved rather than model-informative.
