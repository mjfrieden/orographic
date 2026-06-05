"""
engine/orographic/scout.py

Signal generation layer for the Orographic pipeline.

Inference uses a trained LightGBM classifier (scout_model.pkl) to predict
the probability that a stock will have a positive 5-day forward return.
That probability is mapped to a scout_score in [-1, +1].

If the model file is absent, the system gracefully degrades to the
original linear heuristic formula so the pipeline never hard-fails.
"""
from __future__ import annotations

import logging
import os
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .event_features import (
    latest_event_feature_snapshot,
    load_event_feature_frame,
)
from .market_data import history
from .schemas import MarketRegime, ScoutSignal
from .sentinel import fetch_ai_multiplier

log = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).parent / "models"
_MODEL_PATH = _MODEL_DIR / "scout_model.pkl"
_SCALER_PATH = _MODEL_DIR / "scout_scaler.pkl"
_SIDE_MODEL_PATH = _MODEL_DIR / "scout_side_model.pkl"
SIDE_MODEL_MODE_ENV = "OROGRAPHIC_SIDE_MODEL_MODE"
REGIME_SAME_SIDE_BONUS = 0.08
REGIME_COUNTERTREND_PENALTY = 0.18
REGIME_COUNTERTREND_MIN_ABS_SCORE = 0.35
SHADOW_SIDE_VETO_MIN_PROB = 0.50
SHADOW_SIDE_VETO_MIN_MARGIN = 0.10


# ── Model loader (singleton, loaded once per process) ────────────────────────

@lru_cache(maxsize=1)
def _load_model() -> tuple | None:
    """
    Returns (model, scaler, feature_cols) or None if no model file exists.
    The lru_cache ensures we pay the I/O cost only once.
    """
    if not _MODEL_PATH.exists() or not _SCALER_PATH.exists():
        log.warning(
            "Scout model not found at %s. "
            "Run engine/train_scout_model.py to train it. "
            "Falling back to heuristic linear scoring.",
            _MODEL_PATH,
        )
        return None
    try:
        import joblib

        model = joblib.load(_MODEL_PATH)
        meta  = joblib.load(_SCALER_PATH)
        log.info("✓ Scout model loaded from %s", _MODEL_PATH)
        return (
            model,
            meta["scaler"],
            meta["feature_cols"],
            meta.get("calibrator"),
            meta.get("calibration_method", "none"),
            meta.get("primary_target", "underlying_forward_return"),
            meta.get("target_description", "probability that forward 5-day underlying return is positive"),
            meta.get("positive_class_name", "bullish"),
        )
    except Exception as exc:
        log.warning("Failed to load Scout model (%s) — using heuristic fallback.", exc)
        return None


@lru_cache(maxsize=1)
def _load_side_model() -> dict[str, object] | None:
    if not _SIDE_MODEL_PATH.exists():
        return None
    try:
        import joblib

        artifact = joblib.load(_SIDE_MODEL_PATH)
        return artifact if isinstance(artifact, dict) and "model" in artifact else None
    except Exception as exc:
        log.warning("Failed to load Scout side model (%s) — using derived side probabilities.", exc)
        return None


# ── Utilities ────────────────────────────────────────────────────────────────

def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _empty_direction_counts() -> dict[str, int]:
    return {"call": 0, "put": 0}


def _side_aware_probabilities(score: float) -> dict[str, float]:
    """
    Convert Scout's signed directional score into a side-aware three-way view.

    Existing Scout artifacts are binary bull-probability models, so these are
    routing probabilities over call edge, put edge, and no trade. A future
    explicit side-aware artifact can replace the internals without changing
    the snapshot contract.
    """
    signed = _clip(float(score))
    strength = min(abs(signed), 1.0)
    no_trade = _clip(1.0 - strength * 1.8, 0.05, 0.90)
    active_mass = 1.0 - no_trade
    dominant = 0.50 + strength / 2.0
    if signed >= 0:
        call_edge = active_mass * dominant
        put_edge = active_mass - call_edge
    else:
        put_edge = active_mass * dominant
        call_edge = active_mass - put_edge
    return {
        "call_edge": round(call_edge, 4),
        "put_edge": round(put_edge, 4),
        "no_trade": round(no_trade, 4),
    }


def _side_model_activation_mode() -> str:
    mode = os.getenv(SIDE_MODEL_MODE_ENV, "shadow").strip().lower()
    return "active" if mode in {"active", "live"} else "shadow"


