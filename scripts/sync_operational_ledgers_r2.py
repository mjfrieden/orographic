"""Lossless v1 snapshots with verified restore receipts and manifest-last writes.

The receipt/stale checks are NOT compare-and-swap. Every writer must hold the
same namespace-wide lock across pull, local update, and push (see workflows).
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

OPERATIONAL_PREFIX = "orographic/operational-ledgers/v1"
STATE_PATH = Path("output/operational-ledger-restore.json")
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024  # Existing Wrangler transport limit.
MAX_MANIFEST_BYTES = 1024 * 1024
COPY_BYTES = 1024 * 1024
LEDGER_PATHS = (
    Path("web/data/diagnostics/prospective_pick_ledger.json"),
    Path("web/data/diagnostics/research_run_ledger.json"),
)
RETIRED_LEDGER_PATHS = {
    Path("web/data/diagnostics/moonshot_prospective_ledger.json"),
    Path("web/data/diagnostics/side_aware_scout_shadow_ledger.json"),
}


class MissingObject(RuntimeError):
    """A specific R2 object is absent, not a generic read/auth/bucket failure."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_key(prefix: str, path: Path, sha256: str) -> str:
    return f"{prefix}/objects/{path.name}/{sha256}.json.gz"


def _wrangler_object(action: str, *, bucket: str, key: str, file_path: Path) -> None:
    try:
        subprocess.run(
            ["npx", "wrangler", "r2", "object", action, f"{bucket}/{key}", "--remote", "--file", str(file_path)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        # Wrangler uses this specific message for R2 NoSuchKey. Generic HTTP
        # 404, missing bucket, token, network and unrecognized errors fail closed.
        output = (exc.stdout or "") + "\n" + (exc.stderr or "")
        if action == "get" and "The specified key does not exist." in output:
            raise MissingObject(f"R2 object is missing: {key}") from exc
        raise


def _write_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, separators=(",", ":"))
        staged = Path(stream.name)
    staged.replace(path)


def _scope(bucket: str, prefix: str, paths: tuple[Path, ...]) -> dict[str, Any]:
    return {"bucket": bucket, "prefix": prefix, "paths": [str(path) for path in paths]}


def _validate_manifest(raw: bytes, *, prefix: str, paths: tuple[Path, ...]) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise RuntimeError("Operational manifest exceeds its size limit.")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("artifact") != "orographic_operational_ledger_manifest" or manifest.get("schema_version") != 1 or manifest.get("prefix") != prefix:
        raise RuntimeError("Unsupported operational manifest or wrong storage prefix.")
    records = manifest.get("ledgers")
    if not isinstance(records, list):
        raise RuntimeError("Operational manifest has no ledger list.")
    seen: set[Path] = set()
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("Invalid operational ledger record.")
        path = Path(str(record.get("path") or ""))
        if path in seen:
            raise RuntimeError(f"Duplicate operational ledger path: {path}")
        seen.add(path)
        if path not in paths and path not in RETIRED_LEDGER_PATHS:
            raise RuntimeError(f"Operational manifest contains an unexpected path: {path}")
        digest = record.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None or record.get("object_key") != _object_key(prefix, path, digest):
            raise RuntimeError(f"Invalid operational object key/hash for {path}")
        for name in ("bytes", "compressed_bytes"):
            if type(record.get(name)) is not int or record[name] < 0:
                raise RuntimeError(f"Invalid {name} for {path}")
    if not set(paths).issubset(seen):
        raise RuntimeError("Operational manifest is missing required ledgers; refusing a partial baseline.")
    return manifest


def _read_manifest(*, bucket: str, prefix: str, target: Path) -> bytes:
    _wrangler_object("get", bucket=bucket, key=f"{prefix}/manifest.json", file_path=target)
    if target.stat().st_size > MAX_MANIFEST_BYTES:
        raise RuntimeError("Operational manifest exceeds its size limit.")
    return target.read_bytes()


def _assert_baseline(*, bucket: str, prefix: str, baseline: bytes | None, target: Path) -> None:
    try:
        current = _read_manifest(bucket=bucket, prefix=prefix, target=target)
    except MissingObject:
        if baseline is None:
            return
        raise RuntimeError("Operational manifest disappeared after restore; refusing publication.")
    if baseline is None or current != baseline:
        raise RuntimeError("Operational manifest changed after restore; restore again before publishing.")


def _fingerprint(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(COPY_BYTES):
            digest.update(chunk)
            size += len(chunk)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or size != before.st_size:
        raise RuntimeError(f"Operational ledger changed while hashing: {path}")
    return digest.hexdigest(), size


def _stage_archive(path: Path, archive: Path, *, prefix: str) -> dict[str, Any]:
    # Validate without retaining an additional raw bytes + compressed bytes copy.
    before = path.stat()
    with path.open("rb") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object while staging {path}")
    del payload
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source, archive.open("wb") as target:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=target, mtime=0) as compressed:
            while chunk := source.read(COPY_BYTES):
                digest.update(chunk)
                size += len(chunk)
                compressed.write(chunk)
                if target.tell() > MAX_ARCHIVE_BYTES:
                    raise RuntimeError(f"Operational archive exceeds Wrangler's upload limit: {path}")
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or size != before.st_size:
        raise RuntimeError(f"Operational ledger changed while staging: {path}")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RuntimeError(f"Operational archive exceeds Wrangler's upload limit: {path}")
    return {"path": str(path).replace("\\", "/"), "object_key": _object_key(prefix, path, digest.hexdigest()),
            "bytes": size, "compressed_bytes": archive.stat().st_size, "sha256": digest.hexdigest()}


def push_ledgers(*, bucket: str, prefix: str, paths: tuple[Path, ...] = LEDGER_PATHS,
                 state_path: Path = STATE_PATH) -> dict[str, Any]:
    receipt = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("verified") is not True or receipt.get("scope") != _scope(bucket, prefix, paths):
        raise RuntimeError("A successful restore receipt for this exact storage scope is required.")
    baseline_text = receipt.get("manifest")
    baseline = baseline_text.encode("utf-8") if isinstance(baseline_text, str) else None
    if baseline is None and receipt.get("bootstrap") is not True:
        raise RuntimeError("An explicit missing-manifest bootstrap receipt is required.")
    if baseline is not None and _sha256(baseline) != receipt.get("manifest_sha256"):
        raise RuntimeError("Restore receipt manifest checksum mismatch.")
    previous = _validate_manifest(baseline, prefix=prefix, paths=paths) if baseline is not None else None
    old_records = {record["path"]: record for record in previous["ledgers"]} if previous else {}
    records: list[dict[str, Any]] = []
    # Use the caller's workspace filesystem rather than an unknown RAM-backed /tmp.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="operational-r2-", dir=state_path.parent) as tmpdir:
        temp_root = Path(tmpdir)
        _assert_baseline(bucket=bucket, prefix=prefix, baseline=baseline, target=temp_root / "current.json")
        uploads: list[tuple[str, Path]] = []
        for index, path in enumerate(paths):
            # A missing ledger is never silently removed from the current manifest.
            old = old_records.get(str(path).replace("\\", "/"))
            digest, size = _fingerprint(path)
            if old and digest == old["sha256"] and size == old["bytes"]:
                records.append(old)  # No recompression of verified identical bytes.
            else:
                archive = temp_root / f"ledger-{index}.json.gz"
                record = _stage_archive(path, archive, prefix=prefix)
                if record["sha256"] != digest or record["bytes"] != size:
                    raise RuntimeError(f"Operational ledger changed before staging: {path}")
                records.append(record)
                uploads.append((record["object_key"], archive))
        # Retired entries remain readable through old manifests; retain their
        # references too rather than silently discarding restoration metadata.
        records.extend(record for record in old_records.values() if Path(record["path"]) in RETIRED_LEDGER_PATHS and Path(record["path"]) not in paths)
        if previous is not None and not uploads:
            print("Operational ledgers are byte-identical; no objects or manifest uploaded.")
            return previous
        manifest = {"artifact": "orographic_operational_ledger_manifest", "schema_version": 1,
                    "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
                    "prefix": prefix, "ledgers": records}
        raw = (json.dumps(manifest, separators=(",", ":")) + "\n").encode("utf-8")
        _validate_manifest(raw, prefix=prefix, paths=paths)
        for key, archive in uploads:
            _wrangler_object("put", bucket=bucket, key=key, file_path=archive)
        # Stale detection is useful but not atomic CAS: shared writer lock required.
        _assert_baseline(bucket=bucket, prefix=prefix, baseline=baseline, target=temp_root / "current.json")
        for checkpoint in (baseline, raw):
            if checkpoint is not None:
                checkpoint_path = temp_root / "checkpoint.json"
                checkpoint_path.write_bytes(checkpoint)
                _wrangler_object("put", bucket=bucket, key=f"{prefix}/manifests/{_sha256(checkpoint)}.json", file_path=checkpoint_path)
        manifest_path = temp_root / "manifest.json"
        manifest_path.write_bytes(raw)
        _wrangler_object("put", bucket=bucket, key=f"{prefix}/manifest.json", file_path=manifest_path)
        _write_state(state_path, {"verified": True, "scope": _scope(bucket, prefix, paths),
                                 "manifest": raw.decode("utf-8"), "manifest_sha256": _sha256(raw)})
    return manifest


def pull_ledgers(*, bucket: str, prefix: str, allow_missing: bool = False,
                 paths: tuple[Path, ...] = LEDGER_PATHS, state_path: Path = STATE_PATH) -> int:
    # Invalidate a previous receipt BEFORE attempting any read. A failed restore
    # can never authorize a later always() publication from repository seed data.
    _write_state(state_path, {"verified": False})
    with tempfile.TemporaryDirectory(prefix="operational-r2-", dir=state_path.parent) as tmpdir:
        temp_root = Path(tmpdir)
        try:
            raw_manifest = _read_manifest(bucket=bucket, prefix=prefix, target=temp_root / "manifest.json")
        except MissingObject:
            if not allow_missing:
                raise
            _write_state(state_path, {"verified": True, "scope": _scope(bucket, prefix, paths), "bootstrap": True})
            print("Explicit bootstrap: operational manifest is confirmed absent; using local seed ledgers.")
            return 0
        manifest = _validate_manifest(raw_manifest, prefix=prefix, paths=paths)
        staged: list[tuple[Path, Path]] = []
        for index, record in enumerate(manifest["ledgers"]):
            destination = Path(record["path"])
            if destination in RETIRED_LEDGER_PATHS and destination not in paths:
                continue
            archive = temp_root / f"ledger-{index}.json.gz"
            _wrangler_object("get", bucket=bucket, key=record["object_key"], file_path=archive)
            if archive.stat().st_size != record["compressed_bytes"]:
                raise RuntimeError(f"Compressed size mismatch while restoring {destination}")
            restored = temp_root / f"ledger-{index}.json"
            digest = hashlib.sha256()
            size = 0
            with gzip.open(archive, "rb") as source, restored.open("wb") as output:
                while chunk := source.read(COPY_BYTES):
                    size += len(chunk)
                    if size > record["bytes"]:
                        raise RuntimeError(f"Expanded size mismatch while restoring {destination}")
                    digest.update(chunk)
                    output.write(chunk)
            if size != record["bytes"] or digest.hexdigest() != record["sha256"]:
                raise RuntimeError(f"Checksum/size mismatch while restoring {destination}")
            with restored.open("rb") as stream:
                payload = json.load(stream)
            if not isinstance(payload, dict):
                raise RuntimeError(f"Expected a JSON object while restoring {destination}")
            del payload
            staged.append((destination, restored))
        # Validate every ledger before replacing any local destination. Replaces
        # are individually atomic, not a cross-file filesystem transaction.
        for destination, restored in staged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output, restored.open("rb") as source:
                while chunk := source.read(COPY_BYTES):
                    output.write(chunk)
                replacement = Path(output.name)
            replacement.replace(destination)
        _write_state(state_path, {"verified": True, "scope": _scope(bucket, prefix, paths),
                                 "manifest": raw_manifest.decode("utf-8"), "manifest_sha256": _sha256(raw_manifest)})
    return len(staged)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore or publish lossless operational gzip snapshots with a verified baseline.")
    parser.add_argument("action", choices=("pull", "push"))
    parser.add_argument("--bucket", default=os.getenv("OROGRAPHIC_RESEARCH_R2_BUCKET", ""))
    parser.add_argument("--prefix", default=OPERATIONAL_PREFIX)
    parser.add_argument("--state-path", type=Path, default=STATE_PATH)
    parser.add_argument("--bootstrap-from-seed", "--allow-missing", dest="bootstrap", action="store_true",
                        help="Explicit first initialization only: permit a confirmed NoSuchKey, never other read failures.")
    args = parser.parse_args()
    bucket = str(args.bucket).strip()
    if not bucket:
        raise SystemExit("OROGRAPHIC_RESEARCH_R2_BUCKET is required for operational ledger storage.")
    prefix = str(args.prefix).strip().strip("/")
    if not prefix:
        raise SystemExit("An operational storage prefix is required.")
    if args.action == "pull":
        restored = pull_ledgers(bucket=bucket, prefix=prefix, allow_missing=args.bootstrap, state_path=args.state_path)
        print(f"Restored {restored} operational ledgers from R2.")
    else:
        if args.bootstrap:
            raise SystemExit("Bootstrap must be an explicit pull before push.")
        manifest = push_ledgers(bucket=bucket, prefix=prefix, state_path=args.state_path)
        print(f"Verified {len(manifest['ledgers'])} operational ledger records in R2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
