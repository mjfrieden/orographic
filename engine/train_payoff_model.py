"""
Train the Orographic second-stage option payoff model.

The training set is built from strict-real replay output so labels describe
tradable option outcomes instead of only stock direction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.linear_model import LogisticRegression, QuantileRegressor, Ridge
from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error, mean_pinball_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from engine.backtest.options_provider import HistoricalOptionsProvider
from engine.backtest.results import build_option_outcome_dataset_summary, canonicalize_option_outcome_row
from engine.orographic.forge import _breakeven_move_pct, _candidate_moneyness
from engine.orographic.payoff_model import AveragedClassifier, AveragedRegressor, FEATURE_COLS, feature_matrix
from engine.orographic.schemas import ContractCandidate, MarketRegime
from engine.orographic.validation import purged_date_splits

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover - exercised only in minimal local envs
    lgb = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger(__name__)

DEFAULT_INPUT_CANDIDATES = [
    Path("output/option_outcomes_live_recommendations.json"),
    Path("output/option_outcomes_latest.json"),
    Path("output/option_outcomes_12mo.json"),
    Path("output/option_outcomes_2026-04-17_blended_target_dte_7_14_strict_real_execution_stress_12mo.json"),
    Path("output/backtest_results_2026-04-17_blended_target_dte_7_14_strict_real_execution_stress_12mo.json"),
]
DEFAULT_MODEL_PATH = Path("engine/orographic/models/payoff_model.pkl")
DEFAULT_REPORT_PATH = Path("output/payoff_model_training_report_2026-04-18.json")
DEFAULT_MODEL_CARD_PATH = Path("engine/orographic/models/payoff_model_card.json")
DEFAULT_OPTIONS_DATA_DIR = Path("engine/data/options/blended")
MODEL_FAMILIES = ("linear", "tree", "ensemble")


@dataclass
class TradeExample:
    candidate: ContractCandidate
    entry_date: date
    exit_date: date | None
    entry_spot: float
    exit_spot: float | None
    regime_bucket: str
    pnl_pct: float
    prob_positive_option_pnl: int
    prob_no_trade: int
    prob_fill_quality_ok: int
    expected_option_return_pct: float
    prob_exceeds_breakeven: int
    max_favorable_excursion_before_expiry: float
    adverse_excursion_risk: float


def _artifact_rows(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    artifact = str(data.get("artifact") or "").strip()
    if artifact == "option_outcome_dataset":
        rows = data.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)], artifact
        return [], artifact
    rows = data.get("all_trades")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)], artifact or "backtest_results"
    return [], artifact or "unknown"


def default_input_paths() -> list[Path]:
    existing = [path for path in DEFAULT_INPUT_CANDIDATES if path.exists()]
    if existing:
        strict = [path for path in existing if _is_strict_executable_dataset(path)]
        return strict or existing
    return [DEFAULT_INPUT_CANDIDATES[0]]


def _is_strict_executable_dataset(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(payload, dict)
        and payload.get("artifact") == "option_outcome_dataset"
        and str(payload.get("label_policy") or "").startswith("strict_executable_quote_or_fill_v")
    )


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        scout_call_edge_prob=trade.get("scout_call_edge_prob"),
        scout_put_edge_prob=trade.get("scout_put_edge_prob"),
        scout_no_trade_prob=trade.get("scout_no_trade_prob"),
        spread_cost=entry_price,
        allocation_weight=_safe_float(trade.get("allocation_weight"), 1.0),
        iv_rank=_safe_float(trade.get("iv_rank"), 0.5),
        surface_atm_iv=trade.get("surface_atm_iv"),
        surface_skew_slope=trade.get("surface_skew_slope"),
        surface_curvature=trade.get("surface_curvature"),
        surface_put_call_wing_skew=trade.get("surface_put_call_wing_skew"),
        surface_term_slope_30d=trade.get("surface_term_slope_30d"),
        surface_fit_rmse=trade.get("surface_fit_rmse"),
        surface_observation_count=trade.get("surface_observation_count"),
        iv_relative_to_atm=trade.get("iv_relative_to_atm"),
        iv_minus_realized_vol=trade.get("iv_minus_realized_vol"),
        quote_mid=trade.get("quote_mid"),
        quote_spread_dollars=trade.get("quote_spread_dollars"),
        chain_snapshot_at_utc=trade.get("chain_snapshot_at_utc"),
        last_trade_age_seconds=trade.get("last_trade_age_seconds"),
        entry_data_source=str(trade.get("entry_data_source", "real_chain")),
        entry_quote_type=str(trade.get("entry_quote_type", "ask")),
        realized_vol_20d=trade.get("realized_vol_20d"),
        atr_pct_14d=trade.get("atr_pct_14d"),
        premium_pct_of_spot=_safe_float(
            trade.get("premium_pct_of_spot"),
            entry_price / entry_spot if entry_spot > 0 else 0.0,
        ),
        vrp_gap=_safe_float(
            trade.get("vrp_gap"),
            max(_safe_float(trade.get("implied_volatility"), 0.35) - _safe_float(trade.get("realized_vol_20d")), 0.0),
        ),
        expected_edge_after_friction_pct=trade.get("expected_edge_after_friction_pct"),
        sentinel_event_type=trade.get("sentinel_event_type"),
        sentinel_holding_window_fit=trade.get("sentinel_holding_window_fit"),
        sentinel_holding_window_label=trade.get("sentinel_holding_window_label"),
        sentinel_decay_half_life=trade.get("sentinel_decay_half_life"),
        sentinel_time_horizon=trade.get("sentinel_time_horizon"),
        sentinel_confidence=trade.get("sentinel_confidence"),
        sentinel_source_reliability=trade.get("sentinel_source_reliability"),
        sentinel_novelty=trade.get("sentinel_novelty"),
        sentinel_call_relevance=trade.get("sentinel_call_relevance"),
        sentinel_put_relevance=trade.get("sentinel_put_relevance"),
        sentinel_no_trade_relevance=trade.get("sentinel_no_trade_relevance"),
        sentinel_spot_effect=trade.get("sentinel_spot_effect"),
        sentinel_iv_effect=trade.get("sentinel_iv_effect"),
    )


def _breakeven_label(trade: dict[str, Any]) -> int:
    if trade.get("breakeven_after_friction") is not None:
        return int(bool(trade.get("breakeven_after_friction")))
    option_type = str(trade.get("option_type", "call"))
    exit_spot = _safe_float(trade.get("exit_spot"))
    strike = _safe_float(trade.get("strike"))
    entry_price = _safe_float(trade.get("entry_price"))
    if exit_spot <= 0 or strike <= 0 or entry_price <= 0:
        return int(_safe_float(trade.get("pnl_pct")) > 0)
    if option_type == "put":
        return int(exit_spot <= strike - entry_price)
    return int(exit_spot >= strike + entry_price)


def _no_trade_label(trade: dict[str, Any], pnl_pct: float) -> int:
    friction_gate = trade.get("friction_gate_passed")
    if friction_gate is False:
        return 1
    if trade.get("positive_pnl_after_friction") is not None:
        return int(not bool(trade.get("positive_pnl_after_friction")))
    return int(pnl_pct <= 0.0)


def _fill_quality_label(trade: dict[str, Any]) -> int:
    spread_pct = _safe_float(trade.get("entry_spread_pct"), 1.0)
    open_interest = _safe_int(trade.get("entry_open_interest"), 0)
    volume = _safe_int(trade.get("entry_volume"), 0)
    quote_coverage = _safe_float(trade.get("options_data_coverage_pct"), 1.0)
    return int(
        spread_pct <= 0.18
        and (open_interest >= 150 or volume >= 25)
        and quote_coverage >= 0.95
    )


def _path_take_profit_label(mfe: float, threshold: float = 0.25) -> int:
    return int(mfe >= threshold)


def _quote_return_path(
    trade: dict[str, Any],
    options_provider: HistoricalOptionsProvider | None,
) -> tuple[float, float, int]:
    realized = _safe_float(trade.get("hold_period_return_after_friction_pct"), _safe_float(trade.get("pnl_pct")))
    entry_price = _safe_float(trade.get("entry_price"))
    archived_path = trade.get("archived_quote_path") if isinstance(trade.get("archived_quote_path"), dict) else {}
    archived_marks = archived_path.get("marks") if isinstance(archived_path.get("marks"), list) else []
    archived_returns = [
        _safe_float(mark.get("pnl_pct_from_emission"))
        for mark in archived_marks
        if isinstance(mark, dict) and mark.get("pnl_pct_from_emission") is not None
    ]
    archived_returns = [value for value in archived_returns if value is not None]
    if archived_returns:
        archived_returns.append(realized)
        archived_returns.append(0.0)
        return max(archived_returns), min(archived_returns), len(archived_marks)
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


def _regime_bucket_from_trade(trade: dict[str, Any]) -> str:
    explicit_mode = str(trade.get("regime_mode") or "").strip().lower()
    if explicit_mode in {"risk_on", "risk_off", "neutral"}:
        return explicit_mode

    if trade.get("regime_is_risk_on") is not None and bool(trade.get("regime_is_risk_on")):
        return "risk_on"
    if trade.get("regime_is_risk_off") is not None and bool(trade.get("regime_is_risk_off")):
        return "risk_off"

    bias_raw = trade.get("regime_bias")
    if bias_raw is not None:
        bias = _safe_float(bias_raw, 0.0)
        if bias >= 0.1:
            return "risk_on"
        if bias <= -0.1:
            return "risk_off"
        return "neutral"

    alignment_raw = trade.get("regime_alignment_score")
    if alignment_raw is not None:
        alignment = _safe_float(alignment_raw, 0.0)
        if alignment >= 0.15:
            return "aligned"
        if alignment <= -0.15:
            return "counter_regime"
        return "neutral_or_mixed"

    return "unclassified"


def _segment_summary(
    rows: list[dict[str, Any]],
    segment_getter: Any,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        segment = str(segment_getter(row) or "unclassified")
        grouped.setdefault(segment, []).append(row)

    summary: dict[str, Any] = {}
    for segment, segment_rows in sorted(grouped.items()):
        count = len(segment_rows)
        after_returns = [_safe_float(r.get("hold_period_return_after_friction_pct")) for r in segment_rows]
        before_returns = [
            _safe_float(
                r.get("hold_period_return_before_friction_pct"),
                _safe_float(r.get("raw_pnl_pct")),
            )
            for r in segment_rows
        ]
        summary[segment] = {
            "rows": count,
            "positive_pnl_after_friction_rate": round(
                sum(1 for r in segment_rows if bool(r.get("positive_pnl_after_friction"))) / count,
                4,
            ),
            "breakeven_after_friction_rate": round(
                sum(1 for r in segment_rows if bool(r.get("breakeven_after_friction"))) / count,
                4,
            ),
            "friction_flip_count": sum(1 for r in segment_rows if bool(r.get("friction_flipped_winner_to_loser"))),
            "friction_flip_rate": round(
                sum(1 for r in segment_rows if bool(r.get("friction_flipped_winner_to_loser"))) / count,
                4,
            ),
            "avg_return_after_friction_pct": round(float(np.mean(after_returns)), 4) if after_returns else 0.0,
            "avg_return_before_friction_pct": round(float(np.mean(before_returns)), 4) if before_returns else 0.0,
            "avg_friction_drag_pct": round(
                float(np.mean([_safe_float(r.get("friction_drag_pct")) for r in segment_rows])),
                4,
            ),
        }
    return summary


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
    canonical_rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    exact_mark_count = 0
    examples_with_exact_quote_path = 0
    input_artifacts: dict[str, int] = {}
    input_artifact_by_file: dict[str, str] = {}
    input_rows_by_file: dict[str, int] = {}
    for path in input_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        trade_rows, artifact_name = _artifact_rows(data)
        input_artifacts[artifact_name] = input_artifacts.get(artifact_name, 0) + 1
        input_artifact_by_file[str(path)] = artifact_name
        input_rows_by_file[str(path)] = len(trade_rows)
        for trade in trade_rows:
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
            canonical_trade = canonicalize_option_outcome_row(trade)
            try:
                entry_date = date.fromisoformat(str(canonical_trade["entry_date"]))
            except (KeyError, TypeError, ValueError):
                continue
            exit_date = None
            if canonical_trade.get("exit_date"):
                try:
                    exit_date = date.fromisoformat(str(canonical_trade["exit_date"]))
                except ValueError:
                    exit_date = None
            pnl_pct = _safe_float(canonical_trade.get("pnl_pct"))
            if canonical_trade.get("hold_period_return_after_friction_pct") is not None:
                pnl_pct = _safe_float(canonical_trade.get("hold_period_return_after_friction_pct"))
            mfe, adverse, marks = _quote_return_path(canonical_trade, options_provider)
            exact_mark_count += marks
            examples_with_exact_quote_path += int(marks > 0)
            canonical_rows.append(canonical_trade)
            examples.append(
                TradeExample(
                    candidate=_candidate_from_trade(canonical_trade),
                    entry_date=entry_date,
                    exit_date=exit_date,
                    entry_spot=_safe_float(canonical_trade.get("entry_spot")),
                    exit_spot=_safe_float(canonical_trade.get("exit_spot"), None),
                    regime_bucket=_regime_bucket_from_trade(canonical_trade),
                    pnl_pct=pnl_pct,
                    prob_positive_option_pnl=(
                        int(bool(canonical_trade.get("positive_pnl_after_friction")))
                        if canonical_trade.get("positive_pnl_after_friction") is not None
                        else int(pnl_pct > 0.0)
                    ),
                    prob_no_trade=_no_trade_label(canonical_trade, pnl_pct),
                    prob_fill_quality_ok=_fill_quality_label(canonical_trade),
                    expected_option_return_pct=_clip(pnl_pct, -1.0, return_cap),
                    prob_exceeds_breakeven=_breakeven_label(canonical_trade),
                    max_favorable_excursion_before_expiry=_clip(mfe, -1.0, return_cap),
                    adverse_excursion_risk=_clip(adverse, -1.0, return_cap),
                )
            )

    canonical_dataset_files = [
        path
        for path, artifact_name in input_artifact_by_file.items()
        if artifact_name == "option_outcome_dataset"
    ]
    legacy_result_files = [
        path
        for path, artifact_name in input_artifact_by_file.items()
        if artifact_name != "option_outcome_dataset"
    ]
    dataset_summary = build_option_outcome_dataset_summary(canonical_rows)
    side_dataset_summary = _segment_summary(canonical_rows, lambda row: row.get("option_type", "unknown"))
    regime_dataset_summary = _segment_summary(canonical_rows, _regime_bucket_from_trade)
    metadata = {
        "input_files": [str(path) for path in input_paths],
        "input_artifacts": input_artifacts,
        "input_artifact_by_file": input_artifact_by_file,
        "input_rows_by_file": input_rows_by_file,
        "canonical_dataset_files": canonical_dataset_files,
        "legacy_result_files": legacy_result_files,
        "primary_training_source_artifact": "option_outcome_dataset" if canonical_dataset_files else "backtest_results",
        "primary_training_source_files": canonical_dataset_files or [str(path) for path in input_paths],
        "dataset_summary": dataset_summary,
        "side_dataset_summary": side_dataset_summary,
        "regime_dataset_summary": regime_dataset_summary,
        "deduplicated_examples": len(examples),
        "exact_quote_marks_used": exact_mark_count,
        "examples_with_exact_quote_path": examples_with_exact_quote_path,
        "exact_quote_path_coverage_ratio": round(
            examples_with_exact_quote_path / max(len(examples), 1),
            4,
        ),
        "options_data_dir": str(options_data_dir) if options_data_dir else None,
    }
    return examples, metadata


def _tree_classifier(random_state: int = 42) -> Any:
    if lgb is None:
        return HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_depth=4,
            max_iter=180,
            min_samples_leaf=20,
            random_state=random_state,
        )
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


def _tree_regressor(random_state: int = 42) -> Any:
    if lgb is None:
        return HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_depth=4,
            max_iter=220,
            min_samples_leaf=20,
            random_state=random_state,
        )
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


def _linear_classifier(random_state: int = 42) -> Any:
    return LogisticRegression(
        C=0.7,
        class_weight="balanced",
        max_iter=1000,
        random_state=random_state,
    )


def _linear_regressor(random_state: int = 42) -> Any:
    return Ridge(alpha=1.0, random_state=random_state)


def _tree_quantile_regressor(quantile: float, random_state: int = 42) -> Any:
    if lgb is None:
        return HistGradientBoostingRegressor(
            loss="quantile",
            quantile=quantile,
            learning_rate=0.05,
            max_depth=4,
            max_iter=220,
            min_samples_leaf=20,
            random_state=random_state,
        )
    return lgb.LGBMRegressor(
        objective="quantile",
        alpha=quantile,
        n_estimators=260,
        learning_rate=0.04,
        max_depth=4,
        num_leaves=15,
        min_child_samples=20,
        random_state=random_state,
        verbose=-1,
    )


def _fit_quantile_regressor(
    X: np.ndarray,
    y: np.ndarray,
    quantile: float,
    sample_weight: np.ndarray | None = None,
    *,
    family: str = "tree",
) -> Any:
    if family == "ensemble":
        return AveragedRegressor([
            _fit_quantile_regressor(X, y, quantile, sample_weight, family="linear"),
            _fit_quantile_regressor(X, y, quantile, sample_weight, family="tree"),
        ])
    estimator = (
        QuantileRegressor(quantile=quantile, alpha=0.01, solver="highs")
        if family == "linear"
        else _tree_quantile_regressor(quantile)
    )
    model = Pipeline([("scaler", RobustScaler()), ("model", estimator)])
    fit_kwargs = {"model__sample_weight": sample_weight} if sample_weight is not None else {}
    model.fit(X, y, **fit_kwargs)
    return model


def _fit_classifier(
    X: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray | None = None,
    *,
    family: str = "tree",
) -> Any:
    if family == "ensemble":
        return AveragedClassifier(
            [
                _fit_classifier(X, y, sample_weight, family="linear"),
                _fit_classifier(X, y, sample_weight, family="tree"),
            ]
        )
    estimator = _linear_classifier() if family == "linear" else _tree_classifier()
    if len(set(y.tolist())) < 2:
        estimator = DummyClassifier(strategy="constant", constant=int(y[0]) if len(y) else 0)
    model = Pipeline([("scaler", RobustScaler()), ("model", estimator)])
    fit_kwargs = {"model__sample_weight": sample_weight} if sample_weight is not None else {}
    model.fit(X, y, **fit_kwargs)
    return model


def _fit_regressor(
    X: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray | None = None,
    *,
    family: str = "tree",
) -> Any:
    if family == "ensemble":
        return AveragedRegressor(
            [
                _fit_regressor(X, y, sample_weight, family="linear"),
                _fit_regressor(X, y, sample_weight, family="tree"),
            ]
        )
    model = Pipeline([("scaler", RobustScaler()), ("model", _linear_regressor() if family == "linear" else _tree_regressor())])
    fit_kwargs = {"model__sample_weight": sample_weight} if sample_weight is not None else {}
    model.fit(X, y, **fit_kwargs)
    return model


def _positive_proba(model: Pipeline, X: np.ndarray) -> np.ndarray:
    probs = model.predict_proba(X)
    if probs.ndim == 2 and probs.shape[1] > 1:
        return probs[:, 1]
    return np.asarray(model.predict(X), dtype=float)


def _probability_buckets(probs: np.ndarray, y: np.ndarray, buckets: int = 8) -> list[dict[str, Any]]:
    frame = pd.DataFrame({"prob": probs, "label": y}).replace([np.inf, -np.inf], np.nan).dropna()
    if frame.empty:
        return []
    q = min(buckets, max(2, len(frame) // 20))
    frame["bucket"] = pd.qcut(frame["prob"], q=q, duplicates="drop")
    rows: list[dict[str, Any]] = []
    for bucket, group in frame.groupby("bucket", observed=True):
        rows.append(
            {
                "bucket": str(bucket),
                "rows": int(len(group)),
                "mean_pred_prob": round(float(group["prob"].mean()), 4),
                "realized_rate": round(float(group["label"].mean()), 4),
            }
        )
    return rows


def _balanced_sample_weight(sides: np.ndarray, y: np.ndarray | None = None) -> np.ndarray:
    weights = np.ones(len(sides), dtype=float)
    side_counts = Counter(str(side) for side in sides.tolist())
    for i, side in enumerate(sides.tolist()):
        weights[i] *= len(sides) / max(len(side_counts), 1) / max(side_counts[str(side)], 1)
    if y is not None:
        label_counts = Counter(int(v) for v in y.tolist())
        for i, label in enumerate(y.tolist()):
            weights[i] *= len(y) / max(len(label_counts), 1) / max(label_counts[int(label)], 1)
    return weights / max(float(np.mean(weights)), 1e-9)


def _segment_metric_report(
    y_true: np.ndarray,
    probs: np.ndarray,
    segments: np.ndarray,
    *,
    min_rows: int = 20,
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    segment_names = sorted({str(segment) for segment in segments.tolist()})
    for segment in segment_names:
        idx = np.where(segments == segment)[0]
        if len(idx) < min_rows:
            rows[segment] = {"rows": int(len(idx)), "reason": "insufficient_rows"}
            continue
        segment_y = y_true[idx]
        segment_probs = probs[idx]
        rows[segment] = {
            "rows": int(len(idx)),
            "positive_rate": round(float(segment_y.mean()), 4),
            "brier": round(float(brier_score_loss(segment_y, segment_probs)), 4),
            "baseline_brier": round(_baseline_brier(segment_y), 4),
            "auc": round(float(roc_auc_score(segment_y, segment_probs)), 4)
            if len(set(segment_y.tolist())) > 1
            else None,
        }
    return rows


def _family_cv_report(
    X: np.ndarray,
    labels: dict[str, np.ndarray],
    dates: list[date],
    label_dates: list[date],
    sides: np.ndarray,
    regime_buckets: np.ndarray,
    *,
    family: str,
) -> dict[str, Any]:
    if len(X) < 80:
        return {"family": family, "folds": 0, "reason": "insufficient_examples"}

    order = np.argsort(np.array([d.toordinal() for d in dates]), kind="stable")
    X_sorted = X[order]
    y_positive = labels["prob_positive_option_pnl"][order]
    y_breakeven = labels["prob_exceeds_breakeven"][order]
    y_return = labels["expected_option_return_pct"][order]
    sides_sorted = sides[order]
    regimes_sorted = regime_buckets[order]
    dates_sorted = np.array(dates, dtype=object)[order]
    label_dates_sorted = np.array(label_dates, dtype=object)[order]
    splits = list(
        purged_date_splits(
            dates_sorted,
            label_dates_sorted,
            n_splits=min(5, max(2, len(X) // 120)),
        )
    )
    positive_auc: list[float] = []
    breakeven_auc: list[float] = []
    positive_brier: list[float] = []
    breakeven_brier: list[float] = []
    positive_log_loss: list[float] = []
    breakeven_log_loss: list[float] = []
    return_mae: list[float] = []
    quantile_pinball: dict[float, list[float]] = {0.10: [], 0.50: [], 0.90: []}
    interval_coverage: list[float] = []
    conservative_returns: list[float] = []
    conservative_counts: list[int] = []
    oof_positive = np.full(len(X_sorted), np.nan, dtype=float)
    oof_breakeven = np.full(len(X_sorted), np.nan, dtype=float)

    fold_reports: list[dict[str, Any]] = []
    for fold, (train_idx, val_idx) in enumerate(splits, start=1):
        X_train, X_val = X_sorted[train_idx], X_sorted[val_idx]
        pos_train, pos_val = y_positive[train_idx], y_positive[val_idx]
        be_train, be_val = y_breakeven[train_idx], y_breakeven[val_idx]
        ret_train, ret_val = y_return[train_idx], y_return[val_idx]

        train_sides = sides_sorted[train_idx]
        side_weights = _balanced_sample_weight(train_sides)
        pos_model = _fit_classifier(X_train, pos_train, _balanced_sample_weight(train_sides, pos_train), family=family)
        be_model = _fit_classifier(X_train, be_train, _balanced_sample_weight(train_sides, be_train), family=family)
        ret_model = _fit_regressor(X_train, ret_train, side_weights, family=family)
        quantile_models = {
            quantile: _fit_quantile_regressor(X_train, ret_train, quantile, side_weights, family=family)
            for quantile in (0.10, 0.50, 0.90)
        }
        pos_probs = np.clip(_positive_proba(pos_model, X_val), 1e-6, 1 - 1e-6)
        be_probs = np.clip(_positive_proba(be_model, X_val), 1e-6, 1 - 1e-6)
        oof_positive[val_idx] = pos_probs
        oof_breakeven[val_idx] = be_probs

        if len(set(pos_val.tolist())) > 1:
            positive_auc.append(float(roc_auc_score(pos_val, pos_probs)))
            positive_log_loss.append(float(log_loss(pos_val, pos_probs)))
        if len(set(be_val.tolist())) > 1:
            breakeven_auc.append(float(roc_auc_score(be_val, be_probs)))
            breakeven_log_loss.append(float(log_loss(be_val, be_probs)))
        positive_brier.append(float(brier_score_loss(pos_val, pos_probs)))
        breakeven_brier.append(float(brier_score_loss(be_val, be_probs)))
        return_mae.append(float(mean_absolute_error(ret_val, ret_model.predict(X_val))))
        quantile_predictions = {
            quantile: np.asarray(model.predict(X_val), dtype=float)
            for quantile, model in quantile_models.items()
        }
        # Monotonic projection prevents independently fit heads from emitting
        # internally inconsistent intervals at inference time.
        stacked = np.sort(np.column_stack([
            quantile_predictions[0.10],
            quantile_predictions[0.50],
            quantile_predictions[0.90],
        ]), axis=1)
        q10, q50, q90 = stacked[:, 0], stacked[:, 1], stacked[:, 2]
        for quantile, prediction in ((0.10, q10), (0.50, q50), (0.90, q90)):
            quantile_pinball[quantile].append(float(mean_pinball_loss(ret_val, prediction, alpha=quantile)))
        interval_coverage.append(float(np.mean((ret_val >= q10) & (ret_val <= q90))))
        conservative = q10 > 0.0
        conservative_counts.append(int(conservative.sum()))
        if conservative.any():
            conservative_returns.extend(ret_val[conservative].astype(float).tolist())
        fold_reports.append(
            {
                "fold": fold,
                "train_rows": int(len(train_idx)),
                "validation_rows": int(len(val_idx)),
                "validation_start": min(dates_sorted[val_idx]).isoformat(),
                "training_label_end": max(label_dates_sorted[train_idx]).isoformat(),
            }
        )

    valid_positive = np.isfinite(oof_positive)
    valid_breakeven = np.isfinite(oof_breakeven)
    return {
        "family": family,
        "folds": int(len(splits)),
        "split_policy": "date_grouped_purged_by_outcome_date",
        "fold_reports": fold_reports,
        "positive_pnl_auc_mean": round(float(np.mean(positive_auc)), 4) if positive_auc else None,
        "breakeven_auc_mean": round(float(np.mean(breakeven_auc)), 4) if breakeven_auc else None,
        "positive_pnl_brier_mean": round(float(np.mean(positive_brier)), 4) if positive_brier else None,
        "breakeven_brier_mean": round(float(np.mean(breakeven_brier)), 4) if breakeven_brier else None,
        "positive_pnl_log_loss_mean": round(float(np.mean(positive_log_loss)), 4) if positive_log_loss else None,
        "breakeven_log_loss_mean": round(float(np.mean(breakeven_log_loss)), 4) if breakeven_log_loss else None,
        "expected_return_mae_mean": round(float(np.mean(return_mae)), 4) if return_mae else None,
        "return_quantiles": {
            "q10_pinball_mean": round(float(np.mean(quantile_pinball[0.10])), 4) if quantile_pinball[0.10] else None,
            "q50_pinball_mean": round(float(np.mean(quantile_pinball[0.50])), 4) if quantile_pinball[0.50] else None,
            "q90_pinball_mean": round(float(np.mean(quantile_pinball[0.90])), 4) if quantile_pinball[0.90] else None,
            "central_80_interval_coverage": round(float(np.mean(interval_coverage)), 4) if interval_coverage else None,
            "conservative_q10_positive_rows": int(sum(conservative_counts)),
            "conservative_q10_positive_mean_realized_return": (
                round(float(np.mean(conservative_returns)), 4) if conservative_returns else None
            ),
            "policy": "independent quantile heads monotonically projected; q10 > 0 is the pre-registered conservative economic selector",
        },
        "probability_buckets": {
            "prob_positive_option_pnl": _probability_buckets(oof_positive[valid_positive], y_positive[valid_positive]),
            "prob_exceeds_breakeven": _probability_buckets(oof_breakeven[valid_breakeven], y_breakeven[valid_breakeven]),
        },
        "by_segment": {
            "side": {
                "prob_positive_option_pnl": _segment_metric_report(
                    y_positive[valid_positive],
                    oof_positive[valid_positive],
                    sides_sorted[valid_positive],
                ),
                "prob_exceeds_breakeven": _segment_metric_report(
                    y_breakeven[valid_breakeven],
                    oof_breakeven[valid_breakeven],
                    sides_sorted[valid_breakeven],
                ),
            },
            "regime": {
                "prob_positive_option_pnl": _segment_metric_report(
                    y_positive[valid_positive],
                    oof_positive[valid_positive],
                    regimes_sorted[valid_positive],
                ),
                "prob_exceeds_breakeven": _segment_metric_report(
                    y_breakeven[valid_breakeven],
                    oof_breakeven[valid_breakeven],
                    regimes_sorted[valid_breakeven],
                ),
            },
        },
        "by_side": {
            "prob_positive_option_pnl": _segment_metric_report(
                y_positive[valid_positive],
                oof_positive[valid_positive],
                sides_sorted[valid_positive],
            ),
            "prob_exceeds_breakeven": _segment_metric_report(
                y_breakeven[valid_breakeven],
                oof_breakeven[valid_breakeven],
                sides_sorted[valid_breakeven],
            ),
        },
    }


def _family_sort_key(report: dict[str, Any]) -> tuple[float, ...]:
    def val(name: str) -> float:
        raw = report.get(name)
        if raw is None:
            return float("inf")
        return float(raw)

    side_metrics = (
        ((report.get("by_segment") or {}).get("side") or {}).get("prob_positive_option_pnl") or {}
    )
    required_side_rows = [
        row for side in ("call", "put")
        if isinstance((row := side_metrics.get(side)), dict)
        and row.get("brier") is not None
    ]
    missing_side_penalty = 2 - len(required_side_rows)
    worst_side_brier_excess = max(
        (
            float(row["brier"]) - float(row["baseline_brier"])
            for row in required_side_rows
            if row.get("baseline_brier") is not None
        ),
        default=float("inf"),
    )
    worst_side_auc = min(
        (float(row.get("auc") or 0.0) for row in required_side_rows),
        default=0.0,
    )
    quantile_metrics = report.get("return_quantiles") if isinstance(report.get("return_quantiles"), dict) else {}
    q50_pinball = quantile_metrics.get("q50_pinball_mean")

    return (
        float(missing_side_penalty),
        worst_side_brier_excess,
        -worst_side_auc,
        val("positive_pnl_brier_mean"),
        val("breakeven_brier_mean"),
        val("expected_return_mae_mean"),
        float(q50_pinball) if q50_pinball is not None else float("inf"),
        -float(report.get("positive_pnl_auc_mean") or 0.0),
        -float(report.get("breakeven_auc_mean") or 0.0),
    )


def _cv_report(
    X: np.ndarray,
    labels: dict[str, np.ndarray],
    dates: list[date],
    label_dates: list[date],
    sides: np.ndarray,
    regime_buckets: np.ndarray,
) -> dict[str, Any]:
    family_reports = {
        family: _family_cv_report(
            X,
            labels,
            dates,
            label_dates,
            sides,
            regime_buckets,
            family=family,
        )
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
    family: str = "tree",
) -> dict[str, Any]:
    return {
        "family": family,
        "positive_classifier": _fit_classifier(X, labels["prob_positive_option_pnl"], sample_weight, family=family),
        "no_trade_classifier": _fit_classifier(X, labels["prob_no_trade"], sample_weight, family=family),
        "fill_quality_classifier": _fit_classifier(X, labels["prob_fill_quality_ok"], sample_weight, family=family),
        "breakeven_classifier": _fit_classifier(X, labels["prob_exceeds_breakeven"], sample_weight, family=family),
        "expected_return_regressor": _fit_regressor(X, labels["expected_option_return_pct"], sample_weight, family=family),
        "return_quantile_10_regressor": _fit_quantile_regressor(X, labels["expected_option_return_pct"], 0.10, sample_weight, family=family),
        "return_quantile_50_regressor": _fit_quantile_regressor(X, labels["expected_option_return_pct"], 0.50, sample_weight, family=family),
        "return_quantile_90_regressor": _fit_quantile_regressor(X, labels["expected_option_return_pct"], 0.90, sample_weight, family=family),
        "mfe_regressor": _fit_regressor(X, labels["max_favorable_excursion_before_expiry"], sample_weight, family=family),
        "adverse_regressor": _fit_regressor(X, labels["adverse_excursion_risk"], sample_weight, family=family),
        "path_take_profit_classifier": _fit_classifier(X, labels["path_early_profit_take_prob"], sample_weight, family=family),
        "path_mfe_regressor": _fit_regressor(X, labels["path_expected_mfe_pct"], sample_weight, family=family),
        "path_decay_regressor": _fit_regressor(X, labels["path_decay_risk"], sample_weight, family=family),
    }


def _side_observability(examples: list[TradeExample], labels: dict[str, np.ndarray], sides: np.ndarray) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    returns = labels["expected_option_return_pct"]
    for side in ("call", "put"):
        idx = np.where(sides == side)[0]
        if len(idx) == 0:
            rows[side] = {"rows": 0}
            continue
        rows[side] = {
            "rows": int(len(idx)),
            "positive_pnl_rate": round(float(labels["prob_positive_option_pnl"][idx].mean()), 4),
            "breakeven_rate": round(float(labels["prob_exceeds_breakeven"][idx].mean()), 4),
            "avg_expected_option_return_pct": round(float(returns[idx].mean()), 4),
        }
    return rows


def _regime_observability(examples: list[TradeExample], labels: dict[str, np.ndarray], regime_buckets: np.ndarray) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    returns = labels["expected_option_return_pct"]
    for regime_bucket in sorted({str(bucket) for bucket in regime_buckets.tolist()}):
        idx = np.where(regime_buckets == regime_bucket)[0]
        if len(idx) == 0:
            continue
        rows[regime_bucket] = {
            "rows": int(len(idx)),
            "positive_pnl_rate": round(float(labels["prob_positive_option_pnl"][idx].mean()), 4),
            "breakeven_rate": round(float(labels["prob_exceeds_breakeven"][idx].mean()), 4),
            "avg_expected_option_return_pct": round(float(returns[idx].mean()), 4),
        }
    return rows


def _training_feature_matrix(
    examples: list[TradeExample],
    *,
    feature_cols: list[str] = FEATURE_COLS,
) -> np.ndarray:
    """Reconstruct features at each signal date using its stored regime only."""
    rows: list[np.ndarray] = []
    for example in examples:
        mode = example.regime_bucket if example.regime_bucket in {"risk_on", "risk_off", "neutral"} else "neutral"
        bias = 0.25 if mode == "risk_on" else (-0.25 if mode == "risk_off" else 0.0)
        regime = MarketRegime(mode=mode, bias=bias, source_symbol="SPY")
        rows.append(
            feature_matrix(
                [example.candidate],
                regime,
                as_of=example.entry_date,
                feature_cols=feature_cols,
            )[0]
        )
    return np.asarray(rows, dtype=float)


def _baseline_brier(y: np.ndarray) -> float:
    if len(y) == 0:
        return 0.0
    base_rate = float(np.mean(y))
    return float(brier_score_loss(y, np.full(len(y), base_rate)))


def _segment_quality_summary(
    segment_metrics: dict[str, Any],
    *,
    min_rows: int,
    min_auc: float,
    required_segments: tuple[str, ...] | None = None,
    required_qualified: int | None = None,
) -> dict[str, Any]:
    names = list(required_segments or sorted(segment_metrics))
    details: dict[str, Any] = {}
    qualified = 0
    for name in names:
        row = segment_metrics.get(name) if isinstance(segment_metrics, dict) else None
        row = row if isinstance(row, dict) else {}
        rows = int(row.get("rows", 0))
        auc = row.get("auc")
        brier = row.get("brier")
        baseline = row.get("baseline_brier")
        enough_rows = rows >= min_rows
        discrimination_ok = auc is not None and float(auc) >= min_auc
        calibration_ok = (
            brier is not None
            and baseline is not None
            and float(brier) < float(baseline)
        )
        passed = enough_rows and discrimination_ok and calibration_ok
        qualified += int(passed)
        details[name] = {
            "passed": passed,
            "rows": rows,
            "auc": auc,
            "brier": brier,
            "baseline_brier": baseline,
            "checks": {
                "minimum_rows": enough_rows,
                "minimum_auc": discrimination_ok,
                "brier_better_than_baseline": calibration_ok,
            },
        }
    required = required_qualified if required_qualified is not None else len(names)
    return {
        "passed": qualified >= required,
        "qualified_segments": qualified,
        "required_qualified_segments": required,
        "required_min_rows": min_rows,
        "required_min_auc": min_auc,
        "segments": details,
    }


def _promotion_gate_report(
    *,
    training_examples: int,
    min_side_examples: int,
    side_counts: dict[str, int],
    dataset_summary: dict[str, Any],
    regime_dataset_summary: dict[str, Any],
    cv: dict[str, Any],
    positive_baseline_brier: float,
    breakeven_baseline_brier: float,
    primary_artifact: str,
    observed_path_examples: int = 0,
    observed_path_coverage_ratio: float = 0.0,
) -> dict[str, Any]:
    positive_brier = cv.get("positive_pnl_brier_mean")
    breakeven_brier = cv.get("breakeven_brier_mean")
    positive_auc = cv.get("positive_pnl_auc_mean")
    breakeven_auc = cv.get("breakeven_auc_mean")
    regime_segments_with_depth = sum(
        1 for row in regime_dataset_summary.values()
        if isinstance(row, dict) and int(row.get("rows", 0)) >= 25
    )
    cv_segments = cv.get("by_segment") or {}
    side_quality = _segment_quality_summary(
        ((cv_segments.get("side") or {}).get("prob_positive_option_pnl") or {}),
        min_rows=30,
        min_auc=0.53,
        required_segments=("call", "put"),
    )
    regime_quality = _segment_quality_summary(
        ((cv_segments.get("regime") or {}).get("prob_positive_option_pnl") or {}),
        min_rows=25,
        min_auc=0.53,
        required_qualified=2,
    )
    friction_flip_count = int(dataset_summary.get("friction_flip_count", 0))
    quantile_metrics = cv.get("return_quantiles") if isinstance(cv.get("return_quantiles"), dict) else {}
    interval_coverage = quantile_metrics.get("central_80_interval_coverage")
    conservative_rows = int(quantile_metrics.get("conservative_q10_positive_rows") or 0)
    conservative_return = quantile_metrics.get("conservative_q10_positive_mean_realized_return")
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
            "segment_rows": regime_dataset_summary,
        },
        "side_walk_forward_quality": side_quality,
        "regime_walk_forward_quality": regime_quality,
        "friction_flip_observability": {
            "passed": friction_flip_count >= 5,
            "actual": friction_flip_count,
            "required_min": 5,
        },
        "observed_quote_path_coverage": {
            "passed": observed_path_examples >= 150 and observed_path_coverage_ratio >= 0.25,
            "actual_examples": observed_path_examples,
            "actual_ratio": round(observed_path_coverage_ratio, 4),
            "required_min_examples": 150,
            "required_min_ratio": 0.25,
        },
        "positive_pnl_auc": {
            "passed": positive_auc is not None and positive_auc >= 0.53,
            "actual": positive_auc,
            "required_min": 0.53,
        },
        "breakeven_auc": {
            "passed": breakeven_auc is not None and breakeven_auc >= 0.53,
            "actual": breakeven_auc,
            "required_min": 0.53,
        },
        "positive_pnl_brier_vs_baseline": {
            "passed": positive_brier is not None and positive_brier < positive_baseline_brier,
            "actual": positive_brier,
            "baseline": round(positive_baseline_brier, 4),
        },
        "breakeven_brier_vs_baseline": {
            "passed": breakeven_brier is not None and breakeven_brier < breakeven_baseline_brier,
            "actual": breakeven_brier,
            "baseline": round(breakeven_baseline_brier, 4),
        },
        "return_interval_calibration": {
            "passed": interval_coverage is not None and 0.70 <= float(interval_coverage) <= 0.90,
            "actual_central_80_coverage": interval_coverage,
            "required_range": [0.70, 0.90],
        },
        "conservative_after_cost_utility": {
            "passed": conservative_rows >= 30 and conservative_return is not None and float(conservative_return) > 0,
            "selected_rows": conservative_rows,
            "mean_realized_after_cost_return": conservative_return,
            "required_min_rows": 30,
            "required_mean_return_gt": 0.0,
        },
    }
    all_passed = all(bool(gate.get("passed")) for gate in gates.values())
    return {
        "status": "pending_shadow_validation" if all_passed else "hold",
        "gates": gates,
        "summary": (
            "Training-data and walk-forward gates passed; keep shadow/disagreement validation in place before promotion."
            if all_passed
            else "One or more dataset-coverage or walk-forward gates remain below threshold; hold promotion and keep gathering evidence."
        ),
        "required_next_step": (
            "Run shadow disagreement evaluation across 3/6/12-month windows and at least 30 live shadow trading days."
        ),
    }


def train(
    input_paths: list[Path],
    *,
    output_model: Path = DEFAULT_MODEL_PATH,
    output_report: Path = DEFAULT_REPORT_PATH,
    output_model_card: Path = DEFAULT_MODEL_CARD_PATH,
    options_data_dir: Path | None = DEFAULT_OPTIONS_DATA_DIR,
    min_side_examples: int = 75,
    artifact_mode: str = "active_ranker",
) -> dict[str, Any]:
    examples, source_metadata = load_examples(input_paths, options_data_dir=options_data_dir)
    if len(examples) < 50:
        raise RuntimeError(f"Need at least 50 strict-real trades to train payoff model; found {len(examples)}")

    trained_at = date.today().isoformat()
    integrated_heads = [
        "prob_positive_option_pnl",
        "prob_exceeds_breakeven",
        "expected_option_return_pct",
        "return_quantile_10",
        "return_quantile_50",
        "return_quantile_90",
        "prob_no_trade",
        "prob_fill_quality_ok",
        "path_early_profit_take_prob",
        "path_expected_mfe_pct",
        "path_decay_risk",
    ]
    X = _training_feature_matrix(examples, feature_cols=FEATURE_COLS)
    labels = {
        "prob_positive_option_pnl": np.array([example.prob_positive_option_pnl for example in examples], dtype=int),
        "prob_no_trade": np.array([example.prob_no_trade for example in examples], dtype=int),
        "prob_fill_quality_ok": np.array([example.prob_fill_quality_ok for example in examples], dtype=int),
        "expected_option_return_pct": np.array([example.expected_option_return_pct for example in examples], dtype=float),
        "prob_exceeds_breakeven": np.array([example.prob_exceeds_breakeven for example in examples], dtype=int),
        "max_favorable_excursion_before_expiry": np.array([example.max_favorable_excursion_before_expiry for example in examples], dtype=float),
        "adverse_excursion_risk": np.array([example.adverse_excursion_risk for example in examples], dtype=float),
        "path_early_profit_take_prob": np.array(
            [_path_take_profit_label(example.max_favorable_excursion_before_expiry) for example in examples],
            dtype=int,
        ),
        "path_expected_mfe_pct": np.array([example.max_favorable_excursion_before_expiry for example in examples], dtype=float),
        "path_decay_risk": np.array([max(-float(example.adverse_excursion_risk), 0.0) for example in examples], dtype=float),
    }
    dates = [example.entry_date for example in examples]
    label_dates = [example.exit_date or example.entry_date for example in examples]
    sides = np.array([example.candidate.option_type for example in examples], dtype=object)
    regime_buckets = np.array([example.regime_bucket for example in examples], dtype=object)
    cv = _cv_report(X, labels, dates, label_dates, sides, regime_buckets)
    selected_family = str(cv.get("selected_family") or "tree")

    artifact: dict[str, Any] = {
        "artifact": "cost_aware_multi_task_payoff_model",
        "version": 7,
        "mode": artifact_mode,
        "feature_cols": FEATURE_COLS,
        "selected_family": selected_family,
        "global": _fit_bundle(X, labels, _balanced_sample_weight(sides), family=selected_family),
        "by_side": {},
        "metadata": {
            **source_metadata,
            "trained_at": trained_at,
            "min_side_examples": min_side_examples,
            "label_means": {name: round(float(values.mean()), 4) for name, values in labels.items()},
            "regime_counts": dict(sorted(Counter(regime_buckets.tolist()).items())),
            "selected_family": selected_family,
            "model_selection_objective": "minimize worst-side out-of-fold Brier excess before aggregate metrics",
            "family_bakeoff": cv.get("family_bakeoff", {}),
            "activation_policy": (
                "observation_only_never_used_for_routing"
                if artifact_mode == "observation_only_never_used_for_routing"
                else "active_by_default_for_existing payoff ranker; set OROGRAPHIC_PAYOFF_MODEL_MODE=shadow for observation-only scoring"
            ),
            "integrated_heads": integrated_heads,
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
        "examples_with_exact_quote_path": int(source_metadata.get("examples_with_exact_quote_path", 0)),
        "option_chain_coverage_ratio": float(source_metadata.get("exact_quote_path_coverage_ratio", 0.0)),
    }
    dataset_summary = source_metadata.get("dataset_summary", {})
    side_dataset_summary = source_metadata.get("side_dataset_summary", {})
    regime_dataset_summary = source_metadata.get("regime_dataset_summary", {})
    positive_baseline_brier = _baseline_brier(labels["prob_positive_option_pnl"])
    breakeven_baseline_brier = _baseline_brier(labels["prob_exceeds_breakeven"])
    promotion_gates = _promotion_gate_report(
        training_examples=len(examples),
        min_side_examples=min_side_examples,
        side_counts=side_counts,
        dataset_summary=dataset_summary,
        regime_dataset_summary=regime_dataset_summary,
        cv=cv,
        positive_baseline_brier=positive_baseline_brier,
        breakeven_baseline_brier=breakeven_baseline_brier,
        primary_artifact=str(source_metadata.get("primary_training_source_artifact", "")),
        observed_path_examples=int(source_metadata.get("examples_with_exact_quote_path", 0)),
        observed_path_coverage_ratio=float(source_metadata.get("exact_quote_path_coverage_ratio", 0.0)),
    )
    report = {
        "artifact": "cost_aware_payoff_challenger" if artifact_mode == "observation_only_never_used_for_routing" else "payoff_model",
        "version": artifact["version"],
        "model_card_schema_version": 2,
        "trained_at": trained_at,
        "integrated_heads": integrated_heads,
        "training_examples": len(examples),
        "side_counts": side_counts,
        "positive_pnl_rate": round(float(labels["prob_positive_option_pnl"].mean()), 4),
        "no_trade_rate": round(float(labels["prob_no_trade"].mean()), 4),
        "fill_quality_rate": round(float(labels["prob_fill_quality_ok"].mean()), 4),
        "breakeven_rate": round(float(labels["prob_exceeds_breakeven"].mean()), 4),
        "avg_expected_option_return_pct": round(float(labels["expected_option_return_pct"].mean()), 4),
        "avg_mfe_before_expiry": round(float(labels["max_favorable_excursion_before_expiry"].mean()), 4),
        "avg_adverse_excursion_risk": round(float(labels["adverse_excursion_risk"].mean()), 4),
        "side_models_trained": sorted(artifact["by_side"].keys()),
        "selected_family": selected_family,
        "feature_cols": FEATURE_COLS,
        "cross_validation": cv,
        "calibration": {
            "method": "walk_forward_probability_buckets",
            "probability_buckets": cv.get("probability_buckets", {}),
            "brier": {
                "prob_positive_option_pnl": cv.get("positive_pnl_brier_mean"),
                "prob_exceeds_breakeven": cv.get("breakeven_brier_mean"),
                "baseline_prob_positive_option_pnl": round(positive_baseline_brier, 4),
                "baseline_prob_exceeds_breakeven": round(breakeven_baseline_brier, 4),
            },
        },
        "coverage": coverage,
        "observability": {
            "dataset_summary": dataset_summary,
            "by_side": _side_observability(examples, labels, sides),
            "by_side_dataset": side_dataset_summary,
            "by_regime": _regime_observability(examples, labels, regime_buckets),
            "by_regime_dataset": regime_dataset_summary,
            "coverage": coverage,
        },
        "activation_policy": {
            "default": "observation_only" if artifact_mode == "observation_only_never_used_for_routing" else "active",
            "shadow_env": "OROGRAPHIC_PAYOFF_MODEL_MODE=shadow",
            "reason": (
                "Cost-aware multi-task challenger cannot affect Forge order, Council eligibility, sizing, or Tradier routing."
                if artifact_mode == "observation_only_never_used_for_routing"
                else "The payoff ranker remains active and now operates alongside production-active Scout and Sentinel gating."
            ),
        },
        "training_data": {
            "primary_artifact": source_metadata.get("primary_training_source_artifact"),
            "primary_source_files": source_metadata.get("primary_training_source_files", []),
            "canonical_dataset_files": source_metadata.get("canonical_dataset_files", []),
            "legacy_result_files": source_metadata.get("legacy_result_files", []),
            "input_artifact_by_file": source_metadata.get("input_artifact_by_file", {}),
            "dataset_summary": dataset_summary,
        },
        "promotion_gates": promotion_gates,
        "source_metadata": source_metadata,
        "training_policy": {
            "global_fit": "side-balanced sample weights",
            "side_fit": "separate call and put bundles when side has enough examples",
            "min_side_examples": min_side_examples,
            "selected_family": selected_family,
            "evaluated_families": list(MODEL_FAMILIES),
            "model_selection_objective": "worst-side Brier skill and AUC, then aggregate Brier, MAE, and AUC",
            "economic_selection_policy": "q10 after-cost return above zero; prospective replay remains mandatory",
            "quantile_consistency": "independent q10/q50/q90 heads are monotonically projected at evaluation and inference",
        },
    }

    output_model.parent.mkdir(parents=True, exist_ok=True)
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_model_card.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_model)
    report["target_definitions"] = {
        "prob_positive_option_pnl": "1 when realized option PnL pct is positive",
        "prob_exceeds_breakeven": "1 when exit underlying price exceeds the long option breakeven",
        "expected_option_return_pct": "realized option PnL pct clipped to the configured cap",
        "return_quantile_10": "conditional 10th percentile of strict after-cost executable return",
        "return_quantile_50": "conditional median of strict after-cost executable return",
        "return_quantile_90": "conditional 90th percentile of strict after-cost executable return",
        "prob_no_trade": "1 when the realized contract should have been skipped because it failed friction or ended non-positive after friction",
        "prob_fill_quality_ok": "1 when entry spread, depth, volume, and quote coverage indicate live-tradable fill quality",
        "path_early_profit_take_prob": "1 when observed max favorable excursion reaches the early-take-profit threshold",
        "path_expected_mfe_pct": "observed max favorable excursion before expiry",
        "path_decay_risk": "non-negative decay or adverse-excursion pressure derived from the worst observed path return",
        "max_favorable_excursion_before_expiry": "best observed bid-mark return before expiry when real marks exist, otherwise realized return fallback",
        "adverse_excursion_risk": "worst observed bid-mark return before expiry when real marks exist, otherwise realized return fallback",
    }
    report["artifacts"] = {
        "model_path": str(output_model),
        "model_sha256": _sha256_file(output_model),
        "report_path": str(output_report),
        "model_card_path": str(output_model_card),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    output_report.write_text(rendered, encoding="utf-8")
    output_model_card.write_text(rendered, encoding="utf-8")
    log.info("Payoff model saved to %s", output_model)
    log.info("Training report saved to %s", output_report)
    log.info("Model card saved to %s", output_model_card)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train option payoff model from canonical option-outcome datasets or legacy backtest JSON")
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
    parser.add_argument(
        "--artifact-mode",
        choices=["active_ranker", "observation_only_never_used_for_routing"],
        default="active_ranker",
    )
    args = parser.parse_args()

    input_paths = args.input or default_input_paths()
    missing = [path for path in input_paths if not path.exists()]
    if missing:
        for path in missing:
            log.error("Missing input file: %s", path)
        sys.exit(1)

    report = train(
        input_paths,
        output_model=args.output_model,
        output_report=args.output_report,
        output_model_card=args.output_model_card,
        options_data_dir=args.options_data_dir,
        min_side_examples=args.min_side_examples,
        artifact_mode=args.artifact_mode,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
