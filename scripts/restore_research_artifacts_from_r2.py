from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.evidence_store import validate_canonical_bundle  # noqa: E402
from scripts.upload_research_artifacts_to_r2 import (  # noqa: E402
    ARCHIVE_ROOT,
    CANONICAL_PREFIX,
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


def _safe_relative(key: str, prefix: str) -> Path:
    relative = PurePosixPath(key).relative_to(PurePosixPath(prefix))
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"Unsafe R2 object key: {key}")
    return Path(*relative.parts)


def _get_object(bucket: str, key: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "npx",
            "wrangler",
            "r2",
            "object",
            "get",
            f"{bucket}/{key}",
            "--remote",
            "--file",
            str(destination),
        ],
        check=True,
    )


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
    if include_suffixes:
        keys = [key for key in keys if key.endswith(include_suffixes)]
    if max_objects > 0:
        keys = keys[-max_objects:]
    for key in keys:
        destination = output_dir / _safe_relative(key, f"{normalized}/")
        _get_object(bucket, key, destination)
    return len(keys)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore Orographic's canonical or legacy research evidence from R2."
    )
    parser.add_argument("--bucket", default=os.getenv("OROGRAPHIC_RESEARCH_R2_BUCKET", ""))
    parser.add_argument("--account-id", default=os.getenv("CLOUDFLARE_ACCOUNT_ID", ""))
    parser.add_argument("--api-token", default=os.getenv("CLOUDFLARE_API_TOKEN", ""))
    parser.add_argument("--mode", choices=("canonical", "legacy"), default="canonical")
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

    restored = restore_prefix(
        bucket=bucket,
        account_id=account_id,
        api_token=api_token,
        prefix=prefix,
        output_dir=output,
        include_suffixes=suffixes,
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
    print(f"Restored {restored} objects from r2://{bucket}/{prefix} into {output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
