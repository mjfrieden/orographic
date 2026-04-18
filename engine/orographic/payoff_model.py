"""
Second-stage option payoff scoring for Forge candidates.

Scout estimates directional edge. This module estimates whether the selected
option expression itself is likely to make money after premium, strike,
liquidity, and regime context are considered.
"""
from __future__ import annotations

import logging
import warnings
from datetime import date
from math import log1p
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from engine.orographic.schemas import ContractCandidate, MarketRegime

log = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "models" / "payoff_model.pkl"

FEATURE_COLS = [
    "option_type_is_call",
    "side_aligned_directional_edge",
    "heuristic_forge_score",
    "moneyness",
    "abs_delta",
    "premium",
    "spread_pct",
    "log_open_interest",
    "log_volume",
    "implied_volatility",
    "iv_rank",
    "projected_move_pct",
    "breakeven_move_pct",
    "expected_return_pct",
    "extrinsic_ratio",
    "allocation_weight",
    "dte",
    "liquidity_score",
    "regime_bias",
    "regime_is_risk_on",
    "regime_is_risk_off",
    "regime_alignment_score",
]

_ARTIFACT: dict[str, Any] | None = None
_ARTIFACT_LOAD_ATTEMPTED = False


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


def feature_row(
    candidate: ContractCandidate,
    regime: MarketRegime | None = None,
    *,
    as_of: date | None = None,
) -> dict[str, float]:
    directional = side_aligned_directional_edge(candidate)
    liquidity = liquidity_score(candidate)
    regime_alignment = regime_alignment_score(candidate, regime)
    regime_bias = _safe_float(getattr(regime, "bias", 0.0), 0.0)
    heuristic_score = _safe_float(
        getattr(candidate, "pre_payoff_forge_score", None),
        _safe_float(candidate.forge_score),
    )
    return {
        "option_type_is_call": 1.0 if candidate.option_type == "call" else 0.0,
        "side_aligned_directional_edge": directional,
        "heuristic_forge_score": heuristic_score,
        "moneyness": _safe_float(candidate.moneyness),
        "abs_delta": abs(_safe_float(candidate.delta)),
        "premium": max(_safe_float(candidate.premium, _safe_float(candidate.ask)), 0.0),
        "spread_pct": max(_safe_float(candidate.spread_pct), 0.0),
        "log_open_interest": log1p(max(_safe_float(candidate.open_interest), 0.0)),
        "log_volume": log1p(max(_safe_float(candidate.volume), 0.0)),
        "implied_volatility": max(_safe_float(candidate.implied_volatility, 0.35), 0.0),
        "iv_rank": _clip(_safe_float(candidate.iv_rank, 0.5)),
        "projected_move_pct": _safe_float(candidate.projected_move_pct),
        "breakeven_move_pct": _safe_float(candidate.breakeven_move_pct),
        "expected_return_pct": _safe_float(candidate.expected_return_pct),
        "extrinsic_ratio": _clip(_safe_float(candidate.extrinsic_ratio, 1.0)),
        "allocation_weight": max(_safe_float(candidate.allocation_weight, 1.0), 0.0),
        "dte": float(_days_to_expiry(candidate, as_of=as_of)),
        "liquidity_score": liquidity,
        "regime_bias": regime_bias,
        "regime_is_risk_on": 1.0 if getattr(regime, "mode", None) == "risk_on" else 0.0,
        "regime_is_risk_off": 1.0 if getattr(regime, "mode", None) == "risk_off" else 0.0,
        "regime_alignment_score": regime_alignment,
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


def _rank_percentile(values: np.ndarray) -> np.ndarray:
    if len(values) == 0:
        return values
    if len(values) == 1:
        return np.array([0.5], dtype=float)
    order = np.argsort(values)
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.linspace(0.0, 1.0, len(values))
    return ranks


def score_candidates(
    candidates: list[ContractCandidate],
    regime: MarketRegime | None = None,
    *,
    as_of: date | None = None,
    model_path: Path = MODEL_PATH,
) -> list[ContractCandidate]:
    """
    Add payoff-aware predictions and final scores to candidates in-place.

    If no trained artifact is present, candidates keep their existing Forge
    score but still receive auditable blend components where possible.
    """
    if not candidates:
        return candidates

    artifact = _load_artifact(model_path)
    feature_cols = list((artifact or {}).get("feature_cols", FEATURE_COLS))
    X_all = feature_matrix(candidates, regime, as_of=as_of, feature_cols=feature_cols)

    expected_return = np.zeros(len(candidates), dtype=float)
    prob_positive = np.zeros(len(candidates), dtype=float)
    prob_breakeven = np.zeros(len(candidates), dtype=float)
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
    else:
        for i, candidate in enumerate(candidates):
            expected_return[i] = _safe_float(candidate.expected_return_pct)
            prob_positive[i] = _clip(0.50 + expected_return[i] / 4.0)
            prob_breakeven[i] = _clip(0.50 + (_safe_float(candidate.projected_move_pct) - _safe_float(candidate.breakeven_move_pct)) * 4.0)
            mfe[i] = max(expected_return[i], 0.0)
            adverse[i] = min(expected_return[i], 0.0)

    expected_return_rank = _rank_percentile(expected_return)

    for i, candidate in enumerate(candidates):
        pre_payoff_score = _safe_float(
            getattr(candidate, "pre_payoff_forge_score", None),
            _safe_float(candidate.forge_score),
        )
        directional = side_aligned_directional_edge(candidate)
        liquidity = liquidity_score(candidate)
        regime_alignment = regime_alignment_score(candidate, regime)
        final_score = _clip(
            0.25 * directional
            + 0.35 * _clip(float(prob_positive[i]))
            + 0.20 * _clip(float(expected_return_rank[i]))
            + 0.10 * liquidity
            + 0.10 * regime_alignment
        )

        candidate.pre_payoff_forge_score = round(pre_payoff_score, 4)
        candidate.directional_edge = round(directional, 4)
        candidate.liquidity_score = round(liquidity, 4)
        candidate.regime_alignment_score = round(regime_alignment, 4)
        candidate.prob_positive_option_pnl = round(_clip(float(prob_positive[i])), 4)
        candidate.expected_option_return_pct_model = round(float(expected_return[i]), 4)
        candidate.expected_option_return_pct_rank = round(float(expected_return_rank[i]), 4)
        candidate.prob_exceeds_breakeven = round(_clip(float(prob_breakeven[i])), 4)
        candidate.max_favorable_excursion_before_expiry = round(float(mfe[i]), 4)
        candidate.adverse_excursion_risk = round(float(adverse[i]), 4)
        candidate.payoff_model_score = round(final_score, 4)
        candidate.final_candidate_score = round(final_score, 4)
        if artifact:
            candidate.forge_score = round(final_score, 4)
            if not any("payoff model" in note.lower() for note in candidate.notes):
                candidate.notes.append("Payoff model score active")
        else:
            candidate.forge_score = round(pre_payoff_score, 4)

    candidates.sort(key=lambda candidate: candidate.forge_score, reverse=True)
    return candidates
