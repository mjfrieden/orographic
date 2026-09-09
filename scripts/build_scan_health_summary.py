from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _dataset_rows(path: Path) -> int:
    if not path.exists():
        return -1
    if path.suffix.lower() == ".parquet":
        import pandas as pd

        return int(len(pd.read_parquet(path)))
    if path.suffix.lower() == ".csv":
        import pandas as pd

        return int(len(pd.read_csv(path)))
    if path.suffix.lower() == ".json":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return len(loaded) if isinstance(loaded, list) else -1
    return -1


def _present_rows(count: int) -> int:
    return 0 if count < 0 else count


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _ledger_health(path: Path) -> dict[str, Any]:
    ledger = _load_json(path)
    outcome = ledger.get("outcome_summary") if isinstance(ledger.get("outcome_summary"), dict) else {}
    aggregate = ledger.get("aggregate") if isinstance(ledger.get("aggregate"), dict) else {}
    with_any_mark = _int(outcome.get("with_any_mark"))
    missing_quotes = _int(outcome.get("missing_outcome_quotes"))
    quote_coverage = round(1.0 - (missing_quotes / max(with_any_mark, 1)), 4) if with_any_mark > 0 else None
    last_mark = ledger.get("last_mark_summary") if isinstance(ledger.get("last_mark_summary"), dict) else {}
    return {
        "path": str(path),
        "exists": path.exists(),
        "updated_at_utc": ledger.get("updated_at_utc"),
        "runs": _int(aggregate.get("runs")),
        "picks": _int(outcome.get("picks")),
        "pending": _int(outcome.get("pending")),
        "partial": _int(outcome.get("partial")),
        "complete": _int(outcome.get("complete")),
        "with_any_mark": with_any_mark,
        "with_all_fixed_marks": _int(outcome.get("with_all_fixed_marks")),
        "missing_outcome_quotes": missing_quotes,
        "quote_coverage_pct": quote_coverage,
        "marks_written_last_run": _int(last_mark.get("marks_written")),
        "quotes_missing_last_run": _int(last_mark.get("quotes_missing")),
        "capture_policy_v2_picks": _int(outcome.get("capture_policy_v2_picks")),
        "capture_windows_valid": _int(outcome.get("capture_windows_valid")),
        "capture_windows_quote_missing": _int(outcome.get("capture_windows_quote_missing")),
        "capture_windows_stale_quote": _int(outcome.get("capture_windows_stale_quote")),
        "capture_windows_missed": _int(outcome.get("capture_windows_missed")),
        "capture_windows_newly_missed_last_run": _int(last_mark.get("capture_windows_newly_missed")),
        "capture_windows_quote_missing_last_run": _int(last_mark.get("capture_windows_quote_missing")),
        "capture_windows_stale_quote_last_run": _int(last_mark.get("capture_windows_stale_quote")),
        "trajectory_scored_picks": _int(outcome.get("trajectory_scored_picks")),
        "trajectory_marks": _int(outcome.get("trajectory_marks")),
        "trajectory_picks_with_4_marks": _int(outcome.get("trajectory_picks_with_4_marks")),
        "trajectory_active_picks_last_run": _int(last_mark.get("trajectory_active_picks")),
        "trajectory_marks_written_last_run": _int(last_mark.get("trajectory_marks_written")),
        "trajectory_quotes_missing_last_run": _int(last_mark.get("trajectory_quotes_missing")),
        "trajectory_quotes_stale_last_run": _int(last_mark.get("trajectory_quotes_stale")),
    }


def _check(checks: list[dict[str, Any]], name: str, passed: bool, **details: Any) -> None:
    checks.append({"name": name, "passed": bool(passed), **details})


