# Extrinsic Veto Evaluation

Date: 2026-05-03

Primary walk-forward source: `output/alpha_experiment_results_2026-04-21_recovered_edge_6mo.json`
Variant evaluated: `council_cost_cap`

## Headline

The current `max_live_extrinsic_ratio = 0.96` veto looks directionally sensible, but the repo still cannot fully prove that the exact `0.96` line is optimal.

Why:

- In the recovered six-month deployable walk-forward variant, there were **zero executed trades** with `extrinsic_ratio >= 0.96`.
- The near-threshold bucket just below the veto, `0.85-0.95`, was materially weaker than lower-extrinsic buckets.
- The newest live abstain on `2026-05-03` was caused entirely by the extrinsic ceiling, so the rule is active in production behavior now.

## Walk-Forward Read

Variant summary:

- Trades: `34`
- Win rate: `61.8%`
- Total P&L: `+$1,794.00`
- Sharpe: `2.84`
- Max drawdown: `-77.8%`

### P&L by extrinsic bucket

| Bucket | Trades | Win Rate | Total P&L | Avg P&L % | Avg Extrinsic |
| --- | ---: | ---: | ---: | ---: | ---: |
| <0.50 | 11 | 81.8% | +$671.00 | 36.6% | 0.397 |
| 0.50-0.69 | 12 | 50.0% | +$710.00 | 24.3% | 0.620 |
| 0.70-0.84 | 6 | 50.0% | +$393.00 | 29.7% | 0.782 |
| 0.85-0.95 | 5 | 60.0% | +$20.00 | 13.4% | 0.902 |
| >=0.96 | 0 | — | — | — | — |

Interpretation:

- Buckets below `0.85` generated the bulk of the recovered edge.
- The `0.85-0.95` bucket was still slightly positive, but its edge was much thinner than the lower-extrinsic buckets.
- The data set contains **no executed trades** above the current `0.96` veto, so the exact cutoff still needs shadow observation rather than claiming a full proof.

### Near-threshold winners

- `2026-02-02` VZ CALL `extrinsic=0.8736` `pnl=+$321.00` `pnl_pct=123.0%`
- `2026-04-06` DIS CALL `extrinsic=0.8974` `pnl=+$57.00` `pnl_pct=20.9%`
- `2025-11-10` BAC PUT `extrinsic=0.9111` `pnl=+$29.00` `pnl_pct=32.2%`

### Near-threshold losers

- `2026-01-05` DIA PUT `extrinsic=0.9418` `pnl=$-267.00` `pnl_pct=-67.6%`
- `2025-12-22` CSCO CALL `extrinsic=0.8854` `pnl=$-120.00` `pnl_pct=-41.7%`

## Live Abstain Read

Recent board-history summary:

- Total tracked runs: `17`
- Abstain runs: `10`
- Legacy `unknown` abstain reasons mostly come from runs recorded before the new structured abstain audit existed.
- Abstain reasons seen:
- `unknown`: 9
- `extrinsic_limit`: 1

Latest scan audit from `web/data/latest_run.json`:

- Primary reason: `extrinsic_limit`
- Label: `All candidates failed the extrinsic ceiling.`
- Candidate count: `1`
- Core filter passes: `0`
- Extrinsic-only failures: `1`
- Blocked symbols: `BAC`

This means the current live abstain was not driven by side balance, concentration, or low Forge score. It was a pure extrinsic veto on the final surviving contract.

## Verdict

The evidence supports **keeping the high-extrinsic veto for now**, but treating it as a monitored risk rule rather than a mathematically settled optimum.

What the evidence supports:

- High extrinsic is associated with weaker historical trade quality as you approach the veto line.
- The current live system is correctly flagging fully extrinsic weekly options as dangerous.

What the evidence does **not** yet support:

- A claim that `0.96` is definitely the best threshold.
- A claim that every `>= 0.96` candidate should always be skipped.

## Best next measurement

To decide whether the veto is too strict or just right, track extrinsic-veto shadow holdouts prospectively:

1. Count all abstains where `primary_reason = extrinsic_limit`.
2. Log the vetoed contract, `extrinsic_ratio`, `expected_edge_after_friction_pct`, and later realized P&L.
3. Compare those holdouts against lower-extrinsic live picks over at least 20 to 30 veto events.

Until that holdout ledger exists, the current conclusion is:

`High extrinsic is a defensible abstain reason, and the available evidence leans in favor of the veto, but the exact threshold is still under evaluation.`
