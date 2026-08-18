# Unified R&D model stack — 2026-08-12

## Decision

Orographic now defaults to one production product lane: `unified_rnd`. Scout,
side-aware direction, hierarchical Scout, Sentinel, payoff rank, path quality,
and the cost-aware challenger all contribute to the same candidate surface and
Council board. Shadow-board and counterfactual-lane allocation default to zero.

The old promotion-gated behavior remains reproducible as `current_gated`, but
only as a backtest comparison baseline. Only `council.live_board` has
buy-to-open authority. This architectural decision does **not** by itself arm
unattended live brokerage deployment.

## How the unified stack works

1. Directional Scout finds short-horizon symbol edge.
2. The option-payoff side model may correct call versus put only when its
   opposite-side probability is at least 70% with a 20-point margin. This is
   important because the current training card contains very few put-edge
   examples; a naive weighted vote collapsed almost entirely to calls.
3. Hierarchical Scout contributes a bounded 20% side vote. Its no-trade
   probability becomes downstream risk evidence, not a separate product lane.
4. Sentinel adds point-in-time structured event evidence. Historical replay
   uses the event-feature snapshot available on each replay date.
5. Forge combines the active payoff score (60%), path quality (18%), the
   cost-aware challenger (14%), and conservative utility (8%).
6. Council applies portfolio construction and cost caps to that one ranking.

## Archived-chain comparison

The comparison used real archived option chains, 7–14 DTE contracts, a $300
base budget and $600 hard cap, 3% entry and exit slippage, an 18% maximum
spread, and a 90% minimum real-chain coverage requirement. All priced trades
had real entry and exit quotes.

| Window | Stack | Trades | Win rate | P&L | Net return | Sharpe | Max drawdown |
|---|---|---:|---:|---:|---:|---:|---:|
| 3 months | Current gated | 9 | 22.22% | -$214.32 | -14.47% | -2.73 | -64.23% |
| 3 months | Unified R&D | 11 | 45.45% | $89.07 | 4.64% | 1.04 | -47.56% |
| 6 months | Current gated | 12 | 25.00% | -$340.24 | -16.38% | -3.19 | -85.92% |
| 6 months | Unified R&D | 24 | 50.00% | -$26.52 | -0.60% | 0.88 | -68.89% |
| 12 months | Current gated | 22 | 36.36% | $52.02 | 1.27% | 0.05 | -90.90% |
| 12 months | Unified R&D | 38 | 47.37% | $90.01 | 1.27% | 0.65 | -95.03% |

The unified stack improves win rate and Sharpe in every window and P&L in all
three comparisons. It still loses slightly over six months, has only 11–38
trades per window, and its 12-month drawdown is worse and unacceptable.

## Validation limit

This is a fixed-artifact parity/diagnostic replay, not a clean promotion test.
Several current artifacts were trained or retrained after part of the
April 2025–April 2026 archive. Cirrus supplies a leakage-safe expanding-window
historical lab and Orographic now uses point-in-time event joins, but every
Orographic artifact was not retrained inside every fold. Promotion therefore
remains on hold until a true expanding-window retraining run plus prospective
evidence confirms the result.

## Reproduce

```bash
./.venv/bin/python -m engine.backtest.alpha_experiment \
  --months 12 --end-date 2026-04-13 \
  --universe engine/sample_universe.txt \
  --options-data-dir engine/data/options/blended \
  --expiry-policy target_dte --target-dte-min 7 --target-dte-max 14 \
  --base-budget-usd 300 --hard-cost-ceiling-usd 600 --cost-cap-usd 600 \
  --strict-options-data --min-real-coverage-pct 0.9 \
  --entry-slippage-pct 0.03 --exit-slippage-pct 0.03 \
  --max-entry-spread-pct 0.18 --max-exit-spread-pct 0.18 \
  --event-features-path .local/event_features/daily_event_features_2025_2026_sec_macro_selective.parquet \
  --unified-comparison-only \
  --output output/unified_stack_comparison_12mo.json \
  --option-outcome-dir output/unified_stack_comparison_outcomes_12mo
```
