# Orographic Quant ML Improvement Implementation Plan — 2026-08-11

## Objective

Increase after-cost option profitability without weakening abstention discipline. The program prioritizes trustworthy counterfactual evidence, option-specific labels, calibrated economic objectives, and strict separation between research models and Tradier execution authority.

## Milestone 1 — Shadow isolation and counterfactual evidence

Status: **implemented; prospective evidence collection active**

- Make side-aware shadow decisions observational at Scout inference.
- Preserve the prior live no-trade policy by routing would-veto signals into a separate research cohort before live Forge selection.
- Run a bounded research-only Forge batch for that cohort.
- Prohibit research candidates from entering Council, moonshot selection, or Tradier routing.
- Append research candidates to the prospective ledger under `counterfactual_observation` so strict outcome capture can label them.
- Expose live rejections separately from shadow would-veto observations.

Acceptance criteria:

- A shadow guard always returns `execution_effect=none_observation_only`.
- The temporary legacy compatibility holdout is explicit in the snapshot and remains enabled until veto-value evidence supports a live-policy change.
- A would-veto row remains available for research evaluation.
- No counterfactual candidate appears in the live or shadow Council board.
- No counterfactual candidate is eligible for Tradier routing.
- Prospective outcome capture can resolve the research contract using policy-v2 labels.

## Milestone 2 — Veto value and threshold frontier

Status: **implemented; thresholds remain advisory pending evidence gates**

- The dedicated evaluator combines eligible Forge-ranked recommendations with the research-only `counterfactual_observation` lane, so it can estimate both retained and abstained regions of the Scout policy.
- It accepts only strict Friday-close executable labels v2 or newer and isolates the latest Scout side-model artifact.
- Repeated intraday scans collapse to the first Central-date, symbol, and option-contract observation.
- The threshold grid spans no-trade probability and probability-margin cutoffs. Selection uses expanding walk-forward blocks with a one-trading-day embargo; every selected rule is scored only on unseen dates.
- Market-day clustered bootstrap inference controls common daily market shocks. Side and regime coverage gates must also pass.
- The strongest artifact decision is `eligible_for_policy_review`; the evaluator cannot alter Scout thresholds, Council eligibility, or Tradier routing.

## Milestone 3 — Data expansion

Status: **core market and execution telemetry implemented; prospective collection active**

- Full-chain snapshots now retain capture time, last-trade time and age, mid, dollar/percentage spread, two-sided-quote validity, and IV validity. Coverage manifests use schema v2 and report these quality counts.
- Production option expirations and Greeks-enabled chains now prefer the configured Tradier market-data API and retain `entry_data_source=tradier`; yfinance is an explicit availability fallback rather than an unlabelled production source.
- Tradier preview and submission provenance now records quote age, bid/ask/mid/spread, broker round-trip latency, limit price, fill price, fees, fill delay, and signed adverse slippage. Broker errors are persisted as evidence rather than disappearing from the sample.
- Forge candidates now carry point-in-time ATM IV, smile slope and curvature, put–call wing skew, 30-day-normalized term slope, surface fit error and coverage, contract IV relative to ATM, signed IV-minus-realized-volatility, spread dollars, and last-trade age.
- Surface and quote-quality fields flow through prospective ledgers, canonical research datasets, and the payoff-model feature contract. They remain non-authoritative until a newly trained artifact passes purged validation and prospective promotion gates.
- Corrected Forge candidate construction so every eligible chain row is scored before the existing concentration/deduplication policy is applied.
- Improve point-in-time earnings, SEC, macro, and event coverage while retaining first-seen timestamps.
- Track market breadth, volatility term structure, credit conditions, and correlation dispersion.

## Milestone 4 — Cost-aware payoff model

Status: **implemented as an observation-only challenger; prospective evidence collection required**

- The challenger jointly models strict after-cost return q10/q50/q90, positive P&L, breakeven, fill quality, favorable/adverse excursion, early target probability, decay risk, and no-trade probability.
- Linear, tree, and ensemble families use identical date-grouped, outcome-date-purged walk-forward folds. Selection penalizes weak call/put segments before aggregate Brier, MAE, pinball loss, and AUC.
- Separate call/put bundles are trained when each side reaches the pre-registered sample minimum; side and regime calibration quality are mandatory promotion gates.
- The conservative research selector requires q10 after-cost return above zero. Its realized utility and central-80% interval coverage are measured out of fold.
- The new artifact is forced into `observation_only_never_used_for_routing`; it cannot change Forge order, Council eligibility, sizing, or Tradier routing. Promotion still requires complete-run prospective replay and live disagreement evidence.

## Milestone 5 — Side policy redesign

Status: **implemented as an observation-only hierarchical challenger; paired-side and prospective evidence required**

- Stage one estimates whether at least one observed option expression has positive strict after-cost P&L; stage two estimates call versus put conditional on trading.
- Single-side observations may train trade-versus-abstain, but only symbol-dates with both call and put outcomes may train or evaluate direction. This prevents historical call coverage from becoming a false directional advantage.
- Date-grouped, outcome-date-purged walk-forward predictions drive probability metrics and a pre-registered threshold grid.
- Thresholds maximize downside-decile after-cost return subject to minimum selected rows, minimum call/put selections, and minimum regime depth—not balanced accuracy alone.
- Promotion gates require trade-probability Brier skill, directional AUC, paired-side depth, regime coverage, positive mean return, and positive downside-decile return.
- The hierarchical artifact is forcibly observation-only. Its abstentions and directional disagreements are persisted in the Scout evidence ledger and cannot change Scout, Forge, Council, sizing, or Tradier execution.

## Milestone 6 — Path-aware exits

Status: **competing-risk framework implemented; training blocked by invalid trajectory timing**

- A discrete-time, cause-specific hazard challenger now models +25% target, -50% stop, and expiry as competing outcomes. It is structurally observation-only and can provide exit advice but never modify or submit Tradier orders.
- Dataset construction accepts only marks captured after entry and no later than the recorded exit. Post-exit marks are counted as data-quality failures and excluded, preventing future leakage.
- Purged out-of-fold evaluation compares the fixed terminal exit, a mechanical target/stop policy, and a model-conditioned shadow policy on identical entries. Paired lift uses trading-date clustered bootstrap inference.
- Promotion requires at least 150 exact paths, at least 30 target and 30 stop events, no post-exit leakage, and a positive clustered lower confidence bound versus the fixed policy.
- Current canonical data contains 740 terminal outcomes but zero valid pre-exit marks; all 61 archived marks occur after their recorded exits. The system correctly returns HOLD and writes no hazard model.
- Immediate remediation is implemented: the 15-minute outcome workflow now captures the exact active contract on every eligible run through Friday close, deduplicates job retries, rejects stale quotes, and persists path coverage in scan health. Rotating chain archives remain supplemental rather than authoritative for emitted-contract trajectories.
- Deep optimal-stopping methods remain out of scope until dense, timestamp-valid intraday paths exist.

## Milestone 7 — Operator UI and governance

- Add a Why No Trade funnel with live rejections and research observations separated.
- Add model-authority badges: observation, ranking, veto, sizing, and execution.
- Add a counterfactual inspector and evidence clock.
- Display calibration, after-cost return, sample independence, and promotion gates together.
