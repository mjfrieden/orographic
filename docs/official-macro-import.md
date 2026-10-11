# Offline official macro extraction preparation

Status: local review candidate; no activation, collector, refresh schedule or live source requests.

## What this adds

`official_macro_import.prepare` validates one reviewed JSON sidecar against exact
retained UTF-8 source bytes. `ingest` appends it to the existing
`event_observations` contract. This is a local extraction format, not an
independently verified official HTML/JSON parser. Supported source families are
BLS archived CPI/Employment Situation releases and Board FOMC statement pages.
The FOMC path is metadata only; it does not extract or infer policy rates.

BLS quantities are limited to two explicitly selected metrics:
- CPI all-items, seasonally adjusted, month-on-month percent change
- Total nonfarm payroll seasonally adjusted change, in persons (never silently scaled to thousands)

A period, context, adjustment, number and unit each require exact byte spans.
Unicode-aware numeric boundaries and ASCII-only decimal grammar, units, source family, explicit English
reference month and archive date are validated. The archive document month must
follow the reference month. Ambiguous or unsupported forms fail closed. These
checks establish extraction integrity, not that disconnected spans have the
intended semantic relationship. A qualified reviewer must still validate the
metric, sign, unit, reference period, original release versus later correction,
and any third-party content in each document. Synthetic fixtures demonstrate the
sidecar contract only; they are not genuine government observations.

## Time and provenance

All rows are `history_class=reconstructed`. `first_public_at`, `first_seen_at`,
`available_at`, `scheduled_at`, `released_at`, and `event_date` stay null, even if a
review string or receipt claims otherwise. The URL's date is kept only as
`source_document_date`, and a BLS reference month only as `reference_period`.
Publication/embargo text remains a quoted byte span. A publication line is not
proof of first public appearance or successful publication at its stated time.
Request/received timestamps in an optional receipt claim are timezone-validated,
ordered, and source/hash-bound, but remain unauthenticated provenance claims.
Import time, filesystem mtime, source period and release URL dates cannot provide
historical system knowledge. No first-seen clock is invented.

Actual values are reconstructed reviewed-extraction claims. Expected/consensus
and prior stay null. No forecast source, surprise calculation, release-time
trading signal, or forward-filled value is introduced. `first_release_claim`
versus `revision_claim` is explicitly a reviewer assertion, not automatically
verified. The raw SHA-256 identifies the retained version; later source bytes
append a new row and cannot rewrite prior observations through normal SQL.

Both original source bytes and original sidecar bytes are retained verbatim in
the canonical envelope, each with a SHA-256. The envelope is the existing event
artifact, and `/event` is its canonical record pointer. Separate scoped SQL
triggers protect these imported observations, artifacts and registration rows
against update/delete and changed primary-key collisions, including INSERT OR REPLACE with SQLite recursive triggers disabled. Database owners can remove protections: these hashes and
triggers are integrity aids, not cryptographic proof or tamper-proof storage.

The event normalizer still rejects as-of eligibility when evidence is missing.
Even `allow_fixtures=True` cannot admit these null-clock observations. The Fed
registry entry is deliberately `review_required`, limited to Board statement
facts, and does not relax source rights. No model, mart execution schema,
static 23-event calendar, Cirrus model/PR #27 or existing readiness query changes.

## Reuse of existing preparation

The existing local `official_vintages` v2.1 candidate already parses BLS calendar
HTML and offers `export_bls_to_v1`. That implementation is not duplicated or
activated here. Its calendar receipts remain a distinct preparation; their
unknown first-public clocks cannot become v1 release eligibility. The known
three failed hardened HTTP attempts are not retried. There are zero live requests
in this candidate, and no collector module is included in its patch.

## Rights and sources

Source-rights background was reviewed on 2026-10-11 in the earlier preparation:
- BLS attribution/public-domain policy: https://www.bls.gov/bls/linksite.htm
- Board disclaimer/public-domain exceptions: https://www.federalreserve.gov/disclaimer.htm
- BLS calendar shape: https://www.bls.gov/schedule/news_release/cpi.htm
- BLS calendar Eastern-time convention: https://www.bls.gov/schedule/2026/
- FOMC calendar and separate statement/minute links: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

These references do not authenticate a sidecar or a particular source document.
No new source retrieval occurred for this change. A future genuine retrieval
must record its actual retrieval date, attribution, exact source URL and rights
review, including disclaimers and exclusions for protected logos, photos and
third-party material. This preparation authorizes no redistribution guarantee,
retention promise, paid access or expansion to regional-bank sites. FRED/ALFRED
remain blocked pending separate permission review; BEA API keys and Cboe feeds
are outside scope.

## Activation gates and validation

Before any prospective use: separately authorized bounded genuine capture;
successful source-byte/format validation; authenticated and reviewed receipt
origin; document-specific rights review; a separately reviewed first-public and
system-availability proof contract; coverage/freshness policy; and independent
review of the exact patch. A review string or byte span alone cannot satisfy
these gates. This candidate intentionally has no path that turns such claims
into eligibility.

Run only named offline tests:
`python -m unittest tests.test_event_observations tests.test_official_macro_import -v`

No broad test discovery, server, sockets, browser, Miniflare, installation or
network operation is required. The original 21 event-contract tests and new
synthetic regression tests cover raw/sidecar bindings, missing clocks, receipt
claim ordering, unchanged readiness, negative/zero values, source/unit/period
bounds, ambiguity, immutable revisions and idempotence. Passing them does not
prove a genuine source parser or operational data availability.
