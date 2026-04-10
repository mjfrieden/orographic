import logging
from datetime import date
from pathlib import Path
import pandas as pd
from math import erf, exp, log as math_log, sqrt

from engine.backtest.options_store import (
    build_partitioned_store,
    load_coverage_manifest,
    manifest_path,
    partition_file_path,
    partition_root,
)

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
        self._chain_cache: dict[tuple[date, str], tuple[pd.DataFrame, str]] = {}
        self.partition_root = partition_root(self.data_dir)
        self._coverage_manifest = load_coverage_manifest(self.data_dir)
        log.info(f"Initialized HistoricalOptionsProvider at {self.data_dir}")

    def rebuild_store(self, *, force: bool = False) -> dict:
        manifest = build_partitioned_store(self.data_dir, force=force)
        self._coverage_manifest = manifest
        self._chain_cache.clear()
        return manifest

    def coverage_manifest(self) -> dict | None:
        if self._coverage_manifest is None and manifest_path(self.data_dir).exists():
            self._coverage_manifest = load_coverage_manifest(self.data_dir)
        return self._coverage_manifest

    def has_real_coverage(self, symbol: str, as_of: date) -> bool:
        manifest = self.coverage_manifest()
        symbol = symbol.upper()
        if manifest is not None:
            date_key = as_of.isoformat()
            if date_key in manifest.get("quote_dates", {}):
                return symbol in manifest["quote_dates"][date_key].get("symbols", [])
            return False
        return partition_file_path(self.data_dir, as_of, symbol).exists()

    def get_chain(self, symbol: str, as_of: date, fallback_spot: float = 0, fallback_vol: float = 0.35) -> pd.DataFrame:
        chain, _ = self.get_chain_with_source(
            symbol,
            as_of,
            fallback_spot=fallback_spot,
            fallback_vol=fallback_vol,
        )
        return chain

    def get_chain_with_source(
        self,
        symbol: str,
        as_of: date,
        fallback_spot: float = 0,
        fallback_vol: float = 0.35,
    ) -> tuple[pd.DataFrame, str]:
        key = (as_of, symbol)
        if key in self._chain_cache:
            cached_chain, cached_source = self._chain_cache[key]
            return cached_chain.copy(), cached_source

        partition_path = partition_file_path(self.data_dir, as_of, symbol)
        if partition_path.exists():
            try:
                result = pd.read_parquet(partition_path)
                self._chain_cache[key] = (result.copy(), "real_chain")
                return result, "real_chain"
            except Exception as e:
                log.warning(f"Failed to read partitioned options data {partition_path}: {e}")

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
            self._chain_cache[key] = (result.copy(), "real_chain")
            return result, "real_chain"
        
        # If no real data, generate a synthetic B-S fallback chain
        if fallback_spot > 0:
            result = self._generate_synthetic_chain(symbol, as_of, fallback_spot, fallback_vol)
            self._chain_cache[key] = (result.copy(), "synthetic_chain")
            return result, "synthetic_chain"

        empty = pd.DataFrame()
        self._chain_cache[key] = (empty.copy(), "missing")
        return empty, "missing"

    def _generate_synthetic_chain(self, symbol: str, as_of: date, spot: float, vol: float) -> pd.DataFrame:
        """Synthesizes a chain when real OptionsDX data is absent."""
        # Find the upcoming Friday
        days_ahead = 4 - as_of.weekday()
        if days_ahead < 0: days_ahead += 7
        if days_ahead == 0: days_ahead = 7  # If Friday, give next Friday
        
        expire_date = as_of + pd.Timedelta(days=days_ahead)
        tte = max(days_ahead / 365.0, 1.0/365.0)

        # Fixed strike increments ensure Monday/Friday lookups match exactly
        if spot < 50:
            step = 1.0
        elif spot < 150:
            step = 2.5
        else:
            step = 5.0

        base_strike = round(spot / step) * step
        
        rows = []
        for direction in ["call", "put"]:
            # Generate a wide enough range (-15 to +15 steps) to catch most weekly moves
            for offset in range(-15, 16):
                strike = base_strike + (offset * step)
                if strike <= 0:
                    continue
                
                mid = _bs_price(spot, strike, tte, vol, 0.04, direction)
                if mid < 0.01:
                    continue
                
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
        return df
