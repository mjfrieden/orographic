# Atmosphere publication recovery — 2026-10-30

## Verified evidence

Run https://github.com/mjfrieden/orographic/actions/runs/41132458253 at commit
`b3d8f1e9dd5ae04d16aba42de9de4b70e760026f` rebuilt 3,945 training rows, then
failed during MERGE at 21:46:24 UTC. Verification and subsequent persistence
steps did not run. Earlier tables may have been changed: this publisher is not
a multi-table transaction. Do not assume the catalog is unchanged.

Artifact 13014715730 was downloaded and its SHA-256 verified:
`64149eb5082dbfdb365e3757c86d8052e51b770360848522201fc1229ba09219`.
It contains four diagnostics, no Iceberg table metadata, manifests, snapshots,
or missing-object backup. These establish distinct identities:

* Rebuilt: `e60cc6bc3e82f0ddb34139bab24f2706ce2de6cbe0b3b98faeb24ca622cb982e`.
* Previously published, as inspected before the failed merge:
  `ee2b28df4f25c666d01b49238d1d567ac8fd59a6e312553a7f275c90949f9f8e`,
  publication timestamp October 19 21:43:12 UTC.
* Repository web diagnostic at the inspected commit: October 16,
  also `ee2b28df4f25c666d01b49238d1d567ac8fd59a6e312553a7f275c90949f9f8e`.
  The live served endpoint was not independently fetched in this task.

The engine reported HTTP 404 / NoSuchKey for the key:
`__r2_data_catalog/01a031a8-7ef0-7741-9f5c-456088b18315/01a031b5-9b87-7431-bd6b-a95a0344bc59/data/07304215-c8b1-41a2-8353-9a1e91d3d3d2-deletes.parquet`.
The bucket/account were masked. The table name, referencing snapshot and
metadata location cannot be established from this artifact or traceback.

## Local repair

With explicit full-scan consent, the publisher scans every existing destination, including
`mart_publications`, before any remote write. It drains full projections in
batches; COUNT(*) or LIMIT alone would not establish object readability.
Read failures identify the target table and stop publication. Raw engine
messages are excluded from the structured report because they can contain
signed URLs. This adds a full-read cost; it does not provide atomicity,
snapshot pinning or protection against a concurrent deletion after preflight.

With existing authorized catalog access and a validated retained/rebuilt mart:

```sh
python scripts/publish_shared_research_mart.py --mart-dir output/shared_research_mart --preflight --allow-full-scan > preflight.json
```

Both remote modes require `--allow-full-scan` (API `allow_full_scan=True`).
Without it they fail before opening the catalog. The existing scheduled
`--apply` command will therefore fail closed until an operator explicitly
accepts the read cost; this patch does not change that workflow automatically.
This mode only reads remote tables. No flag still performs local validation
only. `--apply` and `--preflight` are mutually exclusive. Preflight success
never sets a published or served identity. A completed publication still
requires the separate verifier and independently fetched served evidence.

## Smallest next recovery step (read-only)

Use an already authorized environment to run preflight and export the affected
table's current metadata location/UUID, snapshot IDs, manifest list and
manifest entries, plus the missing key's object metadata and backup/version
inventory if available. Record hashes of downloaded metadata. Do not create
credentials. No catalog environment values are present in this task; no live
inspection or recovery was attempted here.

Trace the exact delete-file reference and sequence/content semantics. If an
immutable byte-identical backup exists, propose restoring only that exact
missing key using a create-only conditional write. Before approval, record
source key/version, SHA-256, byte size, destination bucket/key, proof the
destination is absent, and all referencing snapshots. None of those backup
identities are yet known. Do not silently remove a delete reference, fabricate
an empty delete file, or overwrite an existing object.

No live repair is approved by this document. There is no verified rollback
snapshot yet. If restoration fails verification, stop publication and retain
evidence; do not automatically delete the restored object or move metadata.
A metadata rollback needs its own approved, verified snapshot and assessment
of all historical rows it would lose. If no exact backup exists, design a
separate replacement namespace from complete validated history, with explicit
consumer cutover/rollback approval; the current local mart alone is not proof
that retained history can be reconstructed.

