from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import erf, exp, log, sqrt
from typing import Iterable

import pandas as pd
import yfinance as yf


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


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
    minimum_days: int = 2,
    maximum_days: int = 8,
    today: date | None = None,
) -> str | None:
    today = today or date.today()
    parsed: list[tuple[str, int]] = []
    for raw in expiries:
        try:
            days = (date.fromisoformat(raw) - today).days
        except ValueError:
            continue
        if minimum_days <= days <= maximum_days:
            parsed.append((raw, days))
    if not parsed:
        return None
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

