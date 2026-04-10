"""
Utilities for building and querying a local historical options store.

The store is optimized for strict replay:
  - raw CSVs remain as the ingestion source of truth
  - partitioned parquet files provide fast per-date / per-symbol access
  - a coverage manifest summarizes what symbols and dates are actually present
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, date
from pathlib import Path
from typing import Any

import pandas as pd

PARTITION_DIRNAME = "partitioned"
MANIFEST_FILENAME = "coverage_manifest.json"


def partition_root(data_dir: str | Path) -> Path:
    return Path(data_dir) / PARTITION_DIRNAME


def manifest_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / MANIFEST_FILENAME


def partition_file_path(data_dir: str | Path, quote_date: date, symbol: str) -> Path:
    return (
        partition_root(data_dir)
        / f"quote_date={quote_date.isoformat()}"
        / f"underlying_symbol={symbol.upper()}"
        / "chain.parquet"
    )


def _standardize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    normalized.columns = [str(col).strip() for col in normalized.columns]

    if "quote_date" not in normalized.columns or "underlying_symbol" not in normalized.columns:
        return pd.DataFrame()

    normalized["quote_date"] = pd.to_datetime(normalized["quote_date"], errors="coerce").dt.date
    normalized["underlying_symbol"] = normalized["underlying_symbol"].astype(str).str.upper()
    if "expire_date" in normalized.columns:
        normalized["expire_date"] = pd.to_datetime(normalized["expire_date"], errors="coerce").dt.date
    if "option_type" in normalized.columns:
        normalized["option_type"] = normalized["option_type"].astype(str).str.upper().str[0]

    for col in ["strike", "bid", "ask", "implied_volatility", "delta", "open_interest", "trade_volume", "volume"]:
        if col in normalized.columns:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")

    normalized = normalized.dropna(subset=["quote_date", "underlying_symbol"])
    return normalized


def _symbol_from_filename(source_name: str | None) -> str:
    if not source_name:
        return "UNKNOWN"
    stem = Path(source_name).stem.lower()
    token = stem.split("_")[0]
    return token.upper()


def _standardize_optionsdx_wide_frame(frame: pd.DataFrame, source_name: str | None = None) -> pd.DataFrame:
    source_symbol = _symbol_from_filename(source_name)
    renamed = frame.rename(columns={col: col.upper().strip() for col in frame.columns}).copy()
    quote_date = pd.to_datetime(renamed["QUOTE_DATE"], errors="coerce").dt.date
    expire_date = pd.to_datetime(renamed["EXPIRE_DATE"], errors="coerce").dt.date
    strike = pd.to_numeric(renamed["STRIKE"], errors="coerce")
    underlying_last = pd.to_numeric(renamed.get("UNDERLYING_LAST"), errors="coerce")

    def build_leg(prefix: str, option_type: str) -> pd.DataFrame:
        bid = pd.to_numeric(renamed.get(f"{prefix}_BID"), errors="coerce")
        ask = pd.to_numeric(renamed.get(f"{prefix}_ASK"), errors="coerce")
        volume = pd.to_numeric(renamed.get(f"{prefix}_VOLUME"), errors="coerce")
        iv = pd.to_numeric(renamed.get(f"{prefix}_IV"), errors="coerce")
        delta = pd.to_numeric(renamed.get(f"{prefix}_DELTA"), errors="coerce")
        last = pd.to_numeric(renamed.get(f"{prefix}_LAST"), errors="coerce")
        leg = pd.DataFrame(
            {
                "quote_date": quote_date,
                "underlying_symbol": source_symbol,
                "expire_date": expire_date,
                "strike": strike,
                "option_type": option_type,
                "bid": bid,
                "ask": ask,
                "last": last,
                "implied_volatility": iv,
                "delta": delta,
                "open_interest": pd.NA,
                "trade_volume": volume,
                "underlying_last": underlying_last,
            }
        )
        leg = leg.dropna(subset=["quote_date", "expire_date", "strike", "bid", "ask"])
        leg = leg[(leg["bid"] > 0) | (leg["ask"] > 0)]
        return leg

    combined = pd.concat(
        [
            build_leg("C", "C"),
            build_leg("P", "P"),
        ],
        ignore_index=True,
    )
    combined["underlying_symbol"] = combined["underlying_symbol"].astype(str).str.upper()
    return combined


def build_partitioned_store(
    data_dir: str | Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    root = partition_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)

    grouped: dict[tuple[date, str], list[pd.DataFrame]] = {}
    source_files: list[str] = []
    raw_files = sorted(
        path
        for path in data_dir.iterdir()
        if path.is_file() and any(
            str(path.name).lower().endswith(suffix)
            for suffix in (".csv", ".csv.gz", ".gz", ".zip")
        )
    )
    for csv_path in raw_files:
        source_files.append(csv_path.name)
        frame = pd.read_csv(csv_path, compression="infer")
        standardized = _standardize_frame(frame)
        if standardized.empty:
            standardized = _standardize_optionsdx_wide_frame(frame, csv_path.name)
        frame = standardized
        if frame.empty:
            continue
        for (quote_date, symbol), chunk in frame.groupby(["quote_date", "underlying_symbol"], dropna=True):
            grouped.setdefault((quote_date, symbol), []).append(chunk.copy())

    symbol_summary: dict[str, dict[str, Any]] = {}
    date_summary: dict[str, dict[str, Any]] = {}
    partition_count = 0
    total_rows = 0

    for (quote_date, symbol), chunks in sorted(grouped.items()):
        combined = pd.concat(chunks, ignore_index=True)
        combined = combined.drop_duplicates().sort_values(
            by=[col for col in ["quote_date", "expire_date", "option_type", "strike"] if col in combined.columns]
        )
        out_path = partition_file_path(data_dir, quote_date, symbol)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if force or not out_path.exists():
            combined.to_parquet(out_path, index=False)
        else:
            existing = pd.read_parquet(out_path)
            merged = pd.concat([existing, combined], ignore_index=True).drop_duplicates()
            merged = merged.sort_values(
                by=[col for col in ["quote_date", "expire_date", "option_type", "strike"] if col in merged.columns]
            )
            merged.to_parquet(out_path, index=False)
            combined = merged

        row_count = int(len(combined))
        partition_count += 1
        total_rows += row_count
        symbol_entry = symbol_summary.setdefault(
            symbol,
            {"quote_dates": [], "expiries": set(), "row_count": 0, "partitions": 0},
        )
        symbol_entry["quote_dates"].append(quote_date.isoformat())
        symbol_entry["row_count"] += row_count
        symbol_entry["partitions"] += 1
        if "expire_date" in combined.columns:
            symbol_entry["expiries"].update(
                sorted({d.isoformat() for d in combined["expire_date"].dropna().tolist()})
            )

        date_entry = date_summary.setdefault(
            quote_date.isoformat(),
            {"symbols": [], "row_count": 0, "partitions": 0},
        )
        date_entry["symbols"].append(symbol)
        date_entry["row_count"] += row_count
        date_entry["partitions"] += 1

    manifest: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "data_dir": str(data_dir.resolve()),
        "partition_root": str(root.resolve()),
        "source_files": source_files,
        "summary": {
            "csv_file_count": len(raw_files),
            "partition_count": partition_count,
            "symbol_count": len(symbol_summary),
            "quote_date_count": len(date_summary),
            "row_count": total_rows,
        },
        "symbols": {},
        "quote_dates": {},
    }

    for symbol, payload in sorted(symbol_summary.items()):
        manifest["symbols"][symbol] = {
            "quote_dates": sorted(set(payload["quote_dates"])),
            "expiries": sorted(payload["expiries"]),
            "row_count": payload["row_count"],
            "partitions": payload["partitions"],
        }
    for quote_date, payload in sorted(date_summary.items()):
        manifest["quote_dates"][quote_date] = {
            "symbols": sorted(set(payload["symbols"])),
            "row_count": payload["row_count"],
            "partitions": payload["partitions"],
        }

    manifest_path(data_dir).write_text(json.dumps(manifest, indent=2))
    return manifest


def load_coverage_manifest(data_dir: str | Path) -> dict[str, Any] | None:
    path = manifest_path(data_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a partitioned local historical options store.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parents[1] / "data" / "optionsdx",
        help="Directory containing raw options CSV files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild parquet partitions even when they already exist.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_partitioned_store(args.data_dir, force=args.force)
    print(json.dumps(manifest["summary"], indent=2))
    print(f"Saved coverage manifest → {manifest_path(args.data_dir)}")
    print(f"Saved partitioned store → {partition_root(args.data_dir)}")


if __name__ == "__main__":
    main()
