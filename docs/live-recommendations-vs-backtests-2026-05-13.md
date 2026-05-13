# Live Recommendations Vs Backtests Investigation

Generated: 2026-05-13

## Finding

The live Tradier experience does not contradict the strongest backtest artifacts as directly as it first appears. The bigger issue is that the live trades were mostly outside the actual validated backtest window and often not auditable as exact live-board recommendations.

The latest current-stack backtest artifact, `output/backtest_results_2026-05-06_current_stack_3mo.json`, reports:

| Metric | Value |
|---|---:|
| Trades | 200 |
| Total P&L | +$339 |
| Total deployed | $34,575 |
| Net return | +0.98% |
| Win rate | 40.5% |
| Sharpe | -0.5039 |
| Max drawdown | -71.97% |

That is not a robust, big-winner profile. It is barely positive and quite fragile.

The stronger May 6 council variants are much smaller samples:

| Artifact Variant | Trades | P&L | Win Rate |
|---|---:|---:|---:|
| `council_only`, 3mo | 3 | +$472 | 100.0% |
| `council_cost_cap`, 3mo | 6 | +$477 | 83.3% |
| `council_cost_cap_path_tiebreaker`, 3mo | 6 | +$501 | 83.3% |
| `council_cost_cap_path_tiebreaker`, 6mo | 25 | +$1,884 | 60.0% |

Those are encouraging but not enough to imply that the next 20 live weekly option trades should contain large winners.

## Critical Mismatch

The trade dates in the current-stack backtest stop before the live trading period:

| Artifact | Backtest Entry Range | Backtest Exit Range | Trades After 2026-04-14 |
|---|---|---|---:|
| `backtest_results_2026-05-06_current_stack_3mo.json` | 2026-02-09 to 2026-04-06 | 2026-02-13 to 2026-04-10 | 0 |
| `backtest_results_2026-04-18_payoff_model_strict_real_execution_smoke_3mo.json` | 2026-01-20 to 2026-04-06 | 2026-01-23 to 2026-04-10 | 0 |

The Tradier position snapshots begin with configured live broker data on 2026-04-14. So the realized live period, roughly 2026-04-14 through 2026-05-13, was mostly out-of-sample relative to the backtest trade ledger on disk.

## Live Board Audit

Saved `board_recommendation_history.json` contains 21 runs from 2026-04-27 through 2026-05-07:

| Lane | Picks |
|---|---:|
| Live board | 8 |
| Shadow board | 36 |
| Abstain runs | 14 |

Unique live-board contracts:

- `IWM260429P00278000`
- `IWM260430P00275000`
- `PYPL260501C00049500`
- `NOW260501C00088000`
- `TLT260501C00085500`
- `NKE260501C00044000`
- `XLE260508P00059000`

Among the 23 reconstructed Tradier positions, only one exact match appears in the saved board history:

| Tradier Position | Board Match | Result |
|---|---|---:|
| `NKE260501C00044000` | Exact live-board pick on 2026-04-29 | +$37.00 inferred |

Near matches:

| Tradier Position | Saved Recommendation Nearby | Lane | Notes |
|---|---|---|---|
| `PYPL260501C00049000` | `PYPL260501C00049500` | Live | Nearby strike; trade reached +$133.50 observed but was not harvested. |
| `NFLX260508C00093000` | `NFLX260508C00091000` | Shadow | Nearby strike; flagged `high_extrinsic`; not live-board quality. |
| `QQQ260511P00687000` | `QQQ260511P00689000` | Shadow | Nearby strike; flagged `high_extrinsic` and `sector_cluster`; not live-board quality. |

This suggests three separate problems:

1. The live board was not producing many recommendations at all.
2. Some traded positions appear to have been shadow/discretionary or unauditable from the saved board history.
3. The few board-aligned trades did not have a systematic profit-harvest mechanism.

## Backtest Assumption Mismatch

The core backtest pricing function enters on the nearest trading day to Monday and exits on Friday:

- Entry: candidate ask price.
- Exit: Friday bid price from the historical options provider.
- Position size: budget-scaled contract count.
- No dynamic intraday stop-loss.
- No dynamic take-profit.
- No live order provenance requirement.

That means the backtest answers: "If we entered these candidates at the weekly open and exited at the weekly close, what happened?"

It does not answer: "If we take ad hoc midweek live/shadow/discretionary contracts, sometimes near expiration, sometimes hold past useful decay windows, and sometimes miss large intraperiod gains, what happens?"

The live Tradier book behaved much closer to the second question.

## Why The Picks Did Not Produce Big Winners

### 1. The strongest backtests were small-sample council filters

The attractive May 6 council 3-month result had only 3 to 6 trades depending on variant. A few winners can make that look excellent, but the confidence interval is huge.

### 2. The current broader stack was weak

The 200-trade current-stack run was only +0.98% net with negative Sharpe. That is not a strong expectancy base for live weekly options.

### 3. Live period was not actually backtested in the saved result ledger

The backtest trades on disk end with entries on 2026-04-06 and exits on 2026-04-10. Live broker snapshots started after that.

### 4. Shadow picks were tempting but dangerous

The shadow lane contained higher average expected edge than the live lane in saved history, but many were high-extrinsic candidates that council intentionally withheld. The live losses in `NFLX` and `QQQ` resemble this failure mode.

### 5. The system lacked path-aware exits

`PYPL260501C00049000` is the cleanest example. It reached +$133.50 observed P&L and ended near flat/down. The recommendation may have been directionally useful, but the execution policy failed to convert path into profit.

## Conclusion

The backtests do not prove that the live recommendations from 2026-04-14 to 2026-05-13 should have been big winners. The saved artifacts show:

- The broad current stack was fragile.
- The strong council variants were small-sample.
- The live period was mostly out-of-sample.
- The actual Tradier trades were not consistently exact live-board recommendations.
- Several live trades were held or selected in ways the backtest did not model.

The next research task should be a true prospective replay: every emitted live and shadow recommendation from 2026-04-14 onward, priced from historical options quotes at its actual emission time, then evaluated under fixed exits: same-day mark, +40% take-profit, -50% stop, next-day close, and Friday close. That will separate model alpha from execution damage.
