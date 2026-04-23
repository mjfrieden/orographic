# Orographic Technical Analysis Report Card

Date: 2026-04-22

Perspective: CTO/new-hire audit with a senior quantitative trading lens. The emphasis is short-horizon options timing, model integrity, execution realism, and regression control.

## Executive Read

Orographic has moved from a prototype signal board into a credible, inspectable options research and scan platform. The Scout -> Forge -> Council separation is the right shape: Scout forms directional views, Forge translates those views into weekly option contracts, and Council applies live-board discipline. The latest snapshot generated 46 Scout signals from a 100-symbol universe, passed 6 symbols into Forge, produced 21 tradable candidates, and selected 1 live contract.

The most important recent development is the April 21 regression/fix cycle. Commit `7c07c72` added side-aware shadow ML controls but accidentally displaced the trained Scout loader body, forcing heuristic fallback even when model artifacts existed. Commit `2946fb7` restored trained Scout loading and recovered payoff edge. The recovered 6-month strict-real experiment shows the deployable `council_cost_cap` variant at 34 trades, 61.8% win rate, +$1,794 P&L, 26.1% net return, and 2.84 Sharpe.

The system is directionally promising, but not yet institutional-grade. The model stack still has three big gaps: Scout predicts 5-day underlying direction rather than option payoff, the active payoff ranker has weak standalone CV signal, and governance claims around side-aware Scout/model cards are ahead of the committed artifacts.

## Current Live Snapshot

Source: `web/data/latest_run.json`, generated `2026-04-22T03:04:36+00:00`.

| Metric | Value |
| --- | ---: |
| Universe size | 100 |
| Scout signals | 46 |
| Scout pre-veto side mix | 43 calls / 57 puts |
| Scout final side mix | 43 calls / 3 puts |
| Counter-regime weak-conviction rejections | 54 |
| Pre-Forge selected symbols | ORCL, GLD, NFLX, SLV, WFC, QQQ |
| Forge candidates | 21 |
| Payoff ranker mode | active, 21 scored candidates |
| Avg learned rank score | 0.5777 |
| Live board | NFLX call |
| Shadow board | SLV call, ORCL put, QQQ call |
| Council status | Not abstaining, but side guard demoted concentration |

The live scan is operationally healthy, but highly filtered. Scout began close to balanced in raw directional read, then regime alignment reduced puts from 57 to 3. Forge was able to produce candidates across all 6 selected symbols. Council then selected only one live position because the live-eligible candidates were too side-concentrated or too extrinsic-heavy.

## Report Card

| Area | Grade | Assessment |
| --- | --- | --- |
| Architecture | A- | The Scout, Forge, Council boundaries are clean and make regressions localizable. |
| Technical-analysis feature set | B+ | Momentum, RSI, realized volatility, ATR, MA distance, volume, SPY trend, SPY volatility, and relative strength are sensible weekly-options timing inputs. |
| Scout direction model | B- | Trained LightGBM loading is restored and tested, but the target remains 5-day underlying direction, not move over breakeven or option P&L. |
| Side-aware Scout | C | Snapshot exposes call/put/no-trade probabilities, but current artifacts do not include `scout_side_model.pkl`; latest live run used `derived_three_class` for all 100 observations. |
| Payoff ranker | B- | This is the right second-stage idea and is active in production. CV AUC is weak: positive P&L AUC 0.5333 and breakeven AUC 0.5435. It helps ranking more than it proves robust standalone prediction. |
| Regime handling | C+ | Regime alignment is explicit and auditable, but it dominated the latest scan, rejecting 54 weak counter-regime puts and leaving only 3 final puts. |
| Forge contract selection | B | The filter waterfall is strong: positive bid/ask, premium, spread, liquidity, moneyness, delta, and net debit are all visible. Still single-leg only. |
| Council portfolio construction | C+ | Useful live/shadow discipline, side guard, sector annotations, and correlation option. The Markowitz/Kelly language is more ambitious than the live implementation. |
| Backtest integrity | B- | Strict-real coverage exists and the recovered experiment reports 100% real entry/exit coverage. However, local public coverage manifest is sparse and the old dashboard backtest artifact is stale/empty. |
| Regression protection | B- | 45 tests pass and Scout loader coverage was added. There is still no CI gate that runs model artifact smoke tests, strict-real replay, and snapshot sanity together. |
| Observability | B | Forge waterfall and promotion readiness are good. Missing: model-card artifacts in checkout, feature drift monitoring, live shadow P&L tracking, and explicit model hash in every live snapshot. |
| Overall | B- | A real research platform with a recovered edge, but it needs stronger governance, more honest model cards, and tighter live-risk controls before sizing up. |

