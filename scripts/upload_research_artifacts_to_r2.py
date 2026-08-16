from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.evidence_store import validate_canonical_bundle  # noqa: E402


ARCHIVE_ROOT = "orographic/research-data"
CANONICAL_PREFIX = "orographic/evidence-canonical/current"


def _default_prefix() -> str:
    return datetime.now(UTC).strftime(f"{ARCHIVE_ROOT}/%Y/%m/%d/%H%M%S")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(paths: list[Path]) -> list[tuple[Path, Path]]:
    files: list[tuple[Path, Path]] = []
    for path in paths:
        if path.is_file():
            files.append((path.parent, path))
        elif path.is_dir():
            files.extend((path, child) for child in sorted(path.rglob("*")) if child.is_file())
    return files


def _put_object(bucket: str, object_key: str, file_path: Path) -> None:
    subprocess.run(
        [
            "npx",
            "wrangler",
            "r2",
            "object",
            "put",
            f"{bucket}/{object_key}",
            "--remote",
            "--file",
            str(file_path),
        ],
        check=True,
    )
    print(f"Uploaded {file_path} -> r2://{bucket}/{object_key}")


def _list_objects(
    *,
    account_id: str,
    api_token: str,
    bucket: str,
    prefix: str,
) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    cursor = ""
    while True:
        query: dict[str, str | int] = {"prefix": prefix, "per_page": 1000}
        if cursor:
            query["cursor"] = cursor
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{account_id}/r2/buckets/{bucket}/objects?{urlencode(query)}"
        )
        request = Request(url, headers={"Authorization": f"Bearer {api_token}"})
        with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed Cloudflare API origin
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("success"):
            raise RuntimeError(f"Cloudflare R2 object listing failed: {payload.get('errors')}")
        objects.extend(row for row in payload.get("result", []) if isinstance(row, dict))
        info = payload.get("result_info") if isinstance(payload.get("result_info"), dict) else {}
        if not info.get("is_truncated"):
            break
        cursor = str(info.get("cursor") or "")
        if not cursor:
            raise RuntimeError("Cloudflare R2 object listing was truncated without a cursor.")
    return objects


def _archive_manifest(prefix: str, files: list[tuple[Path, Path]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for base, file_path in files:
        relative = file_path.name if base == file_path else str(file_path.relative_to(base))
        object_key = f"{prefix}/{base.name}/{relative}".replace("\\", "/")
        records.append(
            {
                "object_key": object_key,
                "source_root": base.name,
                "relative_path": relative.replace("\\", "/"),
                "bytes": file_path.stat().st_size,
                "sha256": _sha256(file_path),
            }
        )
    return {
        "artifact": "orographic_research_snapshot_manifest",
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "prefix": prefix,
        "file_count": len(records),
        "total_bytes": sum(int(row["bytes"]) for row in records),
        "files": records,
    }


def _upload_archive(
    *,
    bucket: str,
    prefix: str,
    paths: list[Path],
    account_id: str,
    api_token: str,
) -> None:
    files = _iter_files(paths)
    if not files:
        print("No research artifact files found to upload.")
        return
    manifest = _archive_manifest(prefix, files)
    for (base, file_path), record in zip(files, manifest["files"], strict=True):
        _put_object(bucket, str(record["object_key"]), file_path)

    with tempfile.TemporaryDirectory(prefix="orographic-r2-manifest-") as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifest_key = f"{prefix}/manifest.json"
        _put_object(bucket, manifest_key, manifest_path)

        if account_id and api_token:
            objects = _list_objects(
                account_id=account_id,
                api_token=api_token,
                bucket=bucket,
                prefix=f"{ARCHIVE_ROOT}/",
            )
            manifests = sorted(
                (
                    {
                        "manifest_key": str(row.get("key")),
                        "last_modified": row.get("last_modified"),
                        "bytes": row.get("size"),
                        "etag": row.get("etag"),
                    }
                    for row in objects
                    if str(row.get("key") or "").endswith("/manifest.json")
                ),
                key=lambda row: str(row["manifest_key"]),
            )
            catalog = {
                "artifact": "orographic_research_snapshot_catalog",
                "schema_version": 1,
                "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
                "snapshot_count": len(manifests),
                "snapshots": manifests,
            }
            catalog_path = Path(tmpdir) / "catalog.json"
            catalog_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
            _put_object(bucket, f"{ARCHIVE_ROOT}/catalog.json", catalog_path)
        else:
            print("Cloudflare account/token unavailable; snapshot manifest uploaded without catalog refresh.")


def _upload_canonical(*, bucket: str, prefix: str, bundle: Path) -> None:
    validate_canonical_bundle(bundle)
    manifest_path = bundle / "evidence_manifest.json"
    files = [path for path in sorted(bundle.rglob("*")) if path.is_file() and path != manifest_path]
    for file_path in files:
        relative = str(file_path.relative_to(bundle)).replace("\\", "/")
        _put_object(bucket, f"{prefix}/{relative}", file_path)
    # Publish the manifest last.  Readers use it as the commit point for the
    # canonical materialization and validate every referenced hash after restore.
    _put_object(bucket, f"{prefix}/evidence_manifest.json", manifest_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload versioned Orographic research snapshots or the canonical evidence bundle to R2."
    )
    parser.add_argument("--bucket", default=os.getenv("OROGRAPHIC_RESEARCH_R2_BUCKET", ""))
    parser.add_argument("--prefix", default="")
    parser.add_argument("--mode", choices=("archive", "canonical"), default="archive")
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path("engine/data/live_options_archive"), Path("output/research_datasets")],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bucket = str(args.bucket).strip()
    if not bucket:
        print("OROGRAPHIC_RESEARCH_R2_BUCKET is not configured; skipping R2 upload.")
        return 0
    if args.mode == "canonical":
        bundle = args.paths[0] if args.paths else Path("output/canonical_evidence")
        _upload_canonical(
            bucket=bucket,
            prefix=str(args.prefix or CANONICAL_PREFIX).strip().strip("/"),
            bundle=bundle,
        )
        return 0

    _upload_archive(
        bucket=bucket,
        prefix=str(args.prefix or _default_prefix()).strip().strip("/"),
        paths=args.paths,
        account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip(),
        api_token=os.getenv("CLOUDFLARE_API_TOKEN", "").strip(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
