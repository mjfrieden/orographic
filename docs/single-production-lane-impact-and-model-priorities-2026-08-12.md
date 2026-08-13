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
| Moonshot | Could be confused with either a hidden rank input or tradable satellite | Separate visible side pick with its own prospective ledger; zero primary-ensemble and broker authority |
| Broker API | Confirmed manual overrides could open held contracts | Candidate lookup and buy-to-open accept Council only |
| Position exits | Existing contracts may not be on today's board | Sell-to-close remains allowed; this reduces risk |
| Diagnostics and archives | Many artifacts use historical “shadow” names | Retained as telemetry compatibility; no routing authority |
| Backtests | Need a baseline for comparison | `current_gated` remains an offline model-stack baseline |
| Operations | Old CLI callers may request alternate sizes | Live CLI no longer exposes those allocation flags |

## Model and policy inventory

| Component | Simple job | Current evidence | Best improvement |
|---|---|---|---|
| Directional Scout | Predicts whether the underlying is more likely up or down over five days | Mean CV AUC 0.5206 across 49,178 rows: only slightly above chance | Replace the broad classifier with regime-aware, calibrated return-distribution heads and test net option utility rather than only underlying sign |
| Side-aware Scout | Chooses call, put, or no-trade from strict option outcomes | The current 740-row outcome artifact has 303 symbol/dates, 17 put-edge labels, and zero explicit matched pairs | Matched call/put capture is now active; accumulate resolved pairs and rebalance puts before relaxing the 70% override gate |
| Hierarchical Scout | Separates trade/abstain from call/put direction | Trade AUC 0.4825; direction head had zero usable OOF rows, and incidental two-sided dates no longer qualify as matched evidence | Retrain only after at least 50 explicit paired examples per side; until then its 20% weight remains an ablation target |
| Sentinel | Converts events into direction, magnitude, horizon, and IV effects | Deterministic policy, not a supervised classifier | Label event-time option outcomes and calibrate by event type, novelty, source reliability, and horizon using point-in-time features |
| Primary payoff model | Estimates positive option P&L, breakeven odds, return, fill, and path heads | Positive-P&L AUC 0.5678; put AUC 0.53 on 73 rows; exact-quote coverage is thin | Best near-term modeling target: retrain on larger strict executable labels, side-balanced folds, and calibrated after-cost utility |
| Path model | Estimates early profit, favorable excursion, and decay risk | Aggregate AUC 0.5432, but side segment AUCs are below 0.47 and regimes are unclassified | Rebuild from timestamped intraday paths with real regime labels; ablate the present 18% rank weight |
| Cost-aware payoff challenger | Adds downside quantiles, fill quality, and target-before-stop | Positive-P&L AUC 0.4403 and breakeven AUC 0.4585—worse than chance | Highest risk-reduction priority: diagnose label/score orientation, retrain, and run zero-weight/inverted-rank ablations before keeping its 14% contribution |
| Volatility payoff observer | Tests volatility-surface features | Superseded at runtime by the cost-aware artifact when present | Fold useful surface features into the primary payoff retrain; retire the duplicate artifact after parity tests |
| Path hazard challenger | Chooses target/stop/expiry behavior | Zero valid pre-exit paths and zero target/stop events | Do not fit yet; repair timestamped path capture first |
| Council | Applies thresholds, diversification, turnover, shock, and sizing policy | The production core-policy replay improved P&L and account drawdown versus the old three-pick research reference in all tested windows | Keep one-pick 0.86/0.84 gates; enforce the $600 entry ceiling server-side and validate next with fold-frozen Cirrus runs |
| Position exit model | Advises harvest/hold/risk exit for open positions | Harvest validation AUC 0.432; several production features were constant in training | Retrain from real trajectories and fills; keep exits mechanically bounded until validation beats simple stop/target rules |
| Moonshot experiment | Surfaces one cheap, convex tail-upside side pick | Dedicated prospective ledger and fixed outcome windows exist; it is not yet a validated production model | Keep it separate and visible, grow independently labeled outcomes, and evaluate tail hit rate/payoff distribution without contaminating the primary ensemble |

## Recommended improvement order

1. **Keep the present ensemble weights while collecting cleaner evidence.** The
   strict-real component ablation below found that removing hierarchical,
   path, or cost-aware inputs reduced P&L in every tested window. Weak
   standalone cards remain a warning, but zero-weighting these interacting
   features now would be contrary to the available end-to-end evidence.
2. **Accumulate paired, side-balanced option outcomes.** Capture is implemented;
   the remaining work is allowing enough same-expiry call/put outcomes to
   resolve before retraining.
3. **Retrain the primary payoff model on strict after-cost labels.** It is the
   strongest learned ranker today and has the clearest path to incremental gain.
4. **Rebuild path and exit datasets from timestamped intraday quotes.** Do not
   optimize exit intelligence against terminal-only observations.
5. **Validate Council against account risk, not premium-return drawdown.** Add
   aggregate open-risk and correlation budgets after fold-frozen replication;
   the corrected 12-month account drawdown was -2.43%, while the earlier -95%
   figure measured compounded return on weekly premium deployed.
6. **Run Cirrus expanding-window retraining and ablations.** Freeze every fold's
   artifacts before scoring its validation dates; the current fixed-artifact
   replay is diagnostic rather than a clean promotion test.

## Primary-ensemble ablation completed 2026-08-12

Moonshot is excluded from this experiment because it is a separate visible
side experiment, not an input to the primary ensemble. Each variant uses the
same 100-symbol universe, strict real option chains, 7–14 DTE selection, 90%
minimum real-chain coverage, liquidity gates, 3% entry and exit slippage, cost
cap, and Council policy. Values below are total P&L; parenthetical values show
the change versus the full unified stack.