## Recent Regression Review

### What broke

The side-aware Scout patch in `7c07c72` moved the trained Scout loading code below an earlier `return None` path inside `_load_side_model`. As a result, `_load_model()` returned `None` whenever artifacts existed but the no-artifact branch did not fire. That silently forced heuristic fallback.

### What fixed it

Commit `2946fb7` restored `_load_model()` to load `scout_model.pkl` and `scout_scaler.pkl` directly, then moved `_load_side_model()` into its own function. The test suite now includes a loader smoke test that asserts the trained artifact returns a 5-part tuple when artifacts exist.

### What still needs hardening

The regression was caught by performance/replay review, not by a full pre-merge model parity gate. A high-value next test is: run one deterministic fixture through Scout with artifacts present, assert `ML model active` appears, assert the score differs from the heuristic fallback, and assert the live/backtest Scout paths produce the same score for the same feature row.

## Model Review

### Scout

Scout extracts daily technical features and predicts whether 5-day forward underlying return is positive. Inference maps `p(bull)` to a signed score in `[-1, +1]`. This is good as a first-stage direction filter, but it is not the correct final target for weekly options.

The current regime policy adds +0.08 for aligned signals, subtracts 0.18 for counter-regime survivors, and rejects counter-regime signals below 0.35 absolute conviction. In the latest live snapshot, that policy was the biggest side-mix driver.

Key concern: `engine/train_scout_model.py` applies the training cutoff after computing `fwd_5d_return`. Rows on the last five days before the cutoff can include labels from after the cutoff. This is small but real label leakage and should be fixed before the next retrain.

### Payoff Ranker

The payoff ranker is the strongest conceptual improvement because it scores actual option expressions. It uses features such as side-aligned directional edge, heuristic Forge score, moneyness, delta, premium, spread, OI, volume, IV, IV rank, projected move, breakeven burden, extrinsic ratio, DTE, liquidity, and regime alignment.

Training report highlights:

| Metric | Value |
| --- | ---: |
| Training examples | 855 |
| Calls / puts | 721 / 134 |
| Positive P&L rate | 47.95% |
| Breakeven rate | 30.29% |
| Positive P&L AUC | 0.5333 |
| Breakeven AUC | 0.5435 |
| Expected-return MAE | 0.6832 |

This is a useful ranker, not a high-confidence probability model. Treat its score as a ranking input until calibration and side-balanced validation improve.

### Council

Council does two helpful things: it keeps a shadow board and prevents blindly taking every top-ranked candidate. The latest live board is a good example: SLV, ORCL, and QQQ had higher raw learned scores than NFLX but were sent to shadow due to extrinsic/side constraints.

The gap is that candidate sizing and portfolio optimization are not fully coupled. Council annotates suggested allocation and risk-adjusted score, while the backtest/pricer applies a separate confidence and allocation-weight budget path. That is acceptable for now, but the system should converge on one sizing contract.

## Backtest Review

Best current evidence is the April 21 recovered strict-real 6-month alpha experiment:

| Variant | Trades | Win Rate | P&L | Net Return | Sharpe | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline_all_candidates | 1037 | 38.9% | +$5,329 | 2.5% | 0.74 | -82.6% |
| council_only | 23 | 47.8% | +$179 | 3.8% | 0.38 | -97.0% |
| council_cost_cap | 34 | 61.8% | +$1,794 | 26.1% | 2.84 | -77.8% |
| council_cost_cap_symbol_priors | 32 | 62.5% | +$848 | 13.2% | 2.52 | -89.3% |

Coverage is reported as 100% real-chain at both entry and exit in that artifact. That is a major improvement from earlier modeled-chain reports.

Risk read:

- The deployable edge is concentrated in `council_cost_cap`, not in naive Council alone.
- Drawdown is still very high for weekly long options.
- Puts are still weaker than calls in aggregate. In baseline, calls made +$17,339 while puts lost -$12,010. In `council_cost_cap`, puts were only +$49 across 15 trades.
- Execution stress is not yet punitive enough: recovered run uses 0% entry and 0% exit slippage, while average exit spreads are about 15%.

## Workflow Review

Strengths:

- Scheduled scans run on GitHub Actions three times per weekday.
- Snapshots are committed and deployed to Cloudflare Pages.
- Tests cover Scout loader, regime rejection, payoff ranker active/shadow behavior, replay strict-mode synthetic rejection, result coverage, and promotion readiness.
- The dashboard exposes Forge waterfall and promotion readiness.

