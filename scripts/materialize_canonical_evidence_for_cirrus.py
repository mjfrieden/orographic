#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

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
    staging = output.parent / f".{output.name}.{uuid.uuid4().hex}.tmp"
    required = {"quote_date", "underlying_symbol"}
    missing = sorted(required - set(quotes.columns))
    if missing and not quotes.empty:
        raise ValueError(f"Canonical option quotes are missing Cirrus partition fields: {missing}")

    normalized = quotes.copy()
    if not normalized.empty:
        normalized["quote_date"] = pd.to_datetime(
            normalized["quote_date"], errors="coerce"
        ).dt.date.astype(str)
        normalized["underlying_symbol"] = (
            normalized["underlying_symbol"].astype(str).str.upper()
        )
        normalized = normalized[
            normalized["quote_date"].ne("NaT")
            & normalized["underlying_symbol"].ne("")
        ].drop(columns=["canonical_source_file"], errors="ignore")

    partitions = 0
    rows = 0
    latest_quote_date = None
    staging.mkdir(parents=True, exist_ok=False)
    try:
        groups = (
            normalized.groupby(["quote_date", "underlying_symbol"], dropna=True, sort=True)
            if not normalized.empty else []
        )
        for (quote_date, symbol), frame in groups:
            path = (
                staging
                / f"quote_date={quote_date}"
                / f"underlying_symbol={symbol}"
                / "chain.parquet"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False)
            partitions += 1
            rows += len(frame)
            latest_quote_date = max(latest_quote_date or str(quote_date), str(quote_date))

        expected_rows = int(
            manifest.get("evidence", {})
            .get("cumulative_inventory", {})
            .get("option_quote_rows", 0)
        )
        expected_partitions = (
            int(normalized.groupby(["quote_date", "underlying_symbol"], dropna=True).ngroups)
            if not normalized.empty else 0
        )
        if rows != expected_rows:
            raise ValueError(
                f"Cirrus materialization row mismatch: wrote {rows}, canonical manifest has {expected_rows}"
            )
        if partitions != expected_partitions:
            raise ValueError(
                f"Cirrus materialization partition mismatch: wrote {partitions}, expected {expected_partitions}"
            )

        result: dict[str, object] = {
            "artifact": "cirrus_canonical_archive_materialization",
            "schema_version": 2,
            "status": "passed",
            "canonical_bundle_id": manifest.get("bundle_id"),
            "canonical_manifest": str(canonical / "evidence_manifest.json"),
            "output_dir": str(output),
            "partitions": partitions,
            "expected_partitions": expected_partitions,
            "rows": rows,
            "expected_rows": expected_rows,
            "latest_quote_date": latest_quote_date,
            "checks": {
                "canonical_bundle_valid": True,
                "row_count_matches": rows == expected_rows,
                "partition_count_matches": partitions == expected_partitions,
                "stale_partitions_replaced": True,
            },
        }
        (staging / "canonical_materialization.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        backup = output.parent / f".{output.name}.{uuid.uuid4().hex}.bak"
        if output.exists():
            output.rename(backup)
        try:
            staging.rename(output)
        except Exception:
            if backup.exists() and not output.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
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
