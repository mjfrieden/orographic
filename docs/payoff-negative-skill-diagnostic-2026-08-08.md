# Payoff Negative-Skill Diagnostic — 2026-08-08

## Executive finding

The model failure is not caused by label leakage. All 740 strict executable rows pass timestamp ordering and quote-completeness checks. The dominant problems are distribution shift, loss of put coverage in July, harmful directional features, and uncalibrated probabilities.

## Temporal integrity

- Required timestamp violations: **0**
- Entry quotes observed after decisions: **0**
- Regimes observed after decisions: **0**
- Labels available before exit evidence: **0**
- Missing executable quote sides: **0**

The training code previously reconstructed historical DTE using the current date and forced every row into a neutral regime. Both defects are fixed: DTE now uses each entry date and regime features use the stored signal-time regime.

## Distribution shift

| Month | Rows | Calls | Puts | Positive rate | Mean executable return | Median return |
|---|---:|---:|---:|---:|---:|---:|
| 2026-05 | 12 | 12 | 0 | 25.00% | -30.59% | -35.79% |
| 2026-06 | 216 | 157 | 59 | 42.59% | +14.23% | -14.73% |
| 2026-07 | 181 | 177 | 4 | 28.73% | -22.52% | -25.29% |

July is effectively a call-only sample, so it cannot validate a general side-aware policy. The largest standardized feature shifts are regime state, regime bias/alignment, Scout side probabilities, option side, and open interest.

## Locked fixed-linear ablation

All ablations use the same outcome-aware purged folds and a fixed linear family to avoid model-family cherry-picking.

| Feature set | Positive-P&L AUC | Brier | Return MAE |
|---|---:|---:|---:|
| Full | 0.4626 | 0.3966 | 0.8796 |
| Directional / Scout | 0.4276 | 0.3528 | 0.7537 |
| Liquidity / friction | 0.4907 | 0.3091 | 0.7405 |
| Regime / event | 0.4829 | 0.2683 | 0.6877 |
| Volatility / contract | **0.5481** | 0.3229 | 0.7346 |
| Full without directional | **0.5366** | 0.3314 | 0.7668 |
| Full without liquidity / friction | 0.4774 | 0.3484 | 0.7945 |

Directional features reduce out-of-fold performance. Volatility/contract features show weak rank discrimination, but Brier remains worse than a naive base-rate forecast; the result is not deployable probability skill.

## Scientific next action

Freeze a shadow challenger using the volatility/contract feature set with no Scout directional inputs. Calibrate it only inside each training fold, pre-register the feature list and thresholds, and evaluate on new prospective data. Do not promote until both sides pass, July-like call-heavy periods are represented, and the paired live-shadow bootstrap passes the existing gates.

Machine-readable results: `output/payoff_skill_diagnostics_latest.json`.
