#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

git fetch origin main
git show origin/main:web/data/latest_run.json > web/data/latest_run.json

python - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("web/data/latest_run.json").read_text(encoding="utf-8"))
print("Synced web/data/latest_run.json")
print("generated_at_utc:", payload.get("generated_at_utc"))
print(
    "live_board:",
    [row.get("contract_symbol") for row in payload.get("council", {}).get("live_board", [])],
)
PY
