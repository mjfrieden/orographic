from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from engine.orographic.event_observatory import load_observatory
from engine.orographic.event_outcomes import enrich_outcomes_with_events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build point-in-time event coverage for Orographic recommendation outcomes."
    )
    parser.add_argument(
        "--observatory",
        type=Path,
        default=Path("engine/data/event_observatory/event_observatory.parquet"),
    )
    parser.add_argument(
        "--outcomes",
        type=Path,
        default=Path("output/research_datasets/all_recommendation_outcomes.parquet"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/research_datasets/event_enriched_option_outcomes.parquet"),
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=Path("output/research_datasets/event_outcome_coverage.json"),
    )
    parser.add_argument("--lookback-days", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    outcomes = pd.read_parquet(args.outcomes)
    observations = load_observatory(args.observatory)
    enriched, report = enrich_outcomes_with_events(
        outcomes, observations, lookback_days=max(int(args.lookback_days), 0)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_parquet(args.output, index=False)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"Saved event-enriched outcomes -> {args.output}")
    print(f"Saved event coverage report -> {args.report_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
