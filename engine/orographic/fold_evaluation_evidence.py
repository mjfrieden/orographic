"""Verify local, content-addressed Scout fold completion receipts.

Completion is not evidence of model edge. The independent promotion gates still
apply. Never load/pickle/execute a model while checking its identity.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing_timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result


def _artifact(root: Path, reference: object) -> bytes:
    if not isinstance(reference, dict) or not _sha(reference.get("sha256")):
        raise ValueError("missing_artifact_identity")
    relative = reference.get("path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("invalid_artifact_path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("artifact_outside_bundle")
    raw = path.read_bytes()
    if not raw or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("artifact_hash_mismatch")
    return raw


def verify_fold_evaluation(
    pair_readiness: dict[str, Any],
    manifest_path: Path | None,
    expected_sha256: str | None,
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed on absent, malformed, stale, or unresolvable evidence.

    Freshness is identity-based: a changed pair corpus or fold split invalidates
    earlier receipts, even if their timestamps are recent. A regenerated plan
    with the same corpus/splits does not invalidate an immutable completed run.
    """
    result: dict[str, Any] = {"completed_folds": 0, "status": "missing_or_invalid", "reason": "missing_evaluation"}
    try:
        if manifest_path is None or not _sha(expected_sha256):
            return result
        path = Path(manifest_path)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("manifest_hash_mismatch")
        report = json.loads(raw)
        if (report.get("artifact") != "scout_fold_frozen_evaluation"
                or type(report.get("schema_version")) is not int or report["schema_version"] not in (1, 2)
                or report.get("status") != "completed"
                or report.get("execution_effect") != "none_research_only"
                or report.get("active_model_change_allowed") is not False):
            raise ValueError("invalid_evaluation_contract")
        corpus = pair_readiness.get("coverage", {}).get("evidence_sha256")
        if not _sha(corpus) or report.get("source_evidence_sha256") != corpus:
            raise ValueError("source_evidence_mismatch")
        if (report.get("label_policy") != "strict_executable_quote_or_fill_v2"
                or report.get("label_policy") != pair_readiness.get("source", {}).get("label_policy")):
            raise ValueError("label_policy_mismatch")
        for key in ("run_id", "candidate_id"):
            if not isinstance(report.get(key), str) or not report[key].strip():
                raise ValueError("missing_run_or_candidate_identity")
        if not isinstance(report.get("evaluator_revision"), str) or not re.fullmatch(r"[0-9a-f]{40}", report["evaluator_revision"]):
            raise ValueError("missing_evaluator_revision")
        completed = _timestamp(report.get("completed_at_utc"))
        now = now_utc or datetime.now(timezone.utc)
        if completed > now:
            raise ValueError("future_completion")
        planned = pair_readiness.get("fold_frozen_evaluation_plan", {}).get("folds")
        evidence_kind = report.get("evidence_kind", "unclassified")
        expected_ids = {}
        if report["schema_version"] == 2:
            from .scout_pair_readiness import _pair_records, _record_hash, _fold_plan
            from .validation import purged_date_splits
            inputs = json.loads(_artifact(path.parent, report.get("input_bundle")))
            sources = report.get("evaluator_sources")
            if not isinstance(sources, list) or len(sources) != 4:
                raise ValueError("missing_evaluator_source_snapshots")
            for reference in sources:
                _artifact(path.parent, reference)
            if (inputs.get("artifact") != "scout_archived_pair_inputs"
                    or inputs.get("schema_version") != 1
                    or report.get("comparison_policy") != "scout_three_class_argmax_strict_executable_returns_v1"
                    or evidence_kind not in {"real", "synthetic"} or inputs.get("evidence_kind") != evidence_kind
                    or inputs.get("comparison_policy") != report.get("comparison_policy")
                    or inputs.get("baseline_policy_id") != report.get("baseline_policy_id")
                    or inputs.get("baseline_artifact_sha256") != report.get("baseline_artifact_sha256")):
                raise ValueError("input_provenance_mismatch")
            records, _ = _pair_records(inputs["outcomes"]["rows"])
            if _record_hash(records) != corpus or _fold_plan(records) != pair_readiness["fold_frozen_evaluation_plan"]:
                raise ValueError("archived_pair_plan_mismatch")
            for p, (_, indices) in zip(planned, purged_date_splits(
                    [r["decision_date"] for r in records], [r["label_available_date"] for r in records], n_splits=5), strict=True):
                expected_ids[p["fold"]] = [records[i]["pair_id"] for i in indices]
        folds = report.get("folds")
        if not isinstance(planned, list) or not isinstance(folds, list) or not folds:
            raise ValueError("missing_folds")
        plans = {}
        for plan in planned:
            number = plan.get("fold")
            if type(number) is not int or number in plans:
                raise ValueError("invalid_plan_fold_identity")
            plans[number] = plan
        seen: set[int] = set()
        bundles: set[str] = set()
        for fold in folds:
            number = fold.get("fold")
            if type(number) is not int or number in seen or number not in plans:
                raise ValueError("duplicate_or_unknown_fold")
            plan = plans[number]
            if fold.get("status") != "completed" or plan.get("ready") is not True:
                raise ValueError("fold_not_completed_or_planned_ready")
            for key in ("training_evidence_sha256", "validation_evidence_sha256"):
                if not _sha(plan.get(key)) or fold.get(key) != plan[key]:
                    raise ValueError("fold_evidence_mismatch")
            for key in ("training_labels_available_through", "validation_start"):
                if fold.get(key) != plan.get(key):
                    raise ValueError("fold_dates_mismatch")
            # Parse dates rather than trusting lexicographic strings.
            if date.fromisoformat(plan["training_labels_available_through"]) >= date.fromisoformat(plan["validation_start"]):
                raise ValueError("training_label_leakage")
            frozen = _timestamp(fold.get("frozen_at_utc"))
            scored = _timestamp(fold.get("scoring_started_at_utc"))
            finished = _timestamp(fold.get("completed_at_utc"))
            if (not frozen <= scored <= finished <= completed
                    or scored.date() < date.fromisoformat(plan["validation_start"])):
                raise ValueError("invalid_completion_order")
            _artifact(path.parent, fold.get("model_bundle"))
            model_hash = fold["model_bundle"]["sha256"]
            if model_hash in bundles:
                raise ValueError("reused_fold_model_bundle")
            scores = json.loads(_artifact(path.parent, fold.get("scores")))
            expected = {
                "artifact": "scout_fold_validation_scores", "status": "completed",
                "fold": number, "run_id": report["run_id"], "candidate_id": report["candidate_id"],
                "evaluator_revision": report["evaluator_revision"],
                "model_bundle_sha256": model_hash,
                "training_evidence_sha256": plan["training_evidence_sha256"],
                "validation_evidence_sha256": plan["validation_evidence_sha256"],
            }
            if any(scores.get(key) != value for key, value in expected.items()):
                raise ValueError("score_provenance_mismatch")
            count = scores.get("evaluated_pairs")
            if type(count) is not int or count <= 0 or count != plan.get("validation_pairs"):
                raise ValueError("incomplete_validation_scores")
            if report["schema_version"] == 2:
                import numpy as np
                predictions = scores.get("predictions")
                if (not isinstance(predictions, list)
                        or [r.get("pair_id") for r in predictions] != expected_ids[number]):
                    raise ValueError("prediction_pair_membership_mismatch")
                for row in predictions:
                    if row.get("action") not in {"call", "put", "no_trade"}:
                        raise ValueError("invalid_prediction_action")
                    for key in ("after_cost_return", "baseline_after_cost_return"):
                        value = row.get(key)
                        if type(value) not in (int, float) or not math.isfinite(value):
                            raise ValueError("invalid_prediction_return")
                pair_by_id = {r["pair_id"]: r for r in records}
                input_by_id = {r["pair_id"]: r for r in inputs["rows"]}
                daily = {}
                for row in predictions:
                    pair = pair_by_id[row["pair_id"]]
                    archived = input_by_id[row["pair_id"]]
                    if row["decision_date"] != pair["decision_date"]:
                        raise ValueError("prediction_date_mismatch")
                    for action, key in ((row["action"], "after_cost_return"),
                            (archived["baseline_action"], "baseline_after_cost_return")):
                        expected_return = 0.0 if action == "no_trade" else (
                            pair[action + "_return"] - archived["additional_cost_return"][action])
                        if not math.isclose(row[key], expected_return, abs_tol=1e-12):
                            raise ValueError("prediction_cost_mismatch")
                    daily.setdefault(row["decision_date"], []).append(row)
                for prefix, key in (("", "after_cost_return"), ("baseline_", "baseline_after_cost_return")):
                    values = [float(np.mean([r[key] for r in rows])) for rows in daily.values()]
                    for name, expected_value in (("mean_after_cost_return", np.mean(values)),
                            ("downside_decile", np.quantile(values, .1))):
                        if not math.isclose(scores.get("metrics", {}).get(prefix + name, float("nan")), expected_value, abs_tol=1e-12):
                            raise ValueError("independent_date_metrics_mismatch")
            metrics = scores.get("metrics", {})
            for key in ("mean_after_cost_return", "downside_decile", "baseline_mean_after_cost_return", "baseline_downside_decile"):
                value = metrics.get(key)
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("missing_or_invalid_after_cost_metrics")
            seen.add(number)
            bundles.add(model_hash)
        return {"completed_folds": len(seen), "status": "verified", "reason": None,
                "evidence_kind": evidence_kind, "completion_contract_version": report["schema_version"],
                "manifest_sha256": expected_sha256, "run_id": report["run_id"],
                "candidate_id": report["candidate_id"], "evaluator_revision": report["evaluator_revision"]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result["reason"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return result
