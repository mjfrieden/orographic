from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.evidence_store import validate_canonical_bundle  # noqa: E402


ARCHIVE_ROOT = "orographic/research-data"
CANONICAL_PREFIX = "orographic/evidence-canonical/current"
CIRRUS_EXPORT_ROOT = "cirrus"
CIRRUS_EXPORT_PREFIX = "cirrus/options_research_bundle/current"
# Cheap extra listings for bundles uploaded under the wrong root. Do not list
# orographic/research-data/ — that prefix is the live-options archive.
CIRRUS_EXPORT_SEARCH_PREFIXES = (
    "cirrus/",
    "orographic/cirrus/",
    "options_research_bundle/",
)


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


# Wrangler's REST transport caps individual objects at 300 MiB. Keep canonical
# bytes/keys unchanged; use S3 multipart for large objects instead of compression
# or a new manifest format that existing restorers would not understand.
WRANGLER_MAX_BYTES = 300 * 1024 * 1024


@lru_cache(maxsize=1)
def _large_object_client():
    import boto3
    from botocore.config import Config

    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = (os.environ.get("CLOUDFLARE_R2_API_TOKEN") or
             os.environ.get("CLOUDFLARE_API_TOKEN", "")).strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not token:
        raise RuntimeError("Large R2 uploads require the Cloudflare account and R2 API token")
    # Both account-owned and user-owned R2 tokens are supported. Never log the
    # token, derived S3 secret, or verification response.
    token_id = None
    for path in (f"accounts/{account}/tokens/verify", "user/tokens/verify"):
        request = Request("https://api.cloudflare.com/client/v4/" + path,
                          headers={"Authorization": f"Bearer {token}"})
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.loads(response.read())
        except HTTPError as exc:
            if exc.code in (400, 401, 403):
                continue
            raise RuntimeError("R2 token verification request failed") from None
        result = payload.get("result") or {}
        if payload.get("success") and result.get("status") == "active":
            token_id = result.get("id")
            break
    if not isinstance(token_id, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", token_id):
        raise RuntimeError("Cannot verify an active R2 API token for multipart upload")
    # Cloudflare documents S3 credentials as token ID + SHA256(token value).
    return boto3.client(
        "s3", endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=token_id,
        aws_secret_access_key=hashlib.sha256(token.encode()).hexdigest(),
        region_name="auto",
        config=Config(retries={"mode": "standard", "max_attempts": 4},
                      request_checksum_calculation="when_required",
                      response_checksum_validation="when_required"),
    )



def _put_object(bucket: str, object_key: str, file_path: Path) -> None:
    if file_path.stat().st_size > WRANGLER_MAX_BYTES:
        from boto3.s3.transfer import TransferConfig

        _large_object_client().upload_file(
            str(file_path), bucket, object_key,
            Config=TransferConfig(multipart_threshold=64 * 1024 * 1024,
                                  multipart_chunksize=64 * 1024 * 1024,
                                  max_concurrency=2),
        )
        print(f"Uploaded {file_path} -> r2://{bucket}/{object_key} (multipart)")
        return
    command = [
        "npx", "wrangler", "r2", "object", "put",
        f"{bucket}/{object_key}", "--remote", "--file", str(file_path),
    ]
    for attempt in range(4):
        result = subprocess.run(command, capture_output=True, text=True)
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            print(f"Uploaded {file_path} -> r2://{bucket}/{object_key}")
            return
        # Retrying the same object is idempotent. Do not retry authorization,
        # invalid input, or unknown failures, and preserve manifest-last ordering.
        transient = re.search(r"\b(?:429|5\d\d)\b|timed? out|ECONNRESET|ETIMEDOUT|fetch failed", output, re.I)
        if not transient or attempt == 3:
            print(output, file=sys.stderr)
            raise subprocess.CalledProcessError(
                result.returncode, command, output=result.stdout, stderr=result.stderr,
            )
        delay = 2 ** (attempt + 1)
        print(f"Transient R2 upload failure; retry {attempt + 1}/3 in {delay}s.")
        time.sleep(delay)


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
    manifest = validate_canonical_bundle(bundle)
    # Immutable destinations make publishing the current manifest the sole commit
    # point. A failed upload cannot replace bytes referenced by the previous one.
    generation = _sha256(bundle / "evidence_manifest.json")
    for record in manifest["files"]:
        relative = str(record["path"])
        record["object_key"] = f"{prefix}-bundles/{generation}/{relative}"
        _put_object(bucket, record["object_key"], bundle / relative)
    with tempfile.TemporaryDirectory(prefix="orographic-publish-") as tmpdir:
        published = Path(tmpdir) / "evidence_manifest.json"
        published.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        _put_object(bucket, f"{prefix}/evidence_manifest.json", published)


def _upload_cirrus(*, bucket: str, prefix: str, bundle: Path) -> None:
    from engine.orographic.shared_research_mart import validate_cirrus_export

    validate_cirrus_export(bundle)
    manifest_path = bundle / "manifest.json"
    files = [path for path in sorted(bundle.rglob("*")) if path.is_file() and path != manifest_path]
    for file_path in files:
        relative = str(file_path.relative_to(bundle)).replace("\\", "/")
        _put_object(bucket, f"{prefix}/{relative}", file_path)
    # Publish the manifest last so restores treat it as the Cirrus commit point.
    _put_object(bucket, f"{prefix}/manifest.json", manifest_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload versioned Orographic research snapshots, the canonical evidence bundle, or a Cirrus options_research_bundle to R2."
    )
    parser.add_argument("--bucket", default=os.getenv("OROGRAPHIC_RESEARCH_R2_BUCKET", ""))
    parser.add_argument("--prefix", default="")
    parser.add_argument("--mode", choices=("archive", "canonical", "cirrus"), default="archive")
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
    if args.mode == "cirrus":
        bundle = args.paths[0] if args.paths else Path("output/cirrus_export")
        _upload_cirrus(
            bucket=bucket,
            prefix=str(args.prefix or CIRRUS_EXPORT_PREFIX).strip().strip("/"),
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
