# Orographic Call vs Put Technical Analysis Report

Date: 2026-04-15

## Executive Take

Orographic is not structurally incapable of recommending puts, but the current live workflow is effectively call-only under today's tape.

The key distinction is:

- the model is not hardcoded to calls
- the workflow is hard-gated by market regime
- today's regime is `risk_on`, which explicitly deletes every bearish signal before contract selection begins

So your anecdotal read is directionally right for the current environment, even though the broader research stack has produced puts historically.

## Bottom Line

1. Orographic can recommend puts.
2. Orographic is currently biased toward calls because the live regime is strongly `risk_on`.
3. The present system will usually not surface puts until the regime engine flips to `neutral` or `risk_off`.
4. The bearish path is weaker than the bullish path because the model is trained as a bull-probability classifier, not as a symmetric call/put payoff model.

## Evidence From The Current Live Snapshot

Source: `web/data/latest_run.json`

- Live regime: `risk_on`
- Regime bias: `0.5198`
- Top scout signals shown in the snapshot: `8 calls`, `0 puts`
- Forge candidates shown in the snapshot: `10 calls`, `0 puts`
- Live board: `3 calls`, `0 puts`
- Shadow board: `2 calls`, `0 puts`

That is a full-stack directional collapse into calls for the current run.

There is another important technical-analysis wrinkle in the same snapshot: the live calls are not classic breakout calls. Of the top 8 scout names:

- `7/8` had negative 5-day momentum
- `7/8` had negative 20-day momentum
- `5/8` had RSI below `40`

That means the current model is behaving more like a bullish mean-reversion engine inside a risk-on macro regime than a pure momentum-chasing call engine.

## Where The Bias Enters

### 1. Scout is trained as a bullish classifier

In `engine/train_scout_model.py`, the training label is whether the stock's forward 5-day return is positive:

- `label = 1` if `fwd_5d_return > 0`
- `label = 0` otherwise

That means the model is fundamentally estimating bull probability, not explicitly estimating:

- downside move probability
- put payoff probability
- probability of beating option breakeven

In live inference, that probability is mapped into a signed score, and the sign determines direction:

- positive score -> `call`
- negative score -> `put`

This is important because the short side is currently just the inverse of a bullish stock forecast, not a dedicated bearish-options forecast.

## 2. Regime logic hard-vetoes one side

This is the biggest reason today's scan is all calls.

In `engine/orographic/scout.py`, regime classification sets:

- `risk_on` when bias `>= 0.18`
- `risk_off` when bias `<= -0.18`
- `extreme_vol` when VIX is dislocated

Then the live signal builder applies a hard veto:

- in `risk_on`, every `put` signal is dropped
- in `risk_off`, every `call` signal is dropped
- in `extreme_vol`, the signal is dropped entirely

So if the market regime says `risk_on`, the system does not merely rank puts lower. It removes them from the pipeline.

The replay path uses the same veto logic in `engine/backtest/replay.py`, so this directional gating is embedded in both live and historical workflow logic.

## 3. Forge never reopens the directional question

`engine/orographic/forge.py` only looks at the option chain for the direction already chosen by Scout:

- if Scout says `call`, Forge scans calls
- if Scout says `put`, Forge scans puts

Forge does not compare both sides and decide which structure is better. It only optimizes the side it inherited.

That means any directional imbalance created upstream survives into contract ranking almost unchanged.

## 4. Council can trim concentration, but it cannot manufacture puts

`engine/orographic/council.py` has a side-balance guard, but it only acts on the candidate set it receives.

If no puts make it through Scout and Forge, Council cannot create them. It can only:

- keep the existing call-heavy board
- demote excess calls to shadow
- abstain if too little remains

This is why side-balance logic is not enough to solve a regime-driven directional skew.

## Historical Evidence That Orographic Can Recommend Puts

The historical artifacts show puts are not dead code.

### Walk-forward validation

Source: `web/data/walk_forward_results.json`

- Total trades: `27`
- Calls: `21`
- Puts: `6`
- Put win rate: `66.67%`
- Put P&L: `+$208.23`
- Call P&L: `+$1,774.94`

So the selected walk-forward variant did produce puts, and those puts were not obviously broken.

### Broader alpha experiment

Source: `docs/alpha_experiment_results.json`

Baseline variant trade mix:

- Calls: `683`
- Puts: `481`

That is still call-heavy, but it is clearly not call-only.

The selected production-style variant (`council_cost_cap_symbol_priors`) also kept some short-side exposure:

- Calls: `21`
- Puts: `6`

Given the replay veto logic, those puts could only have come from weeks where the regime was not `risk_on`.

## Why The Current Live Book Still Feels Like "Only Calls"

Three things are stacking together:

### 1. Macro context dominates the trained model

I inspected the trained LightGBM feature importances. The top three features were:

1. `spy_rv20`
2. `spy_mom_5d`
3. `spy_mom_20d`

That tells us the model is leaning heavily on broad market context, not just single-name chart structure.

### 2. The regime filter then amplifies that macro bias

Once the market is tagged `risk_on`, the workflow becomes one-sided by design:

- bearish signals are removed
- bullish signals get a score bonus

So the regime layer is not just a filter. It is an amplifier.

### 3. The model appears to like rebound setups during risk-on tape

The current top calls are mostly oversold names with negative recent momentum and weak RSI. That suggests the live stack is often reading:

- broad tape still constructive
- local name oversold
- buy upside mean reversion

That combination will naturally produce lots of calls during any broad market rebound phase.

## Can Orographic Recommend Puts If Conditions Change?

Yes, but not instantly.

Under the current design, puts should appear when one of these happens:

1. The regime moves from `risk_on` to `neutral`.
2. The regime moves from `risk_on` to `risk_off`.
3. The raw model score for a name turns negative during a `neutral` regime.

In a `risk_off` regime, the system should actually become put-only, because the same veto logic flips to the other side.

The practical issue is timing. Because the workflow waits for the regime gate to change, bearish recommendations may arrive later than a desk would want during an abrupt reversal.

## Trading-Desk Assessment

From a quant discretionary perspective, Orographic today is best described as:

- a short-horizon bull-probability model
- wrapped in a top-down regime filter
- expressed through weekly options with tight feasibility rules

That is not the same thing as a balanced long-gamma directional engine.

If the market starts rolling over, Orographic can get to puts, but the sequence is currently:

1. macro regime must stop being `risk_on`
2. negative raw scores must survive the regime gate
3. put chains must pass Forge liquidity and pricing filters

So the system has bearish capacity, but its transition speed into bearish recommendations is gated.

## What I Would Watch Next

If we want confidence that Orographic will respond to a market turn fast enough, the most important diagnostics to add are:

1. Daily pre-veto direction counts:
   calls vs puts before regime gating.
2. Daily veto counts:
   how many puts were removed by `risk_on`, and how many calls were removed by `risk_off`.
3. Live side-mix time series:
   scout, forge, and council side distribution by day.
4. Regime transition lag:
   how many trading days after SPY weakness the model begins surfacing puts.
5. Side-segmented performance:
   call win rate / P&L vs put win rate / P&L by regime.

## Recommendation

My conclusion is:

- your observation is correct for the current live environment
- the reason is mostly workflow gating, not a hardcoded inability to trade puts
- Orographic can recommend puts, but it will usually do so only after the regime engine permits them

If we want a more symmetric engine, the next upgrade should be to move from "bull probability with signed inversion" toward one of these:

- separate bull and bear models
- direct option payoff targets
- softer regime penalties instead of hard one-side vetoes

That would make the system more responsive to tape transitions and reduce the risk that it stays call-heavy too long during a regime change.
