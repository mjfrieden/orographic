# Atmosphere event observation extension v1

This is a local, additive research foundation intended for Orographic base
`4959fa459e471134ad0fed4daf34d23e720a3aee`. It does not modify the execution
mart's ten tables, production imports, rankings, risk gates, provider collectors,
or live state. Apply the additive patch to a verified copy of that base.

## Existing source contract, not a duplicate collector

The legacy Cirrus weekly pipeline already records candidate `earnings_date` and its Tier-1 calendar reader
consumes `events[].release_at`, `covered_from`, `covered_through`, and
`source_urls`. The adapters consume these existing JSON artifacts offline.
A Tier-1 `release_at` is treated as a **scheduled** release, never proof that an
actual publication or value was available. Candidate `earnings_date` remains a
date, never midnight. Neither scan time nor `generated_at` proves first-public,
first-seen, or available-at. No timestamp is generated from the current clock,
file metadata, archive filename, or a ticker.

The earnings adapter accepts a `candidate_pointer` into the full original JSON
artifact, defaulting to the root array. It hashes and retains the **full original
bytes**, preserving the source pointer and original candidate payload. The
Tier-1 adapter hashes and retains the full calendar. A missing earnings date
creates no event: this is not evidence that no event existed. Coverage windows,
source URLs and provider errors are retained in the artifact, not promoted to
coverage attestation. The pinned Cirrus source contract was verified through the GitHub connector at
`92870db4ff114a5b694d2f2d68d5bb9c06d81eb9`: `pipeline.py` records
`earnings_date` in candidate dictionaries and exposes `/ranked_ideas` and
`/shadow_ideas` arrays in its weekly output. These are **legacy weekly** source
contracts; no claim is made that current tournament models consume these fields.

