from __future__ import annotations

import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys


def _default_prefix() -> str:
    return datetime.now(UTC).strftime("orographic/research-data/%Y/%m/%d/%H%M%S")


def _iter_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(child for child in path.rglob("*") if child.is_file()))
    return files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload Orographic research data artifacts to Cloudflare R2 with wrangler.")
    parser.add_argument("--bucket", default=os.getenv("OROGRAPHIC_RESEARCH_R2_BUCKET", ""))
    parser.add_argument("--prefix", default=os.getenv("OROGRAPHIC_RESEARCH_R2_PREFIX", _default_prefix()))
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
    files = _iter_files(args.paths)
    if not files:
        print("No research artifact files found to upload.")
        return 0
    prefix = str(args.prefix).strip().strip("/")
    for file_path in files:
        base = next((path for path in args.paths if path in file_path.parents or path == file_path), file_path.parent)
        relative = file_path.name if base == file_path else str(file_path.relative_to(base))
        object_key = f"{prefix}/{base.name}/{relative}".replace("\\", "/")
        command = ["npx", "wrangler", "r2", "object", "put", f"{bucket}/{object_key}", "--file", str(file_path)]
        subprocess.run(command, check=True)
        print(f"Uploaded {file_path} -> r2://{bucket}/{object_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
