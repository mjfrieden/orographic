from __future__ import annotations

import argparse
from http.client import IncompleteRead
import json
import os
from pathlib import Path, PurePosixPath
import time
import socket
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.evidence_store import validate_canonical_bundle  # noqa: E402
from scripts.upload_research_artifacts_to_r2 import (  # noqa: E402
    ARCHIVE_ROOT,
    CANONICAL_PREFIX,
    CIRRUS_EXPORT_PREFIX,
    CIRRUS_EXPORT_ROOT,
)


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


def _list_delimited_prefixes(
    *,
    account_id: str,
    api_token: str,
    bucket: str,
    prefix: str = "",
) -> list[str]:
    """List folder-like prefixes without enumerating every object under them."""
    delimited: list[str] = []
    cursor = ""
    while True:
        query: dict[str, str | int] = {"prefix": prefix, "delimiter": "/", "per_page": 1000}
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
        info = payload.get("result_info") if isinstance(payload.get("result_info"), dict) else {}
        for item in info.get("delimited") or []:
            if isinstance(item, str) and item and item not in delimited:
                delimited.append(item)
        if not info.get("is_truncated"):
            break
        cursor = str(info.get("cursor") or "")
        if not cursor:
            raise RuntimeError("Cloudflare R2 object listing was truncated without a cursor.")
    return delimited


def _list_delimited_index(
    *,
    account_id: str,
    api_token: str,
    bucket: str,
    prefix: str = "",
) -> dict[str, list]:
    """List one folder level: child prefixes plus files in that folder only."""
    delimited: list[str] = []
    objects: list[dict[str, Any]] = []
    cursor = ""
    while True:
        query: dict[str, str | int] = {"prefix": prefix, "delimiter": "/", "per_page": 1000}
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
        for item in info.get("delimited") or []:
            if isinstance(item, str) and item and item not in delimited:
                delimited.append(item)
        if not info.get("is_truncated"):
            break
        cursor = str(info.get("cursor") or "")
        if not cursor:
            raise RuntimeError("Cloudflare R2 object listing was truncated without a cursor.")
    return {
        "delimited": delimited,
        "objects": [
            {
                "key": str(row.get("key") or ""),
                "size": row.get("size"),
                "last_modified": row.get("last_modified"),
            }
            for row in objects
            if row.get("key")
        ],
    }


def _safe_relative(key: str, prefix: str) -> Path:
    relative = PurePosixPath(key).relative_to(PurePosixPath(prefix))
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"Unsafe R2 object key: {key}")
    return Path(*relative.parts)


def _get_object(bucket: str, key: str, destination: Path, *, account_id: str = "", api_token: str = "") -> None:
    account_id = account_id or os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_token = api_token or os.environ["CLOUDFLARE_API_TOKEN"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    url = (f"https://api.cloudflare.com/client/v4/accounts/{quote(account_id, safe='')}/r2/"
           f"buckets/{quote(bucket, safe='')}/objects/{quote(key, safe='/')}")
    request = Request(url, headers={"Authorization": f"Bearer {api_token}"})
    for attempt in range(3):
        try:
            print(f"Downloading {key}", flush=True)
            with urlopen(request, timeout=60) as response, partial.open("wb") as handle:
                started = time.monotonic()
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
                    if time.monotonic() - started > 300:
                        raise TimeoutError("R2 download exceeded five minutes")
            partial.replace(destination)
            return
        except (HTTPError, URLError, TimeoutError, socket.timeout, ConnectionError, IncompleteRead) as exc:
            partial.unlink(missing_ok=True)
            transient = not isinstance(exc, HTTPError) or exc.code == 429 or 500 <= exc.code < 600
            if not transient or attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))


