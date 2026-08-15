"""Build a compact, UI-safe view of Orographic model governance.

The output deliberately separates research readiness from execution authority.
No value in this artifact can activate a model or route a Tradier order.
"""
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


def _float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _generated_at(value: dict[str, Any]) -> datetime | None:
    raw = value.get("generated_at_utc")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _progress(current: int, required: int, label: str) -> dict[str, Any]:
    safe_required = max(required, 1)
    return {
        "current": current,
        "required": required,
        "remaining": max(required - current, 0),
        "progress_pct": round(min(current / safe_required, 1.0), 4),
        "label": label,
    }


def _failed_gate_names(gates: dict[str, Any], *, limit: int = 4) -> list[str]:
    failed: list[str] = []
    for name, raw in gates.items():
        gate = _dict(raw)
        if gate.get("passed") is False:
            failed.append(str(name).replace("_", " "))
    return failed[:limit]


def build_model_governance_summary(
    *,
    scan_health: dict[str, Any],
    scout_card: dict[str, Any],
    payoff_card: dict[str, Any],
    payoff_evidence: dict[str, Any],
    veto_evidence: dict[str, Any],
    path_evidence: dict[str, Any],
    scout_pair_readiness: dict[str, Any] | None = None,
    payoff_stack_audit: dict[str, Any] | None = None,
    capture_health: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    active_capture_health = _dict(capture_health)
    capture_generated = _generated_at(active_capture_health)
    scan_generated = _generated_at(scan_health)
    use_capture_health = bool(active_capture_health) and (
        scan_generated is None
        or (capture_generated is not None and capture_generated >= scan_generated)
    )
    health_source = active_capture_health if use_capture_health else scan_health
    labels = _dict(health_source.get("labels"))
    health_checks = {
        str(row.get("name")): row
        for row in health_source.get("checks", [])
        if isinstance(row, dict)
    }
    trajectory_check = _dict(health_checks.get("trajectory_capture_health"))
    active_paths = _int(labels.get("trajectory_active_picks_last_run"))
    written_paths = _int(labels.get("trajectory_marks_written_last_run"))
    path_marks = _int(labels.get("trajectory_marks"))
    path_contracts = _int(labels.get("trajectory_scored_picks"))
    if not trajectory_check:
        capture_status = "hold"
        capture_headline = "Awaiting the first scheduled trajectory-health report."
    elif trajectory_check.get("passed"):
        capture_status = "pass"
        capture_headline = (
            "No contracts are currently due for path capture."
            if active_paths == 0
            else f"Captured fresh path marks for {written_paths} active contracts."
        )
    else:
        capture_status = "fail"
        capture_headline = "Active contracts are missing fresh, timestamp-valid path evidence."

    pair_readiness = _dict(scout_pair_readiness)
    pair_coverage = _dict(pair_readiness.get("coverage"))
    pair_label_counts = _dict(pair_coverage.get("label_counts"))
    scout_counts = _dict(scout_card.get("paired_direction_counts"))
    scout_call = _int(
        pair_label_counts.get("call_edge")
        if pair_readiness
        else scout_counts.get("call")
    )
    scout_put = _int(
        pair_label_counts.get("put_edge")
        if pair_readiness
        else scout_counts.get("put")
    )
    scout_pairs = (
        _int(pair_coverage.get("complete_explicit_pairs"))
        if pair_readiness
        else _int(scout_card.get("rows"))
    )
    scout_gates = _dict(
        pair_readiness.get("promotion_gates") or scout_card.get("promotion_gates")
    )
    fold_plan = _dict(pair_readiness.get("fold_frozen_evaluation_plan"))
    scout_status = "hold" if pair_readiness else str(scout_card.get("status") or "hold")
    scout_progress = (
        _progress(scout_pairs, 150, "complete matched call/put pairs")
        if pair_readiness
        else _progress(min(scout_call, 50) + min(scout_put, 50), 100, "paired call + put rows")
    )
    scout = {
        "id": "scout",
        "title": "Scout abstention + direction",
        "layer": "Signal selection",
        "status": scout_status,
        "authority": "observation_only",
        "execution_effect": "none",
        "summary": "Two-stage challenger learns trade-versus-abstain before call-versus-put.",
        "progress": scout_progress,
        "metrics": [
            {
                "label": "Complete matched pairs" if pair_readiness else "Independent rows",
                "value": scout_pairs,
                "format": "integer",
            },
            {"label": "Trade AUC", "value": _float(_dict(scout_card.get("cross_validation")).get("trade_auc")), "format": "decimal"},
            {"label": "Call-edge / put-edge", "value": f"{scout_call} / {scout_put}", "format": "text"},
            {"label": "Ready frozen folds", "value": _int(fold_plan.get("ready_folds")), "format": "integer"},
        ],
        "blockers": _failed_gate_names(scout_gates),
        "next_action": str(
            pair_readiness.get("next_action")
            or "Collect paired, strict after-cost call and put outcomes before selecting direction thresholds."
        ),
    }

    payoff_coverage = _dict(payoff_evidence.get("coverage"))
    payoff_resolved = _int(payoff_coverage.get("resolved_recommendations"))
    payoff_gates = _dict(_dict(payoff_card.get("promotion_gates")).get("gates"))
    payoff_cv = _dict(payoff_card.get("cross_validation"))
    payoff_audit = _dict(payoff_stack_audit)
    payoff_audit_coverage = _dict(payoff_audit.get("coverage"))
    payoff_audit_gates = _dict(payoff_audit.get("sample_gates"))
    retrained_variant = _dict(_dict(payoff_audit.get("variants")).get("fold_frozen_retrained"))
    retrained_lift = _dict(retrained_variant.get("paired_lift_vs_zero_cost_aware"))
    payoff = {
        "id": "payoff",
        "title": "Cost-aware payoff ranker",
        "layer": "Contract ranking",
        "status": str(payoff_evidence.get("decision") or _dict(payoff_card.get("promotion_gates")).get("status") or "hold"),
        "authority": "observation_only",
        "execution_effect": "none",
        "summary": "Predicts after-cost return quantiles, breakeven probability, and fill quality.",
        "progress": _progress(payoff_resolved, 100, "resolved prospective recommendations"),
        "metrics": [
            {"label": "Training examples", "value": _int(payoff_card.get("training_examples")), "format": "integer"},
            {"label": "Positive-P&L AUC", "value": _float(payoff_cv.get("positive_pnl_auc_mean")), "format": "decimal"},
            {"label": "Exact paths", "value": _int(_dict(payoff_card.get("coverage")).get("examples_with_exact_quote_path")), "format": "integer"},
            {"label": "Frozen audit dates", "value": _int(payoff_audit_coverage.get("evaluated_validation_dates")), "format": "integer"},
            {"label": "Retrained lift", "value": _float(retrained_lift.get("mean_lift")), "format": "decimal"},
        ],
        "blockers": _failed_gate_names(payoff_audit_gates or payoff_gates),
        "next_action": str(
            payoff_audit.get("next_action")
            or _dict(payoff_card.get("promotion_gates")).get("required_next_step")
            or "Accumulate prospective disagreements and require positive after-cost paired lift."
        ),
    }

    veto_coverage = _dict(veto_evidence.get("coverage"))
    veto_readiness = _dict(veto_evidence.get("readiness"))
    veto_progress = _dict(_dict(veto_readiness.get("progress")).get("resolved_current_rule_vetoes"))
    veto_current = _int(veto_progress.get("current") or veto_coverage.get("resolved_current_rule_vetoes"))
    veto_required = _int(veto_progress.get("required")) or 100
    veto = {
        "id": "veto",
        "title": "Counterfactual no-trade veto",
        "layer": "Risk filtering",
        "status": str(veto_evidence.get("decision") or "hold"),
        "authority": "advisory_only",
        "execution_effect": "none",
        "summary": "Measures whether Scout's no-trade preference avoids losses without suppressing profitable trades.",
        "progress": _progress(veto_current, veto_required, "resolved independent vetoes"),
        "metrics": [
            {"label": "Independent scored", "value": _int(veto_coverage.get("independent_recommendations")), "format": "integer"},
            {"label": "Resolved vetoes", "value": veto_current, "format": "integer"},
            {"label": "Trading days", "value": _int(veto_coverage.get("independent_veto_trading_days")), "format": "integer"},
        ],
        "blockers": [str(value).replace("_", " ") for value in veto_readiness.get("blocking_sample_gates", [])[:4]],
        "next_action": str(veto_readiness.get("next_action") or "Capture strict Friday-close veto outcomes."),
    }

    path_quality = _dict(path_evidence.get("data_quality"))
    path_gates = _dict(path_evidence.get("promotion_gates"))
    exact_paths = _int(_dict(_dict(path_evidence.get("evaluation")).get("exact_path_records")).get("actual"))
    if not exact_paths:
        exact_paths = _int(_dict(path_gates.get("minimum_exact_paths")).get("actual"))
    required_paths = _int(_dict(path_gates.get("minimum_exact_paths")).get("required_min")) or 150
    exit_model = {
        "id": "exit",
        "title": "Competing-risk exit model",
        "layer": "Exit advice",
        "status": str(path_evidence.get("status") or "hold"),
        "authority": "advice_only",
        "execution_effect": "none",
        "summary": "Separately estimates target, stop, and expiry hazards from timestamp-valid option paths.",
        "progress": _progress(exact_paths, required_paths, "exact pre-exit paths"),
        "metrics": [
            {"label": "Valid path records", "value": exact_paths, "format": "integer"},
            {"label": "Target / stop events", "value": f"{_int(_dict(path_quality.get('event_counts')).get('target'))} / {_int(_dict(path_quality.get('event_counts')).get('stop'))}", "format": "text"},
            {"label": "Rejected post-exit marks", "value": _int(path_quality.get("invalid_post_exit_marks")), "format": "integer"},
        ],
        "blockers": _failed_gate_names(path_gates),
        "next_action": "Collect dense 15-minute Tradier marks until target and stop hazards are estimable without leakage.",
    }

    challengers = [scout, payoff, veto, exit_model]
    all_ready = all(item["status"] == "pass" for item in challengers)
    overall_status = "fail" if capture_status == "fail" else "pass" if all_ready else "hold"
    generated = (now_utc or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "artifact": "model_governance_summary",
        "schema_version": 1,
        "generated_at_utc": generated,
        "status": overall_status,
        "headline": (
            "Research data capture needs attention; challenger authority remains disabled."
            if overall_status == "fail"
            else "All challenger promotion gates passed."
            if overall_status == "pass"
            else "Production policy is unchanged while challengers collect binding evidence."
        ),
        "live_authority": {
            "policy": "active_production_models_only",
            "challenger_order_routing": False,
            "challenger_sizing_effect": False,
            "challenger_council_eligibility": False,
            "summary": "No research challenger can size, select, or transmit a Tradier order.",
        },
        "data_capture": {
            "status": capture_status,
            "headline": capture_headline,
            "active_contracts_last_run": active_paths,
            "marks_written_last_run": written_paths,
            "trajectory_contracts": path_contracts,
            "trajectory_marks": path_marks,
            "missing_quotes_last_run": _int(labels.get("trajectory_quotes_missing_last_run")),
            "stale_quotes_last_run": _int(labels.get("trajectory_quotes_stale_last_run")),
        },
        "challengers": challengers,
        "summary": {
            "challengers": len(challengers),
            "ready": sum(item["status"] == "pass" for item in challengers),
            "held": sum(item["status"] != "pass" for item in challengers),
            "observation_only": len(challengers),
        },
    }
