# Market Shock Overlay

Generated: 2026-06-10

## Goal

Orographic should not treat all markets as equally tradeable. The new market-shock overlay creates explicit labels and Council policies for external conditions that can dominate short-dated option outcomes.

This is separate from alpha scoring. Its first job is to decide whether the platform should trade, tighten, observe, or abstain.

## Labels

| Label | Typical Conditions | Strategy |
| --- | --- | --- |
| `constructive_risk_on` | Positive index trend, contained VIX, risk-on headlines | Allow standard Council selection with normal extrinsic discipline. |
| `melt_up_fomo` | Strong risk-on tape with crowding | Allow calls, but tighten score and extrinsic gates because weekly calls can be overpriced. |
| `orderly_risk_off` | Weak tape without full volatility break | Tighten live promotion; puts still need post-friction edge. |
| `ai_tech_unwind` | QQQ and semiconductors underperform SPY sharply | Penalize call promotion and require low-extrinsic, high-confidence setups. |
| `geopolitical_macro_risk_off` | Macro, war, commodity, or risk-off event features dominate | Prefer observe/put-side candidates only when payoff evidence is strong. |
| `extreme_vol_deleveraging` | VIX spike, index selloff, or Scout `extreme_vol` regime | Hard abstain from live short-dated single-name options; keep shadow outcomes. |
| `normal_crosscurrents` | No strong shock signal | Use standard gates. |

## Implementation

- `engine/orographic/market_shock.py` classifies cross-asset and event-feature inputs.
- `Council` receives the policy and records it in `summary.market_shock`.
- Active mode can raise effective live-score gates, tighten max extrinsic, and force global abstention.
- Pipeline snapshots now include top-level `market_shock` and `diagnostics.market_shock` sections.

## Research Use

Every future run should preserve this label beside emitted and rejected candidates. That lets us evaluate:

- whether shock abstention avoided losses,
- whether it missed rare good trades,
- which labels need separate payoff/ranker models,
- whether good-market labels like `melt_up_fomo` require different exits than calm risk-on markets.

Promotion rule: do not loosen a shock label until it has at least 30 to 50 prospective observations with quote-verified outcomes.
