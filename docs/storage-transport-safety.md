# Storage transport and operational ledger rollout

## Canonical archives

- Newly built JSON is compactly encoded without changing decoded observations.
  File checksums and bundle identity are calculated from the new exact bytes.
  Existing archived objects are not rewritten, removed, or expired.
- Before canonical publication, the entire bundle is copied beside its source
  and revalidated. Allow one additional bundle of local filesystem space. Use
  disk-backed storage: a RAM-backed source filesystem still consumes RAM.
  Uploads read only the staged copy; the current manifest is published last.
  Concurrent generations cannot mix file hashes, but last-writer-wins does not
  guarantee chronological freshness. Existing writer serialization is still
  required; this transport does not add a distributed lock or conditional PUT.
- Files up to 300 MiB retain the existing Wrangler route. Larger files require
  already-configured `CLOUDFLARE_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, and
  `R2_SECRET_ACCESS_KEY`. Missing configuration fails closed. No REST token is
  converted into credentials, and no AWS ambient credential chain is used.
- Multipart uploads use the account's R2 S3 endpoint, 64 MiB parts, at most two
  workers, and at most four attempts per SDK request. Inputs exceeding 10,000
  parts fail rather than enlarging the parts. Managed-transfer failures attempt to abort
  their own incomplete upload; network or authorization failures can prevent cleanup. Other objects are never deleted.
- Original hashes and reader validation remain authoritative. A multipart ETag
  is not treated as a whole-object SHA-256. No live upload is needed by tests.

## Operational history remains a separate rollout

This change does not switch the operational ledgers to a new storage format.
Full-history duplication and already-retained object bytes are not eliminated
by transport fixes or JSON compaction. A partitioned format needs its own
representative storage-growth, request-count, restore-latency, concurrency and
recovery measurements before activation. Do not infer hosting headroom from
synthetic transfer tests or assume that compaction puts every file below the
single-request limit.

## CI isolation

Pull requests use synthetic providers and local object-store doubles. The CLI,
writers, byte hashes, failure handling, and snapshot validator are exercised;
the independent real-model artifact hash gate remains unchanged. Shared-mart
PR tests have no provider/catalog secrets or real evidence downloads. Production
shared-mart sync is eligible only on the existing schedule or explicit manual
dispatch. A code push, including merging this fix to main, cannot run that job.
The existing schedule and manual inputs are unchanged; they are not dispatched
by these tests. Other scan and broker/execution schedules are untouched.

Neither a missing catalog object nor historical catalog inconsistency is repaired
by transport changes. Do not delete catalog files or blindly retry publication
to work around such a failure.
