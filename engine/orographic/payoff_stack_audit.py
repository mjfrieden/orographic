"""Leakage-safe payoff-stack orientation and retraining audit.

The audit is research-only. It never writes a model artifact or changes the
rank weights used by Forge, Council, sizing, or Tradier.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from engine.train_payoff_model import (
    TradeExample,
    _balanced_sample_weight,
    _fit_classifier,
    _fit_quantile_regressor,
    _positive_proba,
    _training_feature_matrix,
)
from .payoff_model import COST_AWARE_SHADOW_MODEL_PATH, _attach_payoff_shadow_observations
from .validation import purged_date_splits


STACK_WEIGHTS = {
    "primary": 0.60,
    "path": 0.18,
    "cost_rank": 0.14,
    "conservative": 0.08,
}


def _clip(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if not np.isfinite(parsed):
        parsed = default
    return max(0.0, min(1.0, parsed))


def _rank_percentile(values: np.ndarray) -> np.ndarray:
    if len(values) <= 1:
        return np.full(len(values), 0.5, dtype=float)
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.linspace(0.0, 1.0, len(values))
    return ranks


def _hash_rows(examples: list[TradeExample], indices: np.ndarray) -> str:
    identities = [
        {
            "symbol": examples[index].candidate.symbol,
            "contract": examples[index].candidate.contract_symbol,
            "entry_date": examples[index].entry_date.isoformat(),
            "label_date": (examples[index].exit_date or examples[index].entry_date).isoformat(),
        }
        for index in indices.tolist()
    ]
    payload = json.dumps(identities, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stack_score(
    example: TradeExample,
    *,
    cost_rank: float,
    conservative: float,
    include_cost: bool = True,
) -> float:
    primary = _clip(
        example.candidate.payoff_model_score,
        _clip(example.candidate.forge_score, 0.5),
    )
    path = _clip(example.candidate.path_holding_quality_score, 0.5)
    if include_cost:
        return _clip(
            STACK_WEIGHTS["primary"] * primary
            + STACK_WEIGHTS["path"] * path
            + STACK_WEIGHTS["cost_rank"] * _clip(cost_rank, 0.5)
            + STACK_WEIGHTS["conservative"] * _clip(conservative, 0.5)
        )
    total = STACK_WEIGHTS["primary"] + STACK_WEIGHTS["path"]
    return _clip(
        STACK_WEIGHTS["primary"] / total * primary
        + STACK_WEIGHTS["path"] / total * path
    )


def _pick_row(
    examples: list[TradeExample],
    indices: list[int],
    scores: dict[int, float],
) -> dict[str, Any]:
    chosen = max(
        indices,
        key=lambda index: (
            scores[index],
            examples[index].candidate.contract_symbol,
        ),
    )
    example = examples[chosen]
    return {
        "decision_date": example.entry_date.isoformat(),
        "symbol": example.candidate.symbol,
        "contract_symbol": example.candidate.contract_symbol,
        "option_type": example.candidate.option_type,
        "score": round(float(scores[chosen]), 6),
        "realized_after_cost_return": round(float(example.pnl_pct), 6),
    }


def _variant_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = np.asarray([float(row["realized_after_cost_return"]) for row in rows], dtype=float)
    sides = Counter(str(row["option_type"]) for row in rows)
    if not len(returns):
        return {"decision_dates": 0, "picks": 0}
    return {
        "decision_dates": len({row["decision_date"] for row in rows}),
        "picks": len(rows),
        "mean_after_cost_return": round(float(np.mean(returns)), 6),
        "median_after_cost_return": round(float(np.median(returns)), 6),
        "sum_after_cost_return": round(float(np.sum(returns)), 6),
        "win_rate": round(float(np.mean(returns > 0)), 6),
        "downside_decile": round(float(np.quantile(returns, 0.10)), 6),
        "side_counts": dict(sorted(sides.items())),
    }


def _paired_lift(
    variant_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    *,
    bootstrap_iterations: int,
    random_seed: int,
) -> dict[str, Any]:
    variant = {row["decision_date"]: float(row["realized_after_cost_return"]) for row in variant_rows}
    baseline = {row["decision_date"]: float(row["realized_after_cost_return"]) for row in baseline_rows}
    dates = sorted(set(variant) & set(baseline))
    differences = np.asarray([variant[value] - baseline[value] for value in dates], dtype=float)
    if not len(differences):
        return {"paired_dates": 0, "mean_lift": None, "ci_95": [None, None]}
    rng = np.random.default_rng(random_seed)
    sampled = differences[
        rng.integers(0, len(differences), size=(max(bootstrap_iterations, 100), len(differences)))
    ].mean(axis=1)
    return {
        "paired_dates": len(dates),
        "mean_lift": round(float(np.mean(differences)), 6),
        "ci_95": [
            round(float(np.quantile(sampled, 0.025)), 6),
            round(float(np.quantile(sampled, 0.975)), 6),
        ],
        "probability_positive": round(float(np.mean(sampled > 0)), 6),
    }


def build_payoff_stack_audit(
    examples: list[TradeExample],
    *,
    source: dict[str, Any] | None = None,
    fixed_artifact_path: Path = COST_AWARE_SHADOW_MODEL_PATH,
    minimum_train_rows: int = 80,
    minimum_validation_rows: int = 20,
    minimum_train_rows_per_side: int = 15,
    minimum_ready_folds: int = 3,
    minimum_validation_dates: int = 15,
    minimum_history_days: int = 90,
    required_window_days: tuple[int, int, int] = (90, 180, 365),
    bootstrap_iterations: int = 4000,
    random_seed: int = 42,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    if not examples:
        return {
            "artifact": "payoff_stack_fold_frozen_audit",
            "schema_version": 1,
            "status": "hold_insufficient_evidence",
            "execution_effect": "none_research_only",
            "active_model_change_allowed": False,
            "coverage": {"rows": 0, "decision_dates": 0},
            "folds": [],
            "variants": {},
            "next_action": "Collect strict executable option outcomes before evaluating payoff orientation.",
        }

    order = np.argsort(
        np.asarray([example.entry_date.toordinal() for example in examples]),
        kind="stable",
    )
    ordered = [examples[index] for index in order.tolist()]
    X = _training_feature_matrix(ordered)
    feature_dates = np.asarray([example.entry_date for example in ordered], dtype=object)
    label_dates = np.asarray(
        [example.exit_date or example.entry_date for example in ordered],
        dtype=object,
    )
    y_positive = np.asarray([example.prob_positive_option_pnl for example in ordered], dtype=int)
    y_return = np.asarray([example.expected_option_return_pct for example in ordered], dtype=float)
    sides = np.asarray([example.candidate.option_type for example in ordered], dtype=object)
    splits = list(purged_date_splits(feature_dates, label_dates, n_splits=5))

    variant_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fold_reports: list[dict[str, Any]] = []
    fixed_available = fixed_artifact_path.exists()
    for fold_number, (train_idx, validation_idx) in enumerate(splits, start=1):
        train_sides = Counter(str(sides[index]) for index in train_idx.tolist())
        validation_dates = sorted({feature_dates[index] for index in validation_idx.tolist()})
        ready = (
            len(train_idx) >= minimum_train_rows
            and len(validation_idx) >= minimum_validation_rows
            and min(train_sides.get("call", 0), train_sides.get("put", 0))
            >= minimum_train_rows_per_side
        )
        report = {
            "fold": fold_number,
            "ready": ready,
            "train_rows": int(len(train_idx)),
            "validation_rows": int(len(validation_idx)),
            "train_side_counts": dict(sorted(train_sides.items())),
            "training_labels_available_through": max(label_dates[train_idx]).isoformat(),
            "validation_start": min(validation_dates).isoformat(),
            "validation_end": max(validation_dates).isoformat(),
            "training_evidence_sha256": _hash_rows(ordered, train_idx),
            "validation_evidence_sha256": _hash_rows(ordered, validation_idx),
            "artifact_policy": "fit once on purged training rows, freeze for the full validation block, then discard",
        }
        fold_reports.append(report)
        if not ready:
            continue

        weights = _balanced_sample_weight(sides[train_idx], y_positive[train_idx])
        positive_model = _fit_classifier(
            X[train_idx],
            y_positive[train_idx],
            weights,
            family="linear",
        )
        q10_model = _fit_quantile_regressor(
            X[train_idx],
            y_return[train_idx],
            0.10,
            _balanced_sample_weight(sides[train_idx]),
            family="linear",
        )
        fold_positive = _positive_proba(positive_model, X[validation_idx])
        fold_q10 = np.asarray(q10_model.predict(X[validation_idx]), dtype=float)

        by_date: dict[date, list[int]] = defaultdict(list)
        for index in validation_idx.tolist():
            by_date[feature_dates[index]].append(index)
        position = {index: offset for offset, index in enumerate(validation_idx.tolist())}
        for decision_date, date_indices in sorted(by_date.items()):
            local = np.asarray([position[index] for index in date_indices], dtype=int)
            retrained_rank = _rank_percentile(fold_positive[local])
            retrained_conservative = np.clip(0.5 + fold_q10[local] / 0.50, 0.0, 1.0)

            if fixed_available:
                candidates = [ordered[index].candidate for index in date_indices]
                _attach_payoff_shadow_observations(
                    candidates,
                    None,
                    as_of=decision_date,
                    shadow_model_path=fixed_artifact_path,
                )

            scores: dict[str, dict[int, float]] = defaultdict(dict)
            for local_index, index in enumerate(date_indices):
                example = ordered[index]
                scores["zero_cost_aware"][index] = _stack_score(
                    example,
                    cost_rank=0.5,
                    conservative=0.5,
                    include_cost=False,
                )
                scores["fold_frozen_retrained"][index] = _stack_score(
                    example,
                    cost_rank=float(retrained_rank[local_index]),
                    conservative=float(retrained_conservative[local_index]),
                )
                scores["fold_frozen_inverted"][index] = _stack_score(
                    example,
                    cost_rank=1.0 - float(retrained_rank[local_index]),
                    conservative=1.0 - float(retrained_conservative[local_index]),
                )
                if fixed_available:
                    fixed_rank = _clip(example.candidate.payoff_shadow_rank, 0.5)
                    fixed_conservative = _clip(
                        0.5
                        + float(example.candidate.payoff_shadow_conservative_utility or 0.0)
                        / 0.50,
                        0.5,
                    )
                    scores["current_fixed_artifact"][index] = _stack_score(
                        example,
                        cost_rank=fixed_rank,
                        conservative=fixed_conservative,
                    )
                    scores["fixed_artifact_inverted"][index] = _stack_score(
                        example,
                        cost_rank=1.0 - fixed_rank,
                        conservative=1.0 - fixed_conservative,
                    )
            for name, date_scores in scores.items():
                variant_rows[name].append(_pick_row(ordered, date_indices, date_scores))

    baseline = variant_rows.get("zero_cost_aware", [])
    variants: dict[str, Any] = {}
    for offset, (name, rows) in enumerate(sorted(variant_rows.items())):
        variants[name] = {
            "metrics": _variant_metrics(rows),
            "paired_lift_vs_zero_cost_aware": _paired_lift(
                rows,
                baseline,
                bootstrap_iterations=bootstrap_iterations,
                random_seed=random_seed + offset,
            ),
            "uses_future_trained_artifact": name.startswith("current_fixed")
            or name.startswith("fixed_artifact"),
            "picks": rows,
        }

    ready_folds = sum(bool(row["ready"]) for row in fold_reports)
    evaluated_dates = int(
        variants.get("fold_frozen_retrained", {}).get("metrics", {}).get("decision_dates", 0)
    )
    first_date = min(feature_dates)
    last_date = max(feature_dates)
    history_days = (last_date - first_date).days
    validation_sides = Counter(
        row["option_type"] for row in variant_rows.get("fold_frozen_retrained", [])
    )
    leakage_safe = all(
        row["training_labels_available_through"] < row["validation_start"]
        for row in fold_reports
        if row["ready"]
    )
    sample_gates = {
        "minimum_ready_folds": {
            "passed": ready_folds >= minimum_ready_folds,
            "actual": ready_folds,
            "required": minimum_ready_folds,
        },
        "minimum_validation_dates": {
            "passed": evaluated_dates >= minimum_validation_dates,
            "actual": evaluated_dates,
            "required": minimum_validation_dates,
        },
        "minimum_history_days": {
            "passed": history_days >= minimum_history_days,
            "actual": history_days,
            "required": minimum_history_days,
        },
        "full_3_6_12_month_windows": {
            "passed": all(history_days >= days for days in required_window_days),
            "actual_history_days": history_days,
            "windows": {
                label: {"passed": history_days >= days, "required_days": days}
                for label, days in zip(
                    ("3_month", "6_month", "12_month"),
                    required_window_days,
                    strict=True,
                )
            },
        },
        "validation_side_coverage": {
            "passed": min(validation_sides.get("call", 0), validation_sides.get("put", 0)) >= 5,
            "actual": dict(sorted(validation_sides.items())),
            "required_each": 5,
        },
        "no_label_leakage": {"passed": leakage_safe},
    }
    retrained_lift = variants.get("fold_frozen_retrained", {}).get(
        "paired_lift_vs_zero_cost_aware", {}
    )
    inverted_lift = variants.get("fold_frozen_inverted", {}).get(
        "paired_lift_vs_zero_cost_aware", {}
    )
    retrained_lower = (retrained_lift.get("ci_95") or [None])[0]
    inverted_lower = (inverted_lift.get("ci_95") or [None])[0]
    all_sample_gates = all(bool(gate["passed"]) for gate in sample_gates.values())
    if not all_sample_gates:
        conclusion = "hold_insufficient_window"
        next_action = "Extend strict executable history, then rerun the same frozen-fold audit."
    elif inverted_lower is not None and float(inverted_lower) > 0:
        conclusion = "orientation_inversion_requires_retraining_review"
        next_action = "Audit label/score orientation and retrain offline; do not invert the production weight directly."
    elif retrained_lower is not None and float(retrained_lower) > 0:
        conclusion = "retrained_cost_signal_requires_extended_validation"
        next_action = "Repeat across full 3/6/12-month strict windows before changing the unified weight."
    else:
        conclusion = "hold_no_reliable_cost_aware_lift"
        next_action = "Keep the current weight unchanged while accumulating longer strict data and prospective disagreements."

    generated = (now_utc or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "artifact": "payoff_stack_fold_frozen_audit",
        "schema_version": 1,
        "generated_at_utc": generated,
        "status": conclusion,
        "execution_effect": "none_research_only",
        "active_model_change_allowed": False,
        "source": source or {},
        "methodology": {
            "split": "expanding walk-forward grouped by decision date with labels purged by availability date",
            "model_family": "pre-registered linear logistic + q10 quantile regression",
            "weights": STACK_WEIGHTS,
            "selection": "one top-scored contract per validation decision date",
            "fixed_artifact_warning": "fixed-artifact variants are diagnostic only and cannot satisfy promotion gates",
        },
        "coverage": {
            "rows": len(ordered),
            "decision_dates": len(set(feature_dates.tolist())),
            "first_decision_date": first_date.isoformat(),
            "last_decision_date": last_date.isoformat(),
            "history_days": history_days,
            "evaluated_validation_dates": evaluated_dates,
            "fixed_artifact_available": fixed_available,
        },
        "sample_gates": sample_gates,
        "folds": fold_reports,
        "variants": variants,
        "next_action": next_action,
    }
