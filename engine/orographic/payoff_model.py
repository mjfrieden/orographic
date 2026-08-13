"""
Second-stage option payoff scoring for Forge candidates.

Scout estimates directional edge. This module estimates whether the selected
option expression itself is likely to make money after premium, strike,
liquidity, and regime context are considered.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import warnings
from datetime import date
from math import log1p
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from engine.orographic.schemas import ContractCandidate, MarketRegime

log = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "models" / "payoff_model.pkl"
SHADOW_MODEL_PATH = Path(__file__).parent / "models" / "payoff_volatility_shadow.pkl"
COST_AWARE_SHADOW_MODEL_PATH = Path(__file__).parent / "models" / "payoff_cost_aware_challenger.pkl"
RANKER_MODE_ENV = "OROGRAPHIC_PAYOFF_MODEL_MODE"
STACK_MODE_ENV = "OROGRAPHIC_MODEL_STACK"

FEATURE_COLS = [
    "option_type_is_call",
    "side_aligned_directional_edge",
    "scout_call_edge_prob",
    "scout_put_edge_prob",
    "scout_no_trade_prob",
    "heuristic_forge_score",
    "moneyness",
    "abs_delta",
    "premium",
    "premium_pct_of_spot",
    "spread_pct",
    "log_open_interest",
    "log_volume",
    "implied_volatility",
    "iv_rank",
    "surface_available",
    "surface_atm_iv",
    "surface_skew_slope",
    "surface_curvature",
    "surface_put_call_wing_skew",
    "surface_term_slope_30d",
    "surface_fit_rmse",
    "surface_observation_count_log",
    "iv_relative_to_atm",
    "iv_minus_realized_vol",
    "quote_spread_dollars",
    "last_trade_age_log_seconds",
    "realized_vol_20d",
    "atr_pct_14d",
    "vrp_gap",
    "projected_move_pct",
    "breakeven_move_pct",
    "expected_return_pct",
    "heuristic_edge_after_friction_pct",
    "extrinsic_ratio",
    "allocation_weight",
    "dte",
    "liquidity_score",
    "fill_quality_score",
    "regime_bias",
    "regime_is_risk_on",
    "regime_is_risk_off",
    "regime_alignment_score",
    "sentinel_holding_window_fit",
    "sentinel_confidence",
    "sentinel_side_relevance",
    "sentinel_no_trade_relevance",
    "sentinel_spot_effect",
    "sentinel_iv_effect",
    "sentinel_event_present",
    "sentinel_time_horizon_score",
    "sentinel_decay_half_life_score",
    "sentinel_source_reliability_score",
    "sentinel_novelty_score",
]

_ARTIFACT: dict[str, Any] | None = None
_ARTIFACT_LOAD_ATTEMPTED = False


@dataclass
class AveragedClassifier:
    models: list[Any]

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.models:
            return np.column_stack([np.ones(len(X)), np.zeros(len(X))])
        probs = []
        for model in self.models:
            if hasattr(model, "predict_proba"):
                model_probs = model.predict_proba(X)
                if model_probs.ndim == 2 and model_probs.shape[1] > 1:
                    probs.append(model_probs[:, 1].astype(float))
                    continue
            probs.append(np.asarray(model.predict(X), dtype=float))
        mean_prob = np.mean(np.vstack(probs), axis=0)
        return np.column_stack([1.0 - mean_prob, mean_prob])

    def predict(self, X: np.ndarray) -> np.ndarray:
        probs = self.predict_proba(X)
        return (probs[:, 1] >= 0.5).astype(int)


@dataclass
class AveragedRegressor:
    models: list[Any]

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self.models:
            return np.zeros(len(X), dtype=float)
        predictions = [np.asarray(model.predict(X), dtype=float) for model in self.models]
        return np.mean(np.vstack(predictions), axis=0)


def _activation_mode() -> str:
    mode = os.getenv(RANKER_MODE_ENV, "active").strip().lower()
    return "shadow" if mode in {"shadow", "observe", "off"} else "active"


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


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


def _days_to_expiry(candidate: ContractCandidate, as_of: date | None = None) -> int:
    try:
        expiry = date.fromisoformat(candidate.expiry)
    except ValueError:
        return 7
    reference = as_of or date.today()
    return max((expiry - reference).days, 0)


def side_aligned_directional_edge(candidate: ContractCandidate) -> float:
    """Map Scout's signed score into side-specific edge for calls and puts."""
    scout_score = _clip(_safe_float(candidate.scout_score), -1.0, 1.0)
    if candidate.option_type == "put":
        return (1.0 - scout_score) / 2.0
    return (scout_score + 1.0) / 2.0


