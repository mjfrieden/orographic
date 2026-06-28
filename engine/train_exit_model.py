from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = REPO_ROOT / "web" / "data" / "diagnostics" / "prospective_pick_ledger.json"
DEFAULT_MODEL_OUTPUT = REPO_ROOT / "engine" / "orographic" / "models" / "position_exit_model.json"
DEFAULT_PUBLIC_MODEL_OUTPUT = REPO_ROOT / "web" / "data" / "diagnostics" / "position_exit_model_latest.json"
DEFAULT_REFERENCE_OUTPUT = REPO_ROOT / "web" / "data" / "diagnostics" / "position_exit_reference_latest.json"
FEATURE_NAMES = [
    "is_call",
    "days_to_expiry",
    "final_candidate_score",
    "prob_no_trade",
    "prob_fill_quality_ok",
    "expected_edge_after_friction_pct",
    "expected_option_return_pct_model",
    "path_holding_quality_score",
    "path_early_profit_take_prob",
    "path_decay_risk",
    "scout_call_edge_prob",
    "scout_put_edge_prob",
    "scout_no_trade_prob",
    "sentinel_confidence",
    "sentinel_call_relevance",
    "sentinel_put_relevance",
    "sentinel_no_trade_relevance",
    "delta",
    "implied_volatility",
    "iv_rank",
    "moneyness",
    "premium_pct_of_spot",
    "breakeven_move_pct",
    "projected_move_pct",
    "extrinsic_ratio",
    "realized_vol_20d",
    "atr_pct_14d",
    "regime_is_risk_on",
    "regime_is_risk_off",
]
HEAD_DEFINITIONS = {
    "hold": "Position finished positive without a strong harvest or damage signal; holding remained defensible.",
    "harvest": "Position produced enough favorable excursion or terminal gain that profit-taking was justified.",
    "sell": "Position suffered enough terminal damage or adverse path pressure that exiting was prudent.",
}


@dataclass
class TrainingRow:
    run_generated_at_utc: str
    contract_symbol: str
    feature_map: dict[str, float]
    labels: dict[str, int]
    metadata: dict[str, Any]


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        parsed = float(value)
        if not np.isfinite(parsed):
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _bool_flag(value: object) -> int:
    return 1 if bool(value) else 0


def _first_hit_rule(path_rules: dict[str, Any]) -> str:
    first_hit = path_rules.get("first_hit")
    if isinstance(first_hit, dict):
        return str(first_hit.get("rule") or "").strip().lower()
    return str(first_hit or "").strip().lower()


def _extract_feature_map(entry: dict[str, Any], pick: dict[str, Any]) -> dict[str, float]:
    scores = pick.get("scores") if isinstance(pick.get("scores"), dict) else {}
    risk = pick.get("risk_features") if isinstance(pick.get("risk_features"), dict) else {}
    regime = entry.get("regime") if isinstance(entry.get("regime"), dict) else {}
    option_type = str(pick.get("option_type") or "").strip().lower()
    return {
        "is_call": 1.0 if option_type == "call" else 0.0,
        "days_to_expiry": _safe_float(pick.get("days_to_expiry")),
        "final_candidate_score": _safe_float(scores.get("final_candidate_score")),
        "prob_no_trade": _safe_float(scores.get("prob_no_trade")),
        "prob_fill_quality_ok": _safe_float(scores.get("prob_fill_quality_ok")),
        "expected_edge_after_friction_pct": _safe_float(scores.get("expected_edge_after_friction_pct")),
        "expected_option_return_pct_model": _safe_float(scores.get("expected_option_return_pct_model")),
        "path_holding_quality_score": _safe_float(scores.get("path_holding_quality_score")),
        "path_early_profit_take_prob": _safe_float(scores.get("path_early_profit_take_prob")),
        "path_decay_risk": _safe_float(scores.get("path_decay_risk")),
        "scout_call_edge_prob": _safe_float(risk.get("scout_call_edge_prob")),
        "scout_put_edge_prob": _safe_float(risk.get("scout_put_edge_prob")),
        "scout_no_trade_prob": _safe_float(risk.get("scout_no_trade_prob")),
        "sentinel_confidence": _safe_float(risk.get("sentinel_confidence")),
        "sentinel_call_relevance": _safe_float(risk.get("sentinel_call_relevance")),
        "sentinel_put_relevance": _safe_float(risk.get("sentinel_put_relevance")),
        "sentinel_no_trade_relevance": _safe_float(risk.get("sentinel_no_trade_relevance"), 1.0),
        "delta": abs(_safe_float(risk.get("delta"))),
        "implied_volatility": _safe_float(risk.get("implied_volatility")),
        "iv_rank": _safe_float(risk.get("iv_rank")),
        "moneyness": _safe_float(risk.get("moneyness")),
        "premium_pct_of_spot": _safe_float(risk.get("premium_pct_of_spot")),
        "breakeven_move_pct": _safe_float(risk.get("breakeven_move_pct")),
        "projected_move_pct": _safe_float(risk.get("projected_move_pct")),
        "extrinsic_ratio": _safe_float(risk.get("extrinsic_ratio")),
        "realized_vol_20d": _safe_float(risk.get("realized_vol_20d")),
        "atr_pct_14d": _safe_float(risk.get("atr_pct_14d")),
        "regime_is_risk_on": 1.0 if str(regime.get("mode") or "").strip().lower() == "risk_on" else 0.0,
        "regime_is_risk_off": 1.0 if str(regime.get("mode") or "").strip().lower() == "risk_off" else 0.0,
    }


