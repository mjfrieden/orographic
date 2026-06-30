#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

git fetch origin main
git restore --source origin/main --worktree -- web/data

python scripts/build_research_datasets.py \
  --prospective-ledger web/data/diagnostics/prospective_pick_ledger.json \
  --moonshot-ledger web/data/diagnostics/moonshot_prospective_ledger.json \
  --output-dir output/research_datasets

python scripts/audit_research_data_capture.py \
  --prospective-ledger web/data/diagnostics/prospective_pick_ledger.json \
  --moonshot-ledger web/data/diagnostics/moonshot_prospective_ledger.json \
  --research-dataset-dir output/research_datasets \
  --min-archive-rows 0 \
  --allow-missing-empty-moonshot-ledger \
  --output output/research_datasets/research_data_capture_audit.json

python - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("web/data/latest_run.json").read_text(encoding="utf-8"))
research_audit = json.loads(Path("output/research_datasets/research_data_capture_audit.json").read_text(encoding="utf-8"))
print("Synced web/data/latest_run.json")
print("generated_at_utc:", payload.get("generated_at_utc"))
print(
    "live_board:",
    [row.get("contract_symbol") for row in payload.get("council", {}).get("live_board", [])],
)
print("Synced web/data/diagnostics/")
print("research_dataset_rows:", research_audit.get("summary", {}).get("combined_dataset_rows"))
PY
