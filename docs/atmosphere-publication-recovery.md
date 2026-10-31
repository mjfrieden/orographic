# Atmosphere bounded publication preflight

PR107 is a draft. The prior full-scan rollout gate has been replaced by default
bounded REST/manifest/object-existence checks. Existing scheduled and manual
`--apply` commands no longer require `--allow-full-scan`. They proceed only when
all checks complete, and fail closed on missing objects, insufficient access,
unsupported metadata or exhausted budgets. No schedule or credential changes.

## Contracts inspected

- Apache Iceberg REST specification: https://github.com/apache/iceberg/blob/main/open-api/rest-catalog-open-api.yaml
  Config defaults/overrides and prefix; LoadTable metadata-location/metadata;
  vended storage-credentials take precedence over legacy config credentials.
- Apache Iceberg format: https://iceberg.apache.org/spec/
  Current snapshot manifest list; live entry status 0/1 versus removed status 2;
  data content 0, position deletes 1, equality deletes 2.
- DuckDB metadata interface: https://duckdb.org/docs/stable/extensions/iceberg/overview
  Metadata functions exist, but do not expose the required wire-byte/request
  budgets. SQL LIMIT is not a transport budget.
- Cloudflare configuration: https://developers.cloudflare.com/basin-catalog/config-examples/pyiceberg/
  Existing catalog token and warehouse; access delegation uses temporary
  server-vended credentials. No credential creation or persistence is performed.
- Botocore S3SigV4Auth signs bounded urllib HEAD/GET requests; fastavro parses
  Avro manifests. No SDK credential search, retries, redirect following, or
  remote-signing calls. Existing boto3 dependency supplies botocore.

## Limits and behavior

Global budget for one invocation: 32 MiB metadata response bodies, less than
4 MiB per document, 128 manifests, 2,048 unique live objects across tables,
4,096 HTTP requests, and 100,000 manifest entries. A 120-second cooperative
deadline is checked before requests, body reads and entries; socket operations
have a maximum 5-second timeout. DNS/CPU parsing are not a hard wall-clock or
memory bound. Metadata-body budgets count encoded Avro bytes, not decompressed
Python objects. Responses reaching the document cap without observed EOF are
rejected conservatively. Defaults are conservative limits, not measurements of
production inventory. No retry or fallback to unbounded scanning exists.

The reader uses only the configured catalog.cloudflarestorage.com account/bucket
and that account's R2 S3 endpoint. It negotiates vended credentials, loads known
tables without listing storage, checks the REST metadata against its JSON object,
reads current-snapshot Avro lists/manifests, and HEADs every distinct live data,
position-delete and equality-delete object with expected size. Removed manifest
entries are not live snapshot references. Unknown content/status values fail.
Only .json/.avro paths can receive metadata GETs; no Parquet GETs are issued.

All table metadata identities are reloaded after inspection. Changes detected
during the check stop publication. A later catalog/object change remains possible:
this is not atomic publication or a lock. Earlier table writes may persist when
a later MERGE fails; diagnostics report uncertain commit outcome. HEAD proves
existence and declared length, not content/checksum correctness.

Supported: Iceberg v1/v2 current snapshots with manifest-list, S3 locations in
the configured bucket, client-side planning and vended S3 credentials. Remote
signing, v3, partial/read-restricted metadata, unsupported endpoints and layouts
fail closed. Real R2 response compatibility remains unverified pending access.

`python scripts/publish_shared_research_mart.py --mart-dir <validated-mart> --preflight`
performs only bounded remote reads. No flag performs local planning only.
`--apply` uses the same bounded gate before DuckDB attachment or remote writes.
`--allow-full-scan` remains an explicit alternative diagnostic mode; it has not
been enabled in workflows and is outside current production authorization.

## Verified incident and pending access

Failure run: https://github.com/mjfrieden/orographic/actions/runs/41132458253
Base failure commit: b3d8f1e9dd5ae04d16aba42de9de4b70e760026f.
Retained artifact13014715730 ZIP SHA256:
64149eb5082dbfdb365e3757c86d8052e51b770360848522201fc1229ba09219.
Rebuilt 3,945 training rows / mart:
e60cc6bc3e82f0ddb34139bab24f2706ce2de6cbe0b3b98faeb24ca622cb982e.
Previously published identity observed before failure:
ee2b28df4f25c666d01b49238d1d567ac8fd59a6e312553a7f275c90949f9f8e.
The repository diagnostic remained October16; live serving was not independently
verified. The artifact contains four diagnostics, no Iceberg manifests or backups.

Missing key:
__r2_data_catalog/01a031a8-7ef0-7741-9f5c-456088b18315/01a031b5-9b87-7431-bd6b-a95a0344bc59/data/07304215-c8b1-41a2-8353-9a1e91d3d3d2-deletes.parquet

Minimum existing read access: REST config and known research_mart table loads;
GET for their metadata JSON/current manifest-list/manifests, HEAD for referenced
live data/delete objects. Initial incident-only diagnosis can be scoped to the
UUID prefix above plus table mapping. No object listing is needed for preflight.
Namespace listing is needed only if table-to-UUID mapping is otherwise unknown.
Backup discovery requires separate scoped reads of an identified backup location.

Pending evidence: exact table UUID/name, metadata location/hash, current snapshot,
manifest-list and delete-entry lineage (status/content/sequence/size), current
HEAD result, vended credential scope and non-secret config shape, object/manifest
counts within limits. Do not provide tokens or signed URLs in reports.

If an exact immutable backup is found, propose the precise source/version/hash,
destination missing key and create-only restore, with verification and rollback
identities, for separate approval. Never remove a delete reference, create an
empty replacement, rewrite metadata, or delete storage to manufacture success.
No verified backup or rollback snapshot has been found. No live repair attempted.

## Validation scope

Fixtures use actual Avro serialization for fields consumed by the checker and
mocked read-only REST/object transports. They exercise all three file contents,
missing deletes, removed entries, budgets, body caps, signed HEAD construction,
metadata mismatch, identity changes, healthy default --apply, blocked default
writes and uncertain partial commits. These are not real R2 integration tests.
Aggregate Linux CI must pass on the final pushed head. Prior head7498b39 passed
601 Python tests/125 subtests,109 observation,4 smoke,555 JS/1 skip and22 mart tests;
those counts do not certify the new implementation. No production reads, writes,
full scans, serving verification, model promotion or trading actions are included.
