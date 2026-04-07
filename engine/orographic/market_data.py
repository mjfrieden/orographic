from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import erf, exp, log, sqrt
from typing import Iterable

import pandas as pd
import yfinance as yf

# Session-level cache for the risk-free rate (fetched once per process)
_RF_RATE_CACHE: float | None = None


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def fetch_risk_free_rate() -> float:
    """
    Returns the current annualised risk-free rate sourced from the
    3-month US T-Bill yield (^IRX, reported as a percentage by yfinance).
    Result is cached for the life of the process. Falls back to 0.043
    if the data feed is unavailable.
    """
    global _RF_RATE_CACHE
    if _RF_RATE_CACHE is not None:
        return _RF_RATE_CACHE
    try:
        irx = yf.Ticker("^IRX").history(period="5d", interval="1d", auto_adjust=False)
        if isinstance(irx.columns, pd.MultiIndex):
            irx.columns = [c[0] if isinstance(c, tuple) else c for c in irx.columns]
        close = pd.to_numeric(irx["Close"], errors="coerce").dropna()
        if not close.empty:
            # ^IRX is quoted as a percentage (e.g. 4.85 means 4.85%)
            _RF_RATE_CACHE = round(float(close.iloc[-1]) / 100.0, 5)
            return _RF_RATE_CACHE
    except Exception:
        pass
    _RF_RATE_CACHE = 0.043   # Sensible fallback (approx current FF rate)
    return _RF_RATE_CACHE


def compute_iv_rank(symbol: str, iv_now: float, lookback_days: int = 252) -> float:
    """
    Compute IV Rank (IVR) for `symbol`: the percentile of `iv_now` relative
    to the range of implied-volatility proxy values over the past `lookback_days`.

    We approximate historical IV via the rolling 20-day realised volatility of
    the underlying (no subscription required). IVR = 0 means IV at its low;
    IVR = 1 means IV at its cycle high.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1y", interval="1d", auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        close = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(close) < 30:
            return 0.5
        # Rolling 20d realised vol as IV proxy (annualised)
        rv_series = close.pct_change().rolling(20).std() * (252 ** 0.5)
        rv_series = rv_series.dropna().tail(lookback_days)
        if rv_series.empty:
            return 0.5
        iv_min = float(rv_series.min())
        iv_max = float(rv_series.max())
        if iv_max <= iv_min:
            return 0.5
        return round((iv_now - iv_min) / (iv_max - iv_min), 4)
    except Exception:
        return 0.5


def black_scholes_delta(
    *,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    volatility: float,
    option_type: str,
) -> float | None:
    if spot <= 0 or strike <= 0 or time_to_expiry_years <= 0 or volatility <= 0:
        return None
    denom = volatility * sqrt(time_to_expiry_years)
    if denom <= 0:
        return None
    d1 = (
        log(spot / strike)
        + (risk_free_rate + 0.5 * volatility * volatility) * time_to_expiry_years
    ) / denom
    if option_type == "call":
        return normal_cdf(d1)
    return normal_cdf(d1) - 1.0


def history(symbol: str, period: str = "6mo") -> pd.DataFrame:
    frame = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    if frame.empty:
        raise RuntimeError(f"No history returned for {symbol}")
    return frame


def option_expiries(symbol: str) -> list[str]:
    expiries = list(yf.Ticker(symbol).options)
    return [value for value in expiries if value]


def option_chain(symbol: str, expiry: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    chain = yf.Ticker(symbol).option_chain(expiry)
    return chain.calls.copy(), chain.puts.copy()


def next_expiry(
    expiries: Iterable[str],
    *,
    minimum_days: int = 1,
    maximum_days: int = 10,
    today: date | None = None,
) -> str | None:
    today = today or date.today()
    parsed: list[tuple[str, int]] = []
    for raw in expiries:
        try:
            # Ensure we're comparing date objects correctly across timezones/clock-skew
            expiry_date = date.fromisoformat(raw)
            days = (expiry_date - today).days
        except (ValueError, TypeError):
            continue
        
        # Only consider future expiries within the window
        if days >= minimum_days and days <= maximum_days:
            parsed.append((raw, days))
            
    if not parsed:
        return None
        
    # Sort by proximity and return the earliest valid expiry
    parsed.sort(key=lambda item: item[1])
    return parsed[0][0]


@dataclass
class CrossAssetSnapshot:
    spy_bias: float
    vix_level: float
    vix_change_5d: float


def cross_asset_snapshot() -> CrossAssetSnapshot:
    spy = history("SPY", period="3mo")
    vix = history("^VIX", period="3mo")

    spy_close = pd.to_numeric(spy["Close"], errors="coerce").dropna()
    vix_close = pd.to_numeric(vix["Close"], errors="coerce").dropna()

    if len(spy_close) < 21 or len(vix_close) < 6:
        return CrossAssetSnapshot(spy_bias=0.0, vix_level=0.0, vix_change_5d=0.0)

    spy_bias = float(spy_close.iloc[-1] / spy_close.iloc[-20] - 1.0)
    vix_level = float(vix_close.iloc[-1])
    vix_change_5d = float(vix_close.iloc[-1] / vix_close.iloc[-6] - 1.0)
    return CrossAssetSnapshot(
        spy_bias=spy_bias,
        vix_level=vix_level,
        vix_change_5d=vix_change_5d,
    )

