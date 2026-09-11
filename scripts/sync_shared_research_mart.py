#!/usr/bin/env python3
"""Restore a Cirrus export if present and rebuild the shared research mart."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.shared_mart_consumers import (  # noqa: E402
    build_shared_mart_consumer_bundle,
)
from engine.orographic.shared_mart_shadow import build_shared_mart_shadow_evidence  # noqa: E402
from engine.orographic.iceberg_mart import inspect_iceberg_source  # noqa: E402
from engine.orographic.shared_research_mart import (  # noqa: E402
    build_shared_research_mart,
    validate_cirrus_export,
    validate_shared_research_mart,
)
from scripts.restore_research_artifacts_from_r2 import (  # noqa: E402
    _get_object,
    _list_delimited_index,
    _list_delimited_prefixes,
    _list_objects,
    cirrus_bundle_prefixes,
    restore_prefix,
)
from scripts.upload_research_artifacts_to_r2 import (  # noqa: E402
    CIRRUS_EXPORT_PREFIX,
    CIRRUS_EXPORT_SEARCH_PREFIXES,
)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _candidate_canonical_dirs(explicit: Path | None) -> list[Path]:
    dirs = []
    if explicit is not None:
        dirs.append(explicit)
    dirs.extend(
        [
            Path("output/canonical_evidence"),
            Path("output/restored_canonical_evidence"),
        ]
    )
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in dirs:
        resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _valid_canonical_dir(path: Path) -> Path | None:
    if (path / "evidence_manifest.json").exists():
        return path
    return None


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
        if path in seen:
            continue
        seen.add(path)
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


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _cirrus_credentials() -> tuple[str, str, str, str]:
    bucket = os.getenv("OROGRAPHIC_RESEARCH_R2_BUCKET", "").strip()
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = (
        os.getenv("CLOUDFLARE_R2_API_TOKEN")
        or os.getenv("CLOUDFLARE_API_TOKEN")
        or ""
    ).strip()
    prefix = os.getenv("OROGRAPHIC_CIRRUS_EXPORT_R2_PREFIX", CIRRUS_EXPORT_PREFIX).strip()
    return bucket, account_id, api_token, prefix


def _cirrus_like_search_prefix(prefix: str) -> bool:
    lowered = prefix.lower().replace("\\", "/")
    if "research-data" in lowered or "live_options" in lowered:
        return False
    return "cirrus" in lowered or "options_research_bundle" in lowered


def _discover_cirrus_search_prefixes(
    *,
    account_id: str,
    api_token: str,
    bucket: str,
) -> tuple[list[str], dict[str, list[str]]]:
    """Find Cirrus-like folder roots without listing the live-options archive."""
    bucket_roots = _list_delimited_prefixes(
        account_id=account_id,
        api_token=api_token,
        bucket=bucket,
        prefix="",
    )
    orographic_roots: list[str] = []
    if any(item.rstrip("/") == "orographic" or item.startswith("orographic/") for item in bucket_roots):
        orographic_roots = _list_delimited_prefixes(
            account_id=account_id,
            api_token=api_token,
            bucket=bucket,
            prefix="orographic/",
        )
    extras = [
        item if item.endswith("/") else f"{item}/"
        for item in (*bucket_roots, *orographic_roots)
        if _cirrus_like_search_prefix(item)
    ]
    shared_mart_roots: list[str] = []
    shared_mart_objects: list[str] = []
    if any(item.rstrip("/") == "shared-research-mart" or item.startswith("shared-research-mart/") for item in bucket_roots):
        index = _list_delimited_index(
            account_id=account_id,
            api_token=api_token,
            bucket=bucket,
            prefix="shared-research-mart/",
        )
        shared_mart_roots = list(index.get("delimited") or [])
        shared_mart_objects = [str(row.get("key") or "") for row in index.get("objects") or [] if row.get("key")]
        nested_roots: list[str] = []
        for child in shared_mart_roots:
            child_prefix = child if child.endswith("/") else f"{child}/"
            nested = _list_delimited_index(
                account_id=account_id,
                api_token=api_token,
                bucket=bucket,
                prefix=child_prefix,
            )
            nested_roots.extend(str(item) for item in nested.get("delimited") or [])
            shared_mart_objects.extend(
                str(row.get("key") or "") for row in nested.get("objects") or [] if row.get("key")
            )
        shared_mart_roots = list(dict.fromkeys([*shared_mart_roots, *nested_roots]))
        shared_mart_objects = list(dict.fromkeys(shared_mart_objects))
        extras.extend(
            item if item.endswith("/") else f"{item}/"
            for item in shared_mart_roots
            if _cirrus_like_search_prefix(item)
        )
    search = list(dict.fromkeys([*CIRRUS_EXPORT_SEARCH_PREFIXES, *extras]))
    return search, {
        "bucket_roots": bucket_roots,
        "orographic_roots": orographic_roots,
        "shared_mart_roots": shared_mart_roots,
        "shared_mart_objects": shared_mart_objects[:50],
    }


def _restore_cirrus_from_r2(output_dir: Path, allow_missing: bool) -> dict:
    bucket, account_id, api_token, current_prefix = _cirrus_credentials()
    if not all((bucket, account_id, api_token)):
        return {"status": "skipped_missing_credentials", "prefix": current_prefix, "objects": 0}
    discovery: dict[str, list[str]] = {
        "bucket_roots": [],
        "orographic_roots": [],
        "shared_mart_roots": [],
        "shared_mart_objects": [],
    }
    search_prefixes = list(CIRRUS_EXPORT_SEARCH_PREFIXES)
    try:
        search_prefixes, discovery = _discover_cirrus_search_prefixes(
            account_id=account_id,
            api_token=api_token,
            bucket=bucket,
        )
        listed: list[dict] = []
        seen_keys: set[str] = set()
        for search_prefix in search_prefixes:
            for row in _list_objects(
                account_id=account_id,
                api_token=api_token,
                bucket=bucket,
                prefix=search_prefix,
            ):
                key = str(row.get("key") or "")
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                listed.append(row)
    except Exception as exc:  # noqa: BLE001 - sync must fail closed to a diagnostic, not the live scan
        if not allow_missing:
            raise
        return {
            "status": "restore_failed",
            "prefix": current_prefix,
            "error": str(exc),
            "objects": 0,
            **discovery,
        }
    candidates = cirrus_bundle_prefixes(listed)
    listed_prefixes = [
        {
            "prefix": row["prefix"],
            "is_current": row["is_current"],
            "last_modified": row.get("last_modified"),
        }
        for row in candidates
    ]
    base = {
        "listed_objects": len(listed),
        "listed_prefixes": listed_prefixes,
        "listed_search_prefixes": search_prefixes,
        "bucket_roots": discovery.get("bucket_roots") or [],
        "orographic_roots": discovery.get("orographic_roots") or [],
        "shared_mart_roots": discovery.get("shared_mart_roots") or [],
        "shared_mart_objects": discovery.get("shared_mart_objects") or [],
        "prefix": current_prefix,
    }
    errors: list[dict[str, str]] = []
    for candidate in candidates:
        _reset_dir(output_dir)
        try:
            restored = restore_prefix(
                bucket=bucket,
                account_id=account_id,
                api_token=api_token,
                prefix=str(candidate["prefix"]),
                output_dir=output_dir,
            )
        except Exception as exc:  # noqa: BLE001 - try the next dated prefix
            errors.append({"prefix": str(candidate["prefix"]), "error": str(exc)})
            continue
        if restored == 0:
            errors.append({"prefix": str(candidate["prefix"]), "error": "empty_prefix"})
            continue
        if _valid_cirrus_dir(output_dir) is None:
            errors.append({"prefix": str(candidate["prefix"]), "error": "invalid_bundle"})
            continue
        pin = "current" if candidate.get("is_current") else "fallback"
        return {
            **base,
            "status": "restored",
            "prefix": str(candidate["prefix"]),
            "objects": restored,
            "output_dir": str(output_dir),
            "cirrus_pin": pin,
        }
    if errors:
        base["restore_errors"] = errors
    return {**base, "status": "missing", "objects": 0}


def _missing_cirrus_next_action(restore_info: dict | None) -> str:
    listed = []
    if isinstance(restore_info, dict):
        listed = [
            str(row.get("prefix"))
            for row in restore_info.get("listed_prefixes") or []
            if isinstance(row, dict) and row.get("prefix")
        ]
    publish = (
        "python scripts/upload_research_artifacts_to_r2.py --mode cirrus "
        "<cirrus-options_research_bundle>"
    )
    if listed:
        return (
            "Cirrus objects exist under r2://$OROGRAPHIC_RESEARCH_R2_BUCKET/cirrus/ but no valid "
            f"bundle could be restored. Promote a validated prefix to {CIRRUS_EXPORT_PREFIX} with "
            f"{publish}, or pass --cirrus-export-dir, then rerun this sync. Listed: "
            + ", ".join(listed)
        )
    return (
        "Publish a current Cirrus options_research_bundle with "
        f"{publish} to r2://$OROGRAPHIC_RESEARCH_R2_BUCKET/{CIRRUS_EXPORT_PREFIX} "
        "or pass --cirrus-export-dir, then rerun this sync."
    )


def _shared_mart_archive_keys(restore_info: dict | None) -> list[str]:
    if not isinstance(restore_info, dict):
        return []
    keys: list[str] = []
    for key in restore_info.get("shared_mart_objects") or []:
        text = str(key or "")
        if text.endswith(".tar.gz"):
            keys.append(text)
    return keys


def _restore_shared_mart_archive(
    *,
    restore_info: dict | None,
    output_dir: Path,
    allow_missing: bool,
) -> dict:
    keys = _shared_mart_archive_keys(restore_info)
    if not keys:
        return {"status": "missing", "objects": 0}
    bucket, account_id, api_token, _prefix = _cirrus_credentials()
    if not all((bucket, account_id, api_token)):
        return {"status": "skipped_missing_credentials", "objects": 0}
    object_key = keys[-1]
    _reset_dir(output_dir)
    archive_path = output_dir.parent / "shared-research-mart-archive.tar.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _get_object(bucket, object_key, archive_path)
        import tarfile

        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(output_dir, filter="data")
        manifests = list(output_dir.rglob("mart_manifest.json"))
        if not manifests:
            raise ValueError(f"No mart_manifest.json in {object_key}")
        mart_root = manifests[0].parent
        validate_shared_research_mart(mart_root)
    except Exception as exc:  # noqa: BLE001 - fall back to the Cirrus-missing diagnostic
        if not allow_missing:
            raise
        return {
            "status": "restore_failed",
            "object_key": object_key,
            "error": str(exc),
            "objects": 0,
        }
    return {
        "status": "restored",
        "object_key": object_key,
        "mart_dir": str(mart_root),
        "cirrus_pin": "fallback",
        "objects": 1,
    }


def _inspect_iceberg_cirrus() -> dict:
    """Best-effort Iceberg recency probe. Never fails the mart sync."""
    try:
        return inspect_iceberg_source(source_system="cirrus")
    except Exception as exc:  # noqa: BLE001 - catalog outages must not block sync
        return {"status": "inspect_failed", "error": str(exc), "current_export": False}


def _copy_mart(source: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    source_resolved = source.resolve()
    if dest_resolved == source_resolved:
        return
    _reset_dir(dest)
    dest.rmdir()
    shutil.copytree(source, dest)


def refresh_orographic_on_restored_mart(
    *,
    canonical_dir: Path,
    frozen_mart_dir: Path,
    output_dir: Path,
) -> dict:
    """Rebuild with current Orographic canonical while keeping archived Cirrus rows."""
    frozen_manifest = validate_shared_research_mart(frozen_mart_dir)
    reused_id = frozen_manifest.get("mart_id")
    try:
        manifest = build_shared_research_mart(
            orographic_canonical_dir=canonical_dir,
            cirrus_mart_dir=frozen_mart_dir,
            output_dir=output_dir,
        )
        return {
            "status": "refreshed",
            "orographic_refreshed": True,
            "reused_from_mart_id": reused_id,
            "mart_id": manifest.get("mart_id"),
            "manifest": manifest,
        }
    except Exception as exc:  # noqa: BLE001 - keep the dated archive if refresh fails
        _copy_mart(frozen_mart_dir, output_dir)
        copied = validate_shared_research_mart(output_dir)
        return {
            "status": "copied_frozen",
            "orographic_refreshed": False,
            "reused_from_mart_id": reused_id,
            "mart_id": copied.get("mart_id"),
            "refresh_error": str(exc),
            "manifest": copied,
        }


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
    canonical = None
    for candidate in _candidate_canonical_dirs(args.orographic_canonical_dir):
        canonical = _valid_canonical_dir(candidate)
        if canonical is not None:
            break
    if canonical is None:
        payload = {
            "artifact": "orographic_shared_mart_sync",
            "schema_version": 1,
            "generated_at_utc": _now_iso(),
            "status": "missing_orographic_canonical",
            "source_systems": [],
            "checked_dirs": [str(path) for path in _candidate_canonical_dirs(args.orographic_canonical_dir)],
            "next_action": (
                "Build output/canonical_evidence, or restore it to "
                "output/restored_canonical_evidence, before attempting a mart sync."
            ),
        }
        _write(args.output, payload)
        print(json.dumps(payload, indent=2))
        return 0 if args.allow_missing else 1

    iceberg = _inspect_iceberg_cirrus()
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
        archive = _restore_shared_mart_archive(
            restore_info=restore_info,
            output_dir=Path("output/restored_shared_research_mart"),
            allow_missing=args.allow_missing,
        )
        if archive.get("status") == "restored":
            frozen_mart = Path(str(archive["mart_dir"]))
            refresh = refresh_orographic_on_restored_mart(
                canonical_dir=canonical,
                frozen_mart_dir=frozen_mart,
                output_dir=args.mart_dir,
            )
            mart_dir = args.mart_dir
            manifest = refresh["manifest"]
            consumer = build_shared_mart_consumer_bundle(mart_dir, args.consumer_dir)
            shadow = build_shared_mart_shadow_evidence(args.consumer_dir)
            args.shadow_output.parent.mkdir(parents=True, exist_ok=True)
            args.shadow_output.write_text(json.dumps(shadow, indent=2) + "\n", encoding="utf-8")
            source_systems = sorted({
                str(row.get("source_system"))
                for row in manifest.get("sources") or []
                if isinstance(row, dict) and row.get("source_system")
            }) or ["cirrus", "orographic"]
            refreshed = bool(refresh.get("orographic_refreshed"))
            payload = {
                "artifact": "orographic_shared_mart_sync",
                "schema_version": 1,
                "generated_at_utc": _now_iso(),
                "status": "ready_two_source",
                "mart_id": manifest.get("mart_id"),
                "source_systems": source_systems,
                "consumer_status": consumer.get("status"),
                "training_rows": (consumer.get("views") or {}).get("orographic_training_v1", {}).get("rows"),
                "shadow_status": shadow.get("status"),
                "cirrus_pin": "fallback",
                "cirrus_export_is_current": False,
                "orographic_refreshed": refreshed,
                "reused_from_mart_id": refresh.get("reused_from_mart_id"),
                "iceberg": iceberg,
                "restore": {
                    **(restore_info or {}),
                    "archive": archive,
                    "refresh": {
                        "status": refresh.get("status"),
                        "orographic_refreshed": refreshed,
                        "reused_from_mart_id": refresh.get("reused_from_mart_id"),
                        "refresh_error": refresh.get("refresh_error"),
                    },
                    "cirrus_pin": "fallback",
                },
                "production_changes_allowed": False,
                "next_action": (
                    "Two-source mart rebuilt from current Orographic canonical and Cirrus rows in "
                    "shared-research-mart/staging. It is a dated Cirrus fallback, not weekly alpha "
                    "versus Cirrus. Publish a current Cirrus options_research_bundle with python "
                    "scripts/upload_research_artifacts_to_r2.py --mode cirrus <bundle-dir>."
                    if refreshed
                    else (
                        "Two-source mart restored from shared-research-mart/staging. "
                        "Orographic refresh failed, so the frozen archive was kept. It is a dated "
                        "fallback, not weekly alpha versus Cirrus. Publish a current Cirrus "
                        "options_research_bundle with python scripts/upload_research_artifacts_to_r2.py "
                        "--mode cirrus <bundle-dir>."
                    )
                ),
            }
            _write(args.output, payload)
            print(json.dumps({
                "status": payload["status"],
                "mart_id": payload.get("mart_id"),
                "cirrus_pin": payload["cirrus_pin"],
                "training_rows": payload.get("training_rows"),
                "orographic_refreshed": refreshed,
                "archive": archive.get("object_key"),
                "iceberg": {
                    "status": iceberg.get("status"),
                    "max_decision_at_utc": iceberg.get("max_decision_at_utc"),
                    "recommendation_rows": iceberg.get("recommendation_rows"),
                },
            }, indent=2))
            return 0
        payload = {
            "artifact": "orographic_shared_mart_sync",
            "schema_version": 1,
            "generated_at_utc": _now_iso(),
            "status": "cirrus_export_unavailable",
            "source_systems": ["orographic"],
            "restore": restore_info,
            "archive": archive,
            "iceberg": iceberg,
            "production_changes_allowed": False,
            "orographic_canonical_bundle": str(canonical / "evidence_manifest.json"),
            "next_action": _missing_cirrus_next_action(restore_info),
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
    cirrus_pin = "local"
    if isinstance(restore_info, dict):
        cirrus_pin = str(restore_info.get("cirrus_pin") or "fallback")
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
        "cirrus_pin": cirrus_pin,
        "cirrus_export_is_current": cirrus_pin != "fallback",
        "restore": restore_info,
        "iceberg": iceberg,
        "production_changes_allowed": False,
        "next_action": (
            "Keep using the mart for observation-only backtests; do not route from it."
            if cirrus_pin != "fallback"
            else (
                "Two-source mart rebuilt from a dated Cirrus R2 prefix. Promote a current export to "
                f"{CIRRUS_EXPORT_PREFIX} with python scripts/upload_research_artifacts_to_r2.py "
                "--mode cirrus <bundle-dir> before claiming weekly alpha versus Cirrus."
            )
        ),
    }
    _write(args.output, payload)
    print(json.dumps({key: payload[key] for key in (
        "status", "mart_id", "source_systems", "training_rows", "shadow_status"
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
