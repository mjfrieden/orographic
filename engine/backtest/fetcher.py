"""
engine/backtest/fetcher.py

Historical equity price fetcher with local Parquet cache.
Uses yfinance for equity OHLCV — no expensive data subscription needed.
Option pricing is reconstructed via Black-Scholes using historical realized vol.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── Equity history ──────────────────────────────────────────────────────────

def _cache_path(symbol: str, kind: str = "equity") -> Path:
    return CACHE_DIR / f"{symbol.upper()}_{kind}.parquet"


def fetch_equity_history(
    symbol: str,
    start: date,
    end: date,
    *,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Return daily OHLCV for `symbol` between `start` and `end`.
    Results are cached locally as Parquet for fast subsequent loads.
    """
    path = _cache_path(symbol, "equity")

    if path.exists() and not force_refresh:
        cached = pd.read_parquet(path)
        cached.index = pd.to_datetime(cached.index, utc=True)
        # Check if cache covers the requested window
        cached_start = cached.index.min().date()
        cached_end = cached.index.max().date()
        if cached_start <= start and cached_end >= end:
            mask = (cached.index.date >= start) & (cached.index.date <= end)
            return cached[mask].copy()

    log.info("Fetching equity history for %s (%s → %s)", symbol, start, end)
    ticker = yf.Ticker(symbol)
    # Fetch a little extra on each side for rolling calc headroom
    fetch_start = start - timedelta(days=90)
    frame = ticker.history(
        start=fetch_start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
    )
    if frame.empty:
        raise RuntimeError(f"No equity history returned for {symbol}")

    # Flatten any MultiIndex columns from recent yfinance versions
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [col[0] if isinstance(col, tuple) else col for col in frame.columns]

    frame.to_parquet(path)
    mask = (frame.index.date >= start) & (frame.index.date <= end)
    return frame[mask].copy()


def get_price_on(symbol: str, target_date: date, history_df: pd.DataFrame) -> float | None:
    """Return the closing price for `symbol` on `target_date` (or nearest prior trading day)."""
    mask = history_df.index.date <= target_date
    subset = history_df[mask]
    if subset.empty:
        return None
    close = pd.to_numeric(subset["Close"], errors="coerce").dropna()
    if close.empty:
        return None
    return float(close.iloc[-1])


def get_open_on(symbol: str, target_date: date, history_df: pd.DataFrame) -> float | None:
    """Return the opening price for `symbol` on `target_date`."""
    mask = history_df.index.date == target_date
    subset = history_df[mask]
    if subset.empty:
        # Fall back to nearest prior close
        return get_price_on(symbol, target_date, history_df)
    open_px = pd.to_numeric(subset["Open"], errors="coerce").dropna()
    if open_px.empty:
        return None
    return float(open_px.iloc[0])


def realized_vol_as_of(history_df: pd.DataFrame, as_of: date, window: int = 20) -> float:
    """
    Annualised realised volatility calculated from the `window` trading days
    ending on (or before) `as_of`.
    """
    mask = history_df.index.date <= as_of
    close = pd.to_numeric(history_df[mask]["Close"], errors="coerce").dropna()
    if len(close) < window + 1:
        return 0.40  # Sensible fallback for illiquid / short histories
    daily_ret = close.pct_change().dropna().tail(window)
    return float(daily_ret.std() * (252 ** 0.5))


# ── Trading calendar helpers ────────────────────────────────────────────────

def mondays_in_range(start: date, end: date) -> list[date]:
    """Return all Mondays (weekday == 0) between start and end inclusive."""
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() == 0:
            days.append(current)
        current += timedelta(days=1)
    return days


def friday_of_week(monday: date) -> date:
    """Return the Friday of the same ISO week as `monday`."""
    return monday + timedelta(days=4)


def nearest_trading_day(
    target: date,
    history_df: pd.DataFrame,
    direction: str = "back",
) -> date | None:
    """
    Return the nearest date in history_df that has a row.
    direction='back' looks backward (Friday close), 'forward' looks ahead (Monday open).
    """
    available = set(d for d in history_df.index.date)
    delta = -1 if direction == "back" else 1
    candidate = target
    for _ in range(7):
        if candidate in available:
            return candidate
        candidate += timedelta(days=delta)
    return None
