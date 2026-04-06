import logging
import ast
from datetime import date
from pathlib import Path
import pandas as pd
from math import erf, exp, log as math_log, sqrt

log = logging.getLogger(__name__)

# B-S Fallback Helpers ========================================================

def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))

def _bs_price(spot: float, strike: float, tte: float, vol: float, rf: float = 0.04, option_type: str = "call"):
    if spot <= 0 or strike <= 0 or tte <= 0 or vol <= 0: return 0.0
    d1 = (math_log(spot / strike) + (rf + 0.5 * vol * vol) * tte) / (vol * sqrt(tte))
    d2 = d1 - vol * sqrt(tte)
    if option_type == "call": return spot * _normal_cdf(d1) - strike * exp(-rf * tte) * _normal_cdf(d2)
    return strike * exp(-rf * tte) * _normal_cdf(-d2) - spot * _normal_cdf(-d1)

def _bs_delta(spot: float, strike: float, tte: float, vol: float, rf: float = 0.04, option_type: str = "call"):
    if spot <= 0 or strike <= 0 or tte <= 0 or vol <= 0: return None
    d1 = (math_log(spot / strike) + (rf + 0.5 * vol * vol) * tte) / (vol * sqrt(tte))
    return _normal_cdf(d1) if option_type == "call" else _normal_cdf(d1) - 1.0

# Loader ======================================================================

class HistoricalOptionsProvider:
    """
    Scans engine/data/optionsdx for CSVs and loads real EOD option chains.
    Falls back to a synthetic B-S chain if actual data is missing so backtests
    keep running while waiting for users to download terabytes of data.
    """
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._chain_cache: dict[tuple[date, str], pd.DataFrame | None] = {}
        log.info(f"Initialized HistoricalOptionsProvider at {self.data_dir}")

    def get_chain(self, symbol: str, as_of: date, fallback_spot: float = 0, fallback_vol: float = 0.35) -> pd.DataFrame:
        key = (as_of, symbol)
        if key in self._chain_cache:
            return self._chain_cache[key]

        # Look for OptionsDX format CSVs containing this date
        # Expected naming: optionsdx_{symbol}.csv or spydx_2026.csv etc
        frames = []
        for csv_path in self.data_dir.glob("*.csv"):
            try:
                # Naive fast read just to see if the date is in it
                # In prod, we'd index this via DuckDB or partitioned Parquet
                chunk = pd.read_csv(csv_path)
                
                # Basic standardization of OptionsDX columns
                if "quote_date" in chunk.columns:
                    chunk["quote_date"] = pd.to_datetime(chunk["quote_date"]).dt.date
                    day_data = chunk[(chunk["quote_date"] == as_of) & (chunk["underlying_symbol"] == symbol)]
                    if not day_data.empty:
                        frames.append(day_data)
            except Exception as e:
                log.warning(f"Failed to read options data {csv_path}: {e}")

        if frames:
            result = pd.concat(frames)
            self._chain_cache[key] = result
            return result
        
        # If no real data, generate a synthetic B-S fallback chain
        if fallback_spot > 0:
            return self._generate_synthetic_chain(symbol, as_of, fallback_spot, fallback_vol)

        return pd.DataFrame()

    def _generate_synthetic_chain(self, symbol: str, as_of: date, spot: float, vol: float) -> pd.DataFrame:
        """Synthesizes a chain when real OptionsDX data is absent."""
        # Find the upcoming Friday
        days_ahead = 4 - as_of.weekday()
        if days_ahead < 0: days_ahead += 7
        if days_ahead == 0: days_ahead = 7  # If Friday, give next Friday
        
        expire_date = as_of + pd.Timedelta(days=days_ahead)
        tte = max(days_ahead / 365.0, 1.0/365.0)

        rows = []
        for direction in ["call", "put"]:
            for pct in [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08]:
                strike = spot * (1 + pct) if direction == "call" else spot * (1 - pct)
                strike = round(strike, 2)
                mid = _bs_price(spot, strike, tte, vol, 0.04, direction)
                if mid < 0.05: continue
                spread = max(0.05, mid * 0.10)
                ask = mid + spread / 2
                bid = max(0.01, mid - spread / 2)
                delta = _bs_delta(spot, strike, tte, vol, 0.04, direction)

                rows.append({
                    "quote_date": as_of,
                    "underlying_symbol": symbol,
                    "expire_date": expire_date,
                    "strike": strike,
                    "option_type": "C" if direction == "call" else "P",
                    "bid": bid,
                    "ask": ask,
                    "implied_volatility": vol,
                    "delta": delta,
                })

        df = pd.DataFrame(rows)
        self._chain_cache[(as_of, symbol)] = df
        return df
