from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


SEVERITY_ORDER = {"green": 0, "amber": 1, "red": 2}


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _integer(value: object) -> int:
    number = _number(value)
    return int(number) if number is not None else 0


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator > 0 else None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _age_minutes(value: object, now_utc: datetime) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return round((now_utc - parsed).total_seconds() / 60.0, 2)


def _gate(
    code: str,
    severity: str,
    title: str,
    summary: str,
    *,
    metrics: dict[str, Any],
    actions: Iterable[str] = (),
) -> dict[str, Any]:
    if severity not in SEVERITY_ORDER:
        raise ValueError(f"Unsupported severity: {severity}")
    return {
        "code": code,
        "status": severity,
        "blocking": severity == "red",
        "title": title,
        "summary": summary,
        "metrics": metrics,
        "actions": list(actions),
    }


def _ledger_metrics(ledger: dict[str, Any]) -> dict[str, Any]:
    outcome = _as_dict(ledger.get("outcome_summary"))
    picks = _integer(outcome.get("picks"))
    marked = _integer(outcome.get("with_any_mark"))
    complete = _integer(outcome.get("with_all_fixed_marks") or outcome.get("complete"))

    executable_denominator = 0
    executable_numerator = 0
    for entry in _as_list(ledger.get("entries")):
        for pick in _as_list(_as_dict(entry).get("picks")):
            pick = _as_dict(pick)
            outcomes = _as_dict(pick.get("outcomes"))
            friday = _as_dict(_as_dict(outcomes.get("fixed_exit_marks")).get("friday_close"))
            if not friday:
                continue
            executable_denominator += 1
            emission = _as_dict(pick.get("emission_quote"))
            entry_ask = _number(emission.get("ask"))
            exit_bid = _number(friday.get("bid"))
            if entry_ask is not None and entry_ask > 0 and exit_bid is not None and exit_bid >= 0:
                executable_numerator += 1

    return {
        "picks": picks,
        "with_any_mark": marked,
        "complete": complete,
        "pending": _integer(outcome.get("pending")),
        "partial": _integer(outcome.get("partial")),
        "observed_mark_coverage_pct": _ratio(marked, picks),
        "total_cohort_completion_pct": _ratio(complete, picks),
        "executable_quote_rows": executable_numerator,
        "friday_outcome_rows": executable_denominator,
        "executable_quote_coverage_pct": _ratio(executable_numerator, executable_denominator),
        "updated_at_utc": ledger.get("updated_at_utc"),
        "quotes_missing_last_run": _integer(
            _as_dict(ledger.get("last_mark_summary")).get("quotes_missing")
        ),
    }


def _worst_status(gates: list[dict[str, Any]]) -> str:
    return max(gates, key=lambda gate: SEVERITY_ORDER[gate["status"]])["status"]