def build_scan_health_summary(
    *,
    snapshot: Path,
    prospective_ledger: Path,
    moonshot_ledger: Path,
    research_audit: Path,
    archive_manifest: Path,
    recommendation_dataset: Path,
    moonshot_dataset: Path,
    combined_dataset: Path,
    canonical_manifest: Path | None = None,
    cirrus_materialization: Path | None = None,
    mart_sync: Path | None = None,
    output: Path | None = None,
    max_run_age_minutes: int = 240,
    min_quote_coverage_pct: float = 0.95,
    min_recommendation_rows: int = 1,
    r2_status: str = "unknown",
    dashboard_push_status: str = "unknown",
    dashboard_deploy_status: str = "unknown",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    payload = _load_json(snapshot)
    generated_at = _parse_dt(payload.get("generated_at_utc"))
    age_minutes = round((now - generated_at).total_seconds() / 60.0, 2) if generated_at else None
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    council_summary = council.get("summary") if isinstance(council.get("summary"), dict) else {}
    regime = payload.get("regime") if isinstance(payload.get("regime"), dict) else {}

    prospective = _ledger_health(prospective_ledger)
    moonshot = _ledger_health(moonshot_ledger)
    audit = _load_json(research_audit)
    audit_summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
    archive = _load_json(archive_manifest)
    archive_summary = archive.get("summary") if isinstance(archive.get("summary"), dict) else {}
    canonical = _load_json(canonical_manifest) if canonical_manifest is not None else {}
    canonical_evidence = canonical.get("evidence") if isinstance(canonical.get("evidence"), dict) else {}
    cirrus = _load_json(cirrus_materialization) if cirrus_materialization is not None else {}
    mart_sync_payload = _load_json(mart_sync) if mart_sync is not None else {}

    recommendation_rows = _dataset_rows(recommendation_dataset)
    moonshot_rows = _dataset_rows(moonshot_dataset)
    combined_rows = _dataset_rows(combined_dataset)
    moonshot_dataset_missing = moonshot_rows < 0
    effective_moonshot_rows = _present_rows(moonshot_rows)
    total_with_marks = prospective["with_any_mark"] + moonshot["with_any_mark"]
    total_missing_quotes = prospective["missing_outcome_quotes"] + moonshot["missing_outcome_quotes"]
    overall_quote_coverage = (
        round(1.0 - (total_missing_quotes / max(total_with_marks, 1)), 4)
        if total_with_marks > 0
        else None
    )

    checks: list[dict[str, Any]] = []
    _check(checks, "snapshot_exists", snapshot.exists(), path=str(snapshot))
    _check(
        checks,
        "snapshot_is_fresh",
        age_minutes is not None and age_minutes <= max_run_age_minutes,
        actual_age_minutes=age_minutes,
        max_age_minutes=max_run_age_minutes,
    )
    scout_count = _int(summary.get("scout_signal_count"))
    forge_count = _int(summary.get("forge_candidate_count"))
    explicit_abstain = council.get("abstain") is True and scout_count == 0 and forge_count == 0
    _check(
        checks,
        "scan_emitted_scout_signals",
        scout_count > 0 or explicit_abstain,
        actual=scout_count,
        accepted_abstention=explicit_abstain,
    )
    _check(checks, "prospective_ledger_exists", prospective["exists"], path=str(prospective_ledger))
    _check(checks, "moonshot_ledger_exists", moonshot["exists"], path=str(moonshot_ledger))
    _check(
        checks,
        "labeled_outcomes_exist",
        total_with_marks > 0,
        actual_marked_picks=total_with_marks,
    )
    _check(
        checks,
        "quote_coverage",
        overall_quote_coverage is not None and overall_quote_coverage >= min_quote_coverage_pct,
        actual=overall_quote_coverage,
        required_min=min_quote_coverage_pct,
    )
    strict_capture_picks = prospective["capture_policy_v2_picks"] + moonshot["capture_policy_v2_picks"]
    missed_capture_windows = prospective["capture_windows_missed"] + moonshot["capture_windows_missed"]
    retryable_missing_windows = (
        prospective["capture_windows_quote_missing"] + moonshot["capture_windows_quote_missing"]
    )
    stale_quote_windows = prospective["capture_windows_stale_quote"] + moonshot["capture_windows_stale_quote"]
    newly_missed_windows = (
        prospective["capture_windows_newly_missed_last_run"]
        + moonshot["capture_windows_newly_missed_last_run"]
    )
    current_retryable_missing_windows = (
        prospective["capture_windows_quote_missing_last_run"]
        + moonshot["capture_windows_quote_missing_last_run"]
    )
    current_stale_quote_windows = (
        prospective["capture_windows_stale_quote_last_run"]
        + moonshot["capture_windows_stale_quote_last_run"]
    )
    trajectory_active = prospective["trajectory_active_picks_last_run"] + moonshot["trajectory_active_picks_last_run"]
    trajectory_written = prospective["trajectory_marks_written_last_run"] + moonshot["trajectory_marks_written_last_run"]
    trajectory_missing = prospective["trajectory_quotes_missing_last_run"] + moonshot["trajectory_quotes_missing_last_run"]
    trajectory_stale = prospective["trajectory_quotes_stale_last_run"] + moonshot["trajectory_quotes_stale_last_run"]
    _check(
        checks,
        "strict_capture_policy_active",
        strict_capture_picks > 0 or explicit_abstain,
        actual_picks=strict_capture_picks,
        required_min=1,
        awaiting_next_pick=explicit_abstain and strict_capture_picks == 0,
    )
    _check(
        checks,
        "outcome_capture_timing_integrity",
        newly_missed_windows == 0 and current_stale_quote_windows == 0,
        newly_missed_windows=newly_missed_windows,
        retryable_quote_missing_windows=current_retryable_missing_windows,
        stale_quote_windows=current_stale_quote_windows,
        historical_missed_windows=missed_capture_windows,
        historical_retryable_quote_missing_windows=retryable_missing_windows,
        historical_stale_quote_windows=stale_quote_windows,
        note="Historical capture debt remains visible but does not make a healthy current run fail.",
    )
    _check(
        checks,
        "trajectory_capture_health",
        trajectory_active == 0 or (trajectory_written > 0 and trajectory_missing == 0 and trajectory_stale == 0),
        active_picks=trajectory_active,
        marks_written=trajectory_written,
        missing_quotes=trajectory_missing,
        stale_quotes=trajectory_stale,
        note="No active picks is valid; otherwise every scheduled run must add fresh trajectory evidence.",
    )
    _check(checks, "archive_manifest_exists", archive_manifest.exists(), path=str(archive_manifest))
    _check(
        checks,
        "archive_rows_captured",
        _int(archive_summary.get("rows_archived")) > 0 or explicit_abstain,
        actual=_int(archive_summary.get("rows_archived")),
    )
    _check(
        checks,
        "research_audit_passed",
        audit.get("status") == "passed",
        actual=audit.get("status"),
    )
    _check(
        checks,
        "recommendation_dataset_rows",
        recommendation_rows >= min_recommendation_rows,
        actual=recommendation_rows,
        required_min=min_recommendation_rows,
    )
    _check(
        checks,
        "combined_dataset_consistency",
        combined_rows == recommendation_rows + effective_moonshot_rows and combined_rows >= 0,
        actual=combined_rows,
        expected=recommendation_rows + effective_moonshot_rows,
        moonshot_dataset_missing=moonshot_dataset_missing,
    )
    if canonical_manifest is not None:
        canonical_checks = canonical.get("checks") if isinstance(canonical.get("checks"), dict) else {}
        _check(
            checks,
            "canonical_evidence_bundle_valid",
            canonical_manifest.exists()
            and canonical_checks.get("recommendations_unique") is True
            and canonical_checks.get("quotes_unique") is True
            and canonical_checks.get("immutable_labels_preserved") is True
            and canonical_checks.get("inputs_readable") is True,
            # Input-readability failures mean the materialization silently lost
            # a source and must not be treated as healthy.
            path=str(canonical_manifest),
            bundle_id=canonical.get("bundle_id"),
            inputs_readable=canonical_checks.get("inputs_readable"),
        )
    if cirrus_materialization is not None:
        cirrus_checks = cirrus.get("checks") if isinstance(cirrus.get("checks"), dict) else {}
        expected_quote_rows = _int(
            canonical_evidence.get("cumulative_inventory", {}).get("option_quote_rows")
            if isinstance(canonical_evidence.get("cumulative_inventory"), dict) else 0
        )
        _check(
            checks,
            "cirrus_materialization_valid",
            cirrus_materialization.exists()
            and cirrus.get("status") == "passed"
            and cirrus.get("canonical_bundle_id") == canonical.get("bundle_id")
            and _int(cirrus.get("rows")) == expected_quote_rows
            and cirrus_checks.get("canonical_bundle_valid") is True
            and cirrus_checks.get("row_count_matches") is True
            and cirrus_checks.get("partition_count_matches") is True
            and cirrus_checks.get("stale_partitions_replaced") is True,
            path=str(cirrus_materialization),
            bundle_id=cirrus.get("canonical_bundle_id"),
            expected_bundle_id=canonical.get("bundle_id"),
            rows=_int(cirrus.get("rows")),
            expected_rows=expected_quote_rows,
            latest_quote_date=cirrus.get("latest_quote_date"),
        )
    _check(
        checks,
        "dashboard_push_completed",
        dashboard_push_status in {"success", "skipped"},
        actual=dashboard_push_status,
    )
    _check(
        checks,
        "dashboard_deploy_completed",
        dashboard_deploy_status in {"success", "skipped"},
        actual=dashboard_deploy_status,
    )
    _check(
        checks,
        "r2_archival_completed",
        r2_status in {"success", "skipped"},
        actual=r2_status,
    )

    failed = [check for check in checks if not check["passed"]]
    warnings: list[dict[str, Any]] = []
    event_feed_status = str(audit_summary.get("event_feed_status") or "unknown").lower()
    if event_feed_status not in {"unknown", "healthy", "success", "passed"}:
        warnings.append({
            "name": "event_feed_delivery_degraded",
            "status": event_feed_status,
            "new_rows": _int(audit_summary.get("event_feed_new_rows")),
            "http_429_responses": _int(audit_summary.get("event_feed_http_429_responses")),
            "note": "Market and option collection can remain healthy while event evidence is degraded.",
        })
    mart_status = str(mart_sync_payload.get("status") or "")
    if mart_sync_payload and mart_status not in {"ready_two_source", "published"}:
        warnings.append({
            "name": "shared_mart_not_two_source",
            "status": mart_status or "missing",
            "source_systems": mart_sync_payload.get("source_systems") or [],
            "note": mart_sync_payload.get("next_action")
            or "Restore a current Cirrus export and rebuild the shared research mart.",
        })
    report = {
        "artifact": "scan_health_summary",
        "schema_version": 1,
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "status": "passed" if not failed else "failed",
        "snapshot": {
            "path": str(snapshot),
            "generated_at_utc": payload.get("generated_at_utc"),
            "age_minutes": age_minutes,
            "regime_mode": regime.get("mode"),
            "regime_bias": regime.get("bias"),
            "scout_signal_count": scout_count,
            "pre_forge_signal_count": _int(summary.get("pre_forge_signal_count")),
            "forge_candidate_count": forge_count,
            "abstain": council.get("abstain"),
            "live_count": _int(council_summary.get("live_count")),
            "shadow_count": _int(council_summary.get("shadow_count")),
        },
        "labels": {
            "prospective": prospective,
            "moonshot": moonshot,
            "marked_picks": total_with_marks,
            "missing_outcome_quotes": total_missing_quotes,
            "quote_coverage_pct": overall_quote_coverage,
            "capture_policy_v2_picks": strict_capture_picks,
            "capture_windows_missed": missed_capture_windows,
            "capture_windows_quote_missing": retryable_missing_windows,
            "capture_windows_stale_quote": stale_quote_windows,
            "capture_windows_newly_missed_last_run": newly_missed_windows,
            "capture_windows_quote_missing_last_run": current_retryable_missing_windows,
            "capture_windows_stale_quote_last_run": current_stale_quote_windows,
            "trajectory_active_picks_last_run": trajectory_active,
            "trajectory_marks_written_last_run": trajectory_written,
            "trajectory_quotes_missing_last_run": trajectory_missing,
            "trajectory_quotes_stale_last_run": trajectory_stale,
            "trajectory_scored_picks": prospective["trajectory_scored_picks"] + moonshot["trajectory_scored_picks"],
            "trajectory_marks": prospective["trajectory_marks"] + moonshot["trajectory_marks"],
            "trajectory_picks_with_4_marks": prospective["trajectory_picks_with_4_marks"] + moonshot["trajectory_picks_with_4_marks"],
        },
        "research": {
            "audit_status": audit.get("status"),
            "audit_summary": audit_summary,
            "archive_rows": _int(archive_summary.get("rows_archived")),
            "archive_symbols": _int(archive_summary.get("symbols_archived")),
            "recommendation_dataset_rows": recommendation_rows,
            "moonshot_dataset_rows": moonshot_rows,
            "combined_dataset_rows": combined_rows,
            "evidence_lifecycle": canonical_evidence,
            "canonical_bundle_id": canonical.get("bundle_id"),
            "canonical_manifest_path": str(canonical_manifest) if canonical_manifest is not None else None,
            "cirrus_materialization": cirrus,
            "shared_mart_sync": {
                "status": mart_sync_payload.get("status"),
                "mart_id": mart_sync_payload.get("mart_id"),
                "source_systems": mart_sync_payload.get("source_systems"),
                "generated_at_utc": mart_sync_payload.get("generated_at_utc"),
                "next_action": mart_sync_payload.get("next_action"),
            } if mart_sync_payload else None,
        },
        "publishing": {
            "dashboard_push_status": dashboard_push_status,
            "dashboard_deploy_status": dashboard_deploy_status,
            "r2_status": r2_status,
        },
        "checks": checks,
        "failed_checks": failed,
        "warnings": warnings,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an Orographic scheduled-scan health summary.")
    parser.add_argument("--snapshot", type=Path, default=Path("web/data/latest_run.json"))
    parser.add_argument("--prospective-ledger", type=Path, default=Path("web/data/diagnostics/prospective_pick_ledger.json"))
    parser.add_argument("--moonshot-ledger", type=Path, default=Path("web/data/diagnostics/moonshot_prospective_ledger.json"))
    parser.add_argument("--research-audit", type=Path, default=Path("output/research_datasets/research_data_capture_audit.json"))
    parser.add_argument("--archive-manifest", type=Path, default=Path("engine/data/live_options_archive/coverage_manifest.json"))
    parser.add_argument("--recommendation-dataset", type=Path, default=Path("output/research_datasets/option_recommendation_outcomes.parquet"))
    parser.add_argument("--moonshot-dataset", type=Path, default=Path("output/research_datasets/moonshot_outcomes.parquet"))
    parser.add_argument("--combined-dataset", type=Path, default=Path("output/research_datasets/all_recommendation_outcomes.parquet"))
    parser.add_argument("--canonical-manifest", type=Path, default=Path("output/canonical_evidence/evidence_manifest.json"))
    parser.add_argument(
        "--cirrus-materialization",
        type=Path,
        default=Path("engine/data/options/blended/partitioned/canonical_materialization.json"),
    )
    parser.add_argument(
        "--mart-sync",
        type=Path,
        default=Path("web/data/diagnostics/shared_mart_sync_latest.json"),
    )
    parser.add_argument("--output", type=Path, default=Path("output/research_datasets/scan_health_summary.json"))
    parser.add_argument("--max-run-age-minutes", type=int, default=240)
    parser.add_argument("--min-quote-coverage-pct", type=float, default=0.95)
    parser.add_argument("--min-recommendation-rows", type=int, default=1)
    parser.add_argument("--r2-status", default="unknown")
    parser.add_argument("--dashboard-push-status", default="unknown")
    parser.add_argument("--dashboard-deploy-status", default="unknown")
    parser.add_argument("--warn-only", action="store_true", help="Write the report but do not fail on failed checks.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_scan_health_summary(
        snapshot=args.snapshot,
        prospective_ledger=args.prospective_ledger,
        moonshot_ledger=args.moonshot_ledger,
        research_audit=args.research_audit,
        archive_manifest=args.archive_manifest,
        recommendation_dataset=args.recommendation_dataset,
        moonshot_dataset=args.moonshot_dataset,
        combined_dataset=args.combined_dataset,
        canonical_manifest=args.canonical_manifest,
        cirrus_materialization=args.cirrus_materialization,
        mart_sync=args.mart_sync,
        output=args.output,
        max_run_age_minutes=max(int(args.max_run_age_minutes), 1),
        min_quote_coverage_pct=max(min(float(args.min_quote_coverage_pct), 1.0), 0.0),
        min_recommendation_rows=max(int(args.min_recommendation_rows), 0),
        r2_status=str(args.r2_status or "unknown"),
        dashboard_push_status=str(args.dashboard_push_status or "unknown"),
        dashboard_deploy_status=str(args.dashboard_deploy_status or "unknown"),
    )
    print(json.dumps(
        {
            "status": report["status"],
            "snapshot": report["snapshot"],
            "labels": report["labels"],
            "research": report["research"],
            "publishing": report["publishing"],
            "failed_checks": report["failed_checks"],
        },
        indent=2,
    ))
    return 0 if args.warn_only or report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