def liquidity_score(candidate: ContractCandidate) -> float:
    spread_pct = max(_safe_float(candidate.spread_pct), 0.0)
    open_interest = max(_safe_float(candidate.open_interest), 0.0)
    volume = max(_safe_float(candidate.volume), 0.0)
    spread_component = 1.0 - min(spread_pct / 0.18, 1.0)
    oi_component = min(log1p(open_interest) / log1p(1000.0), 1.0)
    volume_component = min(log1p(volume) / log1p(500.0), 1.0)
    return _clip(0.50 * spread_component + 0.30 * oi_component + 0.20 * volume_component)


def fill_quality_score(candidate: ContractCandidate) -> float:
    spread_pct = max(_safe_float(candidate.spread_pct), 0.0)
    open_interest = max(_safe_float(candidate.open_interest), 0.0)
    volume = max(_safe_float(candidate.volume), 0.0)
    spread_component = 1.0 - min(spread_pct / 0.18, 1.0)
    oi_component = min(log1p(open_interest) / log1p(1000.0), 1.0)
    volume_component = min(log1p(volume) / log1p(250.0), 1.0)
    return _clip(0.55 * spread_component + 0.25 * oi_component + 0.20 * volume_component)


def regime_alignment_score(candidate: ContractCandidate, regime: MarketRegime | None) -> float:
    if regime is None or regime.mode == "neutral":
        return 0.55
    if candidate.option_type == "call" and regime.mode == "risk_on":
        return 1.0
    if candidate.option_type == "put" and regime.mode == "risk_off":
        return 1.0
    if abs(_safe_float(regime.bias)) < 0.10:
        return 0.55
    return 0.25


def _time_horizon_score(value: object) -> float:
    bucket = str(value or "").strip().lower()
    mapping = {
        "intraday": 0.2,
        "one_day": 0.4,
        "three_days": 0.7,
        "one_week": 1.0,
        "longer": 0.6,
    }
    return mapping.get(bucket, 0.0)


def _decay_half_life_score(value: object) -> float:
    bucket = str(value or "").strip().lower()
    mapping = {
        "intraday": 0.15,
        "one_day": 0.35,
        "three_days": 0.7,
        "one_week": 1.0,
        "longer": 0.6,
    }
    return mapping.get(bucket, 0.0)


def _ordinal_score(value: object, mapping: dict[str, float], default: float = 0.0) -> float:
    cleaned = str(value or "").strip().lower()
    return mapping.get(cleaned, default)


def _fallback_path_predictions(
    candidate: ContractCandidate,
    regime: MarketRegime | None,
    *,
    as_of: date | None = None,
) -> tuple[float, float, float]:
    from engine.orographic.path_model import _heuristic_predictions, feature_row as path_feature_row

    row = path_feature_row(candidate, regime, as_of=as_of)
    return _heuristic_predictions(candidate, row)


