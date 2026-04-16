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
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .market_data import history
from .schemas import MarketRegime, ScoutSignal
from .sentinel import fetch_ai_multiplier

log = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).parent / "models"
_MODEL_PATH = _MODEL_DIR / "scout_model.pkl"
_SCALER_PATH = _MODEL_DIR / "scout_scaler.pkl"
REGIME_SAME_SIDE_BONUS = 0.08
REGIME_COUNTERTREND_PENALTY = 0.18
REGIME_COUNTERTREND_MIN_ABS_SCORE = 0.35


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
        return model, meta["scaler"], meta["feature_cols"]
    except Exception as exc:
        log.warning("Failed to load Scout model (%s) — using heuristic fallback.", exc)
        return None


# ── Utilities ────────────────────────────────────────────────────────────────

def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _empty_direction_counts() -> dict[str, int]:
    return {"call": 0, "put": 0}


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

    model, scaler, feature_cols = loaded
    row = np.array([[feats.get(col, 0.0) for col in feature_cols]])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        row_scaled = scaler.transform(row)
        prob_bull = float(model.predict_proba(row_scaled)[0][1])

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

    feats = _extract_features(close, frame, spy_close)
    ml_score = _ml_scout_score(feats)
    using_ml = ml_score is not None

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

    conviction_score = raw_score if using_ml else technical_score
    diagnostics["pre_veto_direction"] = direction
    diagnostics["conviction_score"] = round(float(conviction_score), 4)
    diagnostics["base_scout_score"] = round(float(base_scout_score), 4)

    passed_alignment, regime_adjustment, rejection_reason, alignment_note = _apply_regime_alignment(
        direction=direction,
        conviction_score=float(conviction_score),
        regime=regime,
    )
    diagnostics["regime_adjustment"] = round(regime_adjustment, 4)
    diagnostics["counter_regime_survivor"] = bool(
        passed_alignment and regime_adjustment < 0 and regime.mode in {"risk_on", "risk_off"}
    )
    if not passed_alignment:
        diagnostics["reason"] = rejection_reason
        return (None, diagnostics) if return_diagnostics else None

    scout_score = _clip(base_scout_score + regime_adjustment)

    # \u2500\u2500 AI Sentinel overlay \u2500\u2500
    ai_score = fetch_ai_multiplier(symbol)
    scout_score = _clip(scout_score * ai_score.multiplier)

    notes: list[str] = []
    if using_ml:
        notes.append(f"ML model active (prob_bull={raw_score/2+0.5:.2%})")
    else:
        notes.append("heuristic fallback active (model not found)")
    if alignment_note:
        notes.append(alignment_note)
    if ai_score.multiplier != 1.0:
        notes.append(
            f"AI Sentinel ({ai_score.multiplier}x: {ai_score.catalyst}) \u2014 {ai_score.rationale}"
        )
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
        "rejections": [],
        "settings": {
            "regime_same_side_bonus": REGIME_SAME_SIDE_BONUS,
            "regime_countertrend_penalty": REGIME_COUNTERTREND_PENALTY,
            "regime_countertrend_min_abs_score": REGIME_COUNTERTREND_MIN_ABS_SCORE,
        },
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
