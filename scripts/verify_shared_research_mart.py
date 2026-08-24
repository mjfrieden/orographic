#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.iceberg_mart import verify_iceberg_mart  # noqa: E402
from engine.orographic.shared_research_mart import validate_shared_research_mart  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a published shared research mart.")
    parser.add_argument("--mart-dir", type=Path, required=True)
    parser.add_argument("--catalog-name", default="r2_mart")
    parser.add_argument("--namespace", default="research_mart")
    args = parser.parse_args()
    manifest = validate_shared_research_mart(args.mart_dir)
    result = verify_iceberg_mart(
        manifest=manifest,
        catalog_name=args.catalog_name,
        namespace=args.namespace,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