def _label_heads(pick: dict[str, Any]) -> dict[str, int] | None:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    path_rules = outcomes.get("path_rules") if isinstance(outcomes.get("path_rules"), dict) else {}
    fixed_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
    friday_close = fixed_marks.get("friday_close") if isinstance(fixed_marks.get("friday_close"), dict) else {}
    friday_pnl = friday_close.get("pnl_pct_from_emission") if isinstance(friday_close, dict) else None
    friday = _safe_float(friday_pnl, default=np.nan)
    mfe = _safe_float(path_rules.get("max_favorable_excursion_pct"), default=np.nan)
    mae = _safe_float(path_rules.get("max_adverse_excursion_pct"), default=np.nan)
    take25 = bool(path_rules.get("take_profit_25_pct_before_stop_50_pct"))
    take40 = bool(path_rules.get("take_profit_40_pct_before_stop_50_pct"))
    first_hit_rule = _first_hit_rule(path_rules)

    if not np.isfinite(friday) and not np.isfinite(mfe) and not np.isfinite(mae):
        return None

    harvest = int(
        take25
        or take40
        or "take_profit" in first_hit_rule
        or (np.isfinite(friday) and friday >= 0.25)
        or (np.isfinite(mfe) and mfe >= 0.30)
    )
    sell = int(
        (np.isfinite(friday) and friday <= -0.25)
        or (np.isfinite(mae) and mae <= -0.40)
        or (np.isfinite(mae) and np.isfinite(friday) and mae <= -0.25 and friday < 0.0)
    )
    hold = int(
        not harvest
        and not sell
        and np.isfinite(friday)
        and friday >= 0.05
    )
    return {"hold": hold, "harvest": harvest, "sell": sell}


