from __future__ import annotations

import argparse
from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


OPERATIONAL_PREFIX = "orographic/operational-ledgers/v1"
LEDGER_PATHS = (
    Path("web/data/diagnostics/prospective_pick_ledger.json"),
    Path("web/data/diagnostics/moonshot_prospective_ledger.json"),
    Path("web/data/diagnostics/research_run_ledger.json"),
    Path("web/data/diagnostics/side_aware_scout_shadow_ledger.json"),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_key(prefix: str, path: Path, sha256: str) -> str:
    # Content-addressed objects keep the previous manifest readable if a later
    # multi-ledger upload stops before publishing its new manifest.
    return f"{prefix}/objects/{path.name}/{sha256}.json.gz"


def _wrangler_object(action: str, *, bucket: str, key: str, file_path: Path) -> None:
    subprocess.run(
        ["npx", "wrangler", "r2", "object", action, f"{bucket}/{key}", "--remote", "--file", str(file_path)],
        check=True,
    )


def push_ledgers(*, bucket: str, prefix: str, paths: tuple[Path, ...] = LEDGER_PATHS) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="orographic-operational-r2-") as tmpdir:
        temp_root = Path(tmpdir)
        for path in paths:
            if not path.is_file():
                continue
            raw = path.read_bytes()
            json.loads(raw)
            raw_sha256 = _sha256(raw)
            compressed = gzip.compress(raw, compresslevel=6, mtime=0)
            archive = temp_root / f"{path.name}.gz"
            archive.write_bytes(compressed)
            key = _object_key(prefix, path, raw_sha256)
            _wrangler_object("put", bucket=bucket, key=key, file_path=archive)
            records.append(
                {
                    "path": str(path).replace("\\", "/"),
                    "object_key": key,
                    "bytes": len(raw),
                    "compressed_bytes": len(compressed),
                    "sha256": raw_sha256,
                }
            )

        if not records:
            raise RuntimeError("No operational ledgers were available to upload.")
        manifest = {
            "artifact": "orographic_operational_ledger_manifest",
            "schema_version": 1,
            "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "prefix": prefix,
            "ledgers": records,
        }
        manifest_path = temp_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        _wrangler_object("put", bucket=bucket, key=f"{prefix}/manifest.json", file_path=manifest_path)
    return manifest


def pull_ledgers(
    *,
    bucket: str,
    prefix: str,
    allow_missing: bool = False,
    paths: tuple[Path, ...] = LEDGER_PATHS,
) -> int:
    allowed_paths = set(paths)
    with tempfile.TemporaryDirectory(prefix="orographic-operational-r2-") as tmpdir:
        temp_root = Path(tmpdir)
        manifest_path = temp_root / "manifest.json"
        try:
            _wrangler_object("get", bucket=bucket, key=f"{prefix}/manifest.json", file_path=manifest_path)
        except subprocess.CalledProcessError:
            if allow_missing:
                print(f"No operational ledger manifest found at r2://{bucket}/{prefix}; using repository seed data.")
                return 0
            raise
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = manifest.get("ledgers") if isinstance(manifest.get("ledgers"), list) else []
        restored = 0
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            destination = Path(str(record.get("path") or ""))
            if destination not in allowed_paths:
                raise RuntimeError(f"Operational manifest contains an unexpected path: {destination}")
            archive = temp_root / f"ledger-{index}.json.gz"
            _wrangler_object("get", bucket=bucket, key=str(record["object_key"]), file_path=archive)
            raw = gzip.decompress(archive.read_bytes())
            if _sha256(raw) != str(record.get("sha256")):
                raise RuntimeError(f"Checksum mismatch while restoring {destination}")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise RuntimeError(f"Expected a JSON object while restoring {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = destination.with_suffix(destination.suffix + ".r2tmp")
            staged.write_bytes(raw)
            staged.replace(destination)
            restored += 1
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore or publish mutable Orographic ledgers in compressed R2 objects.")
    parser.add_argument("action", choices=("pull", "push"))
    parser.add_argument("--bucket", default=os.getenv("OROGRAPHIC_RESEARCH_R2_BUCKET", ""))
    parser.add_argument("--prefix", default=OPERATIONAL_PREFIX)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    bucket = str(args.bucket).strip()
    if not bucket:
        raise SystemExit("OROGRAPHIC_RESEARCH_R2_BUCKET is required for operational ledger storage.")
    prefix = str(args.prefix).strip().strip("/")
    if args.action == "pull":
        restored = pull_ledgers(bucket=bucket, prefix=prefix, allow_missing=args.allow_missing)
        print(f"Restored {restored} operational ledgers from R2.")
    else:
        manifest = push_ledgers(bucket=bucket, prefix=prefix)
        print(f"Published {len(manifest['ledgers'])} compressed operational ledgers to R2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