def feature_row(
    candidate: ContractCandidate,
    regime: MarketRegime | None = None,
    *,
    as_of: date | None = None,
) -> dict[str, float]:
    directional = side_aligned_directional_edge(candidate)
    liquidity = liquidity_score(candidate)
    fill_quality = fill_quality_score(candidate)
    regime_alignment = regime_alignment_score(candidate, regime)
    regime_bias = _safe_float(getattr(regime, "bias", 0.0), 0.0)
    heuristic_score = _safe_float(
        getattr(candidate, "pre_payoff_forge_score", None),
        _safe_float(candidate.forge_score),
    )
    premium = max(_safe_float(candidate.premium, _safe_float(candidate.ask)), 0.0)
    premium_pct_of_spot = _safe_float(getattr(candidate, "premium_pct_of_spot", None))
    realized_vol_20d = max(_safe_float(getattr(candidate, "realized_vol_20d", None)), 0.0)
    atr_pct_14d = max(_safe_float(getattr(candidate, "atr_pct_14d", None)), 0.0)
    vrp_gap = _safe_float(
        getattr(candidate, "vrp_gap", None),
        max(_safe_float(candidate.implied_volatility, 0.0) - realized_vol_20d, 0.0),
    )
    heuristic_edge_after_friction_pct = _safe_float(
        getattr(candidate, "expected_edge_after_friction_pct", None),
        _safe_float(candidate.expected_return_pct) - _friction_buffer_pct(candidate),
    )
    sentinel_side_relevance = _safe_float(
        getattr(
            candidate,
            "sentinel_call_relevance" if candidate.option_type == "call" else "sentinel_put_relevance",
            None,
        )
    )
    sentinel_event_present = 0.0 if str(getattr(candidate, "sentinel_event_type", "none") or "none").strip().lower() == "none" else 1.0
    return {
        "option_type_is_call": 1.0 if candidate.option_type == "call" else 0.0,
        "side_aligned_directional_edge": directional,
        "scout_call_edge_prob": _clip(_safe_float(getattr(candidate, "scout_call_edge_prob", None))),
        "scout_put_edge_prob": _clip(_safe_float(getattr(candidate, "scout_put_edge_prob", None))),
        "scout_no_trade_prob": _clip(_safe_float(getattr(candidate, "scout_no_trade_prob", None))),
        "heuristic_forge_score": heuristic_score,
        "moneyness": _safe_float(candidate.moneyness),
        "abs_delta": abs(_safe_float(candidate.delta)),
        "premium": premium,
        "premium_pct_of_spot": premium_pct_of_spot,
        "spread_pct": max(_safe_float(candidate.spread_pct), 0.0),
        "log_open_interest": log1p(max(_safe_float(candidate.open_interest), 0.0)),
        "log_volume": log1p(max(_safe_float(candidate.volume), 0.0)),
        "implied_volatility": max(_safe_float(candidate.implied_volatility, 0.35), 0.0),
        "iv_rank": _clip(_safe_float(candidate.iv_rank, 0.5)),
        "surface_available": 1.0 if getattr(candidate, "surface_atm_iv", None) is not None else 0.0,
        "surface_atm_iv": _safe_float(getattr(candidate, "surface_atm_iv", None)),
        "surface_skew_slope": _safe_float(getattr(candidate, "surface_skew_slope", None)),
        "surface_curvature": _safe_float(getattr(candidate, "surface_curvature", None)),
        "surface_put_call_wing_skew": _safe_float(getattr(candidate, "surface_put_call_wing_skew", None)),
        "surface_term_slope_30d": _safe_float(getattr(candidate, "surface_term_slope_30d", None)),
        "surface_fit_rmse": _safe_float(getattr(candidate, "surface_fit_rmse", None)),
        "surface_observation_count_log": log1p(max(_safe_float(getattr(candidate, "surface_observation_count", None)), 0.0)),
        "iv_relative_to_atm": _safe_float(getattr(candidate, "iv_relative_to_atm", None)),
        "iv_minus_realized_vol": _safe_float(getattr(candidate, "iv_minus_realized_vol", None)),
        "quote_spread_dollars": max(_safe_float(getattr(candidate, "quote_spread_dollars", None)), 0.0),
        "last_trade_age_log_seconds": log1p(max(_safe_float(getattr(candidate, "last_trade_age_seconds", None)), 0.0)),
        "realized_vol_20d": realized_vol_20d,
        "atr_pct_14d": atr_pct_14d,
        "vrp_gap": max(vrp_gap, 0.0),
        "projected_move_pct": _safe_float(candidate.projected_move_pct),
        "breakeven_move_pct": _safe_float(candidate.breakeven_move_pct),
        "expected_return_pct": _safe_float(candidate.expected_return_pct),
        "heuristic_edge_after_friction_pct": heuristic_edge_after_friction_pct,
        "extrinsic_ratio": _clip(_safe_float(candidate.extrinsic_ratio, 1.0)),
        "allocation_weight": max(_safe_float(candidate.allocation_weight, 1.0), 0.0),
        "dte": float(_days_to_expiry(candidate, as_of=as_of)),
        "liquidity_score": liquidity,
        "fill_quality_score": fill_quality,
        "regime_bias": regime_bias,
        "regime_is_risk_on": 1.0 if getattr(regime, "mode", None) == "risk_on" else 0.0,
        "regime_is_risk_off": 1.0 if getattr(regime, "mode", None) == "risk_off" else 0.0,
        "regime_alignment_score": regime_alignment,
        "sentinel_holding_window_fit": _safe_float(getattr(candidate, "sentinel_holding_window_fit", None)),
        "sentinel_confidence": _safe_float(getattr(candidate, "sentinel_confidence", None)),
        "sentinel_side_relevance": sentinel_side_relevance,
        "sentinel_no_trade_relevance": _safe_float(getattr(candidate, "sentinel_no_trade_relevance", None)),
        "sentinel_spot_effect": _safe_float(getattr(candidate, "sentinel_spot_effect", None)),
        "sentinel_iv_effect": _safe_float(getattr(candidate, "sentinel_iv_effect", None)),
        "sentinel_event_present": sentinel_event_present,
        "sentinel_time_horizon_score": _time_horizon_score(getattr(candidate, "sentinel_time_horizon", None)),
        "sentinel_decay_half_life_score": _decay_half_life_score(getattr(candidate, "sentinel_decay_half_life", None)),
        "sentinel_source_reliability_score": _ordinal_score(
            getattr(candidate, "sentinel_source_reliability", None),
            {"unknown": 0.0, "low": 0.25, "medium": 0.6, "high": 1.0},
        ),
        "sentinel_novelty_score": _ordinal_score(
            getattr(candidate, "sentinel_novelty", None),
            {"unknown": 0.0, "stale": 0.15, "moderate": 0.55, "fresh": 1.0, "new": 1.0},
        ),
    }


