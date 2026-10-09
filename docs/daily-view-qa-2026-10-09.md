# Daily decision view: verification

Base: `mjfrieden/orographic` commit `02ba3f548c32febb9cf41cb384f6c1a86e4d5f62`.

## Scope

Visibility of status (H1) and error recovery (H9) on the existing Signal & Book view:

- The primary card differentiates loading, unavailable, no decision, current hold, stale/unknown freshness, and ready-to-preview states.
- The visible freshness warning uses the broker's existing `maxSignalAgeMinutes` configuration. Missing generation timestamps do not imply freshness. Freshness labels update once per minute without network requests.
- Recent recommendations are an initially collapsed, read-only disclosure using only `lane === "live"` rows already loaded from the bounded dashboard export. Filters use Chicago calendar dates, symbols, and mark status. Counts describe the loaded export, not the full ledger.
- Details identify quote-based outcome proxies, missing/pending marks, source availability, and the distinction from filled broker orders and account returns. No aggregate performance is promoted to live performance.
- Preview, confirmation, quantity sizing, server eligibility, model decisions, authentication, and routing contracts remain intact. Rebinding existing dynamic controls is idempotent.

## Automated checks

- `node --check web/app.js`: passed.
- `npm run test:js`: 175 tests passed, no failures. Includes the existing Tradier safeguards, order provenance, ticket concurrency, authentication, and DOM contract suites plus 41 new daily-view checks.
- New checks cover stale/current/empty/error/hold states, invalid timestamps, configured age windows, initial failure, recovery, independent ledger loading, repeated refresh coalescing, retained data warnings, all-lane exclusion, bounded export counts, Chicago date boundaries and DST, missing numeric marks, required window completeness, filter intersection/reset, selection, escaping, no order actions in history, show-more bounds, native keyboard control markup, and responsive/touch-target CSS contracts.
- UI behavior tests use a mocked DOM and Node VM. They do not prove browser layout, visual contrast, screen-reader behavior, or actual keyboard focus behavior.
- No Python/model/scan code changed. The Python engine suite and model/snapshot smoke checks were not run locally for this UI-only materialization; existing repository CI remains the full aggregate check.

## Browser QA still required before completion

A supported browser could reach the signed-out login page, but authenticated Signal & Book access was not available for this pass. No desktop/phone rendered UI approval is claimed.

Verify at 1440px, 720px, and 390px widths with authenticated read-only/test data:

1. Loading, initial snapshot error, valid hold, stale hold/candidate, invalid timestamp, and recovery. Check title, status line, and primary card agree.
2. Open/close Recent recommendations; combine and clear all filters; select rows with keyboard and touch; inspect partial, pending, missing, and complete marks. Confirm no horizontal clipping, legible labels, stable focus, and 44px control targets.
3. Check candidate arrows do not intercept book tabs or form fields; selection focus remains usable. Verify single/zero-candidate pager is hidden.
4. Refresh both successful and failed loads; confirm retained history is labeled. Recheck selecting and clearing after refresh.
5. Verify sizing and preview/cancel behavior without transmitting an order. Do not submit a real trade for UI QA.
6. Recheck reduced motion, focus visibility, the existing brand assets, and the protected heading/toolbar spacing contracts.

## Cost and network impact

No dependencies, paid services, new assets, endpoints, data fetches, workflow definitions, or schedules are added. History filters are client-side and use the existing export. A main-branch web change triggers the repository's existing CI and Pages deployment workflows; publication and deployment are separate from this local patch verification.
