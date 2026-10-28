from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.rebuild_readiness import (
    build_rebuild_readiness,
    consumer_from_shadow_evidence,
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_optional(path: Path) -> dict:
    return _read(path) if path.exists() else {}


def _resolve_mart_consumer(manifest_path: Path, shadow_path: Path) -> dict:
    manifest = _read_optional(manifest_path)
    if manifest.get("artifact") == "orographic_shared_mart_consumer_bundle":
        return manifest
    return consumer_from_shadow_evidence(_read_optional(shadow_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build fail-closed Orographic rebuild readiness.")
    parser.add_argument("--pair-readiness", type=Path, default=Path("web/data/diagnostics/scout_pair_readiness_latest.json"))
    parser.add_argument("--exit-shadow", type=Path, default=Path("web/data/diagnostics/exit_policy_shadow_latest.json"))
    parser.add_argument("--promotion", type=Path, default=Path("web/data/diagnostics/promotion_shadow_active_comparison_latest.json"))
    parser.add_argument("--scan-health", type=Path, default=Path("web/data/diagnostics/scan_health_summary_latest.json"))
    parser.add_argument(
        "--mart-consumer-manifest",
        type=Path,
        default=Path("output/shared_mart_consumers/consumer_manifest.json"),
    )
    parser.add_argument(
        "--mart-shadow",
        type=Path,
        default=Path("web/data/diagnostics/shared_mart_shadow_evidence_latest.json"),
    )
    parser.add_argument("--output", type=Path, default=Path("web/data/diagnostics/orographic_rebuild_readiness_latest.json"))
    args = parser.parse_args()
    artifact = build_rebuild_readiness(
        _read(args.pair_readiness),
        _read(args.exit_shadow),
        _read(args.promotion),
        _read(args.scan_health),
        _resolve_mart_consumer(args.mart_consumer_manifest, args.mart_shadow),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps({"status": artifact["status"], "gates": artifact["gates"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
