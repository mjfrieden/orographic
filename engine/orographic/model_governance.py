"""Build the production-v2 governance view consumed by the cockpit."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _generated_at(value: dict[str, Any]) -> datetime | None:
    raw = value.get("generated_at_utc")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def build_model_governance_summary(
    *,
    scan_health: dict[str, Any],
    scout_card: dict[str, Any] | None = None,
    payoff_card: dict[str, Any] | None = None,
    payoff_evidence: dict[str, Any] | None = None,
    veto_evidence: dict[str, Any] | None = None,
    path_evidence: dict[str, Any] | None = None,
    scout_pair_readiness: dict[str, Any] | None = None,
    payoff_stack_audit: dict[str, Any] | None = None,
    capture_health: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return one production model, one authority path, and capture health.

    Retired arguments remain accepted so older automation invocations do not
    fail during rollout. Their contents never appear as cockpit lanes.
    """
    del scout_card, payoff_card, payoff_evidence, veto_evidence, path_evidence
    del scout_pair_readiness, payoff_stack_audit

    capture = _dict(capture_health)
    scan_generated = _generated_at(scan_health)
    capture_generated = _generated_at(capture)
    health = capture if capture and (scan_generated is None or (capture_generated and capture_generated >= scan_generated)) else scan_health
    labels = _dict(health.get("labels"))
    checks = {
        str(row.get("name")): row
        for row in health.get("checks", [])
        if isinstance(row, dict)
    }
    trajectory = _dict(checks.get("trajectory_capture_health"))
    active = _int(labels.get("trajectory_active_picks_last_run"))
    written = _int(labels.get("trajectory_marks_written_last_run"))
    if not trajectory:
        capture_status = "hold"
        capture_headline = "Awaiting scheduled trajectory health."
    elif trajectory.get("passed"):
        capture_status = "pass"
        capture_headline = (
            "No contracts are currently due for path capture."
            if active == 0
            else f"Captured fresh path marks for {written} active contracts."
        )
    else:
        capture_status = "fail"
        capture_headline = "Active contracts are missing fresh path evidence."

    research = _dict(scan_health.get("research"))
    generated = (now_utc or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "artifact": "model_governance_summary",
        "schema_version": 2,
        "generated_at_utc": generated,
        "status": "fail" if capture_status == "fail" else "pass",
        "headline": "The active tail-utility ranker is the only scoring and execution path.",
        "production_model": {
            "profile": "production_v2",
            "scout": "direction plus trade/abstain and side decision",
            "ranker": "four-class after-friction tail-utility rank",
            "score_weights": {
                "expected_tail_utility_rank": 0.70,
                "execution_quality": 0.20,
                "normalized_tail_utility": 0.10,
            },
            "objective": "large_after_friction_return_with_explicit_abstention",
            "research_risk_posture": "active",
            "probability_sizing": False,
            "legacy_model_authority": False,
        },
        "live_authority": {
            "policy": "production_v2_council_only",
            "summary": "Only Council's production board can size, select, or transmit a Tradier order.",
        },
        "data_capture": {
            "status": capture_status,
            "headline": capture_headline,
            "active_contracts_last_run": active,
            "marks_written_last_run": written,
            "trajectory_contracts": _int(labels.get("trajectory_scored_picks")),
            "trajectory_marks": _int(labels.get("trajectory_marks")),
            "missing_quotes_last_run": _int(labels.get("trajectory_quotes_missing_last_run")),
            "stale_quotes_last_run": _int(labels.get("trajectory_quotes_stale_last_run")),
            "canonical_bundle_id": research.get("canonical_bundle_id"),
        },
        "summary": {"production_models": 1, "experiment_lanes": 0},
    }