def cirrus_bundle_prefixes(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return Cirrus export prefixes that contain a commit-point manifest.

    Current (`cirrus/options_research_bundle/current`) sorts first when present.
    Dated or mis-prefixed siblings follow by last_modified descending so a scan
    can rebuild the two-source mart from the newest valid fallback.
    """
    prefixes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in objects:
        key = str(row.get("key") or "")
        if not key.endswith("/manifest.json"):
            continue
        prefix = key[: -len("/manifest.json")].strip("/")
        lowered = prefix.lower()
        looks_cirrus = (
            lowered.startswith(CIRRUS_EXPORT_ROOT)
            or lowered.startswith("orographic/cirrus/")
            or lowered == "options_research_bundle"
            or "/options_research_bundle" in f"/{lowered}"
            or lowered.startswith("cirrus/")
        )
        if not looks_cirrus or prefix in seen:
            continue
        seen.add(prefix)
        prefixes.append(
            {
                "prefix": prefix,
                "manifest_key": key,
                "last_modified": row.get("last_modified"),
                "bytes": row.get("size"),
                "is_current": prefix == CIRRUS_EXPORT_PREFIX,
            }
        )
    prefixes.sort(
        key=lambda row: (bool(row["is_current"]), str(row.get("last_modified") or "")),
        reverse=True,
    )
    return prefixes


def restore_prefix(
    *,
    bucket: str,
    account_id: str,
    api_token: str,
    prefix: str,
    output_dir: Path,
    include_suffixes: tuple[str, ...] = (),
    max_objects: int = 0,
) -> int:
    normalized = prefix.strip().strip("/")
    rows = _list_objects(
        account_id=account_id,
        api_token=api_token,
        bucket=bucket,
        prefix=f"{normalized}/",
    )
    keys = sorted(str(row.get("key") or "") for row in rows if row.get("key"))
    manifest_key = f"{normalized}/evidence_manifest.json"
    if manifest_key in keys:
        manifest_path = output_dir / "evidence_manifest.json"
        _get_object(bucket, manifest_key, manifest_path, account_id=account_id, api_token=api_token)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = manifest.get("files")
        if not isinstance(records, list) or not records:
            raise ValueError("Canonical evidence manifest contains no files")
        for record in records:
            relative = _safe_relative(str(record["path"]), "")
            key = str(record.get("object_key") or f"{normalized}/{relative.as_posix()}")
            allowed = f"{normalized}-bundles/" if record.get("object_key") else f"{normalized}/"
            _safe_relative(key, allowed)
            _get_object(bucket, key, output_dir / relative, account_id=account_id, api_token=api_token)
        validate_canonical_bundle(output_dir)
        return len(records) + 1
    if include_suffixes:
        keys = [key for key in keys if key.endswith(include_suffixes)]
    if max_objects > 0:
        keys = keys[-max_objects:]
    for key in keys:
        destination = output_dir / _safe_relative(key, f"{normalized}/")
        _get_object(bucket, key, destination, account_id=account_id, api_token=api_token)
    return len(keys)


def restore_canonical(*, bucket: str, prefix: str, output_dir: Path) -> int:
    """Snapshot the pointer once, then fetch only its immutable referenced objects."""
    manifest_path = output_dir / 'evidence_manifest.json'
    _get_object(bucket, prefix.rstrip('/') + '/evidence_manifest.json', manifest_path)
    manifest = json.loads(manifest_path.read_text())
    objects = manifest.get('object_prefix', prefix.rstrip('/'))
    allowed = prefix.rstrip('/').rsplit('/', 1)[0] + '/versions/'
    if objects != prefix.rstrip('/') and not objects.startswith(allowed):
        raise ValueError('Unexpected canonical object prefix')
    for record in manifest['files']:
        relative = _safe_relative(prefix.rstrip('/') + '/' + record['path'], prefix.rstrip('/') + '/')
        _get_object(bucket, objects + '/' + relative.as_posix(), output_dir / relative)
    validate_canonical_bundle(output_dir)
    return len(manifest['files']) + 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore Orographic canonical evidence, a Cirrus options_research_bundle, or legacy research snapshots from R2."
    )
    parser.add_argument("--bucket", default=os.getenv("OROGRAPHIC_RESEARCH_R2_BUCKET", ""))
    parser.add_argument("--account-id", default=os.getenv("CLOUDFLARE_ACCOUNT_ID", ""))
    parser.add_argument("--api-token", default=os.getenv("CLOUDFLARE_API_TOKEN", ""))
    parser.add_argument("--mode", choices=("canonical", "legacy", "cirrus"), default="canonical")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--max-objects", type=int, default=0)
    parser.add_argument("--allow-missing", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bucket = str(args.bucket).strip()
    account_id = str(args.account_id).strip()
    api_token = str(args.api_token).strip()
    if not all((bucket, account_id, api_token)):
        message = "R2 restore requires bucket, CLOUDFLARE_ACCOUNT_ID, and CLOUDFLARE_API_TOKEN."
        if args.allow_missing:
            print(message + " Skipping restore.")
            return 0
        raise SystemExit(message)

    if args.mode == "canonical":
        prefix = str(args.prefix or CANONICAL_PREFIX)
        output = args.output_dir or Path("output/restored_canonical_evidence")
        suffixes = (".json", ".parquet")
    elif args.mode == "cirrus":
        prefix = str(args.prefix or CIRRUS_EXPORT_PREFIX)
        output = args.output_dir or Path("output/cirrus_export")
        suffixes = (".json", ".parquet")
    else:
        prefix = str(args.prefix or ARCHIVE_ROOT)
        output = args.output_dir or Path("output/restored_legacy_evidence")
        suffixes = (
            "/manifest.json",
            "/chain.parquet",
            "/all_recommendation_outcomes.parquet",
            "/option_recommendation_outcomes.parquet",
            "/moonshot_outcomes.parquet",
        )

    if args.mode == 'canonical':
        restored = restore_canonical(bucket=bucket, prefix=prefix, output_dir=output)
    else:
        restored = restore_prefix(
            bucket=bucket, account_id=account_id, api_token=api_token,
            prefix=prefix, output_dir=output, include_suffixes=suffixes,
            max_objects=max(int(args.max_objects), 0),
        )
    if restored == 0:
        message = f"No R2 objects found under {prefix}."
        if args.allow_missing:
            print(message)
            return 0
        raise SystemExit(message)
    if args.mode == "canonical":
        validate_canonical_bundle(output)
    elif args.mode == "cirrus":
        from engine.orographic.shared_research_mart import validate_cirrus_export

        validate_cirrus_export(output)
    print(f"Restored {restored} objects from r2://{bucket}/{prefix} into {output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
