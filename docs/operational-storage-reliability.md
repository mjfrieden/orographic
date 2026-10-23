# Operational storage reliability (v1)

Keep the existing content-addressed gzip objects and v1 manifest format. Restored
JSON bytes, order, whitespace, numeric spelling, observations, provenance and
capture-attempt timestamps are not normalized or dropped. Existing v1 readers
can consume new snapshots. No historical objects are deleted. This does not
activate the experimental partitions, repair Iceberg, create credentials, call
providers, change capture times, or claim to eliminate full-history growth.

## Restore and publication safety

- Both active ledgers must be present. Validate every path, namespace, checksum,
  size and JSON object before installing any local file. Local replacements are
  individually atomic, not a multi-file transaction. A partial install leaves no
  valid receipt, so it cannot authorize publication.
- Pull invalidates the previous receipt before the first request. A successful
  receipt binds the bucket, prefix, path list and exact manifest bytes/hash. It is
  local safety state, not an authentication or adversarial security boundary.
- Normal workflows never use repository seeds after a failed restore. Auth,
  timeout, network, missing bucket, generic 404 and malformed responses fail
  closed. Only Wrangler's explicit R2 NoSuchKey message, `The specified key does
  not exist.`, is eligible for an explicit bootstrap. Unfamiliar errors fail
  closed. See the [R2 error reference](https://developers.cloudflare.com/r2/api/error-codes/).
- Stage and validate every changed archive before the first PUT. Missing local
  ledgers cannot silently remove references. A compressed archive above 300 MiB
  stops before upload rather than reaching Wrangler's transport ceiling.
- Exact-byte unchanged ledgers reuse their verified record without recompression
  or archive PUT. An all-unchanged push makes one manifest GET and zero PUTs.
  Capture-attempt metadata remains evidence: timestamp changes still persist.
- Re-read the baseline before uploads and before publication. Preserve exact old
  and new manifests as `manifests/<sha256>.json`, then publish `manifest.json`
  last. Failed uploads/checkpoints leave the old pointer intact. An uncertain
  final PUT must be reconciled by restoring, not blindly retried.

## Coordination and rollout

Scan, outcome capture, canonical recovery and production shared-mart sync share
one repository-wide `orographic-operational-ledger-writer` concurrency group
across refs. Mart concurrency is on its production job, so offline PR checks and
skipped main-push jobs cannot take this lock. Main pushes remain unable to run
production sync. Existing schedule/manual trigger conditions are unchanged.

Outcome publication requires successful operational restore and a non-cancelled
job. It can still persist complete valid observations if a later capture step
reports an error; the strict publication checks still apply.

The stale checks are NOT atomic compare-and-swap. All writers must hold the same
lock across restore, local updates and publication. External/manual writers must
not run concurrently. GitHub concurrency is not FIFO and does not guarantee that
every pending job survives. Before rollout, ensure old workflow versions using
the previous per-ref group have no running or pending jobs. Old branches/scripts
without the new lock must not publish. Do not dispatch production to test code.

Compression/decompression use 1 MiB buffers instead of retaining full raw and
compressed byte strings together. JSON validation still loads one decoded
ledger at a time; total memory is not constant in history size. Workspace staging
needs compressed-file capacity and, on restore, decompressed-file capacity. A
RAM-backed workspace still consumes RAM.

## Explicit first initialization

A new empty namespace needs intentional bootstrap, not an ordinary capture run:

1. Quiesce every writer and verify the intended new empty bucket/prefix. A missing
   production pointer is a recovery incident, not permission to reset history.
2. With complete local seed ledgers, run `python scripts/sync_operational_ledgers_r2.py
   pull --bucket <existing-bucket> --prefix <new-prefix> --bootstrap-from-seed`.
   Only a confirmed missing object creates a bootstrap receipt. The old
   `--allow-missing` spelling remains an alias with this stricter behavior.
3. Run `push` with the same bucket/prefix/state path while holding the same lock.
   Push rechecks absence before uploads and publication. Any existing or newly
   appeared manifest blocks bootstrap.

These are documented commands, not an executed dispatch or authority for live
writes. The default receipt is `output/operational-ledger-restore.json`; use
`--state-path` to isolate another explicitly coordinated workspace.

For code rollback, drain all writers and revert the patch. Data remains v1 with
no conversion, but reverting also removes the safety checks. For data recovery,
inspect an immutable manifest checkpoint and verify all referenced hashes before
an explicitly authorized pointer restoration. No automatic rollback, deletion,
truncation or catalog repair is included.

## Growth limits and measurement

V1 already content-addressed byte-identical archives. No-op reuse saves transfer,
CPU and requests, not additional retained bytes. Changed snapshots still retain
full gzip history plus small immutable manifest checkpoints. No bound on monthly
storage or measured production saving is claimed.

The previous per-entry prototype had conflicting synthetic results across ten
updates: varied 1.65 MB history retained about 0.67 MB instead of 9.37 MB, while
repetitive 0.83 MB history retained about 1.24 MB instead of 0.044 MB. Restore calls
rose from 2 to 202/402. Single prefix/suffix deltas also lose their advantage when
both leading summary fields and trailing observations change. These results do
not support production activation of either protocol.

Before another storage-format change, measure representative real-ledger gzip
sizes, changed-byte locality, retained bytes/day, restore latency and request
budget. Preserve unique observations rather than introducing integrity risks for
small projected savings. Regression tests use synthetic data and deny unmocked
network transports; they do not prove live R2 credentials or upload health.
