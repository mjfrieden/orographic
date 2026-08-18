#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from engine.orographic.evidence_store import validate_canonical_bundle  # noqa: E402


def materialize_cirrus_archive(
    *,
    canonical_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    canonical = Path(canonical_dir)
    manifest = validate_canonical_bundle(canonical)
    quotes = pd.read_parquet(canonical / "live_option_quotes.parquet")
    output = Path(output_dir)
    required = {"quote_date", "underlying_symbol"}
    missing = sorted(required - set(quotes.columns))
    if missing and not quotes.empty:
        raise ValueError(f"Canonical option quotes are missing Cirrus partition fields: {missing}")

    partitions = 0
    rows = 0
    if not quotes.empty:
        normalized = quotes.copy()
        normalized["quote_date"] = pd.to_datetime(
            normalized["quote_date"], errors="coerce"
        ).dt.date.astype(str)
        normalized["underlying_symbol"] = (
            normalized["underlying_symbol"].astype(str).str.upper()
        )
        normalized = normalized.drop(columns=["canonical_source_file"], errors="ignore")
        for (quote_date, symbol), frame in normalized.groupby(
            ["quote_date", "underlying_symbol"], dropna=True, sort=True
        ):
            if not quote_date or quote_date == "NaT" or not symbol:
                continue
            path = (
                output
                / f"quote_date={quote_date}"
                / f"underlying_symbol={symbol}"
                / "chain.parquet"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False)
            partitions += 1
            rows += len(frame)

    result: dict[str, object] = {
        "artifact": "cirrus_canonical_archive_materialization",
        "schema_version": 1,
        "canonical_bundle_id": manifest.get("bundle_id"),
        "canonical_manifest": str(canonical / "evidence_manifest.json"),
        "output_dir": str(output),
        "partitions": partitions,
        "rows": rows,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "canonical_materialization.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize canonical Orographic quotes in the partition layout consumed by Cirrus."
    )
    parser.add_argument(
        "--canonical-dir",
        type=Path,
        default=Path("output/canonical_evidence"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("engine/data/options/blended/partitioned"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = materialize_cirrus_archive(
        canonical_dir=args.canonical_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
