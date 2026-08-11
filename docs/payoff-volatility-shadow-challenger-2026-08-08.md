# Volatility/Contract Payoff Shadow Challenger — 2026-08-08

## Pre-registered specification

- Mode: observation only; read for telemetry but never used for candidate selection, sizing, or routing.
- Family: fixed linear classifier.
- Features: option side, moneyness, absolute delta, IV, IV rank, realized volatility, ATR, VRP gap, projected move, breakeven move, extrinsic ratio, and signal-time DTE.
- Explicitly excluded: Scout score, Scout side probabilities, no-trade probability, and other directional features.
- Validation: nested date-grouped purged walk-forward.
- Calibration: monotonic log-odds intercept fitted only on an inner chronological holdout. Outer validation is never used for fitting or calibration.

## Result

**HOLD. The artifact is suitable for offline shadow scoring only, not live-shadow routing or promotion.**

| Metric | Result | Requirement |
|---|---:|---:|
| Raw aggregate AUC | 0.5588 | Diagnostic |
| Calibrated aggregate AUC | 0.5691 | >= 0.53, pass |
| Raw Brier | 0.3306 | Diagnostic |
| Calibrated Brier | 0.2743 | Better than 0.2253 baseline, fail |
| Call AUC | 0.5770 | >= 0.53, pass |
| Put AUC | 0.5778 | >= 0.53, pass |
| Call Brier | 0.2820 vs 0.2237 | Fail |
| Put Brier | 0.2374 vs 0.2322 | Fail |

Risk-off ranks reasonably (AUC 0.6378) but narrowly misses Brier skill. Neutral and risk-on do not meet the complete quality contract. Zero regimes qualify because discrimination and Brier skill must pass together.

## Interpretation

Removing directional features recovers ranking signal on both calls and puts. The remaining failure is probability reliability: scores are too extreme or unstable across folds and months. This is useful evidence for a ranking-only research lane, but not for expected-value sizing or trade authorization.

## Artifacts

- Shadow model: `engine/orographic/models/payoff_volatility_shadow.pkl`
- Model card: `engine/orographic/models/payoff_volatility_shadow_card.json`
- Trainer: `scripts/train_payoff_shadow_challenger.py`

## Prospective integration milestone

The challenger now scores every Forge candidate alongside the active payoff ranker and records:

- calibrated probability and within-scan percentile rank;
- probability and rank deltas versus the active payoff model;
- a deterministic disagreement flag;
- the challenger artifact hash; and
- Friday-close net executable return for resolved disagreement cases.

These fields flow into the prospective ledger and canonical research datasets. The ledger declares the challenger observation-only and explicitly records that it cannot affect candidate selection, position sizing, or Tradier routing. The model and card are optional artifacts, so their absence cannot disable production.

The active payoff artifact and Tradier execution path were not changed.
