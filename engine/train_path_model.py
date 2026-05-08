"""
Train the Orographic shadow path-quality model.

This model predicts hold-window behavior from canonical option outcome datasets:
  - probability the trade reaches an early profit-taking threshold
  - expected max favorable excursion during the intended hold window
  - decay risk implied by adverse excursion

Unlike the payoff ranker, this artifact is shadow-only by design.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

from engine.orographic.path_model import FEATURE_COLS, feature_matrix
from engine.orographic.schemas import ContractCandidate, MarketRegime
from engine.train_payoff_model import (
    MODEL_FAMILIES,
    DEFAULT_OPTIONS_DATA_DIR,
    TradeExample,
    _balanced_sample_weight,
    _fit_classifier,
    _fit_regressor,
    _positive_proba,
    _probability_buckets,
    _segment_metric_report,
    _sha256_file,
    _safe_float,
    default_input_paths,
    load_examples,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = Path("engine/orographic/models/path_model.pkl")
DEFAULT_REPORT_PATH = Path("output/path_model_training_report_2026-05-05.json")
DEFAULT_MODEL_CARD_PATH = Path("engine/orographic/models/path_model_card.json")


def _early_take_profit_label(
    mfe: float,
    realized_return: float,
    *,
    min_take_profit_pct: float = 0.25,
) -> int:
    _ = realized_return
    return int(mfe >= min_take_profit_pct)


def _path_labels(examples: list[Any]) -> dict[str, np.ndarray]:
    mfe = np.array([float(example.max_favorable_excursion_before_expiry) for example in examples], dtype=float)
    adverse = np.array([float(example.adverse_excursion_risk) for example in examples], dtype=float)
    realized = np.array([float(example.pnl_pct) for example in examples], dtype=float)
    return {
        "path_early_profit_take_prob": np.array(
            [_early_take_profit_label(float(mfe[i]), float(realized[i])) for i in range(len(examples))],
            dtype=int,
        ),
        "path_expected_mfe_pct": mfe,
        "path_decay_risk": np.clip(np.maximum(-adverse, 0.0), 0.0, 1.0).astype(float),
    }


def _family_cv_report(
    X: np.ndarray,
    labels: dict[str, np.ndarray],
    dates: list[date],
    sides: np.ndarray,
    regime_buckets: np.ndarray,
    *,
    family: str,
) -> dict[str, Any]:
    if len(X) < 80:
        return {"family": family, "folds": 0, "reason": "insufficient_examples"}

    order = np.argsort(np.array([d.toordinal() for d in dates]))
    X_sorted = X[order]
    y_early = labels["path_early_profit_take_prob"][order]
    y_mfe = labels["path_expected_mfe_pct"][order]
    y_decay = labels["path_decay_risk"][order]
    sides_sorted = sides[order]
    regimes_sorted = regime_buckets[order]
    tscv = TimeSeriesSplit(n_splits=min(5, max(2, len(X) // 120)))
    early_auc: list[float] = []
    early_brier: list[float] = []
    early_log_loss: list[float] = []
    mfe_mae: list[float] = []
    decay_mae: list[float] = []
    oof_early = np.full(len(X_sorted), np.nan, dtype=float)

    for train_idx, val_idx in tscv.split(X_sorted):
        X_train, X_val = X_sorted[train_idx], X_sorted[val_idx]
        early_train, early_val = y_early[train_idx], y_early[val_idx]
        mfe_train, mfe_val = y_mfe[train_idx], y_mfe[val_idx]
        decay_train, decay_val = y_decay[train_idx], y_decay[val_idx]

        train_sides = sides_sorted[train_idx]
        classifier = _fit_classifier(
            X_train,
            early_train,
            _balanced_sample_weight(train_sides, early_train),
            family=family,
        )
        mfe_model = _fit_regressor(X_train, mfe_train, _balanced_sample_weight(train_sides), family=family)
        decay_model = _fit_regressor(X_train, decay_train, _balanced_sample_weight(train_sides), family=family)
        early_probs = np.clip(_positive_proba(classifier, X_val), 1e-6, 1 - 1e-6)
        oof_early[val_idx] = early_probs

        if len(set(early_val.tolist())) > 1:
            early_auc.append(float(roc_auc_score(early_val, early_probs)))
            early_log_loss.append(float(log_loss(early_val, early_probs)))
        early_brier.append(float(brier_score_loss(early_val, early_probs)))
        mfe_mae.append(float(mean_absolute_error(mfe_val, mfe_model.predict(X_val))))
        decay_mae.append(float(mean_absolute_error(decay_val, decay_model.predict(X_val))))

    valid_early = np.isfinite(oof_early)
    return {
        "family": family,
        "folds": int(tscv.n_splits),
        "early_take_profit_auc_mean": round(float(np.mean(early_auc)), 4) if early_auc else None,
        "early_take_profit_brier_mean": round(float(np.mean(early_brier)), 4) if early_brier else None,
        "early_take_profit_log_loss_mean": round(float(np.mean(early_log_loss)), 4) if early_log_loss else None,
        "path_expected_mfe_mae_mean": round(float(np.mean(mfe_mae)), 4) if mfe_mae else None,
        "path_decay_risk_mae_mean": round(float(np.mean(decay_mae)), 4) if decay_mae else None,
        "probability_buckets": {
            "path_early_profit_take_prob": _probability_buckets(oof_early[valid_early], y_early[valid_early]),
        },
        "by_segment": {
            "side": {
                "path_early_profit_take_prob": _segment_metric_report(
                    y_early[valid_early],
                    oof_early[valid_early],
                    sides_sorted[valid_early],
                ),
            },
            "regime": {
                "path_early_profit_take_prob": _segment_metric_report(
                    y_early[valid_early],
                    oof_early[valid_early],
                    regimes_sorted[valid_early],
                ),
            },
        },
    }


def _family_sort_key(report: dict[str, Any]) -> tuple[float, ...]:
    def val(name: str) -> float:
        raw = report.get(name)
        if raw is None:
            return float("inf")
        return float(raw)

    return (
        val("early_take_profit_brier_mean"),
        val("path_expected_mfe_mae_mean"),
        val("path_decay_risk_mae_mean"),
        -float(report.get("early_take_profit_auc_mean") or 0.0),
    )


def _cv_report(
    X: np.ndarray,
    labels: dict[str, np.ndarray],
    dates: list[date],
    sides: np.ndarray,
    regime_buckets: np.ndarray,
) -> dict[str, Any]:
    family_reports = {
        family: _family_cv_report(X, labels, dates, sides, regime_buckets, family=family)
        for family in MODEL_FAMILIES
    }
    selected_family = min(family_reports, key=lambda family: _family_sort_key(family_reports[family]))
    selected = dict(family_reports[selected_family])
    selected["selected_family"] = selected_family
    selected["family_bakeoff"] = family_reports
    return selected


def _fit_bundle(
    X: np.ndarray,
    labels: dict[str, np.ndarray],
    sample_weight: np.ndarray | None = None,
    *,
    family: str,
) -> dict[str, Any]:
    return {
        "family": family,
        "early_take_profit_classifier": _fit_classifier(
            X,
            labels["path_early_profit_take_prob"],
            sample_weight,
            family=family,
        ),
        "mfe_regressor": _fit_regressor(
            X,
            labels["path_expected_mfe_pct"],
            sample_weight,
            family=family,
        ),
        "decay_risk_regressor": _fit_regressor(
            X,
            labels["path_decay_risk"],
            sample_weight,
            family=family,
        ),
    }


def _baseline_brier(y: np.ndarray) -> float:
    if len(y) == 0:
        return 0.0
    base_rate = float(np.mean(y))
    return float(brier_score_loss(y, np.full(len(y), base_rate)))


def _promotion_gate_report(
    *,
    training_examples: int,
    min_side_examples: int,
    side_counts: dict[str, int],
    regime_dataset_summary: dict[str, Any],
    exact_quote_marks_used: int,
    cv: dict[str, Any],
    early_baseline_brier: float,
    primary_artifact: str,
) -> dict[str, Any]:
    early_auc = cv.get("early_take_profit_auc_mean")
    early_brier = cv.get("early_take_profit_brier_mean")
    regime_segments_with_depth = sum(
        1 for row in regime_dataset_summary.values()
        if isinstance(row, dict) and int(row.get("rows", 0)) >= 25
    )
    gates = {
        "canonical_dataset_source": {
            "passed": primary_artifact == "option_outcome_dataset",
            "actual": primary_artifact,
            "required": "option_outcome_dataset",
        },
        "minimum_training_examples": {
            "passed": training_examples >= 150,
            "actual": training_examples,
            "required_min": 150,
        },
        "side_coverage": {
            "passed": min(side_counts.values()) >= min_side_examples if side_counts else False,
            "actual": side_counts,
            "required_min_per_side": min_side_examples,
        },
        "regime_segment_coverage": {
            "passed": regime_segments_with_depth >= 2,
            "actual_segments_with_min_rows": regime_segments_with_depth,
            "required_min_segments": 2,
        },
        "quote_path_observability": {
            "passed": exact_quote_marks_used >= 25,
            "actual": exact_quote_marks_used,
            "required_min": 25,
        },
        "early_take_profit_auc": {
            "passed": early_auc is not None and early_auc >= 0.53,
            "actual": early_auc,
            "required_min": 0.53,
        },
        "early_take_profit_brier_vs_baseline": {
            "passed": early_brier is not None and early_brier < early_baseline_brier,
            "actual": early_brier,
            "baseline": round(early_baseline_brier, 4),
        },
    }
    all_passed = all(bool(gate.get("passed")) for gate in gates.values())
    return {
        "status": "pending_shadow_validation" if all_passed else "hold",
        "gates": gates,
        "summary": (
            "Path-model data and walk-forward gates passed; keep it shadow-only until disagreement studies confirm lift."
            if all_passed
            else "One or more path-model data coverage or walk-forward gates remain below threshold; keep shadow-only."
        ),
        "required_next_step": "Run shadow disagreement studies comparing terminal-ranker picks against higher path-quality alternatives.",
    }


def train(
    input_paths: list[Path],
    *,
    output_model: Path = DEFAULT_MODEL_PATH,
    output_report: Path = DEFAULT_REPORT_PATH,
    output_model_card: Path = DEFAULT_MODEL_CARD_PATH,
    options_data_dir: Path | None = DEFAULT_OPTIONS_DATA_DIR,
    min_side_examples: int = 75,
) -> dict[str, Any]:
    examples, source_metadata = load_examples(input_paths, options_data_dir=options_data_dir)
    if len(examples) < 50:
        raise RuntimeError(f"Need at least 50 strict-real trades to train path model; found {len(examples)}")

    neutral = MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY")
    X = feature_matrix([example.candidate for example in examples], neutral, feature_cols=FEATURE_COLS)
    labels = _path_labels(examples)
    dates = [example.entry_date for example in examples]
    sides = np.array([example.candidate.option_type for example in examples], dtype=object)
    regime_buckets = np.array([example.regime_bucket for example in examples], dtype=object)
    cv = _cv_report(X, labels, dates, sides, regime_buckets)
    selected_family = str(cv.get("selected_family") or "tree")

    artifact: dict[str, Any] = {
        "version": 1,
        "feature_cols": FEATURE_COLS,
        "selected_family": selected_family,
        "global": _fit_bundle(X, labels, _balanced_sample_weight(sides), family=selected_family),
        "by_side": {},
        "metadata": {
            **source_metadata,
            "trained_at": date.today().isoformat(),
            "min_side_examples": min_side_examples,
            "label_means": {name: round(float(values.mean()), 4) for name, values in labels.items()},
            "regime_counts": dict(sorted(Counter(regime_buckets.tolist()).items())),
            "selected_family": selected_family,
            "family_bakeoff": cv.get("family_bakeoff", {}),
            "activation_policy": "shadow_only_path_model; does not alter live ranking",
        },
    }

    for side in ("call", "put"):
        side_idx = np.where(sides == side)[0]
        if len(side_idx) < min_side_examples:
            continue
        artifact["by_side"][side] = _fit_bundle(
            X[side_idx],
            {name: values[side_idx] for name, values in labels.items()},
            _balanced_sample_weight(sides[side_idx]),
            family=selected_family,
        )

    side_counts = {side: int((sides == side).sum()) for side in ("call", "put")}
    month_counts = Counter(example.entry_date.strftime("%Y-%m") for example in examples)
    coverage = {
        "entry_date_start": min(dates).isoformat(),
        "entry_date_end": max(dates).isoformat(),
        "months": {month: int(count) for month, count in sorted(month_counts.items())},
        "side_counts": side_counts,
        "regime_counts": dict(sorted(Counter(regime_buckets.tolist()).items())),
        "exact_quote_marks_used": int(source_metadata.get("exact_quote_marks_used", 0)),
        "option_chain_coverage_ratio": round(
            float(source_metadata.get("exact_quote_marks_used", 0)) / max(len(examples), 1),
            4,
        ),
    }
    regime_dataset_summary = source_metadata.get("regime_dataset_summary", {})
    early_baseline_brier = _baseline_brier(labels["path_early_profit_take_prob"])
    promotion_gates = _promotion_gate_report(
        training_examples=len(examples),
        min_side_examples=min_side_examples,
        side_counts=side_counts,
        regime_dataset_summary=regime_dataset_summary,
        exact_quote_marks_used=int(source_metadata.get("exact_quote_marks_used", 0)),
        cv=cv,
        early_baseline_brier=early_baseline_brier,
        primary_artifact=str(source_metadata.get("primary_training_source_artifact", "")),
    )

    report = {
        "artifact": "path_model",
        "version": artifact["version"],
        "model_card_schema_version": 1,
        "training_examples": len(examples),
        "side_counts": side_counts,
        "early_take_profit_rate": round(float(labels["path_early_profit_take_prob"].mean()), 4),
        "avg_expected_mfe_pct": round(float(labels["path_expected_mfe_pct"].mean()), 4),
        "avg_decay_risk": round(float(labels["path_decay_risk"].mean()), 4),
        "side_models_trained": sorted(artifact["by_side"].keys()),
        "selected_family": selected_family,
        "feature_cols": FEATURE_COLS,
        "cross_validation": cv,
        "coverage": coverage,
        "activation_policy": {
            "default": "shadow",
            "reason": "Path quality is a new observational layer and must prove disagreement value before any live use.",
        },
        "training_data": {
            "primary_artifact": source_metadata.get("primary_training_source_artifact"),
            "primary_source_files": source_metadata.get("primary_training_source_files", []),
            "canonical_dataset_files": source_metadata.get("canonical_dataset_files", []),
            "legacy_result_files": source_metadata.get("legacy_result_files", []),
            "input_artifact_by_file": source_metadata.get("input_artifact_by_file", {}),
        },
        "promotion_gates": promotion_gates,
        "source_metadata": source_metadata,
        "training_policy": {
            "global_fit": "side-balanced sample weights",
            "side_fit": "separate call and put bundles when side has enough examples",
            "min_side_examples": min_side_examples,
            "selected_family": selected_family,
            "evaluated_families": list(MODEL_FAMILIES),
        },
        "target_definitions": {
            "path_early_profit_take_prob": "1 when max favorable excursion reaches the take-profit zone before expiry",
            "path_expected_mfe_pct": "best observed bid-mark return before expiry when real marks exist, otherwise realized return fallback",
            "path_decay_risk": "normalized adverse excursion magnitude derived from worst observed bid-mark return before expiry",
        },
    }

    output_model.parent.mkdir(parents=True, exist_ok=True)
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_model_card.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_model)
    report["artifacts"] = {
        "model_path": str(output_model),
        "model_sha256": _sha256_file(output_model),
        "report_path": str(output_report),
        "model_card_path": str(output_model_card),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    output_report.write_text(rendered, encoding="utf-8")
    output_model_card.write_text(rendered, encoding="utf-8")
    log.info("Path model saved to %s", output_model)
    log.info("Training report saved to %s", output_report)
    log.info("Model card saved to %s", output_model_card)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train shadow path-quality model from canonical option-outcome datasets")
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        default=None,
        help="Input JSON path; may be repeated. Accepts canonical option_outcome_dataset artifacts and legacy backtest results JSON.",
    )
    parser.add_argument("--output-model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--output-report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--output-model-card", type=Path, default=DEFAULT_MODEL_CARD_PATH)
    parser.add_argument("--options-data-dir", type=Path, default=DEFAULT_OPTIONS_DATA_DIR)
    parser.add_argument("--min-side-examples", type=int, default=75)
    args = parser.parse_args()

    input_paths = args.input or default_input_paths()
    missing = [path for path in input_paths if not path.exists()]
    if missing:
        for path in missing:
            print(f"Missing input file: {path}", file=sys.stderr)
        raise SystemExit(1)

    train(
        input_paths,
        output_model=args.output_model,
        output_report=args.output_report,
        output_model_card=args.output_model_card,
        options_data_dir=args.options_data_dir,
        min_side_examples=args.min_side_examples,
    )


if __name__ == "__main__":
    main()
