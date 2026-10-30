"""Offline exact-pair evaluation of the existing three-class Scout estimator.

No fetch, active-artifact write, threshold tuning, or production authority.
Inputs are archived and externally hash-pinned; incomplete runs never receive
a completion manifest. Synthetic input is permanently marked synthetic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import re

import joblib
import numpy as np
from sklearn.preprocessing import RobustScaler

from engine.train_scout_model import _side_estimator, SIDE_CLASS_MAP, SIDE_CLASS_TO_ID
from .fold_evaluation_evidence import _timestamp, _sha
from .scout_pair_readiness import _pair_records, _record_hash, _fold_plan
from .validation import purged_date_splits

FEATURES = frozenset(("mom_5d mom_10d mom_20d mom_60d rv20 rv60 vol_adj_mom_5d "
    "vol_adj_mom_20d rsi_14 rsi_7 atr_pct_14d price_vs_ma20 volume_ratio vol_regime "
    "spy_mom_5d spy_mom_20d spy_rv20 rel_strength_20d").split())
POLICY = "scout_three_class_argmax_strict_executable_returns_v1"


def _json(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode()


def _write(root, name, raw):
    path = root / name
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": name, "sha256": sha256(raw).hexdigest()}


def _number(value):
    if type(value) not in (float, int) or not np.isfinite(value):
        raise ValueError("finite_numeric_input_required")
    return float(value)


def evaluate_pairs(bundle_path: Path, expected_sha256: str, pair_readiness: dict,
                   output_dir: Path, *, evaluator_revision: str, run_id: str) -> dict:
    raw = Path(bundle_path).read_bytes()
    if not _sha(expected_sha256) or sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("input_bundle_hash_mismatch")
    source = json.loads(raw)
    if (source.get("artifact") != "scout_archived_pair_inputs" or source.get("schema_version") != 1
            or source.get("evidence_kind") not in {"real", "synthetic"}
            or source.get("comparison_policy") != POLICY
            or not _sha(source.get("baseline_artifact_sha256"))
            or not isinstance(source.get("baseline_policy_id"), str) or not source["baseline_policy_id"].strip()):
        raise ValueError("invalid_archived_input_contract")
    if not re.fullmatch(r"[0-9a-f]{40}", evaluator_revision) or not run_id.strip():
        raise ValueError("run_and_evaluator_identity_required")
    payload = source["outcomes"]
    if payload.get("label_policy") != "strict_executable_quote_or_fill_v2":
        raise ValueError("strict_executable_labels_required")
    records, _ = _pair_records(payload["rows"])
    plan = _fold_plan(records)
    if (not records or _record_hash(records) != pair_readiness.get("coverage", {}).get("evidence_sha256")
            or plan != pair_readiness.get("fold_frozen_evaluation_plan")
            or pair_readiness.get("source", {}).get("label_policy") != payload["label_policy"]):
        raise ValueError("pair_corpus_or_exact_plan_mismatch")
    names = source.get("feature_names")
    if not isinstance(names, list) or not names or any(n not in FEATURES for n in names) or len(set(names)) != len(names):
        raise ValueError("invalid_feature_contract")
    archived = source["rows"]
    by_id = {r["pair_id"]: r for r in archived}
    if len(by_id) != len(archived) or set(by_id) != {r["pair_id"] for r in records}:
        raise ValueError("missing_duplicate_or_extra_pair_features")
    outcome_clocks = {}
    for row in payload["rows"]:
        pair_id = row.get("paired_observation_id")
        if pair_id in by_id:
            outcome_clocks.setdefault(pair_id, []).append(_timestamp(row.get("decision_at_utc")))
    now = datetime.now(timezone.utc)
    X, baseline, costs = [], [], []
    for pair in records:
        row = by_id[pair["pair_id"]]
        decision = _timestamp(row["decision_at_utc"])
        available = _timestamp(row["feature_available_at_utc"])
        # Both provider-time and availability lineage are mandatory.
        observed = _timestamp(row["feature_observed_at_utc"])
        clocks = outcome_clocks.get(pair["pair_id"], [])
        if len(clocks) != 2 or any(clock != decision for clock in clocks):
            raise ValueError("archived_decision_clock_mismatch")
        if (observed > available or available > decision or decision > now
                or decision.date().isoformat() != pair["decision_date"]
                or row["symbol"] != pair["symbol"]
                or not _sha(row.get("feature_source_sha256"))
                or _timestamp(row["baseline_emitted_at_utc"]) > decision
                or row.get("baseline_artifact_sha256") != source["baseline_artifact_sha256"]
                or row.get("baseline_policy_id") != source["baseline_policy_id"]
                or pair["label_available_date"] > now.date().isoformat()):
            raise ValueError("point_in_time_or_baseline_provenance_mismatch")
        features = row["features"]
        if set(features) != set(names):
            raise ValueError("feature_columns_mismatch")
        X.append([_number(features[name]) for name in names])
        # Strict labels include the executable spread; explicit additional
        # fee/slippage return deductions are frozen in the pinned input policy.
        cost = {side: _number(row["additional_cost_return"][side]) for side in ("call", "put")}
        if min(cost.values()) < 0 or row["baseline_action"] not in ("call", "put", "no_trade"):
            raise ValueError("invalid_cost_or_baseline_action")
        costs.append(cost)
        action = row["baseline_action"]
        baseline.append(0.0 if action == "no_trade" else pair[action + "_return"] - cost[action])
    X = np.asarray(X)
    y = np.asarray([SIDE_CLASS_TO_ID[r["label"]] for r in records])
    splits = list(purged_date_splits([r["decision_date"] for r in records],
        [r["label_available_date"] for r in records], n_splits=5))
    ready = [(p, split) for p, split in zip(plan["folds"], splits, strict=True) if p["ready"]]
    if len(ready) < 3:
        raise ValueError("insufficient_ready_folds")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    _write(output_dir, "inputs.json", raw)
    from engine import train_scout_model
    from . import scout_pair_readiness, validation
    source_files = [Path(__file__), Path(train_scout_model.__file__),
                    Path(scout_pair_readiness.__file__), Path(validation.__file__)]
    evaluator_sources = [_write(output_dir, "source-" + p.name, p.read_bytes()) for p in source_files]
    report = dict(artifact="scout_fold_frozen_evaluation", schema_version=2,
        status="completed", execution_effect="none_research_only", active_model_change_allowed=False,
        evidence_kind=source["evidence_kind"], run_id=run_id,
        evaluator_sources=evaluator_sources,
        candidate_id="existing_scout_three_class", evaluator_revision=evaluator_revision,
        source_evidence_sha256=_record_hash(records), label_policy=payload["label_policy"],
        input_bundle={"path":"inputs.json", "sha256":expected_sha256},
        comparison_policy=POLICY, baseline_policy_id=source["baseline_policy_id"],
        baseline_artifact_sha256=source["baseline_artifact_sha256"], folds=[])
    for p, (train, validation) in ready:
        if len(np.unique(y[train])) != 3:
            raise ValueError("three_class_training_required")
        scaler = RobustScaler()
        model = _side_estimator(p["fold"])
        model.fit(scaler.fit_transform(X[train]), y[train])
        state = io.BytesIO()
        joblib.dump(dict(model=model, scaler=scaler, calibrator=None,
            calibration_policy="uncalibrated_existing_three_class_argmax",
            feature_names=names, class_map=SIDE_CLASS_MAP, fold=p["fold"],
            input_bundle_sha256=expected_sha256, training_evidence_sha256=p["training_evidence_sha256"]), state)
        model_ref = _write(output_dir, f"fold-{p['fold']}-model.joblib", state.getvalue())
        frozen = datetime.now(timezone.utc).isoformat()
        started = datetime.now(timezone.utc).isoformat()
        predictions = model.predict(scaler.transform(X[validation]))
        if len(predictions) != len(validation) or not set(predictions).issubset(SIDE_CLASS_MAP):
            raise ValueError("incomplete_or_invalid_predictions")
        rows = []
        for index, prediction in zip(validation, predictions, strict=True):
            pair = records[index]
            action = {0:"put", 1:"no_trade", 2:"call"}[int(prediction)]
            value = 0.0 if action == "no_trade" else pair[action + "_return"] - costs[index][action]
            rows.append(dict(pair_id=pair["pair_id"], decision_date=pair["decision_date"],
                action=action, after_cost_return=value, baseline_after_cost_return=baseline[index]))
        # Equal weight per independent date, not per repeated intraday pair.
        dates = sorted({r["decision_date"] for r in rows})
        daily = [{"decision_date":d, **{k:float(np.mean([r[k] for r in rows if r["decision_date"]==d]))
            for k in ("after_cost_return", "baseline_after_cost_return")}} for d in dates]
        metrics = {}
        for prefix, key in (("", "after_cost_return"), ("baseline_", "baseline_after_cost_return")):
            values = [r[key] for r in daily]
            metrics[prefix+"mean_after_cost_return"] = float(np.mean(values))
            metrics[prefix+"downside_decile"] = float(np.quantile(values, .1))
        scores = dict(artifact="scout_fold_validation_scores", status="completed",
            **{k:report[k] for k in ("run_id", "candidate_id", "evaluator_revision")},
            fold=p["fold"], model_bundle_sha256=model_ref["sha256"],
            training_evidence_sha256=p["training_evidence_sha256"],
            validation_evidence_sha256=p["validation_evidence_sha256"],
            evaluated_pairs=len(rows), metrics=metrics, predictions=rows, daily_comparisons=daily)
        scores_ref = _write(output_dir, f"fold-{p['fold']}-scores.json", _json(scores))
        report["folds"].append({**p, "status":"completed", "model_bundle":model_ref,
            "scores":scores_ref, "frozen_at_utc":frozen, "scoring_started_at_utc":started,
            "completed_at_utc":datetime.now(timezone.utc).isoformat()})
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest = _write(output_dir, "completion.json", _json(report))
    return {"manifest":str(output_dir / manifest["path"]), "sha256":manifest["sha256"],
            "completed_folds":len(report["folds"]), "evidence_kind":report["evidence_kind"]}
