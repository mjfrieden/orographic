# Outcome Capture Reliability Milestone — 2026-08-09

## Outcome

Prospective labels now fail closed when a quote is late, missing, or stale. A current Tradier quote can no longer populate an expired historical outcome window.

## Capture contract

| Horizon | Maximum retrieval delay |
|---|---:|
| One hour | 15 minutes |
| End of day | 30 minutes |
| Next-day close | 30 minutes |
| Friday close | 30 minutes |

Tradier bid, ask, and trade timestamps are retained. A broker quote older than 15 minutes is rejected as `stale_quote_retryable`. A missing quote is `quote_missing_retryable`. Once the live tolerance expires, an unfilled window becomes `missed_live_window` and may only be repaired from immutable archived evidence.

## Leakage controls

- Capture policy v2 is stamped into every new recommendation outcome template.
- Legacy pending recommendations are never marked with current market data.
- Stored historical bid/ask marks can be backfilled only when their timestamps satisfy the same delay limit.
- Executable labels continue to use entry ask and exit bid or actual fills.
- Counterfactual challenger evidence requires strict executable outcome v2 and at least 95% valid Friday capture integrity.

## Operations and visibility

The new `Orographic Outcome Capture` GitHub workflow checks for eligible windows every 15 minutes across the US market-hours envelope. The marker avoids Tradier calls when no capture is eligible and avoids writing the ledger when nothing changes. Scan health and the workbench now report strict-policy picks, valid windows, retryable missing quotes, stale quotes, and missed windows.

This milestone changes research evidence collection only. It does not change signals, ranking, position sizing, order preview, or Tradier order submission.
