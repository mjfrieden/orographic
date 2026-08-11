from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from engine.orographic.payoff_model import FEATURE_COLS
from engine.train_payoff_model import _family_cv_report, _training_feature_matrix, load_examples


FEATURE_GROUPS = {
    "directional": [
        "option_type_is_call", "side_aligned_directional_edge", "scout_call_edge_prob",
        "scout_put_edge_prob", "scout_no_trade_prob", "heuristic_forge_score",
    ],
    "liquidity_friction": [
        "option_type_is_call", "premium", "premium_pct_of_spot", "spread_pct",
        "log_open_interest", "log_volume", "breakeven_move_pct",
        "heuristic_edge_after_friction_pct", "extrinsic_ratio", "dte",
        "liquidity_score", "fill_quality_score",
    ],
    "volatility_contract": [
        "option_type_is_call", "moneyness", "abs_delta", "implied_volatility", "iv_rank",
        "realized_vol_20d", "atr_pct_14d", "vrp_gap", "projected_move_pct",
        "breakeven_move_pct", "extrinsic_ratio", "dte",
    ],
    "regime_event": [
        "option_type_is_call", "regime_bias", "regime_is_risk_on", "regime_is_risk_off",
        "regime_alignment_score", "sentinel_holding_window_fit", "sentinel_confidence",
        "sentinel_side_relevance", "sentinel_no_trade_relevance", "sentinel_spot_effect",
        "sentinel_iv_effect", "sentinel_event_present", "sentinel_time_horizon_score",
        "sentinel_decay_half_life_score", "sentinel_source_reliability_score", "sentinel_novelty_score",
    ],
}


def _dt(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def temporal_integrity_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    violations = Counter()
    for row in rows:
        decision = _dt(row.get("decision_at_utc"))
        entry_quote = _dt(row.get("entry_quote_observed_at_utc"))
        exit_quote = _dt(row.get("exit_quote_observed_at_utc"))
        label_available = _dt(row.get("executable_label_available_at_utc"))
        regime_at = _dt(row.get("regime_observed_at_utc"))
        if None in (decision, entry_quote, exit_quote, label_available):
            violations["missing_required_timestamp"] += 1
            continue
        if entry_quote > decision:
            violations["entry_quote_after_decision"] += 1
        if exit_quote <= decision:
            violations["exit_quote_not_after_decision"] += 1
        if label_available < exit_quote:
            violations["label_available_before_exit_quote"] += 1
        if regime_at is None:
            violations["missing_signal_time_regime_timestamp"] += 1
        elif regime_at > decision:
            violations["regime_observed_after_decision"] += 1
        if not all(row.get(key) is not None for key in ("entry_bid", "entry_ask", "exit_bid", "exit_ask")):
            violations["missing_executable_quote_side"] += 1
    return {
        "rows": len(rows),
        "passed": not violations,
        "violations": dict(sorted(violations.items())),
    }


def monthly_distribution_report(examples: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Any]] = {}
    for example in examples:
        grouped.setdefault(example.entry_date.strftime("%Y-%m"), []).append(example)
    rows = []
    for month, month_examples in sorted(grouped.items()):
        returns = np.array([example.pnl_pct for example in month_examples], dtype=float)
        rows.append({
            "month": month,
            "rows": len(month_examples),
            "calls": sum(example.candidate.option_type == "call" for example in month_examples),
            "puts": sum(example.candidate.option_type == "put" for example in month_examples),
            "positive_rate": round(float(np.mean(returns > 0)), 4),
            "mean_return": round(float(np.mean(returns)), 4),
            "median_return": round(float(np.median(returns)), 4),
            "regimes": dict(sorted(Counter(example.regime_bucket for example in month_examples).items())),
        })
    return rows


