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
    mart_consumer: dict[str, Any] | None = None,
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
    mart_consumer = mart_consumer if isinstance(mart_consumer, dict) else {}
    consumer_views = mart_consumer.get("views") if isinstance(mart_consumer.get("views"), dict) else {}
    required_consumer_views = {
        "orographic_training_v1",
        "orographic_execution_quality_v1",
        "orographic_exit_replay_v1",
        "cirrus_orographic_disagreement_v1",
        "orographic_model_monitoring_v1",
    }
    mart_consumer_ready = (
        mart_consumer.get("artifact") == "orographic_shared_mart_consumer_bundle"
        and mart_consumer.get("status") == "ready"
        and mart_consumer.get("production_authority") == "observation_only_never_used_for_routing"
        and required_consumer_views.issubset(consumer_views)
        and set(mart_consumer.get("source_systems") or []) == {"cirrus", "orographic"}
    )
    gates = {
        "shared_mart_consumer": _gate(
            mart_consumer_ready,
            actual="ready" if mart_consumer_ready else "missing_or_invalid",
            required="ready_observation_only",
        ),
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
            "tables": ["recommendations", "execution_outcomes", "option_quotes", "feature_snapshots"],
            "consumer_view": "orographic_training_v1",
            "fallback": "canonical executable outcome artifacts",
            "required_semantics": [
                "entry ask or actual fill",
                "exit bid or actual fill",
                "label availability timestamp",
                "recommendation-time feature availability",
                "side, regime, and contract liquidity fields",
            ],
        },
        "rollout": {
            "p0_consumer_contract": "ready" if mart_consumer_ready else "blocked",
            "p1_research_views": {
                name: int((consumer_views.get(name) or {}).get("rows") or 0)
                for name in sorted(required_consumer_views)
            },
            "p2_shadow_authority": mart_consumer.get("production_authority") or "not_available",
            "p3_promotion": "eligible" if ready else "blocked_by_gates",
        },
        "gates": gates,
        "next_action": (
            "Train a fold-frozen observation-only challenger."
            if ready
            else (
                "Build and validate the shared mart consumer bundle."
                if not mart_consumer_ready
                else "Keep production frozen; collect matched-side and executable exit evidence."
            )
        ),
    }
