# Private native observation Parquet bundle v1

This additive bridge uses the two independently reviewed observation adapters as
staging inputs and exports actual, typed PyArrow Parquet. It writes only
`<native-mart>/observation_bundles/v1/`; the native ten execution-semantic tables,
manifest, publisher, consumer views, and eligibility rules remain untouched.
It verifies the existing native mart before and after export. It does not make
historical data reliable for backtests or activate a live pipeline.

## Local API

First ingest reviewed local sources with `cirrus_tournament_evidence.import_artifacts`
and `event_observations.ingest_cirrus` into dedicated private local SQLite staging
databases. Then use `observation_parquet.write_bundle(mart_dir,
tournament_database=..., event_database=...)`. Both are required, although either
may contain no observation rows. `read_bundle(mart_dir)` verifies the native mart,
observation manifest and every typed Parquet artifact and returns `(manifest,
Arrow tables)`. SQLite is staging, not the native consumer result. No model
loading, provider calls, remote publication, or new credentials are involved.

The native dependency policy remains unchanged. Verification used Python 3.12,
PyArrow 26.0.0 and pandas 2.3.3 in an isolated local venv, installed from official
PyPI. Parquet byte determinism is demonstrated in that environment, not promised
across arbitrary future Arrow versions. Logical source hashes remain exact.

## Namespace and provenance

Eight fixed observation tables retain the reviewed adapter schemas, including
artifact SHA256, source namespace/path, source pointers, original JSON payloads,
event artifact raw bytes, missing-witness reasons, and review provenance. Table
names cannot collide with the ten native tables. Typed empty tables are written.
Version, schema, required fields, source registry, artifact identity, checksums,
foreign keys, zero eligibility, and event observation identity are checked.
Readers verify key order/uniqueness and source attribution. Timestamp fields
retain adapter string precision and nulls; no availability timestamps or values
are invented. Existing event as-of semantics are not bypassed by a native view.
This release provides a verified read API, not a backtest evaluator.

The manifest is deterministic and contains only fixed schema metadata, source
versions, content hashes, native snapshot identity, counts and hard-coded zero
eligibility. It includes no source paths, symbols, event names, raw payloads or
provider errors. Source artifact hashes themselves reside in the private tables.
The native mart manifest hash binds the observations to that exact snapshot.
Rebuilding or replacing the native mart requires a fresh observation export.

## Privacy and atomicity

All output is private evidence. Raw JSON and event source bytes MUST NOT be
committed to public source, attached to public PRs, uploaded to public dashboards,
or passed to the existing mart publisher. Only source code, synthetic tests and
bounded aggregate verification receipts belong in a deliverable. Parquet and
manifest files are mode 0600 and the namespace leaf directory is mode 0700 when
created. Existing parent permissions are not modified. This is not encryption.

Inputs must be trusted reviewed adapter databases on a quiescent local filesystem;
concurrent source or mart mutation is unsupported. Database opens are read-only
transactions. Full existing path symlinks are rejected. Staging files are fsynced,
installed under content-addressed filenames, then a single manifest is atomically
replaced and the directory fsynced. An interrupted export leaves the old manifest
usable, possibly with unreferenced immutable files. Do not garbage-collect those
while readers are active. This is atomic local commit, not distributed locking.
No timestamps are added to manifests, so repeats produce identical manifests and
Parquet hashes in the tested dependency environment.

Bounds: 512 MiB per staging database, 250,000 rows across all tables, 256 MiB
materialized cell bytes, 64 KiB manifest, batches of 1,024 rows. The reader also
checks encoded artifact sizes and decoded batches. Limits reject rather than
truncate. SQLite/PyArrow have transient allocation overhead; these ceilings are
not hard RSS limits or a hostile-Parquet decompression sandbox. The adapters have
their own source-ingestion limits. Do not use arbitrary untrusted databases/files.

## Publisher work still required

Current main's `publish_shared_research_mart.yml` is manual workflow_dispatch;
its publisher validates and publishes the fixed execution mart contract under
`research_mart`. There is no observation namespace hookup in this patch. Before
any scheduled or manual publication, separately review the source-use/privacy
permissions, private object destination and access controls, retention policy,
versioned observation-only Iceberg schema, manifest transfer and scheduler input
contract, retry/idempotency behavior, and bounded public summary allowlist.
Keep raw payload tables private and execution eligibility false. Do not add these
tables to existing execution `TABLE_CONTRACTS` or copy them into execution views.

## v2 review fixes

The writer now runs the complete native observation reader validation against the
staged manifest before commit, including cross-table source attribution. Any
rejection preserves the previous manifest. Writer and reader use the identical
cell-byte budget: UTF-8 text length, raw binary length, and eight bytes for each
numeric or null cell. This is a logical budget, not Arrow buffer or peak RSS size.
Manifest JSON rejects duplicate keys and non-finite values. Exact key allowlists
apply at the root, artifact, summary, and source-version levels, with strict hash,
integer (not boolean), guard, version, and fixed schema metadata validation.
Rehashing a manifest cannot make arbitrary payload metadata acceptable.
