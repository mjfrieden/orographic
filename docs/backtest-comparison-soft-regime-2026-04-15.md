# Soft Regime Gate Backtest Comparison

Date: 2026-04-15

## Setup

Both runs used the same command shape:

```bash
python -m engine.backtest.runner --months 3 --end-date 2026-04-15
```

Window:

- Start: `2026-01-15`
- End: `2026-04-15`
- Universe: default 100-symbol Orographic universe
- Options coverage: modeled/synthetic chains only in both runs

Artifacts:

- Baseline hard-veto run: `output/backtest_results_2026-04-15_baseline_hard_veto_3mo.json`
- Soft-regime run: `output/backtest_results_2026-04-15_soft_regime_3mo.json`

## Headline Comparison

| Metric | Baseline hard veto | Soft regime gate | Change |
| --- | ---: | ---: | ---: |
| Total trades | 746 | 821 | +75 |
| Win rate | 47.86% | 51.16% | +3.30 pp |
| Total P&L | $55,923.80 | $79,854.21 | +$23,930.41 |
| Net return | 40.52% | 51.39% | +10.87 pp |
| Sharpe | 4.0065 | 4.8210 | +0.8145 |
| Max drawdown | -81.32% | -81.32% | unchanged |

## Side Breakdown

### Baseline hard veto

| Side | Trades | Win rate | P&L | Avg P&L % | Expired worthless |
| --- | ---: | ---: | ---: | ---: | ---: |
| Calls | 311 | 50.80% | $28,661.05 | 37.99% | 99 |
| Puts | 435 | 45.75% | $27,262.75 | 47.20% | 197 |

### Soft regime gate

| Side | Trades | Win rate | P&L | Avg P&L % | Expired worthless |
| --- | ---: | ---: | ---: | ---: | ---: |
| Calls | 385 | 57.14% | $51,955.77 | 54.49% | 99 |
| Puts | 436 | 45.87% | $27,898.44 | 49.12% | 197 |

## What Changed

The soft-regime gate added `75` trades and removed `0` trades relative to the baseline trade-key set.

Most of the incremental improvement came from allowing strong counter-regime calls during `risk_off` weeks:

- Week of `2026-03-23`: baseline had `35` puts; soft gate had `3` calls and `35` puts.
- Week of `2026-03-30`: baseline had `10` puts; soft gate had `71` calls and `10` puts.
- Week of `2026-04-06`: baseline had `112` calls; soft gate had `112` calls and `1` put.

Weekly P&L changed materially in the soft-gate run:

| Week | Baseline P&L | Soft-gate P&L | Change |
| --- | ---: | ---: | ---: |
| 2026-03-23 | $2,743.64 | $3,538.18 | +$794.54 |
| 2026-03-30 | -$186.73 | $22,313.45 | +$22,500.18 |
| 2026-04-06 | $13,656.12 | $14,291.81 | +$635.69 |

## Interpretation

The soft-regime change did what it was designed to do: it did not make the system randomly two-sided every week, but it allowed high-conviction counter-regime trades to survive when the old hard-veto policy would have deleted them.

The most important result is not just the higher P&L. It is the behavioral improvement:

- Baseline stayed strictly one-sided in several regime weeks.
- Soft gate preserved the dominant regime direction but allowed exceptions.
- The added exceptions improved win rate, Sharpe, and total P&L in this window.

## Caveat

Both runs had `0.0%` real historical options-chain coverage at entry and exit, so this is a signal-behavior comparison, not an execution-quality claim.

Before treating the P&L delta as capital-allocation evidence, rerun this comparison on an OptionsDX-covered subset or with strict real-chain coverage.