def _training_rows(ledger_payload: dict[str, Any]) -> list[TrainingRow]:
    rows: list[TrainingRow] = []
    entries = ledger_payload.get("entries") if isinstance(ledger_payload.get("entries"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        picks = entry.get("picks") if isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            labels = _label_heads(pick)
            if labels is None:
                continue
            contract_symbol = str(pick.get("contract_symbol") or "").strip().upper()
            if not contract_symbol:
                continue
            feature_map = _extract_feature_map(entry, pick)
            rows.append(
                TrainingRow(
                    run_generated_at_utc=str(entry.get("run_generated_at_utc") or pick.get("run_generated_at_utc") or ""),
                    contract_symbol=contract_symbol,
                    feature_map=feature_map,
                    labels=labels,
                    metadata={
                        "lane": pick.get("lane"),
                        "symbol": pick.get("symbol"),
                        "option_type": pick.get("option_type"),
                    },
                )
            )
    rows.sort(key=lambda row: row.run_generated_at_utc)
    return rows


def _latest_reference_payload(ledger_payload: dict[str, Any], generated_at_utc: str) -> dict[str, Any]:
    latest_by_contract: dict[str, dict[str, Any]] = {}
    entries = ledger_payload.get("entries") if isinstance(ledger_payload.get("entries"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        regime = entry.get("regime") if isinstance(entry.get("regime"), dict) else {}
        picks = entry.get("picks") if isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            contract_symbol = str(pick.get("contract_symbol") or "").strip().upper()
            if not contract_symbol:
                continue
            scores = pick.get("scores") if isinstance(pick.get("scores"), dict) else {}
            risk = pick.get("risk_features") if isinstance(pick.get("risk_features"), dict) else {}
            latest_by_contract[contract_symbol] = {
                "contract_symbol": contract_symbol,
                "symbol": pick.get("symbol"),
                "option_type": pick.get("option_type"),
                "expiry": pick.get("expiry"),
                "strike": pick.get("strike"),
                "days_to_expiry": pick.get("days_to_expiry"),
                "lane": pick.get("lane"),
                "lane_reason": pick.get("lane_reason"),
                "run_generated_at_utc": pick.get("run_generated_at_utc") or entry.get("run_generated_at_utc"),
                "entry_cost_per_contract": (pick.get("emission_quote") or {}).get("contract_cost"),
                "regime_mode": regime.get("mode"),
                "regime_bias": regime.get("bias"),
                "scores": {
                    key: scores.get(key)
                    for key in (
                        "final_candidate_score",
                        "forge_score",
                        "expected_edge_after_friction_pct",
                        "expected_option_return_pct_model",
                        "prob_no_trade",
                        "prob_fill_quality_ok",
                        "prob_positive_option_pnl",
                        "path_holding_quality_score",
                        "path_early_profit_take_prob",
                        "path_decay_risk",
                    )
                },
                "risk_features": {
                    key: risk.get(key)
                    for key in (
                        "delta",
                        "implied_volatility",
                        "iv_rank",
                        "moneyness",
                        "premium_pct_of_spot",
                        "breakeven_move_pct",
                        "projected_move_pct",
                        "extrinsic_ratio",
                        "realized_vol_20d",
                        "atr_pct_14d",
                        "scout_call_edge_prob",
                        "scout_put_edge_prob",
                        "scout_no_trade_prob",
                        "sentinel_confidence",
                        "sentinel_call_relevance",
                        "sentinel_put_relevance",
                        "sentinel_no_trade_relevance",
                    )
                },
            }
    return {
        "artifact": "position_exit_reference",
        "generated_at_utc": generated_at_utc,
        "contracts": len(latest_by_contract),
        "by_contract_symbol": latest_by_contract,
    }


def _fit_head(X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray, y_val: np.ndarray) -> dict[str, Any]:
    positive_rate = round(float(np.mean(y_train)), 4) if len(y_train) else 0.0
    if len(np.unique(y_train)) < 2:
        constant_prob = positive_rate
        auc = None if len(np.unique(y_val)) < 2 else 0.5
        return {
            "kind": "constant",
            "probability": constant_prob,
            "positive_rate": positive_rate,
            "validation_auc": auc,
            "train_rows": int(len(y_train)),
            "validation_rows": int(len(y_val)),
        }

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val) if len(X_val) else np.empty((0, X_train.shape[1]))
    model = LogisticRegression(max_iter=2000, class_weight="balanced", solver="lbfgs")
    model.fit(X_train_scaled, y_train)
    auc = None
    if len(X_val) and len(np.unique(y_val)) >= 2:
        probs = model.predict_proba(X_val_scaled)[:, 1]
        auc = round(float(roc_auc_score(y_val, probs)), 4)
    return {
        "kind": "logistic_regression",
        "intercept": round(float(model.intercept_[0]), 6),
        "coefficients": {
            name: round(float(weight), 6)
            for name, weight in zip(FEATURE_NAMES, model.coef_[0])
        },
        "scaler_mean": {
            name: round(float(value), 6)
            for name, value in zip(FEATURE_NAMES, scaler.mean_)
        },
        "scaler_scale": {
            name: round(float(value if value != 0 else 1.0), 6)
            for name, value in zip(FEATURE_NAMES, scaler.scale_)
        },
        "positive_rate": positive_rate,
        "validation_auc": auc,
        "train_rows": int(len(y_train)),
        "validation_rows": int(len(y_val)),
    }


def train_exit_model(
    ledger_path: Path,
    model_output: Path,
    public_model_output: Path,
    reference_output: Path,
) -> dict[str, Any]:
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    generated_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows = _training_rows(payload)
    if len(rows) < 150:
        raise RuntimeError(f"Not enough completed pick rows for exit-model training ({len(rows)} rows).")

    X = np.asarray([[row.feature_map[name] for name in FEATURE_NAMES] for row in rows], dtype=float)
    split_idx = max(int(len(rows) * 0.8), 1)
    split_idx = min(split_idx, len(rows) - 1)
    X_train, X_val = X[:split_idx], X[split_idx:]

    heads: dict[str, Any] = {}
    label_summary: dict[str, Any] = {}
    for head_name in ("hold", "harvest", "sell"):
        y = np.asarray([row.labels[head_name] for row in rows], dtype=int)
        y_train, y_val = y[:split_idx], y[split_idx:]
        fitted = _fit_head(X_train, y_train, X_val, y_val)
        fitted["label_definition"] = HEAD_DEFINITIONS[head_name]
        heads[head_name] = fitted
        label_summary[head_name] = {
            "positives": int(np.sum(y)),
            "rows": int(len(y)),
            "positive_rate": round(float(np.mean(y)), 4),
        }

    artifact = {
        "artifact": "position_exit_model",
        "version": 1,
        "trained_at_utc": generated_at_utc,
        "source_artifact": str(ledger_path.relative_to(REPO_ROOT)),
        "feature_names": FEATURE_NAMES,
        "training_rows": int(len(rows)),
        "train_rows": int(len(X_train)),
        "validation_rows": int(len(X_val)),
        "heads": heads,
        "label_summary": label_summary,
        "policy": {
            "profit_harvest_min_open_pl_pct": 0.15,
            "profit_harvest_head_min_prob": 0.60,
            "sell_head_min_prob": 0.55,
            "sell_head_loss_override_pct": -0.30,
            "expiry_pressure_dte": 3,
        },
        "notes": [
            "This is an entry-conditioned lightweight model for open long option exits.",
            "Live position PnL, DTE, spread, and regime alignment are applied as post-model overlays inside the Cloudflare function.",
        ],
    }

    reference_payload = _latest_reference_payload(payload, generated_at_utc)
    model_output.parent.mkdir(parents=True, exist_ok=True)
    public_model_output.parent.mkdir(parents=True, exist_ok=True)
    reference_output.parent.mkdir(parents=True, exist_ok=True)
    rendered_model = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    rendered_reference = json.dumps(reference_payload, indent=2, sort_keys=True) + "\n"
    model_output.write_text(rendered_model, encoding="utf-8")
    public_model_output.write_text(rendered_model, encoding="utf-8")
    reference_output.write_text(rendered_reference, encoding="utf-8")

    date_stamp = generated_at_utc[:10]
    dated_model_output = public_model_output.with_name(f"position_exit_model_{date_stamp}.json")
    dated_reference_output = reference_output.with_name(f"position_exit_reference_{date_stamp}.json")
    dated_model_output.write_text(rendered_model, encoding="utf-8")
    dated_reference_output.write_text(rendered_reference, encoding="utf-8")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the Orographic lightweight open-position exit model.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--model-output", type=Path, default=DEFAULT_MODEL_OUTPUT)
    parser.add_argument("--public-model-output", type=Path, default=DEFAULT_PUBLIC_MODEL_OUTPUT)
    parser.add_argument("--reference-output", type=Path, default=DEFAULT_REFERENCE_OUTPUT)
    args = parser.parse_args()

    artifact = train_exit_model(
        ledger_path=args.ledger if args.ledger.is_absolute() else REPO_ROOT / args.ledger,
        model_output=args.model_output if args.model_output.is_absolute() else REPO_ROOT / args.model_output,
        public_model_output=(
            args.public_model_output
            if args.public_model_output.is_absolute()
            else REPO_ROOT / args.public_model_output
        ),
        reference_output=(
            args.reference_output
            if args.reference_output.is_absolute()
            else REPO_ROOT / args.reference_output
        ),
    )
    print(json.dumps(artifact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
