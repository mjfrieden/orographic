from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
import numpy as np
from sklearn.metrics import brier_score_loss, roc_auc_score

from engine.orographic.validation import purged_date_splits
from engine.train_payoff_model import (
    _balanced_sample_weight,
    _baseline_brier,
    _fit_classifier,
    _positive_proba,
    _segment_metric_report,
    _training_feature_matrix,
    load_examples,
)


PROFILE_ID = "volatility_contract_no_directional_v1"
OBSERVATION_ONLY_MODE = "observation_only_never_used_for_routing"
FEATURE_COLS = [
    "option_type_is_call",
    "moneyness",
    "abs_delta",
    "implied_volatility",
    "iv_rank",
    "realized_vol_20d",
    "atr_pct_14d",
    "vrp_gap",
    "projected_move_pct",
    "breakeven_move_pct",
    "extrinsic_ratio",
    "dte",
]
DEFAULT_INPUT = Path("output/option_outcomes_live_recommendations.json")
DEFAULT_MODEL = Path("engine/orographic/models/payoff_volatility_shadow.pkl")
DEFAULT_CARD = Path("engine/orographic/models/payoff_volatility_shadow_card.json")


def _logits(probs: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(probs, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(clipped / (1.0 - clipped)).reshape(-1, 1)


def _fit_sigmoid_calibrator(raw_probs: np.ndarray, y: np.ndarray) -> float | None:
    """Fit a monotonic calibration-in-the-large log-odds intercept."""
    if len(y) == 0:
        return None
    target = float(np.mean(y))
    raw_logits = _logits(raw_probs).ravel()
    low, high = -12.0, 12.0
    for _ in range(80):
        midpoint = (low + high) / 2.0
        calibrated_mean = float(np.mean(1.0 / (1.0 + np.exp(-(raw_logits + midpoint)))))
        if calibrated_mean < target:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def _apply_calibrator(raw_probs: np.ndarray, calibrator: float | None) -> np.ndarray:
    raw = np.clip(np.asarray(raw_probs, dtype=float), 1e-6, 1 - 1e-6)
    if calibrator is None:
        return raw
    logits = _logits(raw).ravel() + float(calibrator)
    return np.clip(1.0 / (1.0 + np.exp(-logits)), 1e-6, 1 - 1e-6)


def _last_inner_split(
    feature_dates: np.ndarray,
    label_dates: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    splits = list(purged_date_splits(feature_dates, label_dates, n_splits=3))
    if not splits:
        raise RuntimeError("Insufficient distinct dates for nested calibration")
    return splits[-1]


def nested_calibrated_cv(
    X: np.ndarray,
    y: np.ndarray,
    feature_dates: np.ndarray,
    label_dates: np.ndarray,
    sides: np.ndarray,
    regimes: np.ndarray,
) -> dict[str, Any]:
    outer_splits = list(purged_date_splits(feature_dates, label_dates, n_splits=5))
    raw_oof = np.full(len(y), np.nan, dtype=float)
    calibrated_oof = np.full(len(y), np.nan, dtype=float)
    folds = []
    for fold, (outer_train, outer_validation) in enumerate(outer_splits, start=1):
        inner_train_rel, calibration_rel = _last_inner_split(
            feature_dates[outer_train],
            label_dates[outer_train],
        )
        inner_train = outer_train[inner_train_rel]
        calibration = outer_train[calibration_rel]
        base = _fit_classifier(
            X[inner_train],
            y[inner_train],
            _balanced_sample_weight(sides[inner_train], y[inner_train]),
            family="linear",
        )
        calibration_raw = _positive_proba(base, X[calibration])
        calibrator = _fit_sigmoid_calibrator(calibration_raw, y[calibration])
        validation_raw = _positive_proba(base, X[outer_validation])
        validation_calibrated = _apply_calibrator(validation_raw, calibrator)
        raw_oof[outer_validation] = validation_raw
        calibrated_oof[outer_validation] = validation_calibrated
        folds.append({
            "fold": fold,
            "model_training_rows": int(len(inner_train)),
            "calibration_rows": int(len(calibration)),
            "validation_rows": int(len(outer_validation)),
            "model_training_label_end": str(max(label_dates[inner_train])),
            "calibration_start": str(min(feature_dates[calibration])),
            "calibration_label_end": str(max(label_dates[calibration])),
            "validation_start": str(min(feature_dates[outer_validation])),
            "raw_brier": round(float(brier_score_loss(y[outer_validation], validation_raw)), 4),
            "calibrated_brier": round(float(brier_score_loss(y[outer_validation], validation_calibrated)), 4),
        })

    valid = np.isfinite(calibrated_oof)
    valid_y = y[valid]
    raw = raw_oof[valid]
    calibrated = calibrated_oof[valid]
    side_report = _segment_metric_report(valid_y, calibrated, sides[valid], min_rows=30)
    regime_report = _segment_metric_report(valid_y, calibrated, regimes[valid], min_rows=25)
    return {
        "split_policy": "nested_date_grouped_purged_outcome_aware",
        "calibration": "fold_local_monotonic_logit_intercept_on_inner_holdout",
        "folds": folds,
        "oof_rows": int(valid.sum()),
        "raw_auc": round(float(roc_auc_score(valid_y, raw)), 4),
        "calibrated_auc": round(float(roc_auc_score(valid_y, calibrated)), 4),
        "raw_brier": round(float(brier_score_loss(valid_y, raw)), 4),
        "calibrated_brier": round(float(brier_score_loss(valid_y, calibrated)), 4),
        "baseline_brier": round(_baseline_brier(valid_y), 4),
        "by_side": side_report,
        "by_regime": regime_report,
    }


def _quality_pass(row: dict[str, Any], *, min_rows: int) -> bool:
    return bool(
        int(row.get("rows", 0)) >= min_rows
        and row.get("auc") is not None
        and float(row["auc"]) >= 0.53
        and row.get("brier") is not None
        and row.get("baseline_brier") is not None
        and float(row["brier"]) < float(row["baseline_brier"])
    )


def acceptance_gates(cv: dict[str, Any]) -> dict[str, Any]:
    sides = cv.get("by_side") or {}
    regimes = cv.get("by_regime") or {}
    gates = {
        "aggregate_discrimination": cv.get("calibrated_auc", 0.0) >= 0.53,
        "aggregate_brier_skill": cv.get("calibrated_brier", 1.0) < cv.get("baseline_brier", 0.0),
        "calibration_non_degrading": cv.get("calibrated_brier", 1.0) <= cv.get("raw_brier", 0.0),
        "call_quality": _quality_pass(sides.get("call") or {}, min_rows=30),
        "put_quality": _quality_pass(sides.get("put") or {}, min_rows=30),
        "two_regimes_qualified": sum(_quality_pass(row, min_rows=25) for row in regimes.values()) >= 2,
    }
    return {"status": "eligible_for_live_shadow" if all(gates.values()) else "hold", "gates": gates}


def train_shadow(input_path: Path, model_path: Path, card_path: Path) -> dict[str, Any]:
    examples, metadata = load_examples([input_path], options_data_dir=None)
    X = _training_feature_matrix(examples, feature_cols=FEATURE_COLS)
    y = np.array([example.prob_positive_option_pnl for example in examples], dtype=int)
    feature_dates = np.array([example.entry_date for example in examples], dtype=object)
    label_dates = np.array([example.exit_date or example.entry_date for example in examples], dtype=object)
    sides = np.array([example.candidate.option_type for example in examples], dtype=object)
    regimes = np.array([example.regime_bucket for example in examples], dtype=object)
    cv = nested_calibrated_cv(X, y, feature_dates, label_dates, sides, regimes)
    gates = acceptance_gates(cv)

    train_idx, calibration_idx = _last_inner_split(feature_dates, label_dates)
    base = _fit_classifier(
        X[train_idx], y[train_idx], _balanced_sample_weight(sides[train_idx], y[train_idx]), family="linear",
    )
    calibrator = _fit_sigmoid_calibrator(_positive_proba(base, X[calibration_idx]), y[calibration_idx])
    artifact = {
        "artifact": "payoff_shadow_challenger",
        "version": 1,
        "mode": OBSERVATION_ONLY_MODE,
        "profile_id": PROFILE_ID,
        "feature_cols": FEATURE_COLS,
        "base_model": base,
        "calibrator": calibrator,
        "training_rows": int(len(train_idx)),
        "calibration_rows": int(len(calibration_idx)),
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, model_path)
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    card = {
        "artifact": "payoff_shadow_challenger_card",
        "version": 1,
        "trained_at": date.today().isoformat(),
        "mode": artifact["mode"],
        "profile_id": PROFILE_ID,
        "pre_registered_features": FEATURE_COLS,
        "excluded_feature_family": "Scout and directional probability features",
        "training_examples": len(examples),
        "side_counts": dict(sorted({side: int((sides == side).sum()) for side in set(sides)}.items())),
        "cross_validation": cv,
        "acceptance": gates,
        "source_metadata": metadata,
        "model_path": str(model_path),
        "model_sha256": digest,
    }
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return card


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the pre-registered volatility/contract payoff shadow challenger.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output-card", type=Path, default=DEFAULT_CARD)
    args = parser.parse_args()
    card = train_shadow(args.input, args.output_model, args.output_card)
    print(json.dumps({"output_model": str(args.output_model), "output_card": str(args.output_card), "acceptance": card["acceptance"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