def feature_matrix(
    candidates: Iterable[ContractCandidate],
    regime: MarketRegime | None = None,
    *,
    as_of: date | None = None,
    feature_cols: list[str] | None = None,
) -> np.ndarray:
    cols = feature_cols or FEATURE_COLS
    rows = [feature_row(candidate, regime, as_of=as_of) for candidate in candidates]
    return np.array([[row.get(col, 0.0) for col in cols] for row in rows], dtype=float)


def _load_artifact(path: Path = MODEL_PATH) -> dict[str, Any] | None:
    global _ARTIFACT, _ARTIFACT_LOAD_ATTEMPTED
    if path != MODEL_PATH:
        if not path.exists():
            return None
        try:
            import joblib

            artifact = joblib.load(path)
            return artifact if isinstance(artifact, dict) and "feature_cols" in artifact else None
        except Exception as exc:
            log.warning("Failed to load payoff model artifact %s: %s", path, exc)
            return None
    if _ARTIFACT_LOAD_ATTEMPTED:
        return _ARTIFACT
    _ARTIFACT_LOAD_ATTEMPTED = True
    if not path.exists():
        return None
    try:
        import joblib

        artifact = joblib.load(path)
        if isinstance(artifact, dict) and "feature_cols" in artifact:
            _ARTIFACT = artifact
        else:
            log.warning("Ignoring malformed payoff model artifact at %s", path)
    except Exception as exc:
        log.warning("Failed to load payoff model artifact %s: %s", path, exc)
        _ARTIFACT = None
    return _ARTIFACT


def _predict_classifier(model: Any, X: np.ndarray, default: float) -> np.ndarray:
    if model is None:
        return np.full(X.shape[0], default, dtype=float)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="X does not have valid feature names.*")
        if hasattr(model, "predict_proba"):
            probs = model.predict_proba(X)
            if probs.ndim == 2 and probs.shape[1] > 1:
                return probs[:, 1].astype(float)
        return np.asarray(model.predict(X), dtype=float)


def _predict_regressor(model: Any, X: np.ndarray, default: float) -> np.ndarray:
    if model is None:
        return np.full(X.shape[0], default, dtype=float)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="X does not have valid feature names.*")
        return np.asarray(model.predict(X), dtype=float)


def _model_bundle(artifact: dict[str, Any], option_type: str) -> dict[str, Any]:
    by_side = artifact.get("by_side", {})
    side_bundle = by_side.get(option_type)
    if side_bundle is not None:
        return side_bundle
    return artifact.get("global", {})


