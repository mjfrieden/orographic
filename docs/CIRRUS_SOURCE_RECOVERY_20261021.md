# Cirrus source publication failure — October 21, 2026

Orographic run 39670206019 failed R2 canonical publication: the 409 MiB
`prospective_pick_ledger.json` exceeds Wrangler's 300 MiB limit. Retrying the
same REST upload cannot recover it. The current Cirrus source pointer still
identifies successful October 12 run 38263538391, artifact 11754038268,
bundle 4b6369e8fac8f67559c8, manifest SHA-256
cdf13260d4d23d28c3339ecd19d394f9225a4528c3971b3f18277f5cbcdf4769.
The immutable pointer was not advanced past failed archival.

Use S3 multipart only above Wrangler's limit. Original object keys, file bytes,
manifest hashes and manifest-last publication stay intact. Derive S3 credentials
in memory from the existing active R2 token ID and SHA-256(token value), as
[documented by Cloudflare](https://developers.cloudflare.com/r2/api/tokens/).
Account-owned and user-owned token verification are supported; secrets are
neither printed nor written. Boto3 retries parts and aborts failed multipart
transfers; two workers and 64 MiB parts bound memory. Smaller uploads retain the
existing Wrangler retry path. No new token, collector or schedule is added.

Separately, Cirrus run 39641001317 hit GitHub Actions artifact storage quota
after collecting 7,379 candidates with full universe coverage and sending
Hybrid META261023C00810000 at 17:20:01 UTC. Its durable delivery record is intact,
but raw hosted artifacts were not retained. This patch does not resolve that
account storage limitation. Increase capacity or explicitly approve cleanup
of verified redundant artifacts; preserve immutable sources and review evidence.

Weekly review 39315413909 failed with `Backtest dataset stale; refresh the
canonical source first`, and its evidence upload also hit the storage quota.
Release 521c84527440-20261012T224322 remains the last release, approaching its
10-day maximum age. Do not bypass freshness checks or publish from this task.
After deployment, verify the next normally scheduled successful source and
its exact artifact/manifest pointer. Weekly model review remains the only owner
of a fresh Cirrus release; this patch dispatches no scan, email or publication.

Cirrus experiment hybrid-liquidity-penalty-001 remains unscored at protocol
95a9c0e74889848a6a3cd3ab365810ea911f05f6e2118bb2c2a492d793375bac,
dataset 44dff98c6e27eab8d374efae679f8c80ece1a3b4f392dd16a3836944787191d6.
All eight retained input artifacts were rehashed successfully. Its independent
execution/admission/family gates remain unresolved; no substitute experiment,
new comparison, retuning or prospective backfill occurred. This run prioritizes
recovery of reproducible production/data defects. New attempted variants and
comparisons: zero/zero; cumulative executed registered candidates/comparisons:
two/four. Research results remain unavailable, not zero.

Validation: 21 focused tests pass, including a sparse 409 MiB regression,
Wrangler threshold boundary, unchanged immutable destination, blocked manifest
publication on upload failure, token derivation/cache and invalid-token rejection,
plus existing evidence/restore/operational-ledger checks. No live authenticated
S3 upload was performed locally; scheduled recovery must verify token access
and actual hosted upload before declaring the source repaired.
