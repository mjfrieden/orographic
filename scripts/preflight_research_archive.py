"""Read-only configuration inventory before either scan archive upload begins."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.r2_multipart import preflight_files
from scripts.upload_research_artifacts_to_r2 import _iter_files
from engine.orographic.evidence_store import validate_canonical_bundle


def inspect_archive(canonical: Path, paths: list[Path]) -> dict:
    manifest = validate_canonical_bundle(canonical)
    files = [path for _, path in _iter_files(paths)]
    files.extend(canonical / str(row["path"]) for row in manifest["files"])
    result = preflight_files(files)
    result.update(artifact="orographic_archive_preflight", schema_version=1,
                  generated_at_utc=datetime.now(timezone.utc).isoformat(),
                  canonical_bundle_id=manifest.get("bundle_id"),
                  evidence_classification="configuration_only_not_remote_verification",
                  next_action=("Map the listed existing authorized R2 S3 credentials on the upload step; "
                               "do not derive them from the Wrangler REST token."
                               if result["status"] == "blocked" else
                               "Size-dependent configuration is present; remote authorization and publication remain unverified."))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        report = inspect_archive(args.canonical, args.paths)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        report = {"artifact": "orographic_archive_preflight", "schema_version": 1,
                  "status": "invalid_local_inputs", "publication_verified": False,
                  "remote_requests_performed": False, "error_type": type(exc).__name__,
                  "canonical_path": str(args.canonical), "requested_paths": [str(p) for p in args.paths],
                  "next_action": "Validate the canonical manifest and required local paths before upload; no remote requests were made."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "files"}, indent=2))
    return 0 if report["status"] == "configuration_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
