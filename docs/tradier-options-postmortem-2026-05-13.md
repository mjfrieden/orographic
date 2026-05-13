# Tradier Options Trade Post-Mortem

Generated: 2026-05-13  
Source: Cloudflare D1 `orographic-position-history`, 75 captured broker position snapshots from 2026-04-14T16:54:21Z through 2026-05-13T19:04:40Z.

## Scope And Caveats

This review reconstructs trade lifecycles from position snapshots, not from a complete Tradier fill/activity export. That means:

- Open/close timing is bounded by capture times, not exact order execution timestamps.
- Closed-trade P&L is inferred from the final observed mark before the position disappeared.
- Snapshot marks are quote-derived where available, often mid-price or last-price, not guaranteed realized exit fills.
- The analysis is still strong enough to diagnose process failures: holding behavior, sizing, expiration risk, side mix, and whether the book respected live-board discipline.

## Executive Diagnosis

The trading loss was not primarily a selection problem. It was a risk-process failure.

Across 23 reconstructed option positions, the book deployed about $3,917 of option premium and had a snapshot-inferred P&L of about -$1,835, a -46.8% return on premium. The account equity fell from a first configured snapshot of $2,031.62 on 2026-04-14 to $722.03 on 2026-05-13. The largest observed equity value was $2,292.29 on 2026-04-21; the lowest observed value was $678.03 on 2026-05-13.

The system repeatedly let weekly options decay into near-total losses. Seven positions were still present beyond the practical expiration-risk window. Several trades briefly worked, then were allowed to revert. The clearest examples were WFC 2026-04-24 calls, SCHW 2026-04-24 calls, DIA 2026-04-24 puts, SBUX 2026-05-08 calls, and PYPL 2026-05-01 calls. PYPL was especially instructive: it reached about +$133.50 open P&L but was last observed near flat-to-down.

## Book-Level Results

| Bucket | Trades | Premium | Inferred P&L | Win Rate |
|---|---:|---:|---:|---:|
| All reconstructed | 23 | $3,917 | -$1,835.00 | 39.1% |
| Closed/disappeared | 20 | $3,387 | -$1,729.50 | 40.0% |
| Still open at last capture | 3 | $530 | -$105.50 | 33.3% |
| Calls | 17 | $2,678 | -$1,169.50 | 35.3% |
| Puts | 6 | $1,239 | -$665.50 | 50.0% |

Worst losses by inferred final mark:

| Contract | Side | Cost | Last Value | Inferred P&L |
|---|---:|---:|---:|---:|
| SPY260507P00725000 | Put | $328 | $1.50 | -$326.50 |
| WFC260424C00081000 | Call | $326 | $2.00 | -$324.00 |
| QQQ260511P00687000 | Put | $279 | $1.00 | -$278.00 |
| NFLX260508C00093000 | Call | $222 | $3.00 | -$219.00 |
| SCHW260424C00092000 | Call | $169 | $2.00 | -$167.00 |

Best outcomes:

| Contract | Side | Cost | Last Value | Inferred P&L |
|---|---:|---:|---:|---:|
| ORCL260515C00192500 | Call | $247 | $317.50 | +$70.50 |
| NKE260501C00044000 | Call | $241 | $278.00 | +$37.00 |
| WMT260508P00130000 | Put | $113 | $138.50 | +$25.50 |
| XLE260417C00055500 | Call | $90 | $110.50 | +$20.50 |
| ABBV260417C00207500 | Call | $217 | $236.00 | +$19.00 |

## Trade Ledger

