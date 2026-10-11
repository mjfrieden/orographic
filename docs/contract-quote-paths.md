# Offline contract/time quote-path adapter (inactive implementation v2)

This additive module does not register a runtime consumer, change existing tables,
write labels, call providers, load models, place orders, or publish anything.
It produces quote-replay witnesses and coverage/exclusions, not broker fills,
realized P&L, promotion evidence, or a claim that missing labels were recovered.

## Why a separate adapter

Source inspected at Orographic `4959fa459e471134ad0fed4daf34d23e720a3aee`:
`shared_research_mart.py` gives canonical shared-market quotes a null
recommendation key. `shared_mart_consumers.py` joins exit replay on that key.
The existing mart and its consumer are deliberately untouched by this extension.
Its permissive midpoint fallback is not inherited here.

## Input and integrity contract

`retained_json(raw_bytes, source_namespace=..., pointer=..., field_map=...)`
accepts an object or object array selected by an RFC6901 pointer. It retains the
exact artifact SHA-256, row pointer, original-row SHA-256, normalized-row SHA-256,
explicit direct field map and an observation ID scoped to source namespace,
artifact hash and pointer. Duplicate JSON keys/nonfinite values are rejected.
Direct field maps copy recorded values only, never fabricate a timestamp or a
new security identity. Existing fields cannot be overwritten. Normalization is
not external verification.

`join_quote_paths(recommendations, quotes, policy=ReplayPolicy(...),
as_of_utc=..., artifacts={sha256: raw_bytes})` requires actual retained source
bytes. It verifies each artifact hash, resolves the recorded pointer and repeats
the recorded field mapping; edited lineage cannot substitute a different source.
Keep this byte registry private when sources are private. Implementation v2
hashes and decodes each referenced artifact once per join call and reconstructs
each distinct input row once. Exact contract identities index sorted availability
times. Binary-search windows, precomputed same-time markets and prefix conflict
counts preserve v1's strict selection/exclusion semantics without scanning every
contract quote for every recommendation. All caches expire with the call.

The report schema and hashes intentionally remain v1-compatible: implementation
v2 changes computational cost and resource bounds, not quote/label semantics.
Optional operation_counts receives deterministic diagnostics only after success;
it is excluded from the report and report hash.

ReplayLimits defaults cap 32 supplied artifacts, 16 MB per artifact, 64 MB total
source bytes, 100,000 combined input rows, 128 KB per normalized row, 128 MB total
normalized rows, 1,000,000 decoded JSON nodes per artifact, JSON/pointer depth 64,
and 200,000 cumulative reconstruction rows. Input iterators stop at the first row
beyond the bound. Decoded traversal uses depth-bounded iterators. Limit failures
raise ReplayLimitError and return no partial report. The sample CLI reads at most
2 MB plus one sentinel byte even if a source grows after it is opened. These are
bounded research defaults, not evidence that a six-million-row mart is supported.

Required common fields:
- source_namespace: the exact stable collection/archive namespace, not a ticker
- security_id: exact source-scoped security identity; not necessarily an opaque ID
- contract_id, contract_namespace: exact contract identity and declared namespace
- deliverable_id, multiplier, currency, expires_at_utc
- identity_evidence: review_status='verified' and a retained reference supporting
  the security/contract/deliverable mapping; this declaration must come from an
  independently reviewed input, not an inference manufactured for this join
- position_kind='single_long_option'

The join requires the entire identity tuple to match, with expiration instants
normalized to UTC. Ticker similarity, unrelated calls/puts, spreads, settlement
proxies, unverified multipliers/deliverables and ambiguous identities never match.

Recommendations additionally require recommendation_id, decision_at_utc and
available_at_utc; availability must be no later than the decision. Quotes require
source_record_id, finite positive bid/ask (ask >= bid), capture_at_utc,
available_at_utc, bid_observed_at_utc and ask_observed_at_utc. Provider clocks are
separate from capture; last-trade times do not substitute for either quote side.
All timestamps require a timezone. No current clock/file time is used as a
historical witness.

## Declared policy and exclusions

ReplayPolicy pins policy ID/version, entry window, fixed holding horizon, exit
window, maximum provider quote age, and maximum capture-to-availability delay.
Its full parameters and SHA-256 are in each report. v1 supports only the first
available ask in the entry window, and the first available bid at/after the fixed
decision-relative horizon. It does not choose the most profitable exit.
Windows include both endpoints. The fixed horizon must follow the entry window.
Both sides must remain fresh at availability; provider <= capture <= available.
Expiry must cover the complete exit window.

The label is unavailable until the entire declared exit window has matured at
the explicit audit cutoff. Missing paths keep entry/exit/label fields null. This
is a conservative completion label for a fixed-window quote replay, not an
actual historical broker fill timestamp. Fees, size/depth, fills, slippage and
execution likelihood are outside v1; no returns are computed.

Only source observations available at/before as_of can create duplicate conflicts.
Future rows cannot poison an earlier eligible path. Reused source IDs with changed
retained content, conflicting interpretations of one source observation, and
conflicting earliest-time bid/ask pairs exclude rather than silently resolve.
Equal-price earliest-time ties select a canonical-row-order witness. Exact copies
are harmless; all original source bytes remain available for audit.

Reports preserve policy, recommendation lineage, eligible quote witnesses,
per-quote rejection lineage, artifact hashes, coverage, and their own SHA-256.
Exclusion counts report the first failed guard, not every missing field. Therefore
`missing_exact_contract_identity` does not by itself mean the raw provider lacks
all identity evidence; normalization and independent identity review may suffice.

