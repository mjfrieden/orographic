# Canonical archive preflight and retained Scout inputs

The October 30 scan run 41123120940 refreshed the dashboard but failed canonical
R2 publication. Small operational ledgers succeeded in capture 41123788959:
they use gzip and Wrangler REST authentication. Canonical files larger than
314,572,800 bytes use the existing bounded S3 multipart implementation. Its
explicit S3 credentials were absent in the scan environment. The retained
canonical prospective ledger is 318,919,097 bytes; this is not a total R2 outage.

The scan upload step now validates the canonical bundle and inventories *both*
upload groups before either writes remotely. `output/r2_archive_preflight.json`
is retained in the fallback GitHub artifact. It contains file sizes, transports,
missing configuration **names**, and no credential values. Configuration-ready
is not a remote authorization or publication receipt. A blocked preflight exits
nonzero; no current canonical manifest advances. Uploaders also check their own
file groups. Missing/empty archives fail; changed source bytes prevent a versioned
archive manifest from publishing. Existing manifest-last canonical staging stays
in place. No schedules, models, retry policies or Cirrus publication logic change.

The intentional behavior change is confined to the final research archive step:
missing multipart configuration now prevents *both* versioned research and
canonical uploads instead of allowing a partial archive first. Operational
ledger publication and dashboard deployment occur earlier and stay independent.
The separate outcome-capture workflow is unchanged. The archive step still
continues to health reporting and the always-run fallback artifact, then reports
failure without publishing a source pointer. No previously safe operational
ledger write is conditioned on the new preflight.

An authorized operator must first determine whether already-provisioned
`R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` exist and are available to the scan
step. Its workflow already maps those secret names. The existing Cloudflare REST
token cannot substitute. This patch creates no credentials and cannot repair
runtime secret availability. Applying configuration or rerunning archival needs
separate authorization; preserve the fallback artifact and previous canonical
generation. Do not delete or recreate remote objects.

## Reproducible read-only input audit

```powershell
python scripts/audit_scout_input_archive.py retained.zip --sha256 <external-sha256> --output new-audit.json
```

The tool checks the externally pinned ZIP, verifies every canonical member's
size/hash, inventories JSON keys and Parquet schemas, and computes the strict
pair corpus. It creates no model, evaluator input or completion receipt. The
output must be new so earlier audit observations are preserved. Schema/key
presence is only discovery evidence, never proof of timely feature availability.

Verified artifact 13010672158 from scan 41123120940:

- ZIP SHA-256 `123b9f111a9b904559176a14058bcfdbd74a17264669ca9d3a31d754d686ed11`.
- Four canonical members validate; 1,132 complete pairs across 54 dates.
- Pair corpus `231f4e33c0c4de9a75deb3b1a7d94695db70c16b7ad9aa4b2d4cbdcd332fa1cc`.
- No `mom_5d`, `mom_10d`, `rv20`, `feature_observed_at_utc`,
  `feature_available_at_utc`, or `feature_source_sha256` keys/columns were found.
- The daily event feature file contains event/narrative fields, not the required
  core Scout vectors or intraday availability lineage.
- A separate retained-ledger audit matches recorded Scout direction, model and
  scaler references, and both original decision clocks for all 1,132 pairs.
  This is recorded baseline lineage, not proof of actual orders or full policy.

The exact remaining input is an existing immutable core Scout feature archive
keyed to these pair IDs and exact decision instants, including original observed
and available clocks and source hashes. No verified filename/location is known.
An authorized original archive export/pointer is required; modern market data,
model scores and same-date fabricated clocks must not fill this gap. True
completed fold evaluation remains 0/3; no training was performed for this repair.
