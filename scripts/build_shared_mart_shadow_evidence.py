#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.shared_mart_shadow import build_shared_mart_shadow_evidence  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build observation-only execution, exit, and cross-system mart diagnostics."
    )
    parser.add_argument(
        "--consumer-dir", type=Path, default=Path("output/shared_mart_consumers")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/data/diagnostics/shared_mart_shadow_evidence_latest.json"),
    )
    args = parser.parse_args()
    artifact = build_shared_mart_shadow_evidence(args.consumer_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": artifact["status"],
        "mart_id": artifact["mart_id"],
        "shadow_entry_gates": artifact["shadow_entry_gates"],
        "next_action": artifact["next_action"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