## Verification and honest limits

Run focused tests without third-party dependencies:

    python -m unittest discover -s engine/tests -p "test_contract_quote_paths*.py" -v

The sample checker accepts explicit source hash, namespace, one or more pointers,
and cutoff; it reads at most 2 MB and prints aggregate quote eligibility only:

    PYTHONPATH=. python scripts/check_contract_quote_sample.py --input sample.json \
      --sha256 VERIFIED_SHA256 --source-namespace reviewed-archive \
      --pointer /quotes --as-of 2026-10-11T00:00:00Z

Local development also extracted the unchanged literal Cirrus fixture rows from
upstream test_shared_research_mart.py without importing pandas/provider code:
one recommendation and one quote were correctly excluded for incomplete strict
identity. The initial v1 run could not run Parquet tests without pyarrow. A separate native
integration worker subsequently ran the unchanged pinned shared_research_mart
suite in its isolated PyArrow environment: six passed and two DuckDB-dependent
tests skipped. This v2 task did not install dependencies or modify that environment.
The complete repository suite remains unrun.

Implementation v2 has 33 local tests, including exact frozen-v1 report hashes for
20 guard scenarios and operation counts at 64, 256 and 1,024 recommendations.
Each scaling case performs exactly two artifact decodes, one validation and
reconstruction per input row, and logarithmically bounded timestamp comparisons.
A separate 300-case seeded differential check also matched complete v1 reports.
This verifies bounded mechanics, not authentic missing-outcome recovery or
full-mart performance.

Bounded authentic checks inspected 13 public retained Orographic Forge candidates
and 3 private retained Cirrus entry quotes. All remained excluded by strict input
identity guards; none establishes a mature joined outcome. Those checks were
quote eligibility checks, not a join of the 5,079-recommendation snapshot and not
a test that its 1,552 missing outcomes can be recovered. No private raw data is
included in this patch.

A separate assessment found the 3 Cirrus quotes already contain exact option
symbol/root, underlying, strike, expiry, side, contract_size, and provider bid/ask
timestamps. Each sample had consistent OCC-style fields, contract_size=100 and
positive uncrossed bid/ask. A follow-on reviewed provider/OCC normalizer should
reuse these exact identifiers instead of demanding arbitrary opaque IDs. Neither
a standard-looking option symbol nor contract_size=100 alone verifies corporate
action/adjusted deliverable status. The strict v1 guard is not relaxed by this
assessment. Availability semantics, identity review and complete retained paths
still need evidence before any recommendation outcomes can qualify.


## Optional coverage diagnostics sidecar

`coverage_diagnostics={}` is a separate optional output dictionary following
`operation_counts`. It is replaced only after a successful join. Its independent
`atmosphere-quote-replay-coverage-v1` schema/hash links the unchanged frozen-v1
replay report hash, policy hash and normalized UTC audit cutoff. It does not add
fields to or change the returned replay report, quote selection or exclusions.

Cohorts count recommendation input rows (including duplicates), eligible quote
replays, excluded rows and first blockers by verified retained source namespace
and UTC decision date. Source verification means exact retained-byte lineage
restoration, not independent endorsement of provider facts. Date grouping uses
only an explicit, timezone-aware decision timestamp from a lineage-verified row.
Malformed/missing dates use a null date with `missing_or_invalid_date`; unverified
lineage uses null source and date with `unverified_source`. These typed buckets
cannot collide with a real namespace of the same name. A valid decision date can
still group a row whose availability clock or another guard excludes replay.
Cohorts are sorted by canonical JSON of the `(namespace, date)` pair. Counts sum
to the global recommendation totals; excluded counts report only the first
failed guard, not a complete missing-evidence inventory. Recommendation duplicates
are not deduplicated or presented as independent observations.

Quote exclusions remain global: a rejected quote is not attributed to a
recommendation cohort merely because a ticker, claimed namespace or date matches.
Reason-to-next-evidence guidance names retained bytes/lineage, independently
reviewed identity/deliverables, recorded clocks, entry/exit quote evidence or
window maturity to review. Unknown future reasons explicitly require review.
Guidance cannot establish recoverability, waive a guard or promise eligibility.
This is quote-replay-only coverage, not returns, fills or backtest readiness.

The sidecar consumes the existing recommendation pass and cached lineage verdict;
it adds no artifact decoding, normalization, quote scanning or window queries.
At most one cohort is created per bounded input recommendation. Existing row,
byte and reconstruction bounds apply before outputs are published, and neither
output dictionary is modified on a limit failure. Supply a separate plain dict
that does not alias the inputs or the operation-count output. With coverage enabled,
operation_counts must also be a plain dict. Nested output aliases are rejected
during existing input/artifact passes; these container checks use the JSON node
and depth limits, including malformed unused artifact values. Inputs and retained
source bytes remain unchanged. No CLI, runtime consumer, storage or network
integration is activated by requesting diagnostics.

Run all adapter checks using the focused unittest command above. The additional
coverage tests compare all 20 frozen-v1 scenario hashes, reconcile totals, exercise
malformed and offset dates, unverified lineage, namespace separation, duplicates,
input permutations, complete reason guidance, unchanged operation counts at
64/256/1,024 recommendations and bounded failures. These are synthetic mechanical
checks, not newly recovered real-world outcomes. The full repository suite is
not implied by these focused results.