| Contract | Expiry | Side | Cost | Last Value | Inferred P&L | Max Observed P&L | Status |
|---|---|---:|---:|---:|---:|---:|---|
| ABBV260417C00207500 | 2026-04-17 | Call | $217 | $236.00 | +$19.00 | +$19.00 | Closed |
| NKE260417C00043000 | 2026-04-17 | Call | $98 | $115.50 | +$17.50 | +$17.50 | Closed |
| XLE260417C00055500 | 2026-04-17 | Call | $90 | $110.50 | +$20.50 | +$20.50 | Closed |
| TLT260417C00086500 | 2026-04-17 | Call | $49 | $7.50 | -$41.50 | -$7.50 | Closed |
| WFC260424C00081000 | 2026-04-24 | Call | $326 | $2.00 | -$324.00 | +$11.00 | Closed |
| SCHW260424C00092000 | 2026-04-24 | Call | $169 | $2.00 | -$167.00 | +$26.50 | Closed |
| DIA260424P00495000 | 2026-04-24 | Put | $435 | $332.50 | -$102.50 | +$37.50 | Closed |
| CMG260424C00035500 | 2026-04-24 | Call | $93 | $1.00 | -$92.00 | -$12.50 | Closed |
| NKE260501C00044000 | 2026-05-01 | Call | $241 | $278.00 | +$37.00 | +$37.00 | Closed |
| PYPL260501C00049000 | 2026-05-01 | Call | $158 | $154.50 | -$3.50 | +$133.50 | Closed |
| NFLX260508C00093000 | 2026-05-08 | Call | $222 | $3.00 | -$219.00 | -$18.00 | Closed |
| SLV260506C00067500 | 2026-05-06 | Call | $136 | $27.00 | -$109.00 | -$53.00 | Closed |
| SPY260507P00725000 | 2026-05-07 | Put | $328 | $1.50 | -$326.50 | -$37.50 | Closed |
| WMT260508P00130000 | 2026-05-08 | Put | $113 | $138.50 | +$25.50 | +$25.50 | Closed |
| CSCO260508C00091000 | 2026-05-08 | Call | $118 | $127.50 | +$9.50 | +$26.50 | Closed |
| SBUX260508C00106000 | 2026-05-08 | Call | $146 | $17.50 | -$128.50 | +$22.50 | Closed |
| WFC260508C00080000 | 2026-05-08 | Call | $85 | $2.00 | -$83.00 | -$69.50 | Closed |
| QQQ260511P00687000 | 2026-05-11 | Put | $279 | $1.00 | -$278.00 | -$6.50 | Closed |
| KO260515P00078000 | 2026-05-15 | Put | $48 | $54.50 | +$6.50 | +$6.50 | Closed |
| BAC260515P00050000 | 2026-05-15 | Put | $36 | $45.50 | +$9.50 | +$9.50 | Closed |
| SHOP260515C00103000 | 2026-05-15 | Call | $138 | $14.50 | -$123.50 | -$35.50 | Open |
| ORCL260515C00192500 | 2026-05-15 | Call | $247 | $317.50 | +$70.50 | +$70.50 | Open |
| USO260515C00146000 | 2026-05-15 | Call | $145 | $92.50 | -$52.50 | -$23.00 | Open |

## Main Failure Modes

### 1. Weekly Options Were Held Too Long

The median holding time for closed positions was roughly 50 hours, but seven positions remained through the practical expiration-danger zone. The damage came from convexity turning against the book: once a weekly option moved from "cheap asymmetric bet" to "decaying lottery ticket," losses became abrupt and near-total.

This was the dominant failure mode. The worst five trades were not modest losers. They were near-total premium losses.

### 2. Profit Capture Was Too Loose

Five trades showed positive observed P&L and later finished negative or near-zero:

| Contract | Max Observed P&L | Final Inferred P&L |
|---|---:|---:|
| PYPL260501C00049000 | +$133.50 | -$3.50 |
| DIA260424P00495000 | +$37.50 | -$102.50 |
| SCHW260424C00092000 | +$26.50 | -$167.00 |
| SBUX260508C00106000 | +$22.50 | -$128.50 |
| WFC260424C00081000 | +$11.00 | -$324.00 |

The book needed an automatic harvest rule. On short-dated long premium, a small win that is not harvested quickly is often just a future loss with better lighting.

### 3. Loss Stops Were Absent Or Too Patient

The worst trades were allowed to become 95-100% premium losses. A simple stop discipline would not need to be sophisticated to improve this history:

- Hard stop at -45% to -55% premium for any long option.
- Time stop before the final 24-36 hours unless the option is already in profit and liquid.
- No averaging down on weekly options without a new, explicit signal and capped total premium.

WFC 2026-04-24 is the warning label: the position was increased, then eventually marked near worthless.

### 4. Live-Board Discipline Was Inconsistent