def _attach_payoff_shadow_observations(
    candidates: list[ContractCandidate],
    regime: MarketRegime | None,
    *,
    as_of: date | None,
    shadow_model_path: Path,
) -> None:
    if shadow_model_path == SHADOW_MODEL_PATH and COST_AWARE_SHADOW_MODEL_PATH.exists():
        shadow_model_path = COST_AWARE_SHADOW_MODEL_PATH
    if not shadow_model_path.exists() or not candidates:
        return
    try:
        import joblib

        artifact = joblib.load(shadow_model_path)
        if not isinstance(artifact, dict) or artifact.get("mode") != "observation_only_never_used_for_routing":
            return
        feature_cols = list(artifact.get("feature_cols") or [])
        base_model = artifact.get("base_model")
        rich_bundle = artifact.get("global") if isinstance(artifact.get("global"), dict) else None
        if not feature_cols or (base_model is None and rich_bundle is None):
            return
        X = feature_matrix(candidates, regime, as_of=as_of, feature_cols=feature_cols)
        defaults = (artifact.get("metadata") or {}).get("label_means", {})
        if rich_bundle is None:
            raw = _predict_classifier(base_model, X, 0.5)
        else:
            raw = np.full(len(candidates), float(defaults.get("prob_positive_option_pnl", 0.5)))
            for option_type in ("call", "put"):
                idx = [i for i, candidate in enumerate(candidates) if candidate.option_type == option_type]
                if not idx:
                    continue
                bundle = _model_bundle(artifact, option_type)
                raw[idx] = _predict_classifier(
                    bundle.get("positive_classifier"), X[idx], float(defaults.get("prob_positive_option_pnl", 0.5))
                )
        intercept = artifact.get("calibrator")
        if intercept is None:
            shadow_probs = np.clip(raw, 1e-6, 1 - 1e-6)
        else:
            clipped = np.clip(raw, 1e-6, 1 - 1e-6)
            logits = np.log(clipped / (1.0 - clipped)) + float(intercept)
            shadow_probs = np.clip(1.0 / (1.0 + np.exp(-logits)), 1e-6, 1 - 1e-6)
        shadow_ranks = _rank_percentile(shadow_probs)
        active_probs = np.array([
            _clip(_safe_float(candidate.prob_positive_option_pnl, 0.5))
            for candidate in candidates
        ])
        active_ranks = _rank_percentile(active_probs)
        artifact_hash = _sha256_file(shadow_model_path)
        rich_predictions: dict[str, np.ndarray] = {}
        if rich_bundle is not None:
            for name in ("q10", "q50", "q90", "fill", "target"):
                rich_predictions[name] = np.zeros(len(candidates), dtype=float)
            for option_type in ("call", "put"):
                idx = [i for i, candidate in enumerate(candidates) if candidate.option_type == option_type]
                if not idx:
                    continue
                bundle = _model_bundle(artifact, option_type)
                for name, model_key in (
                    ("q10", "return_quantile_10_regressor"),
                    ("q50", "return_quantile_50_regressor"),
                    ("q90", "return_quantile_90_regressor"),
                ):
                    rich_predictions[name][idx] = _predict_regressor(
                        bundle.get(model_key), X[idx], float(defaults.get("expected_option_return_pct", 0.0))
                    )
                rich_predictions["fill"][idx] = _predict_classifier(
                    bundle.get("fill_quality_classifier"), X[idx], float(defaults.get("prob_fill_quality_ok", 0.5))
                )
                rich_predictions["target"][idx] = _predict_classifier(
                    bundle.get("path_take_profit_classifier"), X[idx], float(defaults.get("path_early_profit_take_prob", 0.5))
                )
            ordered = np.sort(np.column_stack([
                rich_predictions["q10"], rich_predictions["q50"], rich_predictions["q90"]
            ]), axis=1)
            rich_predictions["q10"] = ordered[:, 0]
            rich_predictions["q50"] = ordered[:, 1]
            rich_predictions["q90"] = ordered[:, 2]
        for idx, candidate in enumerate(candidates):
            probability_delta = float(shadow_probs[idx] - active_probs[idx])
            rank_delta = float(shadow_ranks[idx] - active_ranks[idx])
            decision_disagreement = bool((shadow_probs[idx] >= 0.5) != (active_probs[idx] >= 0.5))
            candidate.payoff_shadow_prob_positive = round(float(shadow_probs[idx]), 4)
            candidate.payoff_shadow_rank = round(float(shadow_ranks[idx]), 4)
            candidate.payoff_shadow_probability_delta = round(probability_delta, 4)
            candidate.payoff_shadow_rank_delta = round(rank_delta, 4)
            candidate.payoff_shadow_disagreement = decision_disagreement or abs(probability_delta) >= 0.15
            candidate.payoff_shadow_mode = "observation_only"
            candidate.payoff_shadow_artifact_sha256 = artifact_hash
            if rich_predictions:
                candidate.payoff_shadow_return_q10 = round(float(rich_predictions["q10"][idx]), 4)
                candidate.payoff_shadow_return_q50 = round(float(rich_predictions["q50"][idx]), 4)
                candidate.payoff_shadow_return_q90 = round(float(rich_predictions["q90"][idx]), 4)
                candidate.payoff_shadow_prob_fill_quality = round(_clip(float(rich_predictions["fill"][idx])), 4)
                candidate.payoff_shadow_prob_target_before_stop = round(_clip(float(rich_predictions["target"][idx])), 4)
                candidate.payoff_shadow_conservative_utility = round(
                    float(rich_predictions["q10"][idx]) * _clip(float(rich_predictions["fill"][idx])),
                    4,
                )
    except Exception as exc:
        log.warning("Payoff shadow scoring unavailable: %s", exc)


def _rank_percentile(values: np.ndarray) -> np.ndarray:
    if len(values) == 0:
        return values
    if len(values) == 1:
        return np.array([0.5], dtype=float)
    order = np.argsort(values)
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.linspace(0.0, 1.0, len(values))
    return ranks


def _friction_buffer_pct(candidate: ContractCandidate) -> float:
    spread_drag = min(max(_safe_float(candidate.spread_pct), 0.0) * 0.60, 0.25)
    extrinsic_drag = max(_safe_float(candidate.extrinsic_ratio, 0.0) - 0.45, 0.0) * 0.45
    return round(spread_drag + 0.03 + 0.03 + extrinsic_drag, 4)