def _preferred_side_from_probabilities(side_probs: dict[str, float]) -> str:
    numeric = {
        "call": float(side_probs.get("call_edge", 0.0)),
        "put": float(side_probs.get("put_edge", 0.0)),
        "no_trade": float(side_probs.get("no_trade", 0.0)),
    }
    return max(numeric.items(), key=lambda item: item[1])[0]


def _side_probability(side_probs: dict[str, float], side: str) -> float:
    if side == "call":
        return float(side_probs.get("call_edge", 0.0))
    if side == "put":
        return float(side_probs.get("put_edge", 0.0))
    if side == "no_trade":
        return float(side_probs.get("no_trade", 0.0))
    return 0.0


def _apply_shadow_side_guard(
    *,
    direction: str,
    side_probs: dict[str, float],
    side_model_mode: str,
    side_activation_mode: str,
) -> tuple[bool, dict[str, object]]:
    preferred = _preferred_side_from_probabilities(side_probs)
    preferred_prob = round(_side_probability(side_probs, preferred), 4)
    direction_prob = round(_side_probability(side_probs, direction), 4)
    margin = round(preferred_prob - direction_prob, 4)
    diagnostics = {
        "applied": False,
        "passed": True,
        "preferred_side": preferred,
        "preferred_probability": preferred_prob,
        "direction_probability": direction_prob,
        "margin_vs_direction": margin,
        "reason": None,
        "note": None,
    }
    if side_model_mode != "trained_option_payoff_three_class" or side_activation_mode == "active":
        return True, diagnostics

    if preferred_prob < SHADOW_SIDE_VETO_MIN_PROB or margin < SHADOW_SIDE_VETO_MIN_MARGIN:
        return True, diagnostics

    diagnostics["applied"] = True
    if preferred == "no_trade":
        diagnostics["passed"] = False
        diagnostics["reason"] = "shadow_no_trade_veto"
        diagnostics["note"] = (
            "side-aware shadow model vetoed the setup as no-trade dominant "
            f"({preferred_prob:.2%}, margin {margin:.2%})"
        )
        return False, diagnostics

    if preferred != direction:
        diagnostics["reason"] = "shadow_direction_conflict"
        diagnostics["note"] = (
            "side-aware shadow model flagged an opposite-side conflict "
            f"was dominant ({preferred} {preferred_prob:.2%}, margin {margin:.2%})"
        )
        return True, diagnostics

    return True, diagnostics


def _ml_side_probabilities(feats: dict[str, float], fallback_score: float) -> tuple[dict[str, float], str]:
    artifact = _load_side_model()
    if artifact is None:
        return _side_aware_probabilities(fallback_score), "derived_three_class"
    try:
        model = artifact["model"]
        scaler = artifact["scaler"]
        feature_cols = list(artifact["feature_cols"])
        class_map = {int(key): value for key, value in dict(artifact.get("class_map", {})).items()}
        row = np.array([[feats.get(col, 0.0) for col in feature_cols]])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            row_scaled = scaler.transform(row)
            probs = model.predict_proba(row_scaled)[0]
        mapped = {"call_edge": 0.0, "put_edge": 0.0, "no_trade": 0.0}
        for cls, prob in zip(model.classes_, probs):
            name = class_map.get(int(cls), str(cls))
            if name in mapped:
                mapped[name] = float(prob)
        total = sum(mapped.values())
        if total <= 0:
            return _side_aware_probabilities(fallback_score), "derived_three_class"
        target = str(artifact.get("target") or "").strip().lower()
        model_mode = (
            "trained_option_payoff_three_class"
            if target == "strict_real_option_payoff"
            else "trained_underlying_three_class"
        )
        return {key: round(value / total, 4) for key, value in mapped.items()}, model_mode
    except Exception as exc:
        log.warning("Scout side model inference failed (%s) — using derived side probabilities.", exc)
        return _side_aware_probabilities(fallback_score), "derived_three_class"


def _apply_regime_alignment(
    *,
    direction: str,
    conviction_score: float,
    regime: MarketRegime,
) -> tuple[bool, float, str | None, str | None]:
    if regime.mode == "extreme_vol":
        return False, 0.0, "extreme_vol", "regime veto: extreme volatility"

    if regime.mode == "neutral":
        return True, 0.0, None, None

    aligned = (
        (regime.mode == "risk_on" and direction == "call")
        or (regime.mode == "risk_off" and direction == "put")
    )
    if aligned:
        return True, REGIME_SAME_SIDE_BONUS, None, f"regime tailwind: {regime.mode}"

    if abs(conviction_score) < REGIME_COUNTERTREND_MIN_ABS_SCORE:
        return (
            False,
            -REGIME_COUNTERTREND_PENALTY,
            "counter_regime_weak_conviction",
            f"counter-regime setup rejected below {REGIME_COUNTERTREND_MIN_ABS_SCORE:.2f} conviction",
        )

    return (
        True,
        -REGIME_COUNTERTREND_PENALTY,
        None,
        f"counter-regime setup survived with penalty in {regime.mode}",
    )


