from __future__ import annotations

from typing import Iterable

import pandas as pd

from .market_data import history
from .schemas import MarketRegime, ScoutSignal
from .sentinel import fetch_ai_multiplier


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))

def _calculate_z_scores(metrics: dict[str, float]) -> dict[str, float]:
    """Calculate cross-sectional Z-Scores for a dictionary of metric values."""
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
    low = pd.to_numeric(frame["Low"], errors="coerce")
    close = pd.to_numeric(frame["Close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(period).mean().iloc[-1]
    spot = close.iloc[-1]
    if pd.isna(atr) or pd.isna(spot) or float(spot) <= 0:
        return 0.0
    return float(atr / spot)


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

    spy_close = pd.to_numeric(spy["Close"], errors="coerce").dropna()
    vix_close = pd.to_numeric(vix["Close"], errors="coerce").dropna()
    if len(spy_close) < 21 or len(vix_close) < 6:
        return MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY")

    spy_5 = float(spy_close.iloc[-1] / spy_close.iloc[-6] - 1.0)
    spy_20 = float(spy_close.iloc[-1] / spy_close.iloc[-21] - 1.0)
    vix_level = float(vix_close.iloc[-1])
    vix_5 = float(vix_close.iloc[-1] / vix_close.iloc[-6] - 1.0)
    bias = _clip((spy_5 * 6.0) + (spy_20 * 4.0) - (vix_5 * 0.8) - ((vix_level - 20.0) / 35.0))

    if bias >= 0.18:
        mode = "risk_on"
    elif bias <= -0.18:
        mode = "risk_off"
    else:
        mode = "neutral"
    return MarketRegime(mode=mode, bias=round(bias, 4), source_symbol="SPY")


def build_signal(symbol: str, regime: MarketRegime, frame: pd.DataFrame, z_score: float) -> ScoutSignal | None:
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(close) < 60:
        return None

    spot = float(close.iloc[-1])
    momentum_5d = float(spot / close.iloc[-6] - 1.0)
    momentum_20d = float(spot / close.iloc[-21] - 1.0)
    realized_vol_20d = float(close.pct_change().rolling(20).std().iloc[-1] * (252 ** 0.5))
    rsi_14 = _rsi(close, period=14)
    atr_pct_14d = _atr_pct(frame, period=14)

    technical_score = _clip(
        momentum_5d * 7.0
        + momentum_20d * 5.0
        + ((rsi_14 - 50.0) / 25.0) * 0.6
        - max(realized_vol_20d - 0.55, 0.0) * 0.45
    )

    empirical_score = _clip(
        (z_score * 0.45)  # Replacing absolute momentum with Cross-Sectional Z-Score
        + (momentum_5d * 2.0)
        - max(atr_pct_14d - 0.045, 0.0) * 1.5
    )
    regime_bonus = 0.0
    direction = "call" if technical_score >= 0 else "put"
    if regime.mode == "risk_on" and direction == "call":
        regime_bonus = 0.08
    elif regime.mode == "risk_off" and direction == "put":
        regime_bonus = 0.08
    elif regime.mode != "neutral":
        regime_bonus = -0.08

    scout_score = _clip(0.58 * technical_score + 0.32 * empirical_score + regime_bonus)
    
    # --- AI Sentinel Overlap ---
    # Fetch AI Multiplier based on real-time news evaluation
    ai_score = fetch_ai_multiplier(symbol)
    
    # Supercharge or penalize the score
    scout_score = _clip(scout_score * ai_score.multiplier)
    
    notes: list[str] = []
    if ai_score.multiplier != 1.0:
        notes.append(f"AI Sentinel ({ai_score.multiplier}x multiplier: {ai_score.catalyst}) - {ai_score.rationale}")
        
    if abs(momentum_5d) > 0.035:
        notes.append("short-term momentum is strong")
    if 40.0 <= rsi_14 <= 60.0:
        notes.append("RSI is balanced, not yet extreme")
    if atr_pct_14d > 0.05:
        notes.append("ATR is elevated; move sizing matters")

    return ScoutSignal(
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


def scan_symbols(symbols: Iterable[str]) -> tuple[MarketRegime, list[ScoutSignal]]:
    regime = infer_market_regime()
    signals: list[ScoutSignal] = []
    
    universe_data = {}
    momentum_metrics = {}
    
    # Pre-fetch and calculate base metrics for cross-sectional ranking
    for symbol in symbols:
        cleaned = symbol.strip().upper()
        if not cleaned:
            continue
        try:
            frame = history(cleaned, period="6mo")
            close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
            if len(close) < 60:
                continue
            spot = float(close.iloc[-1])
            momentum_20d = float(spot / close.iloc[-21] - 1.0)
            realized_vol_20d = float(close.pct_change().rolling(20).std().iloc[-1] * (252 ** 0.5))
            
            # Volatility-Adjusted Momentum metric
            # Prevents rewarding high momentum that was achieved through pure variance
            vol_adj_momentum = momentum_20d / max(realized_vol_20d, 0.05)
            
            universe_data[cleaned] = frame
            momentum_metrics[cleaned] = vol_adj_momentum
        except Exception:
            continue
            
    # Calculate Z-Scores across the entire universe
    z_scores = _calculate_z_scores(momentum_metrics)

    # Build final statistical signals
    for cleaned, frame in universe_data.items():
        z_score = z_scores.get(cleaned, 0.0)
        try:
            signal = build_signal(cleaned, regime, frame, z_score)
        except Exception:
            signal = None
        if signal is not None:
            # Add context constraint
            if z_score > 1.5:
                signal.notes.append("volatility-adjusted relative strength outlier")
            signals.append(signal)

    signals.sort(key=lambda row: abs(row.scout_score), reverse=True)
    return regime, signals