Weak spots:

- The scheduled scan workflow does not run tests before committing a refreshed snapshot.
- The current committed `web/data/backtest_results.json` is stale and empty from 2026-04-09, while the real evidence lives in `output/`.
- README says Scout and payoff model cards are written to `engine/orographic/models/`, but this checkout only contains `.pkl` model artifacts and no committed model-card JSONs.
- The local options coverage manifest has only SPY on three quote dates. The recovered artifact likely depends on an external/blended data directory that is not represented in the default local manifest.

## Improvement Plan

### Phase 1: Lock down regression gates

1. Add a CI job that runs `python -m pytest -q engine/tests` before scheduled snapshot commits.
2. Add a deterministic model parity test: current Scout artifact vs heuristic fallback vs historical replay path.
3. Fail scheduled scan if Scout falls back to heuristic while model artifacts exist.
4. Emit `scout_model_sha256`, `scout_scaler_sha256`, `payoff_model_sha256`, and `side_model_present` in every live snapshot.
5. Update `web/data/backtest_results.json` or remove it from the dashboard if it is no longer canonical.

Success metric: no snapshot can be promoted if model loading silently degrades.

### Phase 2: Make model governance truthful

1. Commit or generate current `scout_model_card.json` and `payoff_model_card.json`.
2. Either commit `scout_side_model.pkl` or label Side-Aware Scout as `derived_three_class`, not trained.
3. Add drift checks for the top 10 Scout and payoff features.
4. Track live shadow disagreement P&L for Side-Aware Scout, Sentinel, and Council risk intelligence.
5. Require 30 live shadow trading days and at least 30 disagreement trades before any promotion.

Success metric: dashboard governance matches actual artifacts, not intended artifacts.

### Phase 3: Improve labels and validation

1. Fix Scout cutoff leakage by dropping rows whose `label_date = feature_date + 5 trading days` exceeds the cutoff.
2. Add direct labels for `move_exceeds_breakeven`, `option_positive_pnl`, and `max_favorable_excursion`.
3. Retrain payoff ranker with side-balanced sampling or class weighting so puts are not underrepresented.
4. Report 3/6/12-month strict-real results with slippage stress: 0%, 3%, 5%, and 10%.
5. Report side, regime, sector, and DTE segment performance in every alpha artifact.

Success metric: payoff ranker AUC clears 0.57 out-of-sample or proves value through statistically stable ranking lift.

### Phase 4: Improve trade construction

1. Add vertical debit spread candidates alongside single-leg longs.
2. Score single-leg vs spread expression on breakeven, max loss, expected convexity, and exit spread.
3. Penalize high extrinsic ratio more aggressively when DTE is under 5 days.
4. Add a no-trade threshold based on expected return after spread and slippage.
5. Add exit logic beyond Friday hold: stop loss, profit-taking, and theta/IV crush exit.

Success metric: lower max drawdown and lower expired-worthless rate without giving back too much convex upside.

### Phase 5: Build trader-grade monitoring

1. Daily board report: live picks, shadow picks, rejected top candidates, side mix, regime, realized P&L, and model hashes.
2. Weekly attribution: Scout edge, Forge economics, payoff ranker lift, Council selection lift, and execution drag.
3. Alert on abnormal side mix, no-trade streaks, model fallback, missing options coverage, and ranker score distribution drift.
4. Add a canary backtest that runs a tiny strict-real replay in CI against fixture data.

Success metric: the system explains not only what it picked, but why it did not pick the other high-ranked candidates.

## Capital Recommendation

Keep live trading in sandbox or minimum-size mode until the regression gates and model cards are fixed. The `council_cost_cap` variant is promising enough for paper/live-shadow observation, but the max drawdown profile and weak payoff-model CV argue against scaling.

Near-term trading posture:

- Trade only Council live board, not all Forge candidates.
- Keep hard cost cap at or below $600.
- Require fresh snapshot timing and live option quote refresh before entry.
- Do not override high-extrinsic shadow demotions manually.
- Review put trades separately; current evidence says calls carry most of the recovered edge.

## Verification

- Test suite: `45 passed in 2.32s`.
- Latest snapshot inspected: `web/data/latest_run.json`, generated `2026-04-22T03:04:36+00:00`.
- Recent regression inspected: `7c07c72` introduced side-aware controls; `2946fb7` restored Scout loading and payoff edge.
- Main recovered evidence inspected: `output/alpha_experiment_results_2026-04-21_recovered_edge_6mo.json`.
