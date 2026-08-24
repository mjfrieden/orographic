#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.iceberg_mart import (  # noqa: E402
    build_iceberg_publication_plan,
    publication_environment,
    publish_iceberg_mart,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and optionally publish the shared research mart to R2 Iceberg."
    )
    parser.add_argument("--mart-dir", type=Path, default=Path("output/shared_research_mart"))
    parser.add_argument("--catalog-name", default="r2_mart")
    parser.add_argument("--namespace", default="research_mart")
    parser.add_argument(
        "--apply", action="store_true",
        help="Write to the configured R2 Data Catalog. Without this flag, only print a plan.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.apply:
        result = build_iceberg_publication_plan(
            mart_dir=args.mart_dir,
            catalog_name=args.catalog_name,
            namespace=args.namespace,
        )
    else:
        env = publication_environment()
        missing = [name for name, value in env.items() if not value]
        if missing:
            raise SystemExit(
                "Iceberg publication is not configured; missing environment values: "
                + ", ".join(missing)
            )
        result = publish_iceberg_mart(
            mart_dir=args.mart_dir,
            catalog_uri=env["catalog_uri"],
            warehouse=env["warehouse"],
            token=env["token"],
            catalog_name=args.catalog_name,
            namespace=args.namespace,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
