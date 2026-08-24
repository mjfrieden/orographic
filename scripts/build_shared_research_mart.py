#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.shared_research_mart import (  # noqa: E402
    build_shared_research_mart,
    validate_shared_research_mart,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the versioned Cirrus + Orographic analytical research mart."
    )
    parser.add_argument(
        "--orographic-canonical-dir",
        type=Path,
        default=Path("output/canonical_evidence"),
    )
    parser.add_argument(
        "--cirrus-export-dir",
        type=Path,
        help="Optional neutral Cirrus research export containing manifest.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/shared_research_mart"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_shared_research_mart(
        orographic_canonical_dir=args.orographic_canonical_dir,
        cirrus_export_dir=args.cirrus_export_dir,
        output_dir=args.output_dir,
    )
    validate_shared_research_mart(args.output_dir)
    print(json.dumps({
        "status": "passed",
        "mart_id": manifest["mart_id"],
        "output_dir": str(args.output_dir),
        "rows": {name: artifact["rows"] for name, artifact in manifest["artifacts"].items()},
        "sources": manifest["sources"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