def _expected_edge_after_friction_pct(
    candidate: ContractCandidate,
    expected_return_pct_model: float,
) -> float:
    gross_edge = expected_return_pct_model if np.isfinite(expected_return_pct_model) else _safe_float(candidate.expected_return_pct)
    return round(float(gross_edge) - _friction_buffer_pct(candidate), 4)


def _utility_after_friction_score(edge_after_friction: float) -> float:
    return _clip(0.5 + edge_after_friction / 0.40, 0.0, 1.0)


def _stability_adjustment(
    *,
    candidate: ContractCandidate,
    edge_after_friction: float,
    prior_live_board_symbols: set[str],
    turnover_switch_penalty: float,
) -> tuple[float, float, bool]:
    is_prior_live_symbol = candidate.symbol.upper() in prior_live_board_symbols
    if is_prior_live_symbol:
        return 0.02, 0.0, True
    penalty = max(turnover_switch_penalty - max(edge_after_friction, 0.0), 0.0)
    if penalty <= 0:
        return 0.0, 0.0, False
    scaled_penalty = min(penalty * 0.75, 0.04)
    return -scaled_penalty, scaled_penalty, False


def score_candidates(
    candidates: list[ContractCandidate],
    regime: MarketRegime | None = None,
    *,
    as_of: date | None = None,
    model_path: Path = MODEL_PATH,
    activation_mode: str | None = None,
    prior_live_board_symbols: list[str] | None = None,
    turnover_switch_penalty: float = 0.03,
    shadow_model_path: Path = SHADOW_MODEL_PATH,
) -> list[ContractCandidate]:
    """
    Add payoff-aware predictions and final scores to candidates in-place.

    If no trained artifact is present, candidates keep their existing Forge
    score but still receive auditable blend components where possible.
    """
    if not candidates:
        return candidates

    artifact = _load_artifact(model_path)
    ranker_mode = (activation_mode or _activation_mode()).strip().lower()
    ranker_mode = "shadow" if ranker_mode in {"shadow", "observe", "off"} else "active"
    artifact_hash = _sha256_file(model_path) if artifact else None
    feature_cols = list((artifact or {}).get("feature_cols", FEATURE_COLS))
    X_all = feature_matrix(candidates, regime, as_of=as_of, feature_cols=feature_cols)
    prior_live_symbols = {
        str(symbol).upper()
        for symbol in (prior_live_board_symbols or [])
        if str(symbol).strip()
    }

    expected_return = np.zeros(len(candidates), dtype=float)
    prob_positive = np.zeros(len(candidates), dtype=float)
    prob_breakeven = np.zeros(len(candidates), dtype=float)
    prob_no_trade = np.zeros(len(candidates), dtype=float)
    prob_fill_quality_ok = np.zeros(len(candidates), dtype=float)
    path_take_profit = np.zeros(len(candidates), dtype=float)
    path_expected_mfe = np.zeros(len(candidates), dtype=float)
    path_decay_risk = np.zeros(len(candidates), dtype=float)
    mfe = np.zeros(len(candidates), dtype=float)
    adverse = np.zeros(len(candidates), dtype=float)

    if artifact:
        for option_type in ("call", "put"):
            idx = [i for i, candidate in enumerate(candidates) if candidate.option_type == option_type]
            if not idx:
                continue
            X = X_all[idx]
            bundle = _model_bundle(artifact, option_type)
            defaults = (artifact.get("metadata") or {}).get("label_means", {})
            prob_positive[idx] = _predict_classifier(
                bundle.get("positive_classifier"),
                X,
                float(defaults.get("prob_positive_option_pnl", 0.50)),
            )
            prob_breakeven[idx] = _predict_classifier(
                bundle.get("breakeven_classifier"),
                X,
                float(defaults.get("prob_exceeds_breakeven", 0.50)),
            )
            prob_no_trade[idx] = _predict_classifier(
                bundle.get("no_trade_classifier"),
                X,
                float(defaults.get("prob_no_trade", 0.50)),
            )
            prob_fill_quality_ok[idx] = _predict_classifier(
                bundle.get("fill_quality_classifier"),
                X,
                float(defaults.get("prob_fill_quality_ok", 0.50)),
            )
            expected_return[idx] = _predict_regressor(
                bundle.get("expected_return_regressor"),
                X,
                float(defaults.get("expected_option_return_pct", 0.0)),
            )
            mfe[idx] = _predict_regressor(
                bundle.get("mfe_regressor"),
                X,
                float(defaults.get("max_favorable_excursion_before_expiry", 0.0)),
            )
            adverse[idx] = _predict_regressor(
                bundle.get("adverse_regressor"),
                X,
                float(defaults.get("adverse_excursion_risk", 0.0)),
            )
            path_take_profit[idx] = _predict_classifier(
                bundle.get("path_take_profit_classifier"),
                X,
                float(defaults.get("path_early_profit_take_prob", 0.5)),
            )
            path_expected_mfe[idx] = _predict_regressor(
                bundle.get("path_mfe_regressor"),
                X,
                float(defaults.get("path_expected_mfe_pct", 0.0)),
            )
            path_decay_risk[idx] = _predict_regressor(
                bundle.get("path_decay_regressor"),
                X,
                float(defaults.get("path_decay_risk", 0.5)),
            )
    else:
        for i, candidate in enumerate(candidates):
            expected_return[i] = _safe_float(candidate.expected_return_pct)
            prob_positive[i] = _clip(0.50 + expected_return[i] / 4.0)
            prob_breakeven[i] = _clip(0.50 + (_safe_float(candidate.projected_move_pct) - _safe_float(candidate.breakeven_move_pct)) * 4.0)
            prob_fill_quality_ok[i] = fill_quality_score(candidate)
            prob_no_trade[i] = _clip(
                0.18
                + 0.35 * (1.0 - prob_fill_quality_ok[i])
                + 0.20 * _clip(_safe_float(candidate.extrinsic_ratio))
                + 0.18 * _clip(_safe_float(getattr(candidate, "scout_no_trade_prob", None)))
                + 0.15 * _clip(_safe_float(getattr(candidate, "sentinel_no_trade_relevance", None)))
                - 0.20 * _clip(side_aligned_directional_edge(candidate)),
                0.0,
                1.0,
            )
            mfe[i] = max(expected_return[i], 0.0)
            adverse[i] = min(expected_return[i], 0.0)
            (
                path_take_profit[i],
                path_expected_mfe[i],
                path_decay_risk[i],
            ) = _fallback_path_predictions(candidate, regime, as_of=as_of)

    expected_return_rank = _rank_percentile(expected_return)

    for i, candidate in enumerate(candidates):
        pre_payoff_score = _safe_float(
            getattr(candidate, "pre_payoff_forge_score", None),
            _safe_float(candidate.forge_score),
        )
        directional = side_aligned_directional_edge(candidate)
        liquidity = liquidity_score(candidate)
        fill_quality = _clip(float(prob_fill_quality_ok[i]))
        regime_alignment = regime_alignment_score(candidate, regime)
        edge_after_friction = _expected_edge_after_friction_pct(candidate, float(expected_return[i]))
        utility_after_friction = _utility_after_friction_score(edge_after_friction)
        no_trade = _clip(float(prob_no_trade[i]))
        normalized_path_mfe = _clip(float(path_expected_mfe[i]) / 0.45, 0.0, 1.0)
        holding_quality = _clip(
            0.40 * _clip(float(path_take_profit[i]))
            + 0.35 * normalized_path_mfe
            + 0.25 * (1.0 - _clip(float(path_decay_risk[i]))),
            0.0,
            1.0,
        )
        stability_adjustment, turnover_risk_penalty, is_prior_live_symbol = _stability_adjustment(
            candidate=candidate,
            edge_after_friction=edge_after_friction,
            prior_live_board_symbols=prior_live_symbols,
            turnover_switch_penalty=turnover_switch_penalty,
        )
        base_final_score = _clip(
            0.16 * directional
            + 0.20 * _clip(float(prob_positive[i]))
            + 0.12 * _clip(float(prob_breakeven[i]))
            + 0.12 * _clip(float(expected_return_rank[i]))
            + 0.10 * liquidity
            + 0.10 * fill_quality
            + 0.10 * holding_quality
            + 0.08 * regime_alignment
            + 0.12 * utility_after_friction
            - 0.10 * no_trade
        )
        final_score = _clip(base_final_score + stability_adjustment)

        candidate.pre_payoff_forge_score = round(pre_payoff_score, 4)
        candidate.directional_edge = round(directional, 4)
        candidate.liquidity_score = round(liquidity, 4)
        candidate.fill_quality_score = round(fill_quality, 4)
        candidate.regime_alignment_score = round(regime_alignment, 4)
        candidate.prob_positive_option_pnl = round(_clip(float(prob_positive[i])), 4)
        candidate.payoff_edge_score = candidate.prob_positive_option_pnl
        candidate.prob_no_trade = round(no_trade, 4)
        candidate.no_trade_score = round(1.0 - no_trade, 4)
        candidate.prob_fill_quality_ok = round(fill_quality, 4)
        candidate.expected_option_return_pct_model = round(float(expected_return[i]), 4)
        candidate.expected_option_return_pct_rank = round(float(expected_return_rank[i]), 4)
        candidate.prob_exceeds_breakeven = round(_clip(float(prob_breakeven[i])), 4)
        candidate.breakeven_edge_score = candidate.prob_exceeds_breakeven
        candidate.max_favorable_excursion_before_expiry = round(float(mfe[i]), 4)
        candidate.adverse_excursion_risk = round(float(adverse[i]), 4)
        candidate.path_early_profit_take_prob = round(_clip(float(path_take_profit[i])), 4)
        candidate.path_expected_mfe_pct = round(float(path_expected_mfe[i]), 4)
        candidate.path_decay_risk = round(_clip(float(path_decay_risk[i])), 4)
        candidate.path_holding_quality_score = round(holding_quality, 4)
        candidate.path_model_mode = "integrated_forge"
        candidate.path_model_artifact_sha256 = artifact_hash
        candidate.friction_buffer_pct = _friction_buffer_pct(candidate)
        candidate.expected_edge_after_friction_pct = edge_after_friction
        candidate.utility_after_friction_score = round(utility_after_friction, 4)
        candidate.stability_adjustment = round(stability_adjustment, 4)
        candidate.turnover_risk_penalty = round(turnover_risk_penalty, 4)
        candidate.prior_live_board_symbol = is_prior_live_symbol
        candidate.payoff_model_score = round(base_final_score, 4)
        candidate.final_candidate_score = round(final_score, 4)
        candidate.learned_rank_score = round(final_score, 4)
        candidate.ranker_artifact_sha256 = artifact_hash
        candidate.call_selector_model_score = None
        candidate.call_selector_contract_score = None
        candidate.call_contract_selector_score = None
        candidate.call_contract_selector_mode = None
        if artifact:
            candidate.ranker_mode = ranker_mode
            if ranker_mode == "active":
                candidate.forge_score = round(final_score, 4)
                if not any("payoff model" in note.lower() for note in candidate.notes):
                    candidate.notes.append("Payoff model ranker active")
                if stability_adjustment > 0:
                    candidate.notes.append("Stability bonus applied for prior live-board continuity")
                elif turnover_risk_penalty > 0:
                    candidate.notes.append(
                        f"Turnover penalty applied ({turnover_risk_penalty:.2f}) because post-friction edge is thin"
                    )
                if not any("forge multi-head" in note.lower() for note in candidate.notes):
                    candidate.notes.append(
                        "Forge multi-head active: "
                        f"no-trade {no_trade:.2f}, fill {fill_quality:.2f}, path {holding_quality:.2f}"
                    )
            else:
                candidate.forge_score = round(pre_payoff_score, 4)
                if not any("payoff model shadow" in note.lower() for note in candidate.notes):
                    candidate.notes.append(f"Payoff model shadow score {final_score:.2f}")
        else:
            candidate.forge_score = round(pre_payoff_score, 4)
            candidate.ranker_mode = "heuristic"

    _attach_payoff_shadow_observations(
        candidates,
        regime,
        as_of=as_of,
        shadow_model_path=shadow_model_path,
    )
    if os.getenv(STACK_MODE_ENV, "").strip().lower() == "unified_rnd":
        from engine.orographic.unified_stack import apply_base_unified_rank

        apply_base_unified_rank(candidates, profile="unified_rnd")
    candidates.sort(key=lambda candidate: candidate.forge_score, reverse=True)
    return candidates


