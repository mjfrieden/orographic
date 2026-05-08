# Model Deployment Decision

Date: `2026-05-06`

## Decision

- Production default remains `council_cost_cap`.
- `council_cost_cap_path_tiebreaker` stays research-only.
- `council_cost_cap_path_tiebreaker_loose` is rejected.

## Why

The updated path-aware stack became a real comparison candidate only after replay was fixed to score the trained path model during walk-forward testing. Once that bug was fixed, the conservative path tie-breaker showed real incremental behavior, but it did not beat the current production default consistently enough across windows to justify promotion.

## Evidence

### 3-month walk-forward

Source: [alpha_experiment_results_2026-05-06_path_tiebreaker_fixed_3mo.json](/Users/mjfrieden/Desktop/2026/Orographic/output/alpha_experiment_results_2026-05-06_path_tiebreaker_fixed_3mo.json)

| Variant | P&L | Win rate | Sharpe | Max drawdown |
|---|---:|---:|---:|---:|
| `council_cost_cap` | `+$477` | `83.3%` | `3.24` | `-28.9%` |
| `council_cost_cap_path_tiebreaker` | `+$501` | `83.3%` | `3.30` | `-28.9%` |
| `council_cost_cap_path_tiebreaker_loose` | `+$120` | `66.7%` | `-0.49` | `-96.6%` |

### 6-month walk-forward

Source: [alpha_experiment_results_2026-05-06_path_tiebreaker_fixed_6mo.json](/Users/mjfrieden/Desktop/2026/Orographic/output/alpha_experiment_results_2026-05-06_path_tiebreaker_fixed_6mo.json)

| Variant | P&L | Win rate | Sharpe | Max drawdown |
|---|---:|---:|---:|---:|
| `council_cost_cap` | `+$1,265` | `60.0%` | `2.66` | `-92.2%` |
| `council_cost_cap_path_tiebreaker` | `+$1,884` | `60.0%` | `3.36` | `-79.8%` |
| `council_cost_cap_path_tiebreaker_loose` | `+$1,379` | `64.0%` | `3.19` | `-96.6%` |

### 12-month walk-forward

Source: [alpha_experiment_results_2026-05-06_path_tiebreaker_fixed_12mo.json](/Users/mjfrieden/Desktop/2026/Orographic/output/alpha_experiment_results_2026-05-06_path_tiebreaker_fixed_12mo.json)

| Variant | P&L | Win rate | Sharpe | Max drawdown |
|---|---:|---:|---:|---:|
| `council_cost_cap` | `+$3,686` | `65.9%` | `3.39` | `-92.2%` |
| `council_cost_cap_path_tiebreaker` | `+$3,395` | `56.8%` | `2.90` | `-80.3%` |
| `council_cost_cap_path_tiebreaker_loose` | `+$1,987` | `54.5%` | `1.98` | `-99.8%` |

## Interpretation

The conservative path-aware tie-breaker improved recent windows and improved drawdown even over 12 months, but it still lagged the current production default on the longest and most important comparison window for both total P&L and Sharpe. That is not a clean promotion outcome.

The loose tie-breaker was too aggressive. It increased turnover, degraded return quality, and is not eligible for further deployment consideration in its current form.

## Implementation note

This decision assumes the replay fix in [engine/backtest/replay.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/replay.py) that now scores the trained path model during walk-forward testing. Earlier tie-breaker comparisons that showed no effect were measuring an inert path layer.

## Operational status

- Keep `council_cost_cap` as the production validation and deployment default.
- Keep `OROGRAPHIC_PATH_MODEL_MODE` in shadow-only practice.
- Continue evaluating the conservative path-aware tie-breaker as a research overlay, especially by regime and side on swap weeks.