Verified source links:
- [Candidate earnings field and weekly output](https://github.com/mjfrieden/Cirrus/blob/92870db4ff114a5b694d2f2d68d5bb9c06d81eb9/src/cirrus_picker/pipeline.py)
- [Tier-1 parser](https://github.com/mjfrieden/Cirrus/blob/92870db4ff114a5b694d2f2d68d5bb9c06d81eb9/src/cirrus_picker/risk_policy.py)
- [Calendar source](https://github.com/mjfrieden/Cirrus/blob/92870db4ff114a5b694d2f2d68d5bb9c06d81eb9/config/tier1_events.json)

The local verification imported all 23 events from the exact calendar blob
`8cbd62af6da56907a711a96d717d03a4b22750d2` (SHA256
`721655fbbd8460e6bfe1ff50c784e537f9c560fb914cc3e6126b7b05f1e1e21f`).
All 23 retained source event IDs and schedule times. All 23 have unavailable
first-public/first-seen/available times, giving zero qualifying as-of rows. This
is a source-shape smoke test, not validation that the calendar's claimed release
dates are historically correct. No historical weekly earnings artifact was
imported; its contract was source-verified and its adapter tested synthetically.

## Storage and public API

`engine/orographic/event_observations.py` uses Python's standard library only.
Use a dedicated SQLite observation database. The four tables are separately
versioned: `event_observation_versions`, `event_source_registry`,
`event_source_artifacts`, and `event_observation_vintages`. There is no automatic
migration of an incompatible version and no automatic import into the mart.

- `initialize(connection)` creates this extension and checks the fixed registry.
- `ingest_cirrus(connection, raw_bytes, kind='earnings', candidate_pointer='/...')`
  or `kind='tier1'` retains existing evidence without inventing vintage metadata.
- `ingest(connection, raw_bytes, source_id=..., records=[(pointer, record)],
  provenance_by_pointer={...})` takes explicit normalized records, verifies each
  record equals the JSON at its retained pointer, and stores all records atomically.
- `as_of(connection, cutoff, issuer_id=None, allow_fixtures=False)` returns only
  qualifying event research inputs. The cutoff must have time and timezone.
- `readiness(connection)` always reports `backtest_ready: false` and
  `execution_ready: false`, regardless of observation count.

Missing metadata is null plus a reason. Missing values are null, never zero.
Actual, expected, and prior are independent `{value: number, unit: string}`
quantities. Units are not inferred, converted, or mixed into a surprise score.
Raw numeric/string provider values remain in the original payload even if the
canonical adapter cannot interpret them. Errors and cancelled/withdrawn statuses
cannot qualify. Malformed JSON, duplicate JSON keys, nonfinite quantities,
malformed timestamps, invalid pointers, source-attribution conflicts, and batch
validation errors fail closed without partial observation writes.

## Point-in-time boundary

A prospective schedule may describe a future event while being known at the
trade cutoff. Scheduled time is therefore distinct from publication/first-seen/
availability time and from actual release time. All supplied timestamps require
per-field evidence references in `provenance.field_evidence`. Real observations
also require `review_status: reviewed` and a nonempty `review_reference`. These
are explicit review inputs, not automatically proven merely by filling fields;
the caller must verify references against retained evidence before supplying
review status. The module does not authenticate attestations or license rights.

`first_public_at`, `first_seen_at`, and `available_at` must all be known, precise,
chronologically consistent with availability, and no later than the cutoff.
Records without a stable event ID, or earnings without a stable issuer ID, do
not join. Issuer identity is never inferred from ticker text; ticker changes
remain descriptive. Event IDs should encode stable issuer/series and reporting
period, never the scheduled timestamp, because schedules can change.

Vintages are append-only. The latest available vintage **at the cutoff** wins,
not the latest calendar seen today. Different payloads or provenance at an
identical source/event/issuer/availability instant quarantine that tie; the
module does not silently overwrite. An invalid newer known vintage does not
fall back to an older valid one. Unknown-availability observations remain
unusable and do not claim known ordering. Independent sources remain separate;
there is no arbitrary source-precedence policy. Actual values require an
attested release timestamp that is no later than availability and the cutoff.
Fixtures are excluded unless explicitly enabled, and enabling them never
changes readiness.

## Source-use registry

- Existing Cirrus earnings and curated calendar: review required. Retention of
  already supplied local evidence does not establish historical/reuse rights.
- BLS public archive: scoped to BLS-authored public-domain documents only;
  explicit document/provenance review remains required. No BLS API calls or
  archive downloads are implemented. API retrieval can lag the publication by
  about a day and must not be substituted for the actual public release time.
- SEC public filing: review required; no API key needed for public documents,
  but public access does not establish every reuse right or event timestamp.
- FRED/ALFRED: explicitly blocked pending permission review. No ingestion.
- Synthetic fixtures: tests only, excluded by default.

This module performs no provider requests, downloads, account creation, key
handling, purchases, remote writes, or schedule creation.

## Remaining work before a reliable backtest

1. Import retained legacy weekly full-source artifacts using the verified
   `/ranked_ideas` or `/shadow_ideas` pointers. Run the adapter on those artifacts and
   expose the real missingness counts without changing readiness.
2. Review source-specific use permissions and retain the review reference.
3. Obtain prospective ingestion witnesses or authentic publication archives for
   first-public/first-seen/available times. Retain every schedule revision rather
   than backfilling today's calendar across past trades. Current records alone
   cannot recover historical knowledge.
4. Add separately reviewed stable issuer/security identifiers, ticker validity
   intervals, splits/dividends/corporate actions, and adjusted/unadjusted price
   semantics. This v1 does **not** supply a corporate-action table or manufacture
   action completeness.
5. Add metric-specific macro revision/release conventions, consensus vintages,
   unit definitions, calendar coverage, cancellations, and missingness analysis.
6. Wire an explicit offline evaluator to these guarded as-of inputs alongside
   validated decisions, labels, quotes, fills, and model-fit cutoffs. Test
   cross-validation and leakage at the evaluator boundary. Do not change the
   execution mart contract or enable live ranking as a side effect.

Focused tests are synthetic and prove guard behavior, not historical coverage,
source licensing, economic predictive value, or production readiness.