After an approved exact-object restoration: repeat the full read-only
preflight, compare pinned metadata identities, then request approval for any
publication. After approved publication, verify source-bundle row counts and
publication identity with `verify_shared_research_mart.py`, independently
fetch the served identity and timestamps, and keep those three observations
separate. No model promotion follows from these software checks.

## Cost, ordering, and read access

No production cost measurement was made. The three-table fixture performs six
SQL executions (one existence lookup plus one full SELECT per table), and
three fetches per SELECT (two data batches then EOF). For N present tables,
the preflight adds 2N SQL executions and a full read of all projected rows,
including retained historical data, plus delete application. An 8,192-row
fetch size bounds the Python batch, not DuckDB memory, bytes read, requests,
runtime, or billing. There is intentionally no claim of a production cost cap.
The October 30 rebuilt fixture inventory already contains 6,962,128 quote
rows; destination history may be larger. Explicit opt-in is mandatory.

Local INSTALL/LOAD, an ephemeral in-memory SECRET, and catalog ATTACH precede
the reads. They are not remote table mutations, but extension installation
can perform network/local-cache operations. Tests mock this setup and make
no production calls. All destination reads finish before CREATE SCHEMA,
CREATE TABLE, MERGE or publication INSERT. An attach or preflight failure
returns a sanitized report with no raw exception text. A later failure is
reported as `publication_failed`, `remote_writes_started=true`, and
`commit_outcome=uncertain`, never as a successful publication.

A stale catalog or changed object after preflight remains unsafe: the fixture
allows the first MERGE and then raises a missing-delete error on the second.
No publication marker is inserted, but earlier writes may persist. Preflight
does not refresh/pin snapshots, compare catalog versions or roll back writes.
Its result must not be described as a concurrency guarantee.

Smallest read-only access for lineage diagnosis: existing authorization to
load/list table metadata in `research_mart` in the configured R2 Data Catalog
warehouse, and GET/HEAD for the metadata JSON, manifest lists and manifests
referenced by the affected table under
`__r2_data_catalog/01a031a8-7ef0-7741-9f5c-456088b18315/01a031b5-9b87-7431-bd6b-a95a0344bc59/`.
Also HEAD the exact missing key above. If the UUID cannot be mapped by loading
known tables, namespace table listing is needed. Backup discovery additionally
needs prefix-scoped listing/GET only for an identified existing backup location;
no such location is known. Full preflight would require GET for all referenced
data/delete objects across all destination tables, a broader and potentially
costly read scope. No PUT, DELETE, catalog commit, new credential or workflow
run permission is needed for the initial metadata diagnosis.

## Local regression evidence

Python 3.12, DuckDB 1.5.6 (within the repository's >=1.4,<2 constraint),
PyArrow 26.0.0. Final patch: 49 tests passed in 13.954 seconds. Unchanged
`b3d8f1e9dd5ae04d16aba42de9de4b70e760026f` baseline: 38 corresponding
existing tests passed in 13.571 seconds. No final failures. Modules:
`test_iceberg_mart`, `test_mart_streaming_rows`, `test_shared_mart_consumers`,
`test_shared_research_mart`, `test_sync_shared_research_mart`,
`test_evidence_store`, plus 11 new `test_iceberg_preflight` tests on the patch.
The first narrower run encountered three sandbox temporary-directory permission
errors; rerunning with TEMP/TMP inside the permitted workspace resolved them.

New fixtures cover explicit cost consent, full-read call counts, missing delete
on a later fetch, absent destinations, local catalog setup command ordering,
attach/preflight/post-preflight error redaction, stale absence followed by a
concurrent create, and successful preflight followed by a failing second MERGE.
Remote engine calls are mocked; these are ordering/reporting tests, not a real
Iceberg delete-file integration or billing benchmark. Full repository tests,
live catalog/object checks, extension installation, real Iceberg integration,
production publication, and served endpoint validation were not run.
