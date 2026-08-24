#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.shared_mart_consumers import (  # noqa: E402
    build_shared_mart_consumer_bundle,
    validate_shared_mart_consumer_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build versioned observation-only views from a validated shared mart."
    )
    parser.add_argument("--mart-dir", type=Path, default=Path("output/shared_research_mart"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/shared_mart_consumers")
    )
    args = parser.parse_args()
    manifest = build_shared_mart_consumer_bundle(args.mart_dir, args.output_dir)
    validate_shared_mart_consumer_bundle(args.output_dir)
    print(json.dumps({
        "status": manifest["status"],
        "mart_id": manifest["mart_id"],
        "production_authority": manifest["production_authority"],
        "views": {name: row["rows"] for name, row in manifest["views"].items()},
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
