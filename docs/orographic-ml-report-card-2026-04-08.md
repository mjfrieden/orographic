# Orographic ML Report Card

Date: 2026-04-08

## Executive Summary

Orographic has improved materially over the last three trading-engine revisions. The stack is now meaningfully more institutional than the original heuristic version: the Scout layer uses a trained LightGBM classifier, the Forge layer has explicit volatility and liquidity filters, the Council layer attempts portfolio-aware selection, and the backtest harness is no longer purely heuristic.

That said, the system is not yet production-consistent. The current live scan runs end to end, but it is failing the final conversion step: on April 8, 2026 the pipeline generated 41 scout signals and 0 forge candidates. That means the present bottleneck is not directional alpha generation, but contract feasibility and workflow calibration. The recent upgrades improved the research narrative faster than they improved live execution quality.

## Recent Developments Reviewed

- `2026-04-05` `e074691`: introduced z-scored momentum, VRP checks, historical options backtesting, and the AI Sentinel overlay.
- `2026-04-05` `ae01196`: upgraded the stack to LightGBM Scout inference, Markowitz-style Council selection, risk-free-rate integration, and model artifact serialization.
- `2026-04-05` `b43bb1f`: added a pre-2026 training cutoff to reduce look-ahead bias in the replay path.
- `2026-04-07` `8ae5074`: tightened the engine around ITM/ATM structures, vertical spreads, confidence sizing, and VIX circuit breakers.
- `2026-04-07` `99a14ea`: hardened the scan pipeline and re-enabled AI Sentinel in the live path.

## Report Card

| Area | Grade | Why it earned the grade |
| --- | --- | --- |
| Research Architecture | A- | The separation between Scout, Forge, Council, and replay is clean and easy to reason about. |
| Directional Alpha Model | B | The LightGBM upgrade is real and the feature set is sensible for short-horizon classification, but it is still a single-horizon bull/bear classifier rather than a return-distribution model. |
| Contract Construction | D+ | The live path currently converts valid signals into zero tradeable contracts under present market conditions. |
| Portfolio Construction | C | Diversification intent is strong, but the Markowitz implementation is still a lightweight overlay rather than a robust risk engine. |
| Backtest Integrity | C- | The replay path is much better than before, but the reporting layer and sizing logic are internally inconsistent enough to overstate confidence. |
| Workflow Reliability | D+ | The scan no longer crashes easily, but the validation loop is thin and the test harness does not run cleanly from the repo root. |
| Production Readiness | C- | Good research momentum, not yet ready for tight capital allocation without another hardening pass. |

## What Improved Recently

### 1. Scout moved from heuristic scoring to actual ML inference

The strongest improvement is the migration from a linear heuristic to a trained LightGBM classifier in [`engine/orographic/scout.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/scout.py). Feature engineering is coherent for a weekly directional system: multi-horizon momentum, realized volatility, RSI, ATR, volume ratio, volatility regime, and SPY-relative context.

This is a legitimate upgrade because:

- the inference path and feature extraction are centralized instead of being duplicated ad hoc
- the training script uses `TimeSeriesSplit` rather than random shuffling
- the cutoff added in [`engine/train_scout_model.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_scout_model.py) reduces the obvious 2026 look-ahead problem

### 2. Regime logic became more decisive

The hard veto for `extreme_vol` plus side-specific vetoes for `risk_on` and `risk_off` in [`engine/orographic/scout.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/scout.py#L300) is directionally correct. This is one of the better recent changes because it prevents the model from firing against the broad tape when volatility is disorderly.

### 3. Forge now thinks more like an options desk

Recent additions in [`engine/orographic/forge.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/forge.py) materially improved the contract-selection logic:

- IV Rank penalty
- VRP penalty
- delta filtering
- explicit liquidity checks
- net-debit handling for candidate spreads

This is the right design direction. The issue is calibration, not concept.

## Where the Stack Is Breaking

### 1. Live tradeability has collapsed

Evidence from a fresh local scan on 2026-04-08:

- `45` symbols fetched
- `41` valid scout signals generated
- `0` forge candidates
- Council abstained

