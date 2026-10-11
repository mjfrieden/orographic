# SEC filing observations, offline research stage

This additive adapter extends the existing Cirrus SEC evidence path. It does not add a collector or change Cirrus, its heuristics, PR #27, the frozen experiment, the 22-feature tournament, production scheduling, or trading. There were no provider requests, bulk downloads, new keys, paid services, or claims of historical retained coverage in this work.

## Accepted input and output

`ingest_sec_filing` accepts exact captured bytes for:

1. SEC submissions JSON, specifically its parallel `filings.recent` arrays and issuer CIK, the same fields Cirrus already reads.
2. One filing-scoped UTF-8 XBRL instance XML for a 10-K, 10-Q, 10-K/A, or 10-Q/A. HTML/inline XBRL, companyfacts, companyconcept, and frame API snapshots are not supported. This deliberately small scope avoids pretending aggregated latest facts are historical captures.
3. A `sec-filing-offline-v1` capture manifest. It binds `submissions_sha256`, `filing_sha256`, zero-padded `cik`, `accession`, `form`, and an accession-specific SEC archive `filing_source_url` ending in XML. Caller-supplied optional timestamps are `first_public_at`, `submissions_received_at`, and `filing_received_at`; each requires a corresponding `_reference` string. Optional review metadata remains a claim, not authentication.

The manifest and both raw artifacts are base64-embedded losslessly in one retained, hashed event artifact. The normalized event references that bundle; provenance also records each individual hash, submissions accession JSON pointer, document URL, and source XML URL. Facts retain XML child indexes, exact raw numeric text, decimal values as strings, precision/decimals, original context and unit XML, periods, dimension/scenario XML, and namespace bindings. No numeric rounding, currencies/units conversion, quarterly derivation, YTD subtraction, or taxonomy merging is performed.

The minimal shared-contract change adds `sec_filing_release` and requires an issuer for that kind. Facts are nested in this filing observation, rather than mislabelled earnings events or macro actuals. Contract actual, expected, prior, schedule, and event-date values remain null. There is no earnings calendar, consensus, or surprise calculation.

## Timing and trust boundary

SEC acceptance is retained as the filing's release timestamp, never as independently proven public dissemination. Filing dates and financial period dates are never promoted to timestamps. Publication and both receipt claims must include exact timezone-bearing timestamps and nonempty references. Missing, date-only, or naive timestamps stay missing. Available time is the maximum of valid acceptance, public-time claim, and both receipt claims; absent requirements leave availability missing. A download today cannot produce eligibility yesterday.

The adapter validates byte bindings, identifier consistency and timestamp precision. It does **not** authenticate receipts, references, a reviewer's identity, the content of an external URL, or public availability. `claim_authentication` explicitly says `not_performed_offline`. An archive URL and matching hash in supplied JSON do not prove that those bytes came from that URL. Authenticating capture witnesses is separate future work.

Most importantly, `sec_public_filing` stays `review_required` in the source registry. All SEC output is currently ineligible for as-of selection, even if a supplied receipt says reviewed. Tests explicitly opt into `synthetic_fixture`; that gate proves mechanics, not data readiness. Both the contract and fact output retain execution-ready false. No claim is made about comprehensive SEC reuse rights, retention rights, production coverage, or backtest readiness.

## Amendments and selection

Each accession is a separate append-only event and revision. No amendment-to-original linkage is guessed. As-of fact selection starts with the existing contract's eligible event vintages, requires stable CIK identity, and groups by exact expanded concept, period, dimensions, original unit semantics, and namespace environment. The most recently accepted eligible filing wins within that exact scope. Equal-acceptance conflicting values/accessions are omitted. An amendment with a nil value does not resurrect an earlier value. Original and amended artifacts remain retained. Conservative XML/namespace equivalence may split semantically equivalent contexts rather than risking a false merge.

The adapter rejects duplicate contexts/units, issuer or DEI form/amendment mismatches, unaligned submission arrays, missing accession matches, missing fact context/unit references, namespace-prefix rebinding or descendant-only declarations, unresolvable unit QNames, nonfinite numeric values, DTD/entity declarations, inline transforms, and unsupported unit shapes. All namespace declarations must be present at the document root; valid instances using descendant-only declarations are conservatively rejected. XML is bounded to 8 MiB, 100,000 elements, and depth 64 before canonicalization. This is a bounded extractor, not a complete XBRL schema validator.

## Verification

Run from a repository root:

- `PYTHONPATH=engine python -m unittest discover -s tests -p test_sec_filing_adapter.py -v`
- `PYTHONPATH=engine python -m unittest discover -s tests -p test_event_observations.py -v`

Only these named pure-offline tests were used. Fixtures are synthetic and make no provider calls. The SEC checks exercise raw-artifact retention, receipt/hash/accession/CIK/form binding, exact cutoff boundaries, amendment history, unavailable witnesses, quarter/YTD and currency/dimension separation, decimal preservation, conflicting facts, nil revisions, and source-use gating.

## Existing Cirrus evidence and separately identified issue

Inspected `mjfrieden/Cirrus` at `7f007485`, `src/cirrus_picker/sec_edgar.py`. Lines 223–227 retain form, filing_date, accepted_at, accession_number, and primary_document; lines 217–219 build the SEC archive URL. The current cache does not provide the receipt/publication witnesses required above. This adapter does not treat cache freshness, mtime, generated timestamps, or a scan date as historical availability.

A separate existing issue is visible at lines 203–210: `age_days = max((as_of_date - filing_date).days, 0)` clips future filing dates to zero, and only rejects excessive age. Example: as_of_date 2026-08-01 with filing_date 2026-08-02 produces age_days 0, so that filter retains the future filing. This is a static arithmetic reproduction, not an executed collector test. It is not fixed in this patch and does not imply a observed live bad selection.

Source: https://github.com/mjfrieden/Cirrus/blob/7f007485/src/cirrus_picker/sec_edgar.py#L203-L227

Contract baseline: https://github.com/mjfrieden/orographic/blob/7559e3f3aaf2c6b4c0f4a2d0013fbe8beb9caf2a/engine/orographic/event_observations.py

Any future use must be separately evaluated under a prospective research protocol with authenticated captures, reviewed source rights, coverage/missingness audit, and explicit approval. It does not automatically feed the frozen tournament.
