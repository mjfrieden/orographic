# Payoff Challenger Prospective Evidence Milestone — 2026-08-08

## Outcome

The volatility/contract challenger now has a prospective, paired evaluation path. Its decision remains **collecting evidence** and it has no execution authority.

The current historical prospective ledger contains 1,396 picks, but none are eligible for this comparison because those records predate challenger telemetry and strict executable outcome contract v2. The evaluator intentionally reports zero rather than mixing legacy marks or reconstructing predictions after the fact.

## Scientific design

- Active and challenger probabilities are compared on the same post-friction recommendation.
- Only strict Friday-close executable labels, version 2 or newer, are accepted.
- Friction-vetoed candidates cannot enter the counterfactual portfolio.
- A rank replay is admitted only when every scored candidate in the scan is resolved, preventing outcome-dependent candidate-set selection.
- Repeated model versions are never pooled; only the latest observed challenger artifact hash is evaluated.
- Calibration, log loss, AUC, and profitability are reported overall, by option side, and by signal-time regime.
- Counterfactual top-one active and challenger selections are compared with a paired-run nonparametric bootstrap.

## Pre-registered eligibility gates

The challenger cannot become eligible even for a limited live-shadow experiment until it has:

- 100 resolved recommendations across 30 resolved runs;
- 30 probability-decision disagreements;
- at least 30 calls and 30 puts;
- at least 25 observations in two regimes; and
- 30 fully resolved rank-replay runs.

After sample readiness, the challenger must have non-worse discrimination, calibration skill versus both the active model and prevalence baseline, positive top-ranked executable returns, a positive 95% bootstrap lower bound, and side/regime stability.

## Operational integration

Normal scans refresh `web/data/diagnostics/payoff_challenger_evidence_latest.json`. Promotion readiness and the workbench evidence panel display the evidence state and sample counts. The artifact explicitly records `execution_effect: none_observation_only`; it cannot modify Forge ranking, Council selection, sizing, or Tradier routing.

## Readiness instrumentation — 2026-08-11

Schema version 2 adds an explicit readiness contract rather than leaving operators to infer progress from Boolean gates. It reports current, required, remaining, and capped progress for resolved recommendations, resolved runs, decision disagreements, call and put coverage, qualified regimes, and complete rank-replay runs. Friday-close capture integrity is reported separately with valid, missed, retryable, observed-window, actual-rate, and required-rate fields.

The artifact also records every blocking sample gate and one deterministic next action. When the current model cohort has no eligible recommendations, the next action is to collect new post-friction observations; the platform does not relax thresholds, reuse legacy labels, reconstruct missing predictions, or force trades to accelerate the experiment.
