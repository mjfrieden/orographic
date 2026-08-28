from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.model_governance import build_model_governance_summary


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the model-governance UI artifact.")
    parser.add_argument("--scan-health", type=Path, default=Path("web/data/diagnostics/scan_health_summary_latest.json"))
    parser.add_argument("--capture-health", type=Path, default=Path("web/data/diagnostics/outcome_capture_health_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("web/data/diagnostics/model_governance_summary_latest.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_model_governance_summary(
        scan_health=_load(args.scan_health),
        capture_health=_load(args.capture_health),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output), "summary": report["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
