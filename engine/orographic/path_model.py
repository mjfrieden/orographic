"""
Shadow-only path-quality scoring for short-dated option candidates.

This layer estimates path-sensitive outcomes like:
  - early profit-taking probability
  - expected max favorable excursion during the intended hold window
  - decay risk over that hold window

It is intentionally shadow-only. The outputs are observational diagnostics that
help us evaluate whether hold-window behavior contains incremental signal beyond
terminal P&L labels.
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

MODEL_PATH = Path(__file__).parent / "models" / "path_model.pkl"
PATH_MODEL_MODE_ENV = "OROGRAPHIC_PATH_MODEL_MODE"

FEATURE_COLS = [
    "option_type_is_call",
    "premium_pct_of_spot",
    "spread_pct",
    "extrinsic_ratio",
    "dte",
    "projected_move_pct",
    "heuristic_edge_after_friction_pct",
    "implied_volatility",
    "iv_rank",
    "vrp_gap",
    "realized_vol_20d",
    "atr_pct_14d",
    "regime_alignment_score",
    "sentinel_holding_window_fit",
    "sentinel_confidence",
    "sentinel_side_relevance",
    "sentinel_no_trade_relevance",
    "sentinel_spot_effect",
    "sentinel_iv_effect",
]

_ARTIFACT: dict[str, Any] | None = None
_ARTIFACT_LOAD_ATTEMPTED = False


@dataclass
class PathSummary:
    mode_counts: dict[str, int]
    scored_candidates: int
    avg_holding_quality_score: float | None
    avg_early_profit_take_prob: float | None
    avg_decay_risk: float | None


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


def _activation_mode() -> str:
    mode = os.getenv(PATH_MODEL_MODE_ENV, "shadow").strip().lower()
    return "shadow" if mode in {"shadow", "observe", "off", "active", "live", "on"} else "shadow"


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def feature_row(
    candidate: ContractCandidate,
    regime: MarketRegime | None = None,
    *,
    as_of: date | None = None,
) -> dict[str, float]:
    side_relevance = _safe_float(
        getattr(
            candidate,
            "sentinel_call_relevance" if candidate.option_type == "call" else "sentinel_put_relevance",
            None,
        )
    )
    regime_alignment = _safe_float(getattr(candidate, "regime_alignment_score", None), 0.55)
    edge_after_friction = _safe_float(
        getattr(candidate, "expected_edge_after_friction_pct", None),
        _safe_float(getattr(candidate, "expected_option_return_pct_model", None), _safe_float(candidate.expected_return_pct)),
    )
    premium_pct_of_spot = _safe_float(
        getattr(candidate, "premium_pct_of_spot", None),
        max(_safe_float(candidate.premium), 0.0) / max(_safe_float(getattr(candidate, "spot", None), 0.0), 1.0),
    )
    return {
        "option_type_is_call": 1.0 if candidate.option_type == "call" else 0.0,
        "premium_pct_of_spot": premium_pct_of_spot,
        "spread_pct": max(_safe_float(candidate.spread_pct), 0.0),
        "extrinsic_ratio": _clip(_safe_float(candidate.extrinsic_ratio, 1.0)),
        "dte": float(_days_to_expiry(candidate, as_of=as_of)),
        "projected_move_pct": _safe_float(candidate.projected_move_pct),
        "heuristic_edge_after_friction_pct": edge_after_friction,
        "implied_volatility": max(_safe_float(candidate.implied_volatility), 0.0),
        "iv_rank": _clip(_safe_float(candidate.iv_rank, 0.5)),
        "vrp_gap": max(_safe_float(getattr(candidate, "vrp_gap", None)), 0.0),
        "realized_vol_20d": max(_safe_float(getattr(candidate, "realized_vol_20d", None)), 0.0),
        "atr_pct_14d": max(_safe_float(getattr(candidate, "atr_pct_14d", None)), 0.0),
        "regime_alignment_score": _clip(regime_alignment),
        "sentinel_holding_window_fit": _clip(_safe_float(getattr(candidate, "sentinel_holding_window_fit", None))),
        "sentinel_confidence": _clip(_safe_float(getattr(candidate, "sentinel_confidence", None))),
        "sentinel_side_relevance": _clip(side_relevance),
        "sentinel_no_trade_relevance": _clip(_safe_float(getattr(candidate, "sentinel_no_trade_relevance", None))),
        "sentinel_spot_effect": _clip(_safe_float(getattr(candidate, "sentinel_spot_effect", None))),
        "sentinel_iv_effect": _clip(_safe_float(getattr(candidate, "sentinel_iv_effect", None))),
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
            log.warning("Failed to load path model artifact %s: %s", path, exc)
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
            log.warning("Ignoring malformed path model artifact at %s", path)
    except Exception as exc:
        log.warning("Failed to load path model artifact %s: %s", path, exc)
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


def _heuristic_predictions(candidate: ContractCandidate, row: dict[str, float]) -> tuple[float, float, float]:
    edge = row["heuristic_edge_after_friction_pct"]
    sentinel_fit = row["sentinel_holding_window_fit"]
    sentinel_confidence = row["sentinel_confidence"]
    side_relevance = row["sentinel_side_relevance"]
    no_trade = row["sentinel_no_trade_relevance"]
    spot_effect = row["sentinel_spot_effect"]
    iv_effect = row["sentinel_iv_effect"]
    dte = max(row["dte"], 1.0)
    premium_intensity = row["premium_pct_of_spot"]
    extrinsic_ratio = row["extrinsic_ratio"]
    projected_move = row["projected_move_pct"]
    regime_alignment = row["regime_alignment_score"]
    vrp_gap = row["vrp_gap"]

    early_take_profit = _clip(
        0.28
        + 0.35 * sentinel_fit
        + 0.18 * sentinel_confidence
        + 0.15 * side_relevance
        + 0.12 * regime_alignment
        + 0.18 * max(edge, 0.0)
        + 0.08 * spot_effect
        - 0.12 * iv_effect
        - 0.15 * no_trade
        - 0.04 * max(dte - 5.0, 0.0) / 5.0,
        0.0,
        1.0,
    )
    expected_mfe = max(
        0.0,
        0.60 * max(edge, 0.0)
        + 0.90 * projected_move
        + 0.08 * sentinel_fit
        + 0.06 * side_relevance
        + 0.05 * spot_effect
        - 0.14 * premium_intensity * 10.0
        - 0.08 * iv_effect,
    )
    decay_risk = _clip(
        0.22
        + 0.28 * extrinsic_ratio
        + 0.14 * premium_intensity * 10.0
        + 0.14 * iv_effect
        + 0.10 * min(vrp_gap / 0.25, 1.0)
        + 0.06 * max(dte - 4.0, 0.0) / 6.0
        - 0.14 * sentinel_fit
        - 0.12 * spot_effect
        - 0.10 * max(edge, 0.0),
        0.0,
        1.0,
    )
    return early_take_profit, expected_mfe, decay_risk


def score_candidates(
    candidates: list[ContractCandidate],
    regime: MarketRegime | None = None,
    *,
    as_of: date | None = None,
    model_path: Path = MODEL_PATH,
    activation_mode: str | None = None,
) -> list[ContractCandidate]:
    if not candidates:
        return candidates

    artifact = _load_artifact(model_path)
    model_mode = (activation_mode or _activation_mode()).strip().lower()
    model_mode = "shadow" if model_mode in {"shadow", "observe", "off", "active", "live", "on"} else "shadow"
    artifact_hash = _sha256_file(model_path) if artifact else None
    feature_cols = list((artifact or {}).get("feature_cols", FEATURE_COLS))
    X_all = feature_matrix(candidates, regime, as_of=as_of, feature_cols=feature_cols)

    early_take_profit = np.zeros(len(candidates), dtype=float)
    expected_mfe = np.zeros(len(candidates), dtype=float)
    decay_risk = np.zeros(len(candidates), dtype=float)

    if artifact:
        for option_type in ("call", "put"):
            idx = [i for i, candidate in enumerate(candidates) if candidate.option_type == option_type]
            if not idx:
                continue
            X = X_all[idx]
            bundle = _model_bundle(artifact, option_type)
            defaults = (artifact.get("metadata") or {}).get("label_means", {})
            early_take_profit[idx] = _predict_classifier(
                bundle.get("early_take_profit_classifier"),
                X,
                float(defaults.get("path_early_profit_take_prob", 0.5)),
            )
            expected_mfe[idx] = _predict_regressor(
                bundle.get("mfe_regressor"),
                X,
                float(defaults.get("path_expected_mfe_pct", 0.0)),
            )
            decay_risk[idx] = _predict_regressor(
                bundle.get("decay_risk_regressor"),
                X,
                float(defaults.get("path_decay_risk", 0.5)),
            )
    else:
        for i, candidate in enumerate(candidates):
            row = feature_row(candidate, regime, as_of=as_of)
            early_take_profit[i], expected_mfe[i], decay_risk[i] = _heuristic_predictions(candidate, row)

    for i, candidate in enumerate(candidates):
        normalized_mfe = _clip(float(expected_mfe[i]) / 0.45, 0.0, 1.0)
        holding_quality = _clip(
            0.40 * _clip(float(early_take_profit[i]))
            + 0.35 * normalized_mfe
            + 0.25 * (1.0 - _clip(float(decay_risk[i]))),
            0.0,
            1.0,
        )
        candidate.path_early_profit_take_prob = round(_clip(float(early_take_profit[i])), 4)
        candidate.path_expected_mfe_pct = round(float(expected_mfe[i]), 4)
        candidate.path_decay_risk = round(_clip(float(decay_risk[i])), 4)
        candidate.path_holding_quality_score = round(holding_quality, 4)
        candidate.path_model_mode = model_mode
        candidate.path_model_artifact_sha256 = artifact_hash
        if not any("path model shadow" in note.lower() for note in candidate.notes):
            candidate.notes.append(
                "Path model shadow: "
                f"hold quality {holding_quality:.2f}, "
                f"take-profit {float(early_take_profit[i]):.2f}, "
                f"decay {float(decay_risk[i]):.2f}"
            )
    return candidates


def summarize_candidates(candidates: list[ContractCandidate]) -> dict[str, Any]:
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