Only a few later contracts were directly traceable to saved board diagnostics. Several executed symbols/strikes were not present in the saved live/shadow boards available in the repository. That does not prove they were discretionary, because earlier diagnostics are incomplete and the snapshots do not store order provenance. It does mean the system lacks an auditable order-to-signal ledger.

For a research-driven trading system, every broker order should carry a durable `run_generated_at_utc`, `lane`, `candidate_id`, expected edge, model mode, and risk flags. Without that, post-mortem quality degrades and discretionary drift can hide inside operational gaps.

### 5. Risk Concentration Was Too High For The Account

The largest observed option cost basis was $1,060 on 2026-05-07 against $1,123.01 account equity. That is effectively all-in long premium exposure. On 2026-04-23, there were five open positions with about $987 cost basis and $1,527.15 equity.

The account was trading like a research sandbox but taking live convexity losses. The appropriate live posture for this evidence base is much smaller: one contract, one open idea cluster, strict loss exits, and no expiry-week carry without an explicit exit clock.

## Trade-Specific Notes

### WFC260424C00081000

This was the most important process failure. Cost basis rose to $326, max observed P&L after adjustment was only about +$11, and the final observed mark was $2. The add-on increased risk without producing a defensible exit edge. This trade argues for banning add-ons in weekly options until the execution system can prove positive expectancy after slippage.

### SPY260507P00725000

This was nearly a full premium loss: $328 cost, $1.50 final observed value. The maximum observed P&L was still negative. This should have been stopped mechanically rather than held into expiration decay.

### QQQ260511P00687000

This was also nearly a full premium loss: $279 cost, $1 final observed value. Nearby saved diagnostics on 2026-05-07 show QQQ puts were shadow-only and flagged `high_extrinsic` plus `sector_cluster`. Even if the exact strike differed, the family resemblance is concerning: this was not a clean live-board-quality trade.

### PYPL260501C00049000

This was the clearest missed profit capture. The position was observed as high as +$133.50 and ended near flat-to-down. A take-profit rule would have paid for several small experimental losses.

### NFLX260508C00093000

This trade never showed a positive observed mark and ended nearly worthless. The saved 2026-05-04 diagnostic had this exact contract as shadow with a `high_extrinsic` flag. That should be treated as a non-live-quality trade unless explicitly overridden.

### ORCL260515C00192500

This was the best open mark at last capture, +$70.50. The lesson is not "hold winners indefinitely"; it is to protect the one winner currently paying for a damaged book. At minimum, the system should define a profit lock, partial harvest, or trailing exit before theta compresses the gain.

## Policy Changes Recommended

1. Enforce a broker-order ledger.

Every submitted order should persist: contract, side, quantity, limit, fill, signal run timestamp, board lane, rank, expected edge, risk flags, model modes, and whether the trade was manual or system-originated.

2. Freeze non-live-board discretionary trades.

Until the ledger exists, only execute contracts present on the live board. Shadow picks can be observed, not bought.

3. Cap total live premium at 20-25% of equity.

For the current account scale, that would have prevented the $1,060 premium exposure observed on 2026-05-07.

4. Cap single-trade premium at 5-8% of equity.

The $435 DIA put, $328 SPY put, $326 WFC call position, and $279 QQQ put were too large relative to account size.

5. Add hard exits.

Suggested starting rule set:

- Stop loss: exit at -50% premium.
- Time stop: exit by noon local time on the session before expiration unless the position is profitable and explicitly re-approved.
- Profit harvest: take profit at +35% to +50% on contracts with 5 DTE or less, or trail after +30%.
- No add-ons for weekly options.

6. Promote path/decay risk from advisory to binding.

If the path model or diagnostics show high decay risk, the trade should either be rejected or assigned a much shorter holding period.

7. Add a "graveyard monitor."

Any option marked below $0.10, or below 20% of original premium, should trigger an immediate exit decision. The current history contains too many positions that were allowed to become residue.

## Bottom Line

The research stack may have signal value, but the live trading process behaved like it had no immune system. The immediate repair is not a new model. It is order provenance, position-level risk limits, and mandatory exits. The strongest next experiment is to rerun this same blotter under mechanical rules: no shadow trades, no add-ons, -50% stop, +40% harvest, and forced exit before the final 24-36 hours. That counterfactual will tell us whether the strategy is broken or whether the execution layer simply let survivable trades become fatal ones.
