#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

git fetch origin main

python - <<'PY'
import subprocess
from pathlib import Path

paths = subprocess.check_output(
    ["git", "ls-tree", "-r", "--name-only", "origin/main", "web/data"],
    text=True,
).splitlines()

for path in paths:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = subprocess.check_output(["git", "show", f"origin/main:{path}"])
    dest.write_bytes(content)

print(f"Synced {len(paths)} tracked production artifact files from origin/main")
PY

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

scan_health_path = Path("web/data/diagnostics/scan_health_summary_latest.json")
if scan_health_path.exists():
    scan_health = json.loads(scan_health_path.read_text(encoding="utf-8"))
    print("Synced web/data/diagnostics/scan_health_summary_latest.json")
    print("scan_health_status:", scan_health.get("status"))
    print(
        "scan_health_snapshot_generated_at_utc:",
        scan_health.get("snapshot", {}).get("generated_at_utc"),
    )
PY
