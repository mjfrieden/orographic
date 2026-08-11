from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _dataset_rows(path: Path) -> int:
    if not path.exists():
        return -1
    if path.suffix.lower() == ".parquet":
        return int(len(pd.read_parquet(path)))
    if path.suffix.lower() == ".csv":
        return int(len(pd.read_csv(path)))
    if path.suffix.lower() == ".json":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return len(loaded) if isinstance(loaded, list) else -1
    return -1


def _ledger_pick_rows(path: Path) -> int:
    ledger = _load_json(path)
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    total = 0
    for entry in entries:
        if isinstance(entry, dict):
            picks = entry.get("picks") if isinstance(entry.get("picks"), list) else []
            total += len(picks)
    return total


def build_audit_report(
    *,
    live_archive_manifest: Path,
    prospective_ledger: Path,
    moonshot_ledger: Path,
    recommendation_dataset: Path,
    moonshot_dataset: Path,
    combined_dataset: Path,
    min_archive_rows: int = 1,
    min_recommendation_rows: int = 0,
    event_quality_report: Path | None = None,
    event_coverage_report: Path | None = None,
    event_enriched_dataset: Path | None = None,
    event_feed_health: Path | None = None,
) -> dict[str, Any]:
    archive = _load_json(live_archive_manifest)
    archive_summary = archive.get("summary") if isinstance(archive.get("summary"), dict) else {}
    archived_rows = int(archive_summary.get("rows_archived") or 0)
    archived_symbols = int(archive_summary.get("symbols_archived") or 0)
    prospective_rows = _ledger_pick_rows(prospective_ledger)
    moonshot_rows = _ledger_pick_rows(moonshot_ledger)
    recommendation_dataset_rows = _dataset_rows(recommendation_dataset)
    moonshot_dataset_rows = _dataset_rows(moonshot_dataset)
    combined_dataset_rows = _dataset_rows(combined_dataset)
    event_quality = _load_json(event_quality_report) if event_quality_report else {}
    event_coverage = _load_json(event_coverage_report) if event_coverage_report else {}
    event_coverage_summary = (
        event_coverage.get("summary") if isinstance(event_coverage.get("summary"), dict) else {}
    )
    event_enriched_rows = _dataset_rows(event_enriched_dataset) if event_enriched_dataset else -1
    feed_health = _load_json(event_feed_health) if event_feed_health else {}

    archive_required = min_archive_rows > 0
    checks = [
        {
            "name": "live_archive_manifest_exists",
            "passed": live_archive_manifest.exists() or not archive_required,
            "actual": str(live_archive_manifest),
            "required": archive_required,
        },
        {
            "name": "live_archive_rows",
            "passed": archived_rows >= min_archive_rows,
            "actual": archived_rows,
            "required_min": min_archive_rows,
        },
        {
            "name": "live_archive_symbols",
            "passed": archived_symbols > 0 if min_archive_rows > 0 else True,
            "actual": archived_symbols,
            "required_min": 1 if min_archive_rows > 0 else 0,
        },
        {
            "name": "prospective_ledger_exists",
            "passed": prospective_ledger.exists(),
            "actual": str(prospective_ledger),
        },
        {
            "name": "moonshot_ledger_exists",
            "passed": moonshot_ledger.exists(),
            "actual": str(moonshot_ledger),
        },
        {
            "name": "recommendation_dataset_exists",
            "passed": recommendation_dataset_rows >= 0,
            "actual": str(recommendation_dataset),
        },
        {
            "name": "moonshot_dataset_exists",
            "passed": moonshot_dataset_rows >= 0,
            "actual": str(moonshot_dataset),
        },
        {
            "name": "combined_dataset_exists",
            "passed": combined_dataset_rows >= 0,
            "actual": str(combined_dataset),
        },
        {
            "name": "recommendation_dataset_rows",
            "passed": recommendation_dataset_rows >= min_recommendation_rows,
            "actual": recommendation_dataset_rows,
            "required_min": min_recommendation_rows,
        },
        {
            "name": "recommendation_dataset_matches_ledger",
            "passed": recommendation_dataset_rows == prospective_rows,
            "actual": recommendation_dataset_rows,
            "expected": prospective_rows,
        },
        {
            "name": "moonshot_dataset_matches_ledger",
            "passed": moonshot_dataset_rows == moonshot_rows,
            "actual": moonshot_dataset_rows,
            "expected": moonshot_rows,
        },
        {
            "name": "combined_dataset_consistency",
            "passed": combined_dataset_rows == recommendation_dataset_rows + moonshot_dataset_rows,
            "actual": combined_dataset_rows,
            "expected": recommendation_dataset_rows + moonshot_dataset_rows,
        },
        {
            "name": "combined_dataset_matches_ledgers",
            "passed": combined_dataset_rows == prospective_rows + moonshot_rows,
            "actual": combined_dataset_rows,
            "expected": prospective_rows + moonshot_rows,
        },
    ]
    if event_quality_report is not None:
        checks.append(
            {
                "name": "event_quality_report_exists",
                "passed": event_quality_report.exists(),
                "actual": str(event_quality_report),
            }
        )
    if event_coverage_report is not None:
        checks.append(
            {
                "name": "event_coverage_report_exists",
                "passed": event_coverage_report.exists(),
                "actual": str(event_coverage_report),
            }
        )
    if event_enriched_dataset is not None:
        checks.extend(
            [
                {
                    "name": "event_enriched_dataset_exists",
                    "passed": event_enriched_rows >= 0,
                    "actual": str(event_enriched_dataset),
                },
                {
                    "name": "event_enriched_dataset_matches_outcomes",
                    "passed": event_enriched_rows == combined_dataset_rows,
                    "actual": event_enriched_rows,
                    "expected": combined_dataset_rows,
                },
            ]
        )
    if event_feed_health is not None:
        checks.append(
            {
                "name": "event_feed_health_exists",
                "passed": event_feed_health.exists(),
                "actual": str(event_feed_health),
            }
        )
    failed = [check for check in checks if not bool(check["passed"])]
    warnings = []
    if feed_health and feed_health.get("status") != "healthy":
        warnings.append(
            {
                "name": "event_feed_degraded",
                "status": feed_health.get("status"),
                "http_429_responses": int(feed_health.get("http_429_responses") or 0),
                "failed_batches": int(feed_health.get("failed_batches") or 0),
                "new_rows": int(feed_health.get("new_rows") or 0),
            }
        )
    return {
        "artifact": "research_data_capture_audit",
        "schema_version": 1,
        "status": "passed" if not failed else "failed",
        "summary": {
            "archived_rows": archived_rows,
            "archived_symbols": archived_symbols,
            "prospective_ledger_pick_rows": prospective_rows,
            "moonshot_ledger_pick_rows": moonshot_rows,
            "recommendation_dataset_rows": recommendation_dataset_rows,
            "moonshot_dataset_rows": moonshot_dataset_rows,
            "combined_dataset_rows": combined_dataset_rows,
            "event_observation_rows": int(event_quality.get("rows") or 0),
            "event_enriched_dataset_rows": event_enriched_rows,
            "rows_with_prior_events": int(event_coverage_summary.get("rows_with_prior_events") or 0),
            "complete_outcome_event_coverage_pct": float(
                event_coverage_summary.get("complete_outcome_event_coverage_pct") or 0.0
            ),
            "event_feed_status": feed_health.get("status"),
            "event_feed_new_rows": int(feed_health.get("new_rows") or 0),
            "event_feed_mapped_symbols": int(feed_health.get("mapped_symbols") or 0),
            "event_feed_http_429_responses": int(feed_health.get("http_429_responses") or 0),
            "event_feed_elapsed_seconds": float(feed_health.get("elapsed_seconds") or 0.0),
        },
        "checks": checks,
        "failed_checks": failed,
        "warnings": warnings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Orographic research data capture artifacts.")
    parser.add_argument("--live-archive-manifest", type=Path, default=Path("engine/data/live_options_archive/coverage_manifest.json"))
    parser.add_argument("--prospective-ledger", type=Path, default=Path("web/data/diagnostics/prospective_pick_ledger.json"))
    parser.add_argument("--moonshot-ledger", type=Path, default=Path("web/data/diagnostics/moonshot_prospective_ledger.json"))
    parser.add_argument("--research-dataset-dir", type=Path, default=Path("output/research_datasets"))
    parser.add_argument("--min-archive-rows", type=int, default=1)
    parser.add_argument("--min-recommendation-rows", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("output/research_datasets/research_data_capture_audit.json"))
    parser.add_argument(
        "--event-quality-report",
        type=Path,
        default=Path("engine/data/event_observatory/event_observatory.parquet.quality.json"),
    )
    parser.add_argument(
        "--event-coverage-report",
        type=Path,
        default=Path("output/research_datasets/event_outcome_coverage.json"),
    )
    parser.add_argument(
        "--event-enriched-dataset",
        type=Path,
        default=Path("output/research_datasets/event_enriched_option_outcomes.parquet"),
    )
    parser.add_argument(
        "--event-feed-health",
        type=Path,
        default=Path("engine/data/event_observatory/gdelt_company_news_health.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_audit_report(
        live_archive_manifest=args.live_archive_manifest,
        prospective_ledger=args.prospective_ledger,
        moonshot_ledger=args.moonshot_ledger,
        recommendation_dataset=args.research_dataset_dir / "option_recommendation_outcomes.parquet",
        moonshot_dataset=args.research_dataset_dir / "moonshot_outcomes.parquet",
        combined_dataset=args.research_dataset_dir / "all_recommendation_outcomes.parquet",
        min_archive_rows=max(int(args.min_archive_rows), 0),
        min_recommendation_rows=max(int(args.min_recommendation_rows), 0),
        event_quality_report=args.event_quality_report,
        event_coverage_report=args.event_coverage_report,
        event_enriched_dataset=args.event_enriched_dataset,
        event_feed_health=args.event_feed_health,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    if report["status"] != "passed":
        print(json.dumps(report["failed_checks"], indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
