"""
Train the Orographic second-stage option payoff model.

The training set is built from strict-real replay output so labels describe
tradable option outcomes instead of only stock direction.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from engine.backtest.options_provider import HistoricalOptionsProvider
from engine.orographic.forge import _breakeven_move_pct, _candidate_moneyness
from engine.orographic.payoff_model import FEATURE_COLS, feature_matrix
from engine.orographic.schemas import ContractCandidate, MarketRegime

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover - exercised only in minimal local envs
    lgb = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger(__name__)

DEFAULT_INPUT = Path("output/backtest_results_2026-04-17_blended_target_dte_7_14_strict_real_execution_stress_12mo.json")
DEFAULT_MODEL_PATH = Path("engine/orographic/models/payoff_model.pkl")
DEFAULT_REPORT_PATH = Path("output/payoff_model_training_report_2026-04-18.json")
DEFAULT_OPTIONS_DATA_DIR = Path("engine/data/options/blended")


@dataclass
class TradeExample:
    candidate: ContractCandidate
    entry_date: date
    exit_date: date | None
    entry_spot: float
    exit_spot: float | None
    pnl_pct: float
    prob_positive_option_pnl: int
    expected_option_return_pct: float
    prob_exceeds_breakeven: int
    max_favorable_excursion_before_expiry: float
    adverse_excursion_risk: float


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        result = float(value)
        if not np.isfinite(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _contract_symbol(trade: dict[str, Any]) -> str:
    if trade.get("contract_symbol"):
        return str(trade["contract_symbol"])
    symbol = str(trade.get("symbol", "UNK")).upper()
    expiry = date.fromisoformat(str(trade["expiry"]))
    option_char = "C" if trade.get("option_type") == "call" else "P"
    strike = int(round(_safe_float(trade.get("strike")) * 1000))
    return f"{symbol}{expiry.strftime('%y%m%d')}{option_char}{strike:08d}"


def _scout_score_from_trade(trade: dict[str, Any]) -> float:
    if trade.get("scout_score") is not None:
        return _clip(_safe_float(trade.get("scout_score")), -1.0, 1.0)
    heuristic = _clip(_safe_float(trade.get("pre_payoff_forge_score"), _safe_float(trade.get("forge_score"), 0.5)), 0.0, 1.0)
    if trade.get("option_type") == "put":
        return _clip(1.0 - 2.0 * heuristic, -1.0, 1.0)
    return _clip(2.0 * heuristic - 1.0, -1.0, 1.0)


def _candidate_from_trade(trade: dict[str, Any]) -> ContractCandidate:
    option_type = str(trade.get("option_type", "call"))
    entry_spot = _safe_float(trade.get("entry_spot"))
    strike = _safe_float(trade.get("strike"))
    entry_price = _safe_float(trade.get("entry_price"))
    spread_pct = _safe_float(trade.get("entry_spread_pct"), 0.18)
    open_interest = _safe_int(trade.get("entry_open_interest"), 0)
    volume = _safe_int(trade.get("entry_volume"), 0)
    fallback_moneyness = _candidate_moneyness(option_type, entry_spot, strike)
    fallback_breakeven = _breakeven_move_pct(option_type, entry_spot, strike, entry_price)
    forge_score = _safe_float(trade.get("pre_payoff_forge_score"), _safe_float(trade.get("forge_score"), 0.5))
    return ContractCandidate(
        symbol=str(trade.get("symbol", "")).upper(),
        contract_symbol=_contract_symbol(trade),
        option_type=option_type,
        expiry=str(trade.get("expiry")),
        strike=strike,
        bid=max(entry_price * max(1.0 - spread_pct, 0.01), 0.01),
        ask=entry_price,
        last=entry_price,
        premium=entry_price,
        contract_cost=entry_price * 100.0,
        spread_pct=spread_pct,
        open_interest=open_interest,
        volume=volume,
        implied_volatility=_safe_float(trade.get("implied_volatility"), 0.35),
        delta=trade.get("delta"),
        moneyness=_safe_float(trade.get("moneyness"), fallback_moneyness),
        projected_move_pct=_safe_float(trade.get("projected_move_pct"), 0.0),
        breakeven_move_pct=_safe_float(trade.get("breakeven_move_pct"), fallback_breakeven),
        expected_return_pct=_safe_float(trade.get("expected_return_pct"), 0.0),
        extrinsic_ratio=_safe_float(trade.get("extrinsic_ratio"), 1.0),
        scout_score=_scout_score_from_trade(trade),
        forge_score=forge_score,
        spread_cost=entry_price,
        allocation_weight=_safe_float(trade.get("allocation_weight"), 1.0),
        iv_rank=_safe_float(trade.get("iv_rank"), 0.5),
        entry_data_source=str(trade.get("entry_data_source", "real_chain")),
        entry_quote_type=str(trade.get("entry_quote_type", "ask")),
    )


def _breakeven_label(trade: dict[str, Any]) -> int:
    option_type = str(trade.get("option_type", "call"))
    exit_spot = _safe_float(trade.get("exit_spot"))
    strike = _safe_float(trade.get("strike"))
    entry_price = _safe_float(trade.get("entry_price"))
    if exit_spot <= 0 or strike <= 0 or entry_price <= 0:
        return int(_safe_float(trade.get("pnl_pct")) > 0)
    if option_type == "put":
        return int(exit_spot <= strike - entry_price)
    return int(exit_spot >= strike + entry_price)


def _quote_return_path(
    trade: dict[str, Any],
    options_provider: HistoricalOptionsProvider | None,
) -> tuple[float, float, int]:
    realized = _safe_float(trade.get("pnl_pct"))
    entry_price = _safe_float(trade.get("entry_price"))
    if options_provider is None or entry_price <= 0:
        return max(0.0, realized), min(0.0, realized), 0

    symbol = str(trade.get("symbol", "")).upper()
    option_char = "C" if trade.get("option_type") == "call" else "P"
    strike = round(_safe_float(trade.get("strike")), 2)
    try:
        entry_date = date.fromisoformat(str(trade["entry_date"]))
        exit_date = date.fromisoformat(str(trade["exit_date"]))
        expiry = date.fromisoformat(str(trade["expiry"]))
    except (KeyError, TypeError, ValueError):
        return max(0.0, realized), min(0.0, realized), 0

    end_date = min(exit_date, expiry)
    returns: list[float] = []
    exact_marks = 0
    for quote_date in pd.date_range(entry_date, end_date, freq="D").date:
        if not options_provider.has_real_coverage(symbol, quote_date):
            continue
        chain, source = options_provider.get_chain_with_source(symbol, quote_date, fallback_spot=0.0, fallback_vol=0.35)
        if source != "real_chain" or chain.empty:
            continue
        match = chain[
            (chain["option_type"] == option_char)
            & (pd.to_datetime(chain["expire_date"], errors="coerce").dt.date == expiry)
            & (pd.to_numeric(chain["strike"], errors="coerce").round(2) == strike)
        ]
        if match.empty:
            continue
        bid = _safe_float(match.iloc[0].get("bid"), 0.0)
        if bid <= 0:
            continue
        returns.append(bid / entry_price - 1.0)
        exact_marks += 1

    returns.append(realized)
    returns.append(0.0)
    return max(returns), min(returns), exact_marks


def load_examples(
    input_paths: list[Path],
    *,
    options_data_dir: Path | None = None,
    return_cap: float = 5.0,
) -> tuple[list[TradeExample], dict[str, Any]]:
    options_provider = None
    if options_data_dir is not None and options_data_dir.exists():
        options_provider = HistoricalOptionsProvider(options_data_dir)

    examples: list[TradeExample] = []
    seen: set[tuple[Any, ...]] = set()
    exact_mark_count = 0
    for path in input_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for trade in data.get("all_trades", []):
            key = (
                trade.get("symbol"),
                trade.get("option_type"),
                trade.get("strike"),
                trade.get("expiry"),
                trade.get("entry_date"),
                trade.get("exit_date"),
            )
            if key in seen:
                continue
            seen.add(key)
            try:
                entry_date = date.fromisoformat(str(trade["entry_date"]))
            except (KeyError, TypeError, ValueError):
                continue
            exit_date = None
            if trade.get("exit_date"):
                try:
                    exit_date = date.fromisoformat(str(trade["exit_date"]))
                except ValueError:
                    exit_date = None
            pnl_pct = _safe_float(trade.get("pnl_pct"))
            mfe, adverse, marks = _quote_return_path(trade, options_provider)
            exact_mark_count += marks
            examples.append(
                TradeExample(
                    candidate=_candidate_from_trade(trade),
                    entry_date=entry_date,
                    exit_date=exit_date,
                    entry_spot=_safe_float(trade.get("entry_spot")),
                    exit_spot=_safe_float(trade.get("exit_spot"), None),
                    pnl_pct=pnl_pct,
                    prob_positive_option_pnl=int(pnl_pct > 0.0),
                    expected_option_return_pct=_clip(pnl_pct, -1.0, return_cap),
                    prob_exceeds_breakeven=_breakeven_label(trade),
                    max_favorable_excursion_before_expiry=_clip(mfe, -1.0, return_cap),
                    adverse_excursion_risk=_clip(adverse, -1.0, return_cap),
                )
            )

    metadata = {
        "input_files": [str(path) for path in input_paths],
        "deduplicated_examples": len(examples),
        "exact_quote_marks_used": exact_mark_count,
        "options_data_dir": str(options_data_dir) if options_data_dir else None,
    }
    return examples, metadata


def _classifier(random_state: int = 42) -> Any:
    if lgb is None:
        return DummyClassifier(strategy="prior")
    return lgb.LGBMClassifier(
        n_estimators=240,
        learning_rate=0.04,
        max_depth=4,
        num_leaves=15,
        subsample=0.85,
        colsample_bytree=0.85,
        min_child_samples=20,
        class_weight="balanced",
        random_state=random_state,
        verbose=-1,
    )


def _regressor(random_state: int = 42) -> Any:
    if lgb is None:
        return DummyRegressor(strategy="mean")
    return lgb.LGBMRegressor(
        n_estimators=260,
        learning_rate=0.04,
        max_depth=4,
        num_leaves=15,
        subsample=0.85,
        colsample_bytree=0.85,
        min_child_samples=20,
        random_state=random_state,
        verbose=-1,
    )


def _fit_classifier(X: np.ndarray, y: np.ndarray) -> Pipeline:
    estimator = _classifier()
    if len(set(y.tolist())) < 2:
        estimator = DummyClassifier(strategy="constant", constant=int(y[0]) if len(y) else 0)
    model = Pipeline([("scaler", RobustScaler()), ("model", estimator)])
    model.fit(X, y)
    return model


def _fit_regressor(X: np.ndarray, y: np.ndarray) -> Pipeline:
    model = Pipeline([("scaler", RobustScaler()), ("model", _regressor())])
    model.fit(X, y)
    return model


def _positive_proba(model: Pipeline, X: np.ndarray) -> np.ndarray:
    probs = model.predict_proba(X)
    if probs.ndim == 2 and probs.shape[1] > 1:
        return probs[:, 1]
    return np.asarray(model.predict(X), dtype=float)


def _cv_report(X: np.ndarray, labels: dict[str, np.ndarray], dates: list[date]) -> dict[str, Any]:
    if len(X) < 80:
        return {"folds": 0, "reason": "insufficient_examples"}

    order = np.argsort(np.array([d.toordinal() for d in dates]))
    X_sorted = X[order]
    y_positive = labels["prob_positive_option_pnl"][order]
    y_breakeven = labels["prob_exceeds_breakeven"][order]
    y_return = labels["expected_option_return_pct"][order]
    tscv = TimeSeriesSplit(n_splits=min(5, max(2, len(X) // 120)))
    positive_auc: list[float] = []
    breakeven_auc: list[float] = []
    return_mae: list[float] = []

    for train_idx, val_idx in tscv.split(X_sorted):
        X_train, X_val = X_sorted[train_idx], X_sorted[val_idx]
        pos_train, pos_val = y_positive[train_idx], y_positive[val_idx]
        be_train, be_val = y_breakeven[train_idx], y_breakeven[val_idx]
        ret_train, ret_val = y_return[train_idx], y_return[val_idx]

        pos_model = _fit_classifier(X_train, pos_train)
        be_model = _fit_classifier(X_train, be_train)
        ret_model = _fit_regressor(X_train, ret_train)

        if len(set(pos_val.tolist())) > 1:
            positive_auc.append(float(roc_auc_score(pos_val, _positive_proba(pos_model, X_val))))
        if len(set(be_val.tolist())) > 1:
            breakeven_auc.append(float(roc_auc_score(be_val, _positive_proba(be_model, X_val))))
        return_mae.append(float(mean_absolute_error(ret_val, ret_model.predict(X_val))))

    return {
        "folds": int(tscv.n_splits),
        "positive_pnl_auc_mean": round(float(np.mean(positive_auc)), 4) if positive_auc else None,
        "breakeven_auc_mean": round(float(np.mean(breakeven_auc)), 4) if breakeven_auc else None,
        "expected_return_mae_mean": round(float(np.mean(return_mae)), 4) if return_mae else None,
    }


def _fit_bundle(X: np.ndarray, labels: dict[str, np.ndarray]) -> dict[str, Any]:
    return {
        "positive_classifier": _fit_classifier(X, labels["prob_positive_option_pnl"]),
        "breakeven_classifier": _fit_classifier(X, labels["prob_exceeds_breakeven"]),
        "expected_return_regressor": _fit_regressor(X, labels["expected_option_return_pct"]),
        "mfe_regressor": _fit_regressor(X, labels["max_favorable_excursion_before_expiry"]),
        "adverse_regressor": _fit_regressor(X, labels["adverse_excursion_risk"]),
    }


def train(
    input_paths: list[Path],
    *,
    output_model: Path = DEFAULT_MODEL_PATH,
    output_report: Path = DEFAULT_REPORT_PATH,
    options_data_dir: Path | None = DEFAULT_OPTIONS_DATA_DIR,
    min_side_examples: int = 75,
) -> dict[str, Any]:
    examples, source_metadata = load_examples(input_paths, options_data_dir=options_data_dir)
    if len(examples) < 50:
        raise RuntimeError(f"Need at least 50 strict-real trades to train payoff model; found {len(examples)}")

    neutral = MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY")
    X = feature_matrix([example.candidate for example in examples], neutral, feature_cols=FEATURE_COLS)
    labels = {
        "prob_positive_option_pnl": np.array([example.prob_positive_option_pnl for example in examples], dtype=int),
        "expected_option_return_pct": np.array([example.expected_option_return_pct for example in examples], dtype=float),
        "prob_exceeds_breakeven": np.array([example.prob_exceeds_breakeven for example in examples], dtype=int),
        "max_favorable_excursion_before_expiry": np.array([example.max_favorable_excursion_before_expiry for example in examples], dtype=float),
        "adverse_excursion_risk": np.array([example.adverse_excursion_risk for example in examples], dtype=float),
    }
    dates = [example.entry_date for example in examples]
    sides = np.array([example.candidate.option_type for example in examples], dtype=object)

    artifact: dict[str, Any] = {
        "version": 1,
        "feature_cols": FEATURE_COLS,
        "global": _fit_bundle(X, labels),
        "by_side": {},
        "metadata": {
            **source_metadata,
            "trained_at": date.today().isoformat(),
            "min_side_examples": min_side_examples,
            "label_means": {name: round(float(values.mean()), 4) for name, values in labels.items()},
        },
    }

    for side in ("call", "put"):
        side_idx = np.where(sides == side)[0]
        if len(side_idx) < min_side_examples:
            continue
        artifact["by_side"][side] = _fit_bundle(
            X[side_idx],
            {name: values[side_idx] for name, values in labels.items()},
        )

    side_counts = {side: int((sides == side).sum()) for side in ("call", "put")}
    report = {
        "training_examples": len(examples),
        "side_counts": side_counts,
        "positive_pnl_rate": round(float(labels["prob_positive_option_pnl"].mean()), 4),
        "breakeven_rate": round(float(labels["prob_exceeds_breakeven"].mean()), 4),
        "avg_expected_option_return_pct": round(float(labels["expected_option_return_pct"].mean()), 4),
        "avg_mfe_before_expiry": round(float(labels["max_favorable_excursion_before_expiry"].mean()), 4),
        "avg_adverse_excursion_risk": round(float(labels["adverse_excursion_risk"].mean()), 4),
        "side_models_trained": sorted(artifact["by_side"].keys()),
        "feature_cols": FEATURE_COLS,
        "cross_validation": _cv_report(X, labels, dates),
        "source_metadata": source_metadata,
    }

    output_model.parent.mkdir(parents=True, exist_ok=True)
    output_report.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_model)
    output_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info("Payoff model saved to %s", output_model)
    log.info("Training report saved to %s", output_report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train option payoff model from strict-real replay output")
    parser.add_argument("--input", action="append", type=Path, default=None, help="Backtest JSON path; may be repeated")
    parser.add_argument("--output-model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--output-report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--options-data-dir", type=Path, default=DEFAULT_OPTIONS_DATA_DIR)
    parser.add_argument("--min-side-examples", type=int, default=75)
    args = parser.parse_args()

    input_paths = args.input or [DEFAULT_INPUT]
    missing = [path for path in input_paths if not path.exists()]
    if missing:
        for path in missing:
            log.error("Missing input file: %s", path)
        sys.exit(1)

    report = train(
        input_paths,
        output_model=args.output_model,
        output_report=args.output_report,
        options_data_dir=args.options_data_dir,
        min_side_examples=args.min_side_examples,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
