# Single production lane: platform impact and model priorities

## Executive assessment

The change has a broad but controllable blast radius. The engine already had a
single canonical Scout → Forge → Council flow, but alternate order surfaces
survived in the browser and broker boundary. Those surfaces are now closed.
Historical research fields remain readable but carry no execution authority.

The largest remaining risk is model quality, not lane ambiguity. The unified
backtest improves on the former gated baseline, but the stack currently gives
bounded influence to models whose own cards show weak or incomplete
out-of-sample evidence. The next work should be walk-forward retraining and
ablation, not adding another lane.

## Blast-radius map

| Surface | Impact | Control |
|---|---|---|
| Scan engine | Alternate allocation settings could split candidates | Production scan forces both allocations to zero |
| Council and snapshot schema | Existing readers expect `shadow_board` | Field remains, always empty in new production scans |
| Web cockpit | Previously fell back to held/Forge contracts | Cockpit shows HOLD when Council has no pick |
| Moonshot display | Looked like an independently tradable satellite | Marked research-only and controls are disabled |
| Broker API | Confirmed manual overrides could open held contracts | Candidate lookup and buy-to-open accept Council only |
| Position exits | Existing contracts may not be on today's board | Sell-to-close remains allowed; this reduces risk |
| Diagnostics and archives | Many artifacts use historical “shadow” names | Retained as telemetry compatibility; no routing authority |
| Backtests | Need a baseline for comparison | `current_gated` remains an offline model-stack baseline |
| Operations | Old CLI callers may request alternate sizes | Live CLI no longer exposes those allocation flags |

## Model and policy inventory

| Component | Simple job | Current evidence | Best improvement |
|---|---|---|---|
| Directional Scout | Predicts whether the underlying is more likely up or down over five days | Mean CV AUC 0.5206 across 49,178 rows: only slightly above chance | Replace the broad classifier with regime-aware, calibrated return-distribution heads and test net option utility rather than only underlying sign |
| Side-aware Scout | Chooses call, put, or no-trade from strict option outcomes | 271 training rows; only 6 put-edge training examples; balanced accuracy 0.3633 | Highest data priority: collect paired call/put outcomes for the same symbol/date and rebalance puts before relaxing the 70% override gate |
| Hierarchical Scout | Separates trade/abstain from call/put direction | Trade AUC 0.4825; direction head had zero usable OOF rows | Retrain only after at least 50 paired examples per side; until then its 20% weight should be an explicit ablation target |
| Sentinel | Converts events into direction, magnitude, horizon, and IV effects | Deterministic policy, not a supervised classifier | Label event-time option outcomes and calibrate by event type, novelty, source reliability, and horizon using point-in-time features |
| Primary payoff model | Estimates positive option P&L, breakeven odds, return, fill, and path heads | Positive-P&L AUC 0.5678; put AUC 0.53 on 73 rows; exact-quote coverage is thin | Best near-term modeling target: retrain on larger strict executable labels, side-balanced folds, and calibrated after-cost utility |
| Path model | Estimates early profit, favorable excursion, and decay risk | Aggregate AUC 0.5432, but side segment AUCs are below 0.47 and regimes are unclassified | Rebuild from timestamped intraday paths with real regime labels; ablate the present 18% rank weight |
| Cost-aware payoff challenger | Adds downside quantiles, fill quality, and target-before-stop | Positive-P&L AUC 0.4403 and breakeven AUC 0.4585—worse than chance | Highest risk-reduction priority: diagnose label/score orientation, retrain, and run zero-weight/inverted-rank ablations before keeping its 14% contribution |
| Volatility payoff observer | Tests volatility-surface features | Superseded at runtime by the cost-aware artifact when present | Fold useful surface features into the primary payoff retrain; retire the duplicate artifact after parity tests |
| Path hazard challenger | Chooses target/stop/expiry behavior | Zero valid pre-exit paths and zero target/stop events | Do not fit yet; repair timestamped path capture first |
| Council | Applies thresholds, diversification, turnover, shock, and sizing policy | Unified backtest still has -68.89% six-month and -95.03% twelve-month max drawdown | Treat drawdown as the main objective: add portfolio loss budget, exposure clustering, and walk-forward threshold optimization |
| Position exit model | Advises harvest/hold/risk exit for open positions | Harvest validation AUC 0.432; several production features were constant in training | Retrain from real trajectories and fills; keep exits mechanically bounded until validation beats simple stop/target rules |

## Recommended improvement order

1. **Ablate the cost-aware and hierarchical contributions.** Their cards show
   below-chance discrimination, so establish whether the unified uplift comes
   from them or occurs despite them.
2. **Collect paired, side-balanced option outcomes.** This directly fixes the
   largest Scout failure: almost no put-edge examples.
3. **Retrain the primary payoff model on strict after-cost labels.** It is the
   strongest learned ranker today and has the clearest path to incremental gain.
4. **Rebuild path and exit datasets from timestamped intraday quotes.** Do not
   optimize exit intelligence against terminal-only observations.
5. **Optimize Council for drawdown, not just rank quality.** The unified stack's
   12-month drawdown remains unacceptable even though P&L and Sharpe improved.
6. **Run Cirrus expanding-window retraining and ablations.** Freeze every fold's
   artifacts before scoring its validation dates; the current fixed-artifact
   replay is diagnostic rather than a clean promotion test.

## Release posture

The architecture is suitable for one production lane, but the evidence does
not justify scaling capital. Keep broker arming and position size conservative
until leakage-safe ablations identify which components truly cause the uplift
and Council drawdown is materially reduced.
