# Payoff Model v6 Retraining Audit — 2026-08-08

## Decision

**HOLD. Do not replace the production payoff model with this candidate.**

The isolated v6 candidate used 2,607 deduplicated examples and outcome-aware purged walk-forward validation. Sample depth is adequate, but the model does not demonstrate out-of-sample option-profit discrimination or probability skill. The new fail-closed gates correctly rejected promotion.

## Candidate results

| Metric | Result | Gate |
|---|---:|---|
| Selected family | Linear | Side-aware family bake-off |
| Positive-P&L AUC | 0.5094 | Fail; minimum 0.53 |
| Positive-P&L Brier | 0.2664 vs 0.2486 baseline | Fail |
| Breakeven AUC | 0.5533 | Pass |
| Breakeven Brier | 0.2650 vs 0.2236 baseline | Fail |
| Calls | AUC 0.4813; Brier 0.2684 vs 0.2489 | Fail |
| Puts | AUC 0.4713; Brier 0.2491 vs 0.2169 | Fail |
| Qualified regimes | 1 of required 2 | Fail |

Neutral was the only qualifying regime (AUC 0.6662; Brier 0.2333 vs 0.2493). Risk-off, risk-on, and unclassified observations failed.

## Data-quality findings

- 2,095 call examples and 512 put examples satisfy the numerical side-depth requirement.
- 1,883 examples have at least one observed quote-path mark: **72.23% trade-level path coverage**.
- The previous coverage calculation incorrectly divided quote marks by trades and could exceed 100%. v6 now counts trades with observed paths.
- Friction drag and winner-to-loser friction flips are both zero throughout the merged corpus. The source rows generally set before- and after-friction returns to identical values and omit explicit entry/exit bid/ask fields. This fails the friction-observability gate.
- 1,841 of 2,607 observations are `unclassified` by regime, limiting interpretable regime learning.

## Required next dataset build

1. Rebuild canonical outcomes from executable entry ask and exit bid snapshots, preserving both pre-friction and post-friction returns.
2. Persist entry/exit bid, ask, timestamps, quote source, and adverse slippage in every label row.
3. Backfill regime labels at signal time; do not infer them from future outcomes.
4. Retain the observed quote path and its source for MFE, decay, and exit-head labels.
5. Retrain v6 without changing thresholds based on this failed candidate, then rerun the same locked gates.

No candidate model artifact was promoted or copied into the production model directory.
