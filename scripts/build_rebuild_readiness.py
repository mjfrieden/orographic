from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.rebuild_readiness import build_rebuild_readiness


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build fail-closed Orographic rebuild readiness.")
    parser.add_argument("--pair-readiness", type=Path, default=Path("web/data/diagnostics/scout_pair_readiness_latest.json"))
    parser.add_argument("--exit-shadow", type=Path, default=Path("web/data/diagnostics/exit_policy_shadow_latest.json"))
    parser.add_argument("--promotion", type=Path, default=Path("web/data/diagnostics/promotion_shadow_active_comparison_latest.json"))
    parser.add_argument("--scan-health", type=Path, default=Path("web/data/diagnostics/scan_health_summary_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("web/data/diagnostics/orographic_rebuild_readiness_latest.json"))
    args = parser.parse_args()
    artifact = build_rebuild_readiness(
        _read(args.pair_readiness), _read(args.exit_shadow), _read(args.promotion), _read(args.scan_health)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps({"status": artifact["status"], "gates": artifact["gates"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
