from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from engine.orographic.headline_intelligence import normalize_headlines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize raw headline feeds into replayable event fields.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-quality", type=float, default=0.5)
    parser.add_argument("--review-queue", type=Path, default=None)
    parser.add_argument("--report-output", type=Path, default=None)
    return parser.parse_args()


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".jsonl":
        return pd.read_json(path, lines=True)
    raise ValueError(f"Unsupported headline input: {path}")


def main() -> None:
    args = parse_args()
    normalized, review = normalize_headlines(
        _read(args.input), source=args.source, default_source_quality=args.source_quality
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(args.output, index=False)
    if args.review_queue and not review.empty:
        args.review_queue.parent.mkdir(parents=True, exist_ok=True)
        review.to_json(args.review_queue, orient="records", lines=True, mode="a")
    report = {
        "artifact": "headline_intelligence_report",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": args.source,
        "rows": len(normalized),
        "review_rows": len(review),
        "event_type_counts": normalized["event_type"].value_counts().to_dict() if not normalized.empty else {},
    }
    report_output = args.report_output or args.output.with_suffix(args.output.suffix + ".report.json")
    report_output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