The critical constraint clash is inside [`engine/orographic/forge.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/forge.py#L52):

- `max_premium = 1.6`
- `min_abs_delta = 0.50`
- moneyness band constrained to `-5%` ITM through `+3%` OTM
- spread cap `<= 18%`

Those filters are collectively too tight for current weekly index and large-cap chains. In a live diagnostic on April 8:

- `MSFT` and `QQQ` had contracts that passed premium, spread, liquidity, and moneyness filters
- those same contracts then failed the delta filter because their deltas were around `0.13` to `0.23`, well below the required `0.50`
- for symbols like `MCD`, `QCOM`, `JNJ`, and `IBM`, the spread filter eliminated the entire cheap weekly set before delta was even evaluated

This is the single biggest operational issue in Orographic right now.

### 2. Backtest metadata and sizing are inconsistent

The pricer now uses a `$500` max budget in [`engine/backtest/pricer.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/pricer.py#L25), but the results artifact still publishes `budget_per_trade_usd: 100.0` in [`engine/backtest/results.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/results.py#L149) and [`engine/backtest/results.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/results.py#L197).

That matters because the saved backtest artifact currently reports:

- period: `2026-01-07` to `2026-04-07`
- trades: `783`
- win rate: `41.76%`
- Sharpe: `1.2637`
- total P&L: `$62,461.30`

But the same artifact also contains trades that routinely violate the stated sizing envelope:

- median `cost_basis`: `$688.63`
- `68.45%` of trades cost more than `$500`
- `28.22%` of trades cost more than `$1,000`
- max `cost_basis`: `$1,997.15`

That gap is largely driven by the `max(1, ...)` contract floor in [`engine/backtest/pricer.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/pricer.py#L136), which forces one contract even when the target budget cannot afford it. So the backtest is not honoring the risk budget in the way the headline claims imply.

### 3. The “Markowitz + Kelly” story is stronger than the implementation

The Council layer does add correlation awareness, but there are two limitations:

- `_kelly_weight` is defined in [`engine/orographic/council.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/council.py#L134) and not used anywhere
- the optimizer uses transformed scout scores and implied vol as a rough proxy set, which is acceptable for a first pass but not yet a true expected-return / covariance engine

This means Council is currently best thought of as “diversified rank selection with a lightweight optimizer wrapper,” not a mature portfolio optimizer.

### 4. The replay path is only partially aligned with the live path

Replay is improved, but it still diverges materially from live execution:

- replay allows `max_premium = 20.00` in [`engine/backtest/replay.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/replay.py#L199)
- live Forge caps premium at `1.6` in [`engine/orographic/forge.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/forge.py#L58)
- replay does not reconstruct the new spread search used in live Forge
- replay still carries transitional code like `signal.option_type if hasattr(signal, "option_type") else signal.direction` in [`engine/backtest/replay.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/replay.py#L208)

So the current backtest is better than before, but not yet a strict mirror of what the live scanner can actually trade.

### 5. Workflow validation is too weak for a fast-changing ML engine

The tests are minimal and currently not runnable from the repo root because they import `orographic.*` directly:

- [`engine/tests/test_council.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_council.py#L5)
- [`engine/tests/test_market_data.py`](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_market_data.py#L5)

Running `python -m unittest discover -s engine/tests -q` from the workspace failed with `ModuleNotFoundError: No module named 'orographic'`.

That is a process problem, not just a developer-experience problem. Right now it is too easy to land changes that alter sizing, feasibility, or replay assumptions without a passing validation gate.

## Technical-Analysis Lens

From a trading-desk perspective, the most promising element in Orographic is the regime-aware directional read. The model is already acting like a short-horizon trend-and-tape engine:

- momentum and relative-strength features are driving classification
- VIX-sensitive regime vetoes are preventing obvious tape fights
- contract selection explicitly tries to avoid buying pure vol at the wrong point in the cycle

The weak link is not signal direction. The weak link is the transition from directional view to executable structure. In technical-analysis terms: Scout is finding trend candidates, but Forge is demanding option structures that rarely coexist with the liquidity, premium, and delta constraints we set. We have a chart read without a consistently fillable instrument.

## Priority Improvement Plan

### Phase 1: Restore live conversion this week

1. Recalibrate Forge to target feasibility first.
2. Add a filter-waterfall diagnostic to show candidate counts after each constraint.
3. Split contract policies by symbol class:
   - index ETFs
   - mega-cap equities
   - high-priced names
4. Decide whether Orographic is a cheap-premium engine or an ITM-delta engine. It cannot be both with the current caps.

Recommended parameter reset:

- raise `max_premium` from `1.6` to a symbol-aware band such as `2.5` to `4.0`
- lower live `min_abs_delta` from `0.50` toward `0.25` to `0.40` unless the spread debit is used consistently
- widen `max_spread_pct` for high-priced names or apply a dollar-spread cap instead of percentage only
- if verticals are the intended structure, rank the spread itself rather than filtering the long leg first and only then attaching a short leg

### Phase 2: Make the backtest honest

1. Fix the budget metadata mismatch in results.
2. Enforce the budget ceiling instead of forcing `1` contract when the contract is too expensive.
3. Align replay contract rules with live Forge rules.
4. Report separate metrics for:
   - naked longs
   - debit spreads
   - trades that exceeded the stated budget
5. Add feasibility metrics:
   - signal-to-candidate conversion
   - candidate-to-live-board conversion
   - average spread pct
   - average delta and premium by selected trade

### Phase 3: Upgrade model quality, not just model complexity

1. Replace binary “positive 5-day return” labeling with richer targets:
   - expected forward return
   - probability of exceeding breakeven
   - probability of 1 ATR move before expiry
2. Add calibration checks:
   - probability buckets
   - realized win rate by decile
   - IC stability by month and regime
3. Add regime-segmented performance reporting:
   - risk-on
   - neutral
   - risk-off
   - extreme-vol
4. Add symbol-cluster validation so one regime or one sector is not driving the entire apparent edge.

### Phase 4: Hardening and operating discipline

1. Make tests runnable from repo root and in CI.
2. Add regression tests for:
   - Forge feasibility under real sample chains
   - budget enforcement
   - replay/live parameter parity
   - abstain behavior in `extreme_vol`
3. Version model artifacts and tie every backtest JSON to:
   - model hash
   - feature list
   - parameters
   - replay settings
4. Add a daily post-scan diagnostic artifact with:
   - top scout names
   - rejection waterfall
   - final board
   - reasons for abstain

## CTO Bottom Line

Orographic is no longer a toy heuristic engine. The recent work moved it into the category of a credible research platform. The problem is that the last 20% of the stack, where research becomes executable risk-taking, is still under-calibrated and under-validated.

My call as CTO would be:

- keep the ML upgrade
- keep the regime vetoes
- keep the volatility-aware contract scoring
- immediately rework Forge feasibility and backtest honesty before allocating more confidence to the Sharpe headline

If we do that, Orographic can move from “interesting quant prototype” to “trustworthy weekly options process.” If we do not, we risk overfitting the story while the live engine keeps abstaining or trading a very different book than the backtest says it is trading.
