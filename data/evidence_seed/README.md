# Evidence migration seed

`strict_option_outcomes.json.gz` is the one-time strict executable-label v2
dataset that existed locally before canonical R2 manifests and restore were
implemented. It contains 740 source rows generated on August 8, 2026.

The file is retained in Git so the first canonical evidence publication cannot
discard the only surviving copy. The canonical compactor reads it together with
new strict outcome artifacts, deduplicates by recommendation/window/contract/run,
and publishes the result to `orographic/evidence-canonical/current` in R2.

SHA-256 of the compressed seed:

```text
77310cf2ab7d5e7954847574830e6e4a56b92427e18df8ba971166396d31bbeb
```

This seed is immutable. New evidence must flow through operational ledgers and
the canonical R2 bundle; do not replace this file with a newer local export.