def build_research_readiness(
    *,
    snapshot: dict[str, Any],
    prospective_ledger: dict[str, Any],
    moonshot_ledger: dict[str, Any],
    research_audit: dict[str, Any],
    event_coverage: dict[str, Any],
    promotion_comparison: dict[str, Any],
    operational_health: dict[str, Any] | None = None,
    source_paths: dict[str, Path] | None = None,
    now_utc: datetime | None = None,
    max_snapshot_age_minutes: int = 240,
    max_evidence_age_minutes: int = 24 * 60,
    amber_completion_pct: float = 0.95,
    red_completion_pct: float = 0.80,
    min_executable_quote_coverage_pct: float = 0.95,
    min_complete_event_coverage_pct: float = 0.50,
) -> dict[str, Any]:
    """Build a fail-closed scientific-readiness view of Orographic artifacts.

    This deliberately does not answer whether a scheduled scan ran. It answers
    whether the current evidence is safe to use for performance or promotion
    claims. Operational scan health and research readiness are separate states.
    """

    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    paths = source_paths or {}
    gates: list[dict[str, Any]] = []

    snapshot_age = _age_minutes(snapshot.get("generated_at_utc"), now)
    prospective_age = _age_minutes(prospective_ledger.get("updated_at_utc"), now)
    moonshot_age = _age_minutes(moonshot_ledger.get("updated_at_utc"), now)
    stale_evidence = [
        name
        for name, age in (("prospective", prospective_age), ("moonshot", moonshot_age))
        if age is None or age > max_evidence_age_minutes
    ]
    freshness_severity = (
        "red"
        if snapshot_age is None or snapshot_age > max_snapshot_age_minutes
        else "amber"
        if stale_evidence
        else "green"
    )
    gates.append(
        _gate(
            "freshness",
            freshness_severity,
            "Artifact freshness",
            "The current snapshot is stale or missing."
            if freshness_severity == "red"
            else "One or more evidence ledgers are stale."
            if freshness_severity == "amber"
            else "Snapshot and evidence ledgers are within their freshness windows.",
            metrics={
                "snapshot_age_minutes": snapshot_age,
                "snapshot_max_age_minutes": max_snapshot_age_minutes,
                "prospective_ledger_age_minutes": prospective_age,
                "moonshot_ledger_age_minutes": moonshot_age,
                "evidence_max_age_minutes": max_evidence_age_minutes,
                "stale_evidence_sources": stale_evidence,
            },
            actions=("Run and publish a fresh scan before using current-state claims.",)
            if freshness_severity != "green"
            else (),
        )
    )

    core = _ledger_metrics(prospective_ledger)
    moonshot = _ledger_metrics(moonshot_ledger)
    total_picks = core["picks"] + moonshot["picks"]
    total_marked = core["with_any_mark"] + moonshot["with_any_mark"]
    total_complete = core["complete"] + moonshot["complete"]
    observed_coverage = _ratio(total_marked, total_picks)
    cohort_completion = _ratio(total_complete, total_picks)
    completion_value = cohort_completion or 0.0
    completion_severity = (
        "red"
        if completion_value < red_completion_pct
        else "amber"
        if completion_value < amber_completion_pct
        else "green"
    )
    gates.append(
        _gate(
            "label_cohort_completion",
            completion_severity,
            "Outcome cohort completion",
            "Too few emitted recommendations have complete fixed-horizon labels."
            if completion_severity == "red"
            else "The cohort is usable only with an explicit incomplete-data caveat."
            if completion_severity == "amber"
            else "The recommendation cohort meets the completion threshold.",
            metrics={
                "total_picks": total_picks,
                "with_any_mark": total_marked,
                "fully_labeled": total_complete,
                "observed_mark_coverage_pct": observed_coverage,
                "total_cohort_completion_pct": cohort_completion,
                "amber_completion_threshold_pct": amber_completion_pct,
                "red_completion_threshold_pct": red_completion_pct,
                "by_lane_family": {"core": core, "moonshot": moonshot},
            },
            actions=(
                "Backfill due fixed-horizon marks and publish matched cohort cutoffs.",
                "Do not describe observed-mark coverage as total cohort completion.",
            )
            if completion_severity != "green"
            else (),
        )
    )

    executable_rows = core["executable_quote_rows"] + moonshot["executable_quote_rows"]
    friday_rows = core["friday_outcome_rows"] + moonshot["friday_outcome_rows"]
    executable_coverage = _ratio(executable_rows, friday_rows)
    executable_severity = (
        "red"
        if executable_coverage is None
        else "amber"
        if executable_coverage < min_executable_quote_coverage_pct
        else "green"
    )
    gates.append(
        _gate(
            "executable_quote_coverage",
            executable_severity,
            "Executable quote coverage",
            "No Friday outcome cohort has executable entry-ask and exit-bid quotes."
            if executable_coverage is None
            else "Executable quote coverage is below the research threshold."
            if executable_severity == "amber"
            else "Friday outcomes have sufficient ask-to-bid quote coverage.",
            metrics={
                "executable_quote_rows": executable_rows,
                "friday_outcome_rows": friday_rows,
                "executable_quote_coverage_pct": executable_coverage,
                "required_min_pct": min_executable_quote_coverage_pct,
                "return_contract": "entry ask to exit bid; costs/slippage reported separately",
            },
            actions=("Do not use midpoint-only outcomes for promotion or performance claims.",)
            if executable_severity != "green"
            else (),
        )
    )

    event_summary = _as_dict(event_coverage.get("summary"))
    audit_summary = _as_dict(research_audit.get("summary"))
    operational_audit_summary = _as_dict(
        _as_dict((operational_health or {}).get("research")).get("audit_summary")
    )
    feed_summary = {**operational_audit_summary, **audit_summary}
    event_feed_status = str(feed_summary.get("event_feed_status") or "unknown").lower()
    complete_event_coverage = _number(
        event_summary.get("complete_outcome_event_coverage_pct")
        if event_summary
        else feed_summary.get("complete_outcome_event_coverage_pct")
    )
    rate_limited = event_feed_status == "rate_limited" or _integer(
        feed_summary.get("event_feed_http_429_responses")
    ) > 0
    if complete_event_coverage is None or complete_event_coverage < min_complete_event_coverage_pct:
        event_severity = "red"
    elif rate_limited or event_feed_status not in {"healthy", "success", "passed"}:
        event_severity = "amber"
    else:
        event_severity = "green"
    gates.append(
        _gate(
            "event_feed_health",
            event_severity,
            "Event data health",
            "Complete outcomes lack sufficient point-in-time event coverage."
            if event_severity == "red"
            else "Event coverage exists, but feed delivery is degraded."
            if event_severity == "amber"
            else "Event feed and complete-outcome coverage meet thresholds.",
            metrics={
                "feed_status": event_feed_status,
                "http_429_responses": _integer(feed_summary.get("event_feed_http_429_responses")),
                "new_rows": _integer(feed_summary.get("event_feed_new_rows")),
                "complete_outcome_event_coverage_pct": complete_event_coverage,
                "required_min_pct": min_complete_event_coverage_pct,
            },
            actions=("Repair/backfill the point-in-time event feed before event-model claims.",)
            if event_severity != "green"
            else (),
        )
    )

    missing_sources = [name for name, path in paths.items() if not path.exists()]
    audit_checks = _as_list(research_audit.get("checks"))
    failed_audit_checks = [
        str(_as_dict(check).get("name") or "unnamed")
        for check in audit_checks
        if _as_dict(check).get("passed") is False
    ]
    required_models = _as_dict(snapshot.get("model_artifacts"))
    missing_required_models = [
        name
        for name, metadata in required_models.items()
        if _as_dict(metadata).get("required") is True and _as_dict(metadata).get("present") is not True
    ]
    integrity_failed = (
        bool(missing_sources)
        or research_audit.get("status") != "passed"
        or bool(failed_audit_checks)
        or bool(missing_required_models)
    )
    gates.append(
        _gate(
            "artifact_integrity",
            "red" if integrity_failed else "green",
            "Artifact integrity",
            "Required artifacts, model files, or consistency checks are missing/failed."
            if integrity_failed
            else "Required research artifacts and model files pass integrity checks.",
            metrics={
                "audit_status": research_audit.get("status"),
                "failed_audit_checks": failed_audit_checks,
                "missing_required_models": missing_required_models,
                "missing_sources": missing_sources,
            },
            actions=("Repair missing artifacts and rerun the research-data audit.",)
            if integrity_failed
            else (),
        )
    )

    promotion_decision = str(promotion_comparison.get("decision") or "unknown").lower()
    windows = [_as_dict(window) for window in _as_list(promotion_comparison.get("windows"))]
    incomplete_windows = [
        str(window.get("window") or "unknown")
        for window in windows
        if window.get("coverage_complete") is not True
    ]
    failed_risk_windows = [
        str(window.get("window") or "unknown")
        for window in windows
        if _as_dict(window.get("checks")).get("sharpe_non_worse") is False
        or _as_dict(window.get("checks")).get("drawdown_non_worse") is False
    ]
    active_modes = [
        name
        for name, mode in _as_dict(snapshot.get("model_modes")).items()
        if str(mode).lower() in {"active", "artifact"}
    ]
    eligible = promotion_decision in {"promote", "passed", "eligible"} and not incomplete_windows
    promotion_severity = "green" if eligible else "red" if active_modes else "amber"
    gates.append(
        _gate(
            "promotion_eligibility",
            promotion_severity,
            "Promotion eligibility",
            "Production-active models are not supported by the latest promotion comparison."
            if promotion_severity == "red"
            else "Promotion evidence is not yet complete."
            if promotion_severity == "amber"
            else "The latest comparison supports promotion eligibility.",
            metrics={
                "decision": promotion_decision,
                "as_of_utc": promotion_comparison.get("as_of_utc"),
                "coverage_complete": not incomplete_windows,
                "incomplete_windows": incomplete_windows,
                "failed_risk_windows": failed_risk_windows,
                "active_model_modes": active_modes,
            },
            actions=("Hold promotion or revert to canary/shadow until canonical gates pass.",)
            if promotion_severity != "green"
            else (),
        )
    )

    status = _worst_status(gates)
    blocking = [gate["code"] for gate in gates if gate["blocking"]]
    warnings = [gate["code"] for gate in gates if gate["status"] == "amber"]
    return {
        "artifact": "research_readiness_health",
        "schema_version": 1,
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "status": status,
        "research_claims_allowed": status == "green",
        "promotion_allowed": next(
            gate["status"] == "green" for gate in gates if gate["code"] == "promotion_eligibility"
        ),
        "headline": (
            "Research evidence is blocked; resolve red gates before performance or promotion claims."
            if status == "red"
            else "Research evidence is provisional; disclose amber caveats."
            if status == "amber"
            else "Research evidence meets configured readiness thresholds."
        ),
        "summary": {
            "green_gates": sum(gate["status"] == "green" for gate in gates),
            "amber_gates": sum(gate["status"] == "amber" for gate in gates),
            "red_gates": sum(gate["status"] == "red" for gate in gates),
            "blocking_gates": blocking,
            "warning_gates": warnings,
            "observed_mark_coverage_pct": observed_coverage,
            "total_cohort_completion_pct": cohort_completion,
            "executable_quote_coverage_pct": executable_coverage,
        },
        "gates": gates,
        "ui_contract": {
            "status_values": ["green", "amber", "red"],
            "primary_fields": [
                "status",
                "headline",
                "research_claims_allowed",
                "promotion_allowed",
                "summary",
            ],
            "gate_fields": ["code", "status", "blocking", "title", "summary", "metrics", "actions"],
            "display_rule": "Never collapse amber/red gates into an operational scan-health pass.",
        },
    }
