from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_outcome_capture_health(
    *,
    prospective_ledger: Path,
    moonshot_ledger: Path,
    token_configured: bool,
    prospective_step_status: str,
    moonshot_step_status: str,
    evidence_step_status: str = "unknown",
    scheduled_at_utc: str = "",
    scheduler: str = "manual",
    max_scheduler_delay_seconds: int = 600,
    min_trajectory_capture_ratio: float = 0.30,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    scheduled_at = _parse_utc(scheduled_at_utc)
    scheduler_delay_seconds = (
        max(0.0, (now - scheduled_at).total_seconds()) if scheduled_at is not None else None
    )
    ledgers = []
    for name, path, step_status in (
        ("prospective", prospective_ledger, prospective_step_status),
        ("moonshot", moonshot_ledger, moonshot_step_status),
    ):
        ledger = _load(path)
        last = ledger.get("last_mark_summary") if isinstance(ledger.get("last_mark_summary"), dict) else {}
        outcome = ledger.get("outcome_summary") if isinstance(ledger.get("outcome_summary"), dict) else {}
        ledgers.append({
            "name": name,
            "path": str(path),
            "exists": path.exists(),
            "step_status": step_status,
            "last_capture_attempt_at_utc": ledger.get("last_capture_attempt_at_utc"),
            "active_picks": _int(last.get("trajectory_active_picks")),
            "marks_written": _int(last.get("trajectory_marks_written")),
            "quotes_missing": _int(last.get("trajectory_quotes_missing")),
            "quotes_stale": _int(last.get("trajectory_quotes_stale")),
            "fixed_windows_valid": _int(last.get("capture_windows_valid")),
            "fixed_windows_newly_missed": _int(last.get("capture_windows_newly_missed")),
            "trajectory_scored_picks": _int(outcome.get("trajectory_scored_picks")),
            "trajectory_marks": _int(outcome.get("trajectory_marks")),
        })

    active = sum(row["active_picks"] for row in ledgers)
    written = sum(row["marks_written"] for row in ledgers)
    missing = sum(row["quotes_missing"] for row in ledgers)
    stale = sum(row["quotes_stale"] for row in ledgers)
    missed = sum(row["fixed_windows_newly_missed"] for row in ledgers)
    trajectory_contracts = sum(row["trajectory_scored_picks"] for row in ledgers)
    trajectory_marks = sum(row["trajectory_marks"] for row in ledgers)
    trajectory_capture_ratio = min(1.0, written / active) if active else 1.0
    min_trajectory_capture_ratio = min(max(float(min_trajectory_capture_ratio), 0.0), 1.0)
    step_failures = [
        row["name"] for row in ledgers if row["step_status"] not in {"success", "skipped"}
    ]
    trajectory_passed = active == 0 or (written > 0 and missing == 0 and stale == 0)
    checks = [
        {
            "name": "scheduler_delivery_fresh",
            "passed": scheduler_delay_seconds is None
            or scheduler_delay_seconds <= max_scheduler_delay_seconds,
            "scheduler": scheduler,
            "scheduled_at_utc": scheduled_at.isoformat().replace("+00:00", "Z")
            if scheduled_at is not None else None,
            "delay_seconds": round(scheduler_delay_seconds, 3)
            if scheduler_delay_seconds is not None else None,
            "max_delay_seconds": max_scheduler_delay_seconds,
            "manual_dispatch": scheduled_at is None,
        },
        {"name": "tradier_capture_configured", "passed": token_configured},
        {"name": "capture_steps_completed", "passed": not step_failures, "failed_steps": step_failures},
        {
            "name": "trajectory_capture_health",
            "passed": trajectory_passed,
            "active_picks": active,
            "marks_written": written,
            "missing_quotes": missing,
            "stale_quotes": stale,
            "capture_ratio": round(trajectory_capture_ratio, 4),
            "minimum_alert_ratio": min_trajectory_capture_ratio,
        },
        {
            "name": "fixed_window_capture_health",
            "passed": missed == 0,
            "missed_windows": missed,
        },
        {
            "name": "evidence_refresh_completed",
            "passed": evidence_step_status in {"success", "skipped"},
            "actual": evidence_step_status,
        },
    ]
    failed = [row for row in checks if not row["passed"]]
    # A single illiquid contract can legitimately retain an old broker quote
    # while the rest of the capture lane remains healthy. Keep that condition
    # visible as degraded health, but page only when trajectory coverage is at
    # or below the service threshold. All other failed checks remain actionable.
    alert_checks = [
        row
        for row in failed
        if row["name"] != "trajectory_capture_health"
        or trajectory_capture_ratio <= min_trajectory_capture_ratio
    ]
    generated = now.isoformat().replace("+00:00", "Z")
    return {
        "artifact": "outcome_capture_health",
        "schema_version": 1,
        "generated_at_utc": generated,
        "status": "failed" if alert_checks else ("degraded" if failed else "passed"),
        "alert_required": bool(alert_checks),
        "scheduler": {
            "source": scheduler,
            "scheduled_at_utc": scheduled_at.isoformat().replace("+00:00", "Z")
            if scheduled_at is not None else None,
            "delivery_delay_seconds": round(scheduler_delay_seconds, 3)
            if scheduler_delay_seconds is not None else None,
            "max_delivery_delay_seconds": max_scheduler_delay_seconds,
        },
        "labels": {
            "trajectory_active_picks_last_run": active,
            "trajectory_marks_written_last_run": written,
            "trajectory_quotes_missing_last_run": missing,
            "trajectory_quotes_stale_last_run": stale,
            "trajectory_capture_ratio_last_run": round(trajectory_capture_ratio, 4),
            "trajectory_minimum_alert_ratio": min_trajectory_capture_ratio,
            "fixed_capture_windows_missed_last_run": missed,
            "trajectory_scored_picks": trajectory_contracts,
            "trajectory_marks": trajectory_marks,
        },
        "ledgers": ledgers,
        "checks": checks,
        "failed_checks": failed,
        "alert_checks": alert_checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the scheduled Tradier outcome-capture health artifact.")
    parser.add_argument("--prospective-ledger", type=Path, default=Path("web/data/diagnostics/prospective_pick_ledger.json"))
    parser.add_argument("--moonshot-ledger", type=Path, default=Path("web/data/diagnostics/moonshot_prospective_ledger.json"))
    parser.add_argument("--token-configured", default="false")
    parser.add_argument("--prospective-step-status", default="unknown")
    parser.add_argument("--moonshot-step-status", default="unknown")
    parser.add_argument("--evidence-step-status", default="unknown")
    parser.add_argument("--scheduled-at-utc", default="")
    parser.add_argument("--scheduler", default="manual")
    parser.add_argument("--max-scheduler-delay-seconds", type=int, default=600)
    parser.add_argument("--min-trajectory-capture-ratio", type=float, default=0.30)
    parser.add_argument("--output", type=Path, default=Path("web/data/diagnostics/outcome_capture_health_latest.json"))
    parser.add_argument("--fail-on-alert", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_outcome_capture_health(
        prospective_ledger=args.prospective_ledger,
        moonshot_ledger=args.moonshot_ledger,
        token_configured=str(args.token_configured).strip().lower() in {"1", "true", "yes"},
        prospective_step_status=str(args.prospective_step_status),
        moonshot_step_status=str(args.moonshot_step_status),
        evidence_step_status=str(args.evidence_step_status),
        scheduled_at_utc=str(args.scheduled_at_utc),
        scheduler=str(args.scheduler),
        max_scheduler_delay_seconds=max(args.max_scheduler_delay_seconds, 1),
        min_trajectory_capture_ratio=float(args.min_trajectory_capture_ratio),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "alert_required": report["alert_required"], "failed_checks": report["failed_checks"]}, indent=2))
    return 1 if args.fail_on_alert and report["alert_required"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
