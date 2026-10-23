# Tradier order identifier compatibility

Tradier's October 23, 2026 compatibility notice says numeric order identifiers
may exceed 32-bit integers as early as November 25, 2026. Its example is
`91759371479`. It recommends reviewing integrations within 30 days and treating
identifiers as strings for future compatibility; string IDs are not imminent.

## Contract and implementation

- Order IDs exposed by the order placement, account and status APIs are opaque
  strings (or `null` when absent/invalid). Existing integer IDs remain usable,
  but consumers must not require a JSON number or perform ID arithmetic.
- `functions/_lib/order_ids.js` preserves integer ID tokens from raw JSON before
  JavaScript can round them. It handles nested IDs and escaped JSON field names,
  preserves quoted IDs including leading zeros, and leaves prices/quantities
  numeric. Non-integer numeric ID spellings (negative, decimal or exponent) are
  rejected before rounding/underflow can turn them into a different ID. It uses
  no runtime-specific JSON reviver extensions or dependencies.
- `functions/_lib/tradier.js` applies this parser only to account order endpoints,
  normalizes both individual and listed orders, encodes lookup IDs as a single
  URL path segment, and compares normalized IDs exactly. Pure dot-segment IDs
  are retained but not looked up, because URL parsers normalize those segments.
- If confirmation is unavailable or mismatched after submission, the accepted
  ID and existing do-not-resubmit behavior are retained. Missing/invalid IDs
  remain an unknown outcome. Already-unsafe JavaScript numbers cannot be
  repaired and are never used as lookup IDs.
- `functions/_lib/order_ledger.js` keeps `broker_order_id` as TEXT, binds the
  string unchanged, and preserves IDs when reading historical event JSON.
  This cannot reconstruct digits lost before an old record was written.

## Audited surfaces and limits

The declared D1 schema already uses TEXT for `broker_order_id`; no database
migration is required by this change. The deployed schema was not inspected or
changed. `order_json` and `payload_json` remain JSON text with string IDs in the
actual broker flow.

The UI receives and displays IDs without numeric conversion. Its existing
single-use submission ticket is unchanged and covered with long/string IDs.
Python's broker code in `engine/orographic/positions.py` reads positions and
quotes, not orders. No Python order-ID consumer or 32-bit order-ID schema was
found in the engine/scripts audit, so no Python change is needed.

There is no broker-order cancellation endpoint or client cancellation workflow
in this repository. The ticket's Cancel button only closes the modal, and
closing a position submits a new `sell_to_close` order by option symbol.
Cancellation was therefore not implemented or exercised. Any future cancel
endpoint must accept the same opaque-string contract and encode IDs safely.

## Offline verification

Run `npm run test:js` for the JavaScript suite. Focused regressions are in
`tests/js/tradier_order_ids.test.js` and `tests/js/order_ticket_safeguards.test.js`.
They use mocked fetch/D1, never real broker requests or trades. Coverage includes
existing IDs, signed/unsigned 32-bit boundaries, Tradier's example, JavaScript's
safe-integer boundary, `9223372036854775807`, leading-zero and opaque strings,
raw JSON, order lookup, API receipts, account/status lists, D1 binding/JSON reads,
malformed inputs, and failure/rejection single-submission safeguards.

## Activation

1. Apply this change on a clean branch based on the reviewed repository version,
   then run `npm run test:js` and the repository's normal CI checks.
2. Review the string-ID API contract with any external consumers. No new
   credentials, configuration, feature flags or database migration are needed.
3. Merge and deploy only with approval. The existing `deploy_pages.yml` runs
   automatically for a main-branch push changing `functions/**`; merging this
   patch will therefore trigger a production Pages deployment when its existing
   credentials are available. Do not treat merge and activation as separate
   safety boundaries.
4. Verify the successful Pages deployment is associated with the reviewed
   commit. Use authorized read-only account/status checks to inspect returned
   ID strings; do not place or cancel an order merely to test compatibility.

Local regression results are not a claim that production has been updated.
