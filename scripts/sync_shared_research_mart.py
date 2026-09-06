#!/usr/bin/env python3
"""Restore a Cirrus export if present and rebuild the shared research mart."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.shared_mart_consumers import (  # noqa: E402
    build_shared_mart_consumer_bundle,
)
from engine.orographic.shared_mart_shadow import build_shared_mart_shadow_evidence  # noqa: E402
from engine.orographic.shared_research_mart import (  # noqa: E402
    build_shared_research_mart,
    validate_cirrus_export,
    validate_shared_research_mart,
)
from scripts.restore_research_artifacts_from_r2 import restore_prefix  # noqa: E402
from scripts.upload_research_artifacts_to_r2 import CIRRUS_EXPORT_PREFIX  # noqa: E402


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _candidate_cirrus_dirs(explicit: Path | None) -> list[Path]:
    dirs = []
    if explicit is not None:
        dirs.append(explicit)
    dirs.extend(
        [
            Path("output/cirrus_export"),
            Path("../Cirrus/analysis/output/options_research_bundle"),
        ]
    )
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in dirs:
        resolved = path if path.exists() else path
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _valid_cirrus_dir(path: Path) -> Path | None:
    if not (path / "manifest.json").exists():
        return None
    try:
        validate_cirrus_export(path)
    except (OSError, ValueError, KeyError):
        return None
    return path


def _restore_cirrus_from_r2(output_dir: Path, allow_missing: bool) -> dict:
    bucket = os.getenv("OROGRAPHIC_RESEARCH_R2_BUCKET", "").strip()
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = (
        os.getenv("CLOUDFLARE_R2_API_TOKEN")
        or os.getenv("CLOUDFLARE_API_TOKEN")
        or ""
    ).strip()
    prefix = os.getenv("OROGRAPHIC_CIRRUS_EXPORT_R2_PREFIX", CIRRUS_EXPORT_PREFIX).strip()
    if not all((bucket, account_id, api_token)):
        return {"status": "skipped_missing_credentials", "prefix": prefix, "objects": 0}
    try:
        restored = restore_prefix(
            bucket=bucket,
            account_id=account_id,
            api_token=api_token,
            prefix=prefix,
            output_dir=output_dir,
        )
    except Exception as exc:  # noqa: BLE001 - sync must fail closed to a diagnostic, not the live scan
        if not allow_missing:
            raise
        return {"status": "restore_failed", "prefix": prefix, "error": str(exc), "objects": 0}
    if restored == 0:
        return {"status": "missing", "prefix": prefix, "objects": 0}
    return {"status": "restored", "prefix": prefix, "objects": restored, "output_dir": str(output_dir)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Keep the Cirrus + Orographic shared research mart in sync with this repo."
    )
    parser.add_argument("--orographic-canonical-dir", type=Path, default=Path("output/canonical_evidence"))
    parser.add_argument("--cirrus-export-dir", type=Path, default=None)
    parser.add_argument("--mart-dir", type=Path, default=Path("output/shared_research_mart"))
    parser.add_argument("--consumer-dir", type=Path, default=Path("output/shared_mart_consumers"))
    parser.add_argument(
        "--shadow-output",
        type=Path,
        default=Path("web/data/diagnostics/shared_mart_shadow_evidence_latest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/data/diagnostics/shared_mart_sync_latest.json"),
    )
    parser.add_argument("--allow-missing", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    canonical = args.orographic_canonical_dir
    if not (canonical / "evidence_manifest.json").exists():
        payload = {
            "artifact": "orographic_shared_mart_sync",
            "schema_version": 1,
            "generated_at_utc": _now_iso(),
            "status": "missing_orographic_canonical",
            "source_systems": ["orographic"] if canonical.exists() else [],
            "next_action": "Build output/canonical_evidence before attempting a mart sync.",
        }
        _write(args.output, payload)
        print(json.dumps(payload, indent=2))
        return 0 if args.allow_missing else 1

    cirrus_dir = None
    restore_info: dict | None = None
    for candidate in _candidate_cirrus_dirs(args.cirrus_export_dir):
        cirrus_dir = _valid_cirrus_dir(candidate)
        if cirrus_dir is not None:
            break
    if cirrus_dir is None:
        restore_info = _restore_cirrus_from_r2(Path("output/cirrus_export"), args.allow_missing)
        cirrus_dir = _valid_cirrus_dir(Path("output/cirrus_export"))

    if cirrus_dir is None:
        payload = {
            "artifact": "orographic_shared_mart_sync",
            "schema_version": 1,
            "generated_at_utc": _now_iso(),
            "status": "cirrus_export_unavailable",
            "source_systems": ["orographic"],
            "restore": restore_info,
            "production_changes_allowed": False,
            "next_action": (
                "Publish a current Cirrus options_research_bundle to "
                f"r2://$OROGRAPHIC_RESEARCH_R2_BUCKET/{CIRRUS_EXPORT_PREFIX} "
                "or pass --cirrus-export-dir, then rerun this sync."
            ),
        }
        _write(args.output, payload)
        print(json.dumps(payload, indent=2))
        return 0 if args.allow_missing else 1

    manifest = build_shared_research_mart(
        orographic_canonical_dir=canonical,
        cirrus_export_dir=cirrus_dir,
        output_dir=args.mart_dir,
    )
    validate_shared_research_mart(args.mart_dir)
    consumer = build_shared_mart_consumer_bundle(args.mart_dir, args.consumer_dir)
    shadow = build_shared_mart_shadow_evidence(args.consumer_dir)
    args.shadow_output.parent.mkdir(parents=True, exist_ok=True)
    args.shadow_output.write_text(json.dumps(shadow, indent=2) + "\n", encoding="utf-8")
    payload = {
        "artifact": "orographic_shared_mart_sync",
        "schema_version": 1,
        "generated_at_utc": _now_iso(),
        "status": "ready_two_source",
        "mart_id": manifest.get("mart_id"),
        "source_systems": sorted({row.get("source_system") for row in manifest.get("sources", [])}),
        "rows": {name: artifact["rows"] for name, artifact in manifest.get("artifacts", {}).items()},
        "consumer_status": consumer.get("status"),
        "training_rows": (consumer.get("views") or {}).get("orographic_training_v1", {}).get("rows"),
        "shadow_status": shadow.get("status"),
        "cirrus_export_dir": str(cirrus_dir),
        "restore": restore_info,
        "production_changes_allowed": False,
        "next_action": "Keep using the mart for observation-only backtests; do not route from it.",
    }
    _write(args.output, payload)
    print(json.dumps({key: payload[key] for key in (
        "status", "mart_id", "source_systems", "training_rows", "shadow_status"
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
