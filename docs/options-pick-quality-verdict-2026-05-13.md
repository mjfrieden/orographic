# Options Pick Quality Verdict

Generated: 2026-05-13

## Bottom Line

The platform is finding **some historically good options picks**, but the evidence is not strong enough to say it is reliably finding live-tradable great options trades.

The best current interpretation:

- Broad candidate generation is weak-to-modest.
- The payoff ranker has real ranking lift, especially in the highest score bucket.
- Council filtering improves results mostly by rejecting many bad trades.
- The live evidence is too thin and too poorly linked to exact recommendations.
- The model cards show weak standalone ML quality, so the system should be treated as a promising research filter, not a proven autonomous options picker.

Execution matters, but it is not the only problem. Pick quality still needs hard validation.

## Evidence For Signal

The historical option-outcome datasets show ranking lift. In the 12-month baseline candidate set:

| Score Bucket | Avg Forge Score | P&L | Return On Premium | Win Rate |
|---|---:|---:|---:|---:|
| Lowest 20% | 0.373 | -$9,253 | -15.1% | 29.2% |
| 20-40% | 0.469 | +$3,652 | +5.4% | 39.2% |
| 40-60% | 0.538 | +$7,643 | +11.3% | 48.0% |
| 60-80% | 0.648 | +$9,312 | +13.8% | 44.1% |
| Highest 20% | 0.818 | +$44,760 | +55.1% | 66.4% |

That is the strongest pro-signal evidence in the repo. The top-ranked candidates are materially better than the bottom-ranked candidates.

The payoff model also shows monotonic-ish lift by predicted option positivity:

| Probability Bucket | Avg Predicted Positive P&L | P&L | Return On Premium | Win Rate |
|---|---:|---:|---:|---:|
| Lowest 20% | 0.205 | -$10,177 | -16.0% | 29.8% |
| 20-40% | 0.390 | +$2,890 | +4.5% | 38.6% |
| 40-60% | 0.489 | +$10,997 | +16.0% | 45.6% |
| 60-80% | 0.627 | +$15,361 | +22.2% | 48.9% |
| Highest 20% | 0.854 | +$37,043 | +47.1% | 63.9% |

This says the platform is not purely random. It has a real ranking signal in historical replay.

## Evidence Against Live-Grade Confidence

### 1. The broad current stack is fragile

The current-stack 3-month backtest:

| Metric | Value |
|---|---:|
| Trades | 200 |
| P&L | +$339 |
| Return on premium | +0.98% |
| Win rate | 40.5% |
| Sharpe | -0.5039 |
| Max drawdown | -71.97% |

That is not robust. It says the average unfiltered or lightly filtered pick is not good enough.

### 2. Council results are good but small-sample

| Window / Variant | Trades | P&L | Return On Premium | Win Rate |
|---|---:|---:|---:|---:|
| 3mo `council_cost_cap` | 6 | +$477 | +51.4% | 83.3% |
| 6mo `council_cost_cap` | 25 | +$1,265 | +24.7% | 60.0% |
| 12mo `council_cost_cap` | 44 | +$3,686 | +41.2% | 65.9% |

This is promising. But 44 trades over 12 months is still a small proof set for weekly options. The system is selective, which is good, but the confidence interval is wide.

### 3. Council is good, but not extraordinary versus random subsets

I sampled random same-sized subsets from the already-filtered baseline candidate pool.

| Variant | Council P&L | Percentile vs Random Same-Sized Subsets |
|---|---:|---:|
| 3mo `council_cost_cap`, n=6 | +$477 | 84.8th percentile |
| 6mo `council_cost_cap`, n=25 | +$1,265 | 82.7th percentile |
| 12mo `council_cost_cap`, n=44 | +$3,686 | 91.8th percentile |

This is useful lift, not a slam dunk. A live system should ideally clear a higher bar before risking meaningful capital.

### 4. Model-card metrics are weak

The payoff model card reports:

- Positive-P&L AUC: 0.5519
- Breakeven AUC: 0.5611
- Put-side positive-P&L AUC: 0.4688
- Put-side breakeven AUC: 0.3758

The directional Scout model card reports:

- Mean AUC: 0.5263
- Mean information coefficient: 0.045
- Last fold AUC: 0.487

The side-aware Scout was explicitly marked shadow-only because it had limited put-edge coverage and weak balanced accuracy.

These are not strong standalone predictors. The platform's edge, if real, comes from combining filters, ranking, and strict abstention. It does not come from a high-confidence model that clearly knows which option will win.

### 5. Live board evidence is nearly absent

From saved board history, there were only 8 live-board picks from 2026-04-27 through 2026-05-07. Among reconstructed Tradier positions, only one exact live-board contract match was found: `NKE260501C00044000`, a small winner.

That is not enough live evidence to judge whether the platform is finding good picks in production.

## What Is Probably True

1. The platform has a historical ranking signal.

The top score buckets are meaningfully better than low score buckets. That is real and worth preserving.

2. The platform is not good enough as a broad scanner.

Taking lots of candidates is not attractive. The current-stack broad result is fragile and drawdown-heavy.

3. The council filter is the main source of deployable quality.

The system looks best when it says "no" most of the time.

4. Puts are a major weakness.

The model-card put-side metrics are poor, and live losses in SPY/QQQ puts are consistent with that weakness. Some backtests show puts doing well in narrow windows, but the model diagnostics do not support high confidence.

5. High score is more informative than council alone.

In the 12-month baseline pool, the top 44 by `forge_score` produced about +$13,570, versus +$3,686 for the 44-trade council-cost-cap set. That does not mean we should blindly take top score, because the comparison may ignore portfolio, cost, and correlation rules. It does mean the council may be too conservative in some places and too permissive in others.

## Verdict

The platform is **finding better-than-random historical options candidates**, but it has **not yet proven live-grade option-pick quality**.

I would not scale this as if the picker is solved. I would treat it as a research alpha with strict paper/live-shadow gating:

- Trade only exact live-board picks.
- Require model score in the top historical score band.
- Block or heavily haircut puts until put-side validation improves.
- Block high-extrinsic shadow picks from live execution.
- Require every live pick to enter a forward outcome ledger, whether traded or not.
- Promote only after at least 30-50 exact live-board recommendations with quote-verified outcomes.

## The Next Test

The decisive test is not another broad backtest. It is a prospective recommendation-quality ledger:

For every emitted live and shadow pick, record:

- Run timestamp
- Contract
- Lane: live, shadow, holdout
- Score and model hashes
- Bid, ask, mid at emission
- Spread percentage
- Extrinsic ratio
- IV rank
- Underlying spot
- Outcome under fixed exits: 1-hour mark, end-of-day, next-day close, +40% take-profit, -50% stop, Friday close

Then judge the platform on untraded recommendations too. If untraded live-board recommendations perform well under fixed exits, execution is the bottleneck. If they do not, the picker needs more work before live risk.
