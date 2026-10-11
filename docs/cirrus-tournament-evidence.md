# Cirrus retained tournament evidence bridge (local, inactive)

This additive Atmosphere module inventories retained Cirrus tournament artifacts and imports genuinely present JSON observations into a separate, versioned SQLite evidence extension. It is not a replay engine, a production hookup, or a promotion to the existing ten mart tables. It never imports Cirrus runtime code, executes/deserializes models, fetches providers, or produces broker fills.

## Invocation

From the repository root:

    python engine/orographic/cirrus_tournament_evidence.py --source /path/to/retained/archive --database /path/to/scratch-mart.sqlite --source-namespace cirrus-gh-run-36897006708
    python -m unittest discover -s tests -p test_cirrus_tournament_evidence.py -v

Only explicitly retained, recognized files are consumed. Point --source at a reviewed archive directory, not arbitrary private folders. Use a stable archive/run namespace. The destination is a local scratch database; do not point this prototype at production state. The module uses Python's standard library. The existing native Atmosphere mart is a manifest plus Parquet directory; this SQLite observation extension is separate and is not native pipeline integration. It is callable as import_artifacts(source, database, namespace).

## Contract and identity

Tables have the `cirrus_tournament_` prefix. The ten existing model/execution mart tables and their eligibility views are untouched.

- artifacts: SHA-256 over exact source bytes, byte size, interpreted file kind, decoder state, explicit unavailable reason. Joblib is hash inventory only; Parquet is hash inventory only because no approved decoder is enabled. No model loading.
- sources: stable caller-supplied archive/run namespace, relative source path, content-addressed artifact. A source path changing contents adds a new historical source row. The namespace is an input label, not a verified upstream receipt.
- observations: immutable artifact hash plus RFC 6901 JSON pointer; explicit kind, existing release ID/model/contract/date/time, retained payload, unavailable reasons, hard-coded observation-only evidence class and zero eligibility. Repeated imports do not update rows. Unexpected existing rows with the same identity but different contents abort the whole transaction.
- release_collisions: reports multiple distinct retained manifest contents using one release ID. A release ID alone is never a global primary key or a proven model/prediction join.

Identical current_scan and dated decision files (and identical release manifest/review aliases) produce one set of observations with multiple source paths. Distinct old/new release payloads remain separate. JSON list order supplies stable pointers within immutable artifacts; new ledger versions append observations, not overwrite history. Consumers must not sum versions or treat repeated position observations as independent positions.

## Field semantics

- raw_quote: raw quote symbol and source collection snapshot. Does not infer scan cutoff, scored-candidate membership, decision release, feature availability, or full-universe completeness from quote count.
- decision: exact decision metadata, including candidate_count and collection diagnostics when present. These are retained producer claims, not a completeness attestation.
- selection: selection payload and parent release_id/scan_date, indexed by the explicit model key. These are scores for selected/abstained records, not decoded full-matrix predictions. The JSON pointer identifies the selection; the two parent context fields are carried into its payload.
- release: retained manifests and release review snapshots. generated_at is a retained event timestamp, not known-at/available-at or proof of completed fitting; current producer assigns it before fit completion.
- quote_proxy_position: retained position/entry/exit quote and reported return fields. Closed positions remain quote proxies. The importer does not relabel a late quote as an on-time exit, infer execution fills, or repair reported returns.
- delivery: recorded email delivery metadata only; sent does not imply a trade.
- scoreboard: aggregate producer-reported proxy statistics only.

`observed_at` is the normalized source event/collection timestamp appropriate to that row, not import time or a point-in-time availability witness. Naive/malformed selected timestamps, invalid dates, duplicate JSON keys, non-finite values, malformed list/object structures, symlink sources, and exit-before-entry records abort the batch before database mutation. Nested provider `greeks.updated_at` is retained verbatim in the payload; its timezone is not invented and it is not exposed as known-at evidence.

All observations have explicit unavailable reasons: no attested decision cutoff, no attested candidate completeness, no attested fit completion, and no retained execution fills. A future stronger schema must not silently remove these constraints merely because fields with similar names appear. Raw quotes also flag that a prediction/release link is unavailable.

## Remaining integration work

A current Atmosphere caller still needs an explicitly reviewed orchestration hookup. These additive tables do not appear automatically in existing analytical views or service endpoints. A separate read-only consumer can inspect evidence now without claiming reliable connected-backtest eligibility. Full scored-row import needs a reviewed Parquet decoder and a schema contract for the real retained columns. Reliable connected backtests additionally need independently retained cutoff/availability/completeness evidence, exact release/prediction hashes, and defined label/exit semantics. Legacy archives cannot be repaired by manufacturing sidecars.

The local smoke database contains private retained observations and must not be published. Deliver only source, tests, and aggregate hash/count evidence. Source artifact files were read only and were not modified.


## Bounded ingestion and filesystem scope

Defaults: 32 MiB encoded per file, 128 MiB decoded per JSON file, 128 MiB encoded across the batch, 192 MiB decoded across the batch, 160 MiB retained serialized payload, 2,048 supported source files, 10,000 visited directory entries, and 250,000 unique observations. The Python Limits object allows explicit smaller limits for constrained hosts and test fixtures. Reject over-limit batches without database mutation; do not silently truncate them. These finite input/payload ceilings bound materialization but do not promise peak RSS equals the byte limits, because parsed Python objects have additional overhead.

Directory enumeration is incremental and capped before adding entries to the path list. Binary files are streamed through SHA-256 and never materialized. Encoded input is read/hash-checked in chunks; JSON is read in bounded chunks, with incremental gzip decompression capped at the smaller remaining per-file/batch decoded allowance. The importer reads at most one extra byte to detect an exceeded ceiling before JSON parsing. Aggregate retained payload and observation counts are capped before retaining additional observations. The real Oct 1 raw archive is 6,084,479 encoded bytes / 88,895,248 decoded bytes / 90,050 rows and fits these defaults.

The root itself and discovered descendant symlinks are rejected. This is an offline reviewed-archive reader, not a hostile-filesystem sandbox: ancestor symlinks, concurrent path replacement, and mutable-source races are outside its guarantees. Inputs must remain quiescent for the import. No source files are written.

An independent exact ten-table SQLite contract-mirror smoke establishes unchanged names/schema/sentinel contents only. It does not establish native Parquet mart integration or consumer availability.
