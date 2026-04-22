# Orographic 6-Month Backtest Regression Fix - 2026-04-21

Window: 2025-10-17 to 2026-04-15. Strict real option data, blended options store, target DTE 7-14, $300 base budget, $600 hard cost cap.

Root cause: the side-aware Scout loader patch accidentally displaced the trained Scout `_load_model()` body, forcing heuristic fallback whenever model artifacts were present. The fix restores trained model loading and keeps new Council sector/risk annotations observational by default.

| Variant | Previous P&L | Recovered P&L | P&L Delta | Previous Sharpe | Recovered Sharpe | Sharpe Delta |
|---|---:|---:|---:|---:|---:|---:|
| baseline_all_candidates | $+5,329.00 | $+5,329.00 | $+0.00 | 0.74 | 0.74 | +0.00 |
| council_only | $+438.00 | $+179.00 | $-259.00 | 2.30 | 0.38 | -1.92 |
| council_cost_cap | $+684.00 | $+1,794.00 | $+1,110.00 | 2.11 | 2.84 | +0.73 |
| council_cost_cap_symbol_priors | $-11.00 | $+848.00 | $+859.00 | 0.93 | 2.52 | +1.59 |

## Recovered Default Results

| Variant | Trades | Win Rate | Total P&L | Net Return | Sharpe | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| baseline_all_candidates | 1037 | 38.9% | $+5,329.00 | 2.5% | 0.74 | -82.6% |
| council_only | 23 | 47.8% | $+179.00 | 3.8% | 0.38 | -97.0% |
| council_cost_cap | 34 | 61.8% | $+1,794.00 | 26.1% | 2.84 | -77.8% |
| council_cost_cap_symbol_priors | 32 | 62.5% | $+848.00 | 13.2% | 2.52 | -89.3% |

Conclusion: the trading edge was recovered without reverting all ML/AI observability work. The deployable `council_cost_cap` variant improved from the prior artifact: +$1,794 vs +$684, Sharpe 2.84 vs 2.11.
