"""Fail-closed readiness contract for the execution/model rebuild."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _gate(passed: bool, *, actual: object, required: object) -> dict[str, Any]:
    return {"passed": bool(passed), "actual": actual, "required": required}


def build_rebuild_readiness(
    pair_readiness: dict[str, Any],
    exit_shadow: dict[str, Any],
    promotion_comparison: dict[str, Any],
    scan_health: dict[str, Any],
) -> dict[str, Any]:
    pair_coverage = pair_readiness.get("coverage") if isinstance(pair_readiness.get("coverage"), dict) else {}
    fold_plan = pair_readiness.get("fold_frozen_evaluation_plan") if isinstance(pair_readiness.get("fold_frozen_evaluation_plan"), dict) else {}
    exit_summary = exit_shadow.get("summary") if isinstance(exit_shadow.get("summary"), dict) else {}
    live_policies = exit_summary.get("live_by_policy") if isinstance(exit_summary.get("live_by_policy"), dict) else {}
    best_live_coverage = max(
        (float(row.get("coverage_pct") or 0.0) for row in live_policies.values() if isinstance(row, dict)),
        default=0.0,
    )
    lifecycle = (
        scan_health.get("research", {}).get("evidence_lifecycle", {})
        if isinstance(scan_health.get("research"), dict)
        else {}
    )
    cohort = lifecycle.get("current_model_cohort") if isinstance(lifecycle.get("current_model_cohort"), dict) else {}
    resolved_current = int(cohort.get("resolved_recommendations") or 0)
    complete_pairs = int(pair_coverage.get("complete_explicit_pairs") or 0)
    ready_folds = int(fold_plan.get("ready_folds") or 0)
    gates = {
        "matched_side_pairs": _gate(complete_pairs >= 150, actual=complete_pairs, required=150),
        "fold_frozen_validation": _gate(ready_folds >= 3, actual=ready_folds, required=3),
        "current_model_resolutions": _gate(resolved_current >= 100, actual=resolved_current, required=100),
        "exit_policy_coverage": _gate(best_live_coverage >= 0.60, actual=round(best_live_coverage, 4), required=0.60),
        "baseline_promotion_ready": _gate(
            str(promotion_comparison.get("decision") or "") == "ready",
            actual=promotion_comparison.get("decision"),
            required="ready",
        ),
    }
    ready = all(row["passed"] for row in gates.values())
    return {
        "artifact": "orographic_rebuild_readiness",
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "ready_for_fold_frozen_challenger" if ready else "hold_collecting_executable_evidence",
        "production_model_change_allowed": ready,
        "preferred_training_source": {
            "interface": "shared research mart",
            "tables": ["recommendations", "recommendation_outcomes", "option_quotes", "features"],
            "fallback": "canonical executable outcome artifacts",
            "required_semantics": [
                "entry ask or actual fill",
                "exit bid or actual fill",
                "label availability timestamp",
                "recommendation-time feature availability",
                "side, regime, and contract liquidity fields",
            ],
        },
        "gates": gates,
        "next_action": (
            "Train a fold-frozen observation-only challenger."
            if ready
            else "Keep production frozen; collect matched-side and executable exit evidence."
        ),
    }
