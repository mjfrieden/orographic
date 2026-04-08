# Orographic Walk-Forward Alpha Experiment

Date: 2026-04-08  
Window tested: 2025-10-09 to 2026-04-07  
Artifact: `docs/alpha_experiment_results.json`

## Goal

Test whether deployable alpha improves when Orographic:

1. Replays only the Council live board instead of all Forge candidates
2. Enforces a hard estimated cost-basis cap of `$500`
3. Applies rolling symbol priors built only from already-closed historical research trades

## Variants

### 1. `baseline_all_candidates`

- Replays every Forge candidate
- No Council selection
- No cost cap
- No symbol priors

### 2. `council_only`

- Replays Council live-board selections only
- Uses historical as-of correlation matrices during replay
- No cost cap
- No symbol priors

### 3. `council_cost_cap`

- Same as `council_only`
- Drops candidates whose estimated cost basis exceeds `$500`

### 4. `council_cost_cap_symbol_priors`

- Same as `council_cost_cap`
- Adds rolling symbol priors using the prior 12 weeks of already-closed research trades
- Boosts the top 5 recent symbols
- Excludes the bottom 5 recent symbols

## Results

| Variant | Trades | Win Rate | Total P&L | Net Return | Sharpe | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| `baseline_all_candidates` | 1164 | 41.1% | +$23,894.43 | 5.9% | 0.58 | -80.3% |
| `council_only` | 23 | 47.8% | +$1,257.68 | 11.2% | 1.19 | -86.8% |
| `council_cost_cap` | 25 | 52.0% | +$1,546.91 | 18.6% | 1.26 | -41.0% |
| `council_cost_cap_symbol_priors` | 27 | 55.6% | +$1,983.17 | 21.8% | 2.07 | -39.3% |

## Readout

### Best deployable variant

`council_cost_cap_symbol_priors` was the strongest variant.

- Best Sharpe: `2.07`
- Best net return: `21.8%`
- Best win rate: `55.6%`
- Best drawdown of the Council-based variants: `-39.3%`

### What actually helped

#### Cost-basis caps mattered immediately

The jump from `council_only` to `council_cost_cap` was the cleanest single improvement:

- Sharpe improved from `1.19` to `1.26`
- Net return improved from `11.2%` to `18.6%`
- Max drawdown improved from `-86.8%` to `-41.0%`

This strongly supports treating expensive weekly structures as a separate regime, not part of the default live board.

#### Rolling symbol priors added useful incremental alpha

Using priors from the broader research book produced a real lift:

- Trades increased from `25` to `27`
- Win rate improved from `52.0%` to `55.6%`
- Total P&L improved from `+$1,546.91` to `+$1,983.17`
- Sharpe improved materially from `1.26` to `2.07`

This suggests Orographic benefits from a short-memory symbol reputation layer.

## Example prior actions

The rolling-prior variant repeatedly excluded symbols like:

- `BRK-B`
- `MA`
- `PEP`
- `QCOM`
- `CRM`

It repeatedly boosted symbols like:

- `MCD`
- `NVDA`
- `NKE`
- `QQQ`
- `PG`

Not every weekly action helped, but the aggregate effect was positive and materially improved risk-adjusted performance.

## Recommended next production steps

1. Add a live Forge/Council gate for `estimated_cost_basis <= $500` by default.
2. Add a rolling symbol prior overlay to the ranking path:
   - boost recent winners modestly
   - suppress or exclude recent persistent losers
3. Add a dashboard panel for prior boosts/exclusions next to the Forge diagnostics.
4. Keep the baseline backtest and the deployable Council-only experiment separate in reporting.

## Implementation notes

The experiment was implemented with:

- historical as-of correlation matrices for Council replay
- walk-forward symbol priors with no future leakage
- a dedicated experiment runner at `engine/backtest/alpha_experiment.py`

The goal was not to replace the baseline backtest, but to create a more deployable research track for live portfolio decisions.