def feature_shift_report(X: np.ndarray, examples: list[Any]) -> list[dict[str, Any]]:
    months = np.array([example.entry_date.strftime("%Y-%m") for example in examples], dtype=object)
    unique_months = sorted(set(months.tolist()))
    max_shift = np.zeros(X.shape[1], dtype=float)
    worst_pair = [None] * X.shape[1]
    for previous, current in zip(unique_months, unique_months[1:]):
        left, right = X[months == previous], X[months == current]
        pooled = np.sqrt((np.var(left, axis=0) + np.var(right, axis=0)) / 2.0)
        shift = np.abs(np.mean(right, axis=0) - np.mean(left, axis=0)) / np.where(pooled > 1e-9, pooled, 1.0)
        for idx, value in enumerate(shift):
            if value > max_shift[idx]:
                max_shift[idx] = value
                worst_pair[idx] = f"{previous}->{current}"
    order = np.argsort(-max_shift)[:10]
    return [
        {"feature": FEATURE_COLS[idx], "max_abs_standardized_mean_shift": round(float(max_shift[idx]), 4), "month_pair": worst_pair[idx]}
        for idx in order
    ]


def ablation_report(X: np.ndarray, examples: list[Any]) -> dict[str, Any]:
    labels = {
        "prob_positive_option_pnl": np.array([example.prob_positive_option_pnl for example in examples], dtype=int),
        "prob_exceeds_breakeven": np.array([example.prob_exceeds_breakeven for example in examples], dtype=int),
        "expected_option_return_pct": np.array([example.expected_option_return_pct for example in examples], dtype=float),
    }
    dates = [example.entry_date for example in examples]
    label_dates = [example.exit_date or example.entry_date for example in examples]
    sides = np.array([example.candidate.option_type for example in examples], dtype=object)
    regimes = np.array([example.regime_bucket for example in examples], dtype=object)
    directional = set(FEATURE_GROUPS["directional"])
    liquidity = set(FEATURE_GROUPS["liquidity_friction"])
    groups = {
        "full": FEATURE_COLS,
        **FEATURE_GROUPS,
        "without_directional": [feature for feature in FEATURE_COLS if feature not in directional],
        "without_liquidity_friction": [feature for feature in FEATURE_COLS if feature not in liquidity],
    }
    reports = {}
    for name, features in groups.items():
        indices = [FEATURE_COLS.index(feature) for feature in features]
        report = _family_cv_report(
            X[:, indices], labels, dates, label_dates, sides, regimes, family="linear",
        )
        reports[name] = {
            "features": features,
            "positive_pnl_auc_mean": report.get("positive_pnl_auc_mean"),
            "positive_pnl_brier_mean": report.get("positive_pnl_brier_mean"),
            "breakeven_auc_mean": report.get("breakeven_auc_mean"),
            "expected_return_mae_mean": report.get("expected_return_mae_mean"),
            "by_side": (report.get("by_segment") or {}).get("side", {}).get("prob_positive_option_pnl", {}),
        }
    return reports


def build_diagnostic(input_path: Path) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    raw_rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    examples, metadata = load_examples([input_path], options_data_dir=None)
    X = _training_feature_matrix(examples)
    return {
        "artifact": "payoff_skill_diagnostic",
        "input": str(input_path),
        "label_policy": payload.get("label_policy"),
        "raw_rows": len(raw_rows),
        "deduplicated_examples": len(examples),
        "temporal_integrity": temporal_integrity_report(raw_rows),
        "monthly_distribution": monthly_distribution_report(examples),
        "top_feature_shifts": feature_shift_report(X, examples),
        "fixed_linear_feature_ablation": ablation_report(X, examples),
        "source_metadata": metadata,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose negative out-of-fold payoff-model skill without tuning gates.")
    parser.add_argument("--input", type=Path, default=Path("output/option_outcomes_live_recommendations.json"))
    parser.add_argument("--output", type=Path, default=Path("output/payoff_skill_diagnostics_latest.json"))
    args = parser.parse_args()
    report = build_diagnostic(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "raw_rows": report["raw_rows"], "deduplicated_examples": report["deduplicated_examples"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