def _calculate_z_scores(metrics: dict[str, float]) -> dict[str, float]:
    """Cross-sectional Z-Scores across a universe of metric values."""
    if not metrics:
        return {}
    values = list(metrics.values())
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    std_dev = variance ** 0.5 if variance > 0 else 1.0
    if std_dev == 0:
        return {k: 0.0 for k in metrics.keys()}
    return {k: (v - mean) / std_dev for k, v in metrics.items()}


def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    up = delta.clip(lower=0.0).rolling(period).mean()
    down = -delta.clip(upper=0.0).rolling(period).mean()
    rs = up / down.replace(0.0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    value = rsi.iloc[-1]
    return float(value) if pd.notna(value) else 50.0


def _atr_pct(frame: pd.DataFrame, period: int = 14) -> float:
    high = pd.to_numeric(frame["High"], errors="coerce")
    low  = pd.to_numeric(frame["Low"],  errors="coerce")
    close = pd.to_numeric(frame["Close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [(high - low).abs(), (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(period).mean().iloc[-1]
    spot = close.iloc[-1]
    if pd.isna(atr) or pd.isna(spot) or float(spot) <= 0:
        return 0.0
    return float(atr / spot)


# ── Feature extraction (shared between training and inference) ────────────────

def _extract_features(
    close: pd.Series,
    frame: pd.DataFrame,
    spy_close: pd.Series | None = None,
    event_snapshot: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Compute the same feature set used during training.
    Returns a dict for a single bar (the most recent).
    """
    rv20 = float(close.pct_change().rolling(20).std().iloc[-1] * (252 ** 0.5))
    rv60 = float(close.pct_change().rolling(60).std().iloc[-1] * (252 ** 0.5))
    mom_5d  = float(close.iloc[-1] / close.iloc[-6]  - 1.0)
    mom_10d = float(close.iloc[-1] / close.iloc[-11] - 1.0)
    mom_20d = float(close.iloc[-1] / close.iloc[-21] - 1.0)
    mom_60d = float(close.iloc[-1] / close.iloc[-61] - 1.0) if len(close) > 61 else mom_20d
    rsi_14  = _rsi(close, 14)
    rsi_7   = _rsi(close, 7)
    atr_pct = _atr_pct(frame, 14)
    ma20    = float(close.rolling(20).mean().iloc[-1])
    price_vs_ma20 = (close.iloc[-1] - ma20) / (atr_pct * close.iloc[-1]) if atr_pct > 0 else 0.0

    vol_series = pd.to_numeric(frame.get("Volume", pd.Series(dtype=float)), errors="coerce")
    volume_ratio = float(
        vol_series.iloc[-1] / vol_series.rolling(20).mean().iloc[-1]
    ) if vol_series.notna().sum() > 20 and vol_series.rolling(20).mean().iloc[-1] > 0 else 1.0

    vol_regime = rv20 / rv60 if rv60 > 0 else 1.0

    feats: dict[str, float] = {
        "mom_5d":           mom_5d,
        "mom_10d":          mom_10d,
        "mom_20d":          mom_20d,
        "mom_60d":          mom_60d,
        "rv20":             rv20,
        "rv60":             rv60,
        "vol_adj_mom_5d":   mom_5d  / rv20 if rv20 > 0 else 0.0,
        "vol_adj_mom_20d":  mom_20d / rv20 if rv20 > 0 else 0.0,
        "rsi_14":           rsi_14,
        "rsi_7":            rsi_7,
        "atr_pct_14d":      atr_pct,
        "price_vs_ma20":    float(price_vs_ma20),
        "volume_ratio":     volume_ratio,
        "vol_regime":       vol_regime,
    }

    if spy_close is not None:
        spy_mom_5d  = float(spy_close.iloc[-1] / spy_close.iloc[-6]  - 1.0) if len(spy_close) >= 6  else 0.0
        spy_mom_20d = float(spy_close.iloc[-1] / spy_close.iloc[-21] - 1.0) if len(spy_close) >= 21 else 0.0
        spy_rv20    = float(spy_close.pct_change().rolling(20).std().iloc[-1] * (252 ** 0.5))
        feats.update({
            "spy_mom_5d":       spy_mom_5d,
            "spy_mom_20d":      spy_mom_20d,
            "spy_rv20":         spy_rv20,
            "rel_strength_20d": mom_20d - spy_mom_20d,
        })

    if event_snapshot:
        feats.update(
            {
                key: float(value)
                for key, value in event_snapshot.items()
                if isinstance(value, (int, float, np.integer, np.floating))
            }
        )

    return feats


def _ml_scout_score(feats: dict[str, float]) -> float | None:
    """
    Run LightGBM inference. Returns a score in [-1, +1] where
    +1 = maximum bullish conviction and -1 = maximum bearish.
    Returns None if the model is unavailable.
    """
    loaded = _load_model()
    if loaded is None:
        return None

    model, scaler, feature_cols, calibrator, calibration_method, _, _, _ = loaded
    row = np.array([[feats.get(col, 0.0) for col in feature_cols]])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        row_scaled = scaler.transform(row)
        prob_bull = float(model.predict_proba(row_scaled)[0][1])
        if calibrator is not None and calibration_method == "isotonic":
            prob_bull = float(calibrator.predict([prob_bull])[0])
        elif calibrator is not None and calibration_method == "platt":
            prob_bull = float(calibrator.predict_proba([[prob_bull]])[0][1])

    # Map [0, 1] \u2192 [-1, +1] so existing downstream code is unchanged
    return _clip((prob_bull - 0.5) * 2.0)


def _heuristic_scout_score(
    momentum_5d: float,
    momentum_20d: float,
    rsi_14: float,
    realized_vol_20d: float,
    atr_pct_14d: float,
    z_score: float,
    regime_bonus: float,
) -> tuple[float, float, float]:
    """Original linear fallback. Returns (technical, empirical, scout) scores."""
    technical_score = _clip(
        momentum_5d * 7.0
        + momentum_20d * 5.0
        + ((rsi_14 - 50.0) / 25.0) * 0.6
        - max(realized_vol_20d - 0.55, 0.0) * 0.45
    )
    empirical_score = _clip(
        (z_score * 0.45)
        + (momentum_5d * 2.0)
        - max(atr_pct_14d - 0.045, 0.0) * 1.5
    )
    scout_score = _clip(0.58 * technical_score + 0.32 * empirical_score + regime_bonus)
    return technical_score, empirical_score, scout_score


# \u2500\u2500 Regime inference \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

def infer_market_regime() -> MarketRegime:
    try:
        spy = history("SPY", period="6mo")
        vix = history("^VIX", period="3mo")
    except Exception as exc:
        return MarketRegime(
            mode="neutral",
            bias=0.0,
            source_symbol="SPY",
            notes=[f"Cross-asset fetch degraded: {exc}"],
        )

    spy_close = pd.to_numeric(spy.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
    vix_close = pd.to_numeric(vix.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
    
    if len(spy_close) < 21 or len(vix_close) < 6:
        return MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY")

    spy_5  = float(spy_close.iloc[-1] / spy_close.iloc[-6]  - 1.0)
    spy_20 = float(spy_close.iloc[-1] / spy_close.iloc[-21] - 1.0)
    vix_level = float(vix_close.iloc[-1])
    vix_5     = float(vix_close.iloc[-1] / vix_close.iloc[-6] - 1.0)
    bias = _clip((spy_5 * 6.0) + (spy_20 * 4.0) - (vix_5 * 0.8) - ((vix_level - 20.0) / 35.0))

    if vix_level > 30.0 or vix_5 > 0.25:
        mode = "extreme_vol"
    elif bias >= 0.18:
        mode = "risk_on"
    elif bias <= -0.18:
        mode = "risk_off"
    else:
        mode = "neutral"
    return MarketRegime(mode=mode, bias=round(bias, 4), source_symbol="SPY")


# \u2500\u2500 Signal builder \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

def build_signal(
    symbol: str,
    regime: MarketRegime,
    frame: pd.DataFrame,
    z_score: float,
    spy_frame: pd.DataFrame | None = None,
    event_feature_store: pd.DataFrame | None = None,
    *,
    return_diagnostics: bool = False,
) -> ScoutSignal | tuple[ScoutSignal | None, dict[str, object]] | None:
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    diagnostics: dict[str, object] = {
        "symbol": symbol,
        "regime_mode": regime.mode,
        "passed": False,
        "reason": None,
    }
    if len(close) < 62:   # need 62 bars for mom_60d + fwd window safety
        diagnostics["reason"] = "insufficient_history"
        return (None, diagnostics) if return_diagnostics else None

    spot = float(close.iloc[-1])
    momentum_5d       = float(spot / close.iloc[-6]  - 1.0)
    momentum_20d      = float(spot / close.iloc[-21] - 1.0)
    realized_vol_20d  = float(close.pct_change().rolling(20).std().iloc[-1] * (252 ** 0.5))
    rsi_14            = _rsi(close, period=14)
    atr_pct_14d       = _atr_pct(frame, period=14)

    # \u2500\u2500 ML inference path \u2500\u2500
    spy_close = None
    if spy_frame is not None:
        spy_close = pd.to_numeric(spy_frame.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
        spy_close = spy_close.reindex(close.index, method="ffill").dropna()

    event_feature_snapshot = latest_event_feature_snapshot(
        symbol,
        event_feature_store,
        as_of=close.index[-1] if len(close.index) else None,
    )
    event_context = event_feature_snapshot.to_context_dict() if event_feature_snapshot is not None else {}
    feats = _extract_features(
        close,
        frame,
        spy_close,
        event_snapshot=event_feature_snapshot.to_feature_dict() if event_feature_snapshot is not None else None,
    )
    ml_score = _ml_scout_score(feats)
    using_ml = ml_score is not None
    primary_target = "underlying_forward_return"
    target_description = "probability that forward 5-day underlying return is positive"
    positive_class_name = "bullish"
    if using_ml:
        loaded = _load_model()
        if loaded is not None:
            _, _, _, _, _, primary_target, target_description, positive_class_name = loaded

    if using_ml:
        raw_score = ml_score
        technical_score = raw_score      # expose as technical for schema compat
        empirical_score = z_score * 0.3  # still blend in cross-sectional rank
        base_scout_score = raw_score
        direction = "call" if raw_score >= 0 else "put"
    else:
        technical_score, empirical_score, base_scout_score = _heuristic_scout_score(
            momentum_5d, momentum_20d, rsi_14, realized_vol_20d,
            atr_pct_14d, z_score, 0.0,
        )
        direction = "call" if technical_score >= 0 else "put"
        raw_score = None   

    side_probs, side_model_mode = (
        _ml_side_probabilities(feats, base_scout_score)
        if using_ml
        else (_side_aware_probabilities(base_scout_score), "heuristic_three_class")
    )
    side_activation_mode = _side_model_activation_mode()
    if side_model_mode == "trained_option_payoff_three_class":
        option_payoff_score = _clip(side_probs["call_edge"] - side_probs["put_edge"])
        diagnostics["side_model_override"] = {
            "target": "strict_real_option_payoff",
            "mode": side_activation_mode,
            "directional_model_score": round(float(raw_score if raw_score is not None else 0.0), 4),
            "option_payoff_score": round(float(option_payoff_score), 4),
        }
        if side_activation_mode == "active":
            direction = "call" if option_payoff_score >= 0 else "put"
            base_scout_score = option_payoff_score
            technical_score = option_payoff_score

    conviction_score = (
        base_scout_score
        if side_model_mode == "trained_option_payoff_three_class" and side_activation_mode == "active"
        else (raw_score if using_ml else technical_score)
    )
    diagnostics["pre_veto_direction"] = direction
    diagnostics["conviction_score"] = round(float(conviction_score), 4)
    diagnostics["base_scout_score"] = round(float(base_scout_score), 4)
    diagnostics["primary_target"] = primary_target
    diagnostics["target_description"] = target_description
    diagnostics["event_dataset_features"] = event_context
    diagnostics["side_aware"] = {
        "model_mode": side_model_mode,
        **side_probs,
    }
    shadow_guard_passed, shadow_guard = _apply_shadow_side_guard(
        direction=direction,
        side_probs=side_probs,
        side_model_mode=side_model_mode,
        side_activation_mode=side_activation_mode,
    )
    diagnostics["side_aware"]["shadow_guard"] = shadow_guard

    passed_alignment, regime_adjustment, rejection_reason, alignment_note = _apply_regime_alignment(
        direction=direction,
        conviction_score=float(conviction_score),
        regime=regime,
    )
    diagnostics["regime_adjustment"] = round(regime_adjustment, 4)
    diagnostics["counter_regime_survivor"] = bool(
        passed_alignment and regime_adjustment < 0 and regime.mode in {"risk_on", "risk_off"}
    )
    if not shadow_guard_passed:
        diagnostics["reason"] = shadow_guard["reason"]
        diagnostics["shadow_side_guard_note"] = shadow_guard["note"]
        return (None, diagnostics) if return_diagnostics else None
    if not passed_alignment:
        diagnostics["reason"] = rejection_reason
        return (None, diagnostics) if return_diagnostics else None

    scout_score = _clip(base_scout_score + regime_adjustment)

    # \u2500\u2500 AI Sentinel overlay \u2500\u2500
    ai_score = fetch_ai_multiplier(
        symbol,
        direction=direction,
        scout_score=scout_score,
        event_context=event_context,
    )
    scout_score = _clip(scout_score * ai_score.multiplier)
    if side_model_mode not in {"trained_underlying_three_class", "trained_option_payoff_three_class"}:
        side_probs = _side_aware_probabilities(scout_score)
        diagnostics["side_aware"].update(
            {
                "call_edge": side_probs["call_edge"],
                "put_edge": side_probs["put_edge"],
                "no_trade": side_probs["no_trade"],
            }
        )
    diagnostics["sentinel"] = {
        "multiplier": round(float(ai_score.multiplier), 4),
        "shadow_multiplier": round(float(ai_score.shadow_multiplier), 4),
        "mode": ai_score.mode,
        "catalyst": ai_score.catalyst,
        "rationale": ai_score.rationale,
        "sentiment_score": round(float(ai_score.sentiment_score), 4),
        "event_type": ai_score.event_type,
        "event_polarity": round(float(ai_score.event_polarity), 4),
        "directional_relevance": ai_score.directional_relevance,
        "novelty": ai_score.novelty,
        "source_reliability": ai_score.source_reliability,
        "time_horizon": ai_score.time_horizon,
        "direction_1d": ai_score.direction_1d,
        "direction_3d": ai_score.direction_3d,
        "direction_5d": ai_score.direction_5d,
        "magnitude_bucket": ai_score.magnitude_bucket,
        "decay_half_life": ai_score.decay_half_life,
        "spot_vs_iv_effect": ai_score.spot_vs_iv_effect,
        "call_relevance": round(float(ai_score.call_relevance), 4),
        "put_relevance": round(float(ai_score.put_relevance), 4),
        "no_trade_relevance": round(float(ai_score.no_trade_relevance), 4),
        "confidence": round(float(ai_score.confidence), 4),
        "direction": ai_score.direction or direction,
        "source": ai_score.source,
        "headlines": list(ai_score.headlines or []),
        "event_context": dict(ai_score.event_context or event_context),
    }

    notes: list[str] = []
    if using_ml:
        probability_label = "p_call_edge" if primary_target == "strict_real_option_direction" else "prob_bull"
        notes.append(f"ML model active ({probability_label}={raw_score/2+0.5:.2%})")
    else:
        notes.append("heuristic fallback active (model not found)")
    if alignment_note:
        notes.append(alignment_note)
    if shadow_guard.get("applied") and shadow_guard.get("note"):
        notes.append(str(shadow_guard["note"]))
    if ai_score.multiplier != 1.0:
        notes.append(
            f"AI Sentinel ({ai_score.multiplier}x: {ai_score.catalyst}) \u2014 {ai_score.rationale}"
        )
    elif ai_score.shadow_multiplier != 1.0:
        notes.append(
            f"Sentinel event shadow-only ({ai_score.shadow_multiplier}x: {ai_score.event_type})"
        )
    if ai_score.time_horizon not in {"unknown", "intraday"} or ai_score.decay_half_life not in {"unknown", "intraday"}:
        notes.append(
            f"Sentinel horizon {ai_score.time_horizon} · decay {ai_score.decay_half_life}"
        )
    if event_feature_snapshot is not None:
        tags = event_feature_snapshot.dataset_tags or "event_dataset"
        notes.append(f"Dataset-backed event context active ({tags})")
    if abs(momentum_5d) > 0.035:
        notes.append("short-term momentum is strong")
    if 40.0 <= rsi_14 <= 60.0:
        notes.append("RSI is balanced, not yet extreme")
    if atr_pct_14d > 0.05:
        notes.append("ATR is elevated; move sizing matters")
    if z_score > 1.5:
        notes.append("volatility-adjusted relative strength outlier")

    signal = ScoutSignal(
        symbol=symbol,
        direction=direction,
        spot=round(spot, 4),
        momentum_5d=round(momentum_5d, 4),
        momentum_20d=round(momentum_20d, 4),
        rsi_14=round(rsi_14, 2),
        realized_vol_20d=round(realized_vol_20d, 4),
        atr_pct_14d=round(atr_pct_14d, 4),
        technical_score=round(technical_score, 4),
        empirical_score=round(empirical_score, 4),
        scout_score=round(scout_score, 4),
        call_edge_prob=side_probs["call_edge"],
        put_edge_prob=side_probs["put_edge"],
        no_trade_prob=side_probs["no_trade"],
        scout_model_mode=side_model_mode,
        sentinel_event=dict(diagnostics["sentinel"]),
        notes=notes,
    )
    diagnostics["passed"] = True
    diagnostics["reason"] = "selected"
    diagnostics["final_direction"] = direction
    diagnostics["final_scout_score"] = signal.scout_score
    return (signal, diagnostics) if return_diagnostics else signal


# \u2500\u2500 Universe scanner \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

def scan_symbols_with_diagnostics(
    symbols: Iterable[str],
) -> tuple[MarketRegime, list[ScoutSignal], dict[str, object]]:
    symbol_list = list(symbols)
    log.info("Starting scan for %d symbols", len(symbol_list))
    regime = infer_market_regime()
    log.info("Inferred market regime: %s (bias: %.4f)", regime.mode, regime.bias)
    
    signals: list[ScoutSignal] = []
    universe_data: dict[str, pd.DataFrame] = {}
    momentum_metrics: dict[str, float] = {}
    skipped_symbols: list[dict[str, object]] = []
    rejection_reasons: dict[str, int] = {}
    scout_diagnostics: dict[str, object] = {
        "symbols_requested": len(symbol_list),
        "symbols_with_history": 0,
        "symbols_with_features": 0,
        "pre_veto_direction_counts": _empty_direction_counts(),
        "final_direction_counts": _empty_direction_counts(),
        "counter_regime_survivors": 0,
        "side_aware_directional_disagreements": 0,
        "side_aware_no_trade_disagreements": 0,
        "shadow_side_veto_rejections": 0,
        "side_aware_scores": [],
        "sentinel_scores": [],
        "rejections": [],
        "settings": {
            "regime_same_side_bonus": REGIME_SAME_SIDE_BONUS,
            "regime_countertrend_penalty": REGIME_COUNTERTREND_PENALTY,
            "regime_countertrend_min_abs_score": REGIME_COUNTERTREND_MIN_ABS_SCORE,
        },
        "event_features": {
            "rows": 0,
            "symbols": 0,
        },
    }
    event_feature_store = load_event_feature_frame()
    if not event_feature_store.empty:
        scout_diagnostics["event_features"] = {
            "rows": int(len(event_feature_store)),
            "symbols": int(event_feature_store["symbol"].nunique()),
        }

    # Fetch SPY once for cross-asset features in ML model
    spy_frame = history("SPY", period="6mo")
    if spy_frame.empty:
        log.warning("SPY history unavailable; cross-asset features will be disabled.")
        spy_frame = None

    # Pre-fetch and compute metrics for cross-sectional Z-scoring
    for symbol in symbol_list:
        cleaned = symbol.strip().upper()
        if not cleaned:
            continue
        try:
            frame = history(cleaned, period="6mo")
            if frame.empty:
                skipped_symbols.append({"symbol": cleaned, "reason": "empty_history"})
                continue
                
            close = pd.to_numeric(frame.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
            if len(close) < 62:
                skipped_symbols.append({"symbol": cleaned, "reason": "insufficient_history"})
                continue
            spot = float(close.iloc[-1])
            momentum_20d     = float(spot / close.iloc[-21] - 1.0)
            realized_vol_20d = float(close.pct_change().rolling(20).std().iloc[-1] * (252 ** 0.5))
            vol_adj_momentum = momentum_20d / max(realized_vol_20d, 0.05)

            universe_data[cleaned]      = frame
            momentum_metrics[cleaned]   = vol_adj_momentum
        except Exception as exc:
            log.debug("Skipping %s due to error: %s", cleaned, exc)
            skipped_symbols.append({"symbol": cleaned, "reason": "history_error", "error": str(exc)})
            continue

    log.info("Successfully fetched data for %d/%d symbols.", len(universe_data), len(symbol_list))
    scout_diagnostics["symbols_with_history"] = len(universe_data)
    scout_diagnostics["symbols_with_features"] = len(momentum_metrics)
    z_scores = _calculate_z_scores(momentum_metrics)

    for cleaned, frame in universe_data.items():
        z_score = z_scores.get(cleaned, 0.0)
        try:
            signal, signal_diagnostics = build_signal(
                cleaned,
                regime,
                frame,
                z_score,
                spy_frame,
                event_feature_store,
                return_diagnostics=True,
            )
        except Exception as exc:
            log.debug("Building signal failed for %s: %s", cleaned, exc)
            signal = None
            signal_diagnostics = {
                "symbol": cleaned,
                "passed": False,
                "reason": "signal_error",
                "error": str(exc),
            }
        pre_direction = signal_diagnostics.get("pre_veto_direction")
        if pre_direction in {"call", "put"}:
            scout_diagnostics["pre_veto_direction_counts"][str(pre_direction)] += 1
        if signal_diagnostics.get("counter_regime_survivor"):
            scout_diagnostics["counter_regime_survivors"] += 1
        side_aware = signal_diagnostics.get("side_aware")
        if isinstance(side_aware, dict):
            active_direction = signal.direction if signal is not None else signal_diagnostics.get("pre_veto_direction")
            preferred_side = _preferred_side_from_probabilities(side_aware)
            if side_aware.get("model_mode") == "trained_option_payoff_three_class":
                comparison_direction = active_direction if active_direction in {"call", "put"} else pre_direction
                if preferred_side == "no_trade":
                    scout_diagnostics["side_aware_no_trade_disagreements"] += 1
                elif comparison_direction in {"call", "put"} and preferred_side != comparison_direction:
                    scout_diagnostics["side_aware_directional_disagreements"] += 1
            shadow_guard = side_aware.get("shadow_guard") if isinstance(side_aware.get("shadow_guard"), dict) else {}
            if shadow_guard.get("reason") == "shadow_no_trade_veto" or signal_diagnostics.get("reason") in {
                "shadow_no_trade_veto",
                "shadow_direction_conflict",
            }:
                scout_diagnostics["shadow_side_veto_rejections"] += 1
            scout_diagnostics["side_aware_scores"].append(
                {
                    "symbol": cleaned,
                    "model_mode": side_aware.get("model_mode"),
                    "call_edge": side_aware.get("call_edge"),
                    "put_edge": side_aware.get("put_edge"),
                    "no_trade": side_aware.get("no_trade"),
                    "active_direction": active_direction if active_direction in {"call", "put"} else None,
                    "active_scout_score": signal.scout_score if signal is not None else None,
                    "pre_veto_direction": signal_diagnostics.get("pre_veto_direction"),
                    "shadow_preferred_side": shadow_guard.get("preferred_side", preferred_side),
                    "shadow_guard_applied": bool(shadow_guard.get("applied")),
                    "shadow_guard_reason": shadow_guard.get("reason"),
                    "passed": bool(signal is not None),
                    "reason": signal_diagnostics.get("reason"),
                }
            )
        sentinel = signal_diagnostics.get("sentinel")
        if isinstance(sentinel, dict):
            scout_diagnostics["sentinel_scores"].append(
                {
                    "symbol": cleaned,
                    "direction": sentinel.get("direction"),
                    "multiplier": sentinel.get("multiplier"),
                    "shadow_multiplier": sentinel.get("shadow_multiplier"),
                    "mode": sentinel.get("mode"),
                    "catalyst": sentinel.get("catalyst"),
                    "sentiment_score": sentinel.get("sentiment_score"),
                    "event_type": sentinel.get("event_type"),
                    "event_polarity": sentinel.get("event_polarity"),
                    "directional_relevance": sentinel.get("directional_relevance"),
                    "time_horizon": sentinel.get("time_horizon"),
                    "direction_1d": sentinel.get("direction_1d"),
                    "direction_3d": sentinel.get("direction_3d"),
                    "direction_5d": sentinel.get("direction_5d"),
                    "magnitude_bucket": sentinel.get("magnitude_bucket"),
                    "decay_half_life": sentinel.get("decay_half_life"),
                    "spot_vs_iv_effect": sentinel.get("spot_vs_iv_effect"),
                    "call_relevance": sentinel.get("call_relevance"),
                    "put_relevance": sentinel.get("put_relevance"),
                    "no_trade_relevance": sentinel.get("no_trade_relevance"),
                    "confidence": sentinel.get("confidence"),
                    "source": sentinel.get("source"),
                }
            )
        if signal is not None:
            signals.append(signal)
            scout_diagnostics["final_direction_counts"][signal.direction] += 1
        else:
            reason = str(signal_diagnostics.get("reason") or "unknown")
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            scout_diagnostics["rejections"].append(signal_diagnostics)

    signals.sort(key=lambda row: abs(row.scout_score), reverse=True)
    scout_diagnostics["skipped_symbols"] = skipped_symbols
    scout_diagnostics["rejection_counts"] = [
        {"reason": reason, "count": count}
        for reason, count in sorted(rejection_reasons.items(), key=lambda item: (-item[1], item[0]))
    ]
    log.info("Generated %d valid scout signals.", len(signals))
    return regime, signals, scout_diagnostics


def scan_symbols(symbols: Iterable[str]) -> tuple[MarketRegime, list[ScoutSignal]]:
    regime, signals, _ = scan_symbols_with_diagnostics(symbols)
    return regime, signals