| Window | Former baseline | Full unified | No hierarchical | No path | No cost-aware | Primary only |
|---|---:|---:|---:|---:|---:|---:|
| 3 months | -$214.32 | $89.07 | -$63.37 (-$152.44) | -$10.88 (-$99.95) | -$430.77 (-$519.84) | -$377.80 (-$466.87) |
| 6 months | -$340.24 | -$26.52 | -$143.02 (-$116.50) | -$157.03 (-$130.51) | -$651.87 (-$625.35) | -$503.18 (-$476.66) |
| 12 months | $52.02 | $90.01 | -$10.28 (-$100.29) | -$40.50 (-$130.51) | -$253.03 (-$343.04) | -$175.21 (-$265.22) |

This is evidence to retain, not promotion evidence. The run uses fixed artifacts
across historical dates rather than fold-frozen expanding-window retraining, the
trade counts are small (11–40 for unified variants), and its legacy drawdown
metric compounds return on premium deployed rather than measuring an account
equity curve. That metric is now retained as `capital_at_risk_max_drawdown`; new
experiments also report `account_max_drawdown` against an explicit initial
account value.

## Council production-parity and loss-budget result

The earlier component comparison used the historical research defaults: three
Council picks and a 0.57 score gate. Production actually uses one pick, call/put
gates of 0.86/0.84, and a 0.90 extrinsic ceiling. A strict-real parity sweep
tested those production core gates against the research reference and lower
one-pick score gates. It does not reconstruct the live market-shock overlay or
prior-board turnover state. Account metrics below assume a stated $10,000
initial value; no leverage or reinvestment is inferred.

| Window | Policy | Trades | Total P&L | Sharpe | Account return | Account max drawdown |
|---|---|---:|---:|---:|---:|---:|
| 3 months | Three-pick research reference | 11 | $89.07 | 1.04 | 0.89% | -2.50% |
| 3 months | Production core policy | 5 | $395.83 | 2.96 | 3.96% | -0.56% |
| 6 months | Three-pick research reference | 24 | -$26.52 | 0.88 | -0.27% | -4.14% |
| 6 months | Production core policy | 13 | $453.56 | 2.17 | 4.54% | -2.44% |
| 12 months | Three-pick research reference | 38 | $90.01 | 0.65 | 0.90% | -5.81% |
| 12 months | Production core policy | 23 | $495.46 | 1.49 | 4.95% | -2.43% |

Decision: retain the production core Council gates. Do not loosen the threshold
from this fixed-artifact diagnostic. The server now enforces the same `$600`
buy-to-open cost-basis ceiling for both preview and submission, and rejects
non-Council previews as well as submissions. Remaining work is Cirrus-style
fold-frozen replication and, once trade counts are materially larger, explicit
aggregate open-risk and correlation budgets.

## Matched call/put capture implemented 2026-08-13

The prior prospective ledger recorded every scored Forge contract, but Forge
only scored the side selected by Scout. Consequently, “all candidates” did not
produce a fair call-versus-put label. The current 740-row canonical artifact
contains 303 symbol/dates, three dates with both sides present by coincidence,
and zero explicit matched pairs.

Forge now selects a research pair from the option chain it already fetched:
one call and one put at the same expiry, each nearest to 0.35 absolute delta
after the existing premium, spread, liquidity, moneyness, and delta filters.
The pair is written to the prospective outcome ledger and receives the same
fixed-window executable outcome capture as other observations. If the selected
same-side contract is already a Forge candidate, its row is annotated and
reused so payoff and path datasets are not duplicated.

Blast-radius controls are explicit:

- paired observations never enter Forge ranking, Council, Moonshot, sizing, or
  Tradier candidate lookup;
- capture failures are logged and skipped without interrupting production
  candidate construction;
- option-chain network traffic does not increase because both sides were
  already fetched for surface construction;
- outcome-mark quote volume can increase by up to two contracts per eligible
  Forge symbol, bounded by the existing 500-symbol marker limit;
- `--no-paired-side-capture` is an operational kill switch;
- the hierarchical trainer accepts only a shared explicit pair identifier as
  call/put direction evidence. Incidental two-sided dates remain visible but
  cannot pass the 50-per-side promotion gate.

This milestone is data infrastructure, not a model promotion. No performance
uplift is claimed until enough pairs have resolved and a purged, fold-frozen
retrain beats the current Scout and production-core Council policy.

## Scout matched-pair readiness gate added 2026-08-13

The repository now emits
`web/data/diagnostics/scout_pair_readiness_latest.json` from both the normal
scan and the 15-minute outcome-capture workflow. The gate requires 150 complete
strict-executable pairs, at least 50 call-edge and 50 put-edge labels, 30
independent decision dates, two regimes containing 25 pairs each, and three
usable purged walk-forward folds. Every evaluation fold must train and freeze
its own artifacts before scoring later dates.

The historical chain audit cannot responsibly accelerate this gate: the local
OptionsDX manifest has 299,617 rows but only one symbol and three quote dates.
That is inadequate breadth for call-versus-put backfill, so the report directs
the system to continue prospective collection instead of synthesizing pairs.

Readiness is deliberately narrower than promotion. Even after all collection
gates pass, the report only permits a pre-registered offline evaluation and
keeps `active_model_change_allowed=false`. Model governance also continues to
show Scout as held. No active model, ensemble weight, Council rule, position
size, Moonshot selection, or Tradier route changes as a result of this gate.

## Release posture

The architecture is suitable for one production lane, but the evidence does
not justify scaling capital. Keep broker arming and position size conservative
until leakage-safe ablations identify which components truly cause the uplift
and Council drawdown is materially reduced.