def summarize_path_heads(candidates: list[ContractCandidate]) -> dict[str, Any]:
    if not candidates:
        return {
            "mode_counts": {},
            "scored_candidates": 0,
            "avg_holding_quality_score": None,
            "avg_early_profit_take_prob": None,
            "avg_decay_risk": None,
        }
    mode_counts: dict[str, int] = {}
    holding_quality: list[float] = []
    take_profit: list[float] = []
    decay: list[float] = []
    for candidate in candidates:
        mode = str(getattr(candidate, "path_model_mode", None) or "shadow")
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        if candidate.path_holding_quality_score is not None:
            holding_quality.append(float(candidate.path_holding_quality_score))
        if candidate.path_early_profit_take_prob is not None:
            take_profit.append(float(candidate.path_early_profit_take_prob))
        if candidate.path_decay_risk is not None:
            decay.append(float(candidate.path_decay_risk))
    return {
        "mode_counts": mode_counts,
        "scored_candidates": len(candidates),
        "avg_holding_quality_score": round(sum(holding_quality) / len(holding_quality), 4) if holding_quality else None,
        "avg_early_profit_take_prob": round(sum(take_profit) / len(take_profit), 4) if take_profit else None,
        "avg_decay_risk": round(sum(decay) / len(decay), 4) if decay else None,
    }
