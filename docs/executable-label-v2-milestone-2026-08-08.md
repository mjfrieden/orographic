# Executable Label v2 Milestone — 2026-08-08

## Outcome

The canonical live-recommendation outcome dataset now admits only timestamped executable quote/fill labels. Legacy midpoint-only outcomes remain available in research ledgers but are excluded from payoff-model training defaults.

## Dataset

- 740 strict quote-complete rows before contract/date deduplication.
- 409 independent training examples after deduplication.
- 346 calls and 63 puts after deduplication.
- Regimes: 185 risk-on, 118 neutral, 106 risk-off; zero unclassified rows.
- 59 midpoint winners became executable losers after spread and fees.
- Mean measured execution drag: 16.24% of midpoint cost basis.
- All 740 canonical rows preserve entry bid/ask, exit bid/ask, timestamps, quote sources, execution-price sources, label availability, and signal-time regime provenance.

The pre-milestone artifact is preserved at `output/archive/option_outcomes_live_recommendations_pre_strict_v2_2026-08-08.json`.

## Locked retraining result

The isolated v6 payoff candidate remains **HOLD**:

| Segment | AUC | Brier | Naive Brier | Result |
|---|---:|---:|---:|---|
| Aggregate positive P&L | 0.4423 | 0.3767 | — | Fail |
| Calls | 0.3278 | 0.3648 | 0.2274 | Fail |
| Puts | 0.4444 | 0.3430 | 0.2314 | Fail |
| Neutral | 0.2559 | 0.4317 | 0.2189 | Fail |
| Risk-off | 0.3953 | 0.3374 | 0.2291 | Fail |
| Risk-on | 0.3550 | 0.3441 | 0.2308 | Fail |

The candidate also fails minimum put depth (63 versus 75) and observed intrahorizon quote-path coverage (10.76% versus 25%). No production model was replaced.

## Next evidence milestone

Accumulate at least 12 additional independent put outcomes and raise observed intrahorizon path coverage to 25%. More importantly, diagnose the negative out-of-fold skill before adding model complexity: test feature-label timing, distribution shift by month, and whether contract liquidity/friction features dominate the current directional features.
