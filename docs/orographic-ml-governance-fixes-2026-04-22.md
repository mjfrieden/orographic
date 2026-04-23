# Orographic ML Governance Fixes

Date: 2026-04-22

## What Changed

This pass addressed the four model/governance issues called out in the April 22 report card.

| Issue | Fix |
| --- | --- |
| Scout only predicted underlying direction | `engine/train_scout_model.py` can now train side-aware Scout from strict-real option payoff labels via `--option-outcome-input`. It also fixes cutoff leakage by filtering on the realized 5-day label date, not only the feature date. |
| Payoff ranker had weak standalone CV AUC | `engine/train_payoff_model.py` now uses side-balanced sample weights, reports side-specific validation, and writes a version 3 model card. Positive-P&L AUC improved from 0.5333 to 0.5519; breakeven AUC improved from 0.5435 to 0.5611. |
| Side-aware Scout was only derived | Added `engine/orographic/models/scout_side_model.pkl`, trained from strict-real option payoff labels. Live scans now report `trained_option_payoff_three_class` observations instead of `derived_three_class`. |
| Model cards were missing | Added `engine/orographic/models/scout_model_card.json` and `engine/orographic/models/payoff_model_card.json` with artifact hashes, validation metrics, target definitions, and limitations. |

## Promotion Decision

The new option-payoff side-aware Scout model is intentionally shadow-only by default. It has 433 strict-real option-labeled symbol/date rows, but only 19 put-edge examples and a walk-forward balanced accuracy of 0.4097. It is useful as observability, not ready to steer live direction.

Activation requires:

```bash
OROGRAPHIC_SIDE_MODEL_MODE=active
```

Do not set that in production until live shadow disagreement P&L and side-balanced validation improve.

## Artifact Policy

The 2026-04-22 directional Scout retrain produced an all-call temp scan, so the active directional `scout_model.pkl` and `scout_scaler.pkl` were restored to the prior recovered production artifacts:

| Artifact | SHA-256 |
| --- | --- |
| `scout_model.pkl` | `94129bb3472218ad0980d9fd433afc54585ee0a0652fa6f997cec187b050ec13` |
| `scout_scaler.pkl` | `99aabfe1f016a30fd7ff052c9d39198be2c46048e1cd69a61e20220e1b3be0ab` |
| `scout_side_model.pkl` | `c7b8a165b9489a426e59bc25ebfb9200f7125923f9fb48fb6414b669da9cbffa` |
| `payoff_model.pkl` | `ec8c13688609ce978b82a6f54ec8bab55004a573d9456ef8197edb266acc893a` |

## Verification

- Focused tests after code changes: `14 passed`.
- Full test suite: `47 passed`.
- Temp scan after restoring the production directional Scout:
  - 100-symbol universe
  - 39 Scout signals
  - raw side mix: 37 calls / 63 puts
  - final side mix: 37 calls / 2 puts
  - 19 Forge candidates
  - 3-contract live board
  - side-aware model mode: `trained_option_payoff_three_class` for 100 observations, shadow mode

## Remaining Work

1. Add CI gates that run tests before scheduled snapshot commits.
2. Add live shadow P&L tracking for Side-Aware Scout disagreements.
3. Improve put-edge coverage before promotion.
4. Add slippage-stressed validation runs after the new payoff model.
5. Add an acceptance threshold that blocks promotion when side-aware walk-forward balanced accuracy is below target.
