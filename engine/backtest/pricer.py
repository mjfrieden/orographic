"""
engine/backtest/pricer.py

Computes entry and exit prices for replayed candidates.

Entry: Monday open price from history (or nearest prior close) → B-S ask
Exit:  Friday close price from history → intrinsic value + residual time value
P&L:   (exit_price - entry_price) * 100 shares per contract
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from math import erf, exp, log as math_log, sqrt

import pandas as pd

from engine.orographic.schemas import ContractCandidate
from engine.backtest.fetcher import get_open_on, get_price_on, nearest_trading_day
from engine.backtest.options_provider import HistoricalOptionsProvider

log = logging.getLogger(__name__)

BUDGET_PER_TRADE = 300.0
HARD_COST_CEILING_USD = 600.0


def _source_score(source: str) -> float:
    return {
        "real_chain": 1.0,
        "hybrid": 0.5,
    }.get(source, 0.0)


def _coverage_pct(entry_source: str, exit_source: str) -> float:
    return round((_source_score(entry_source) + _source_score(exit_source)) / 2.0, 4)


def _get_chain_with_source(
    options_provider: HistoricalOptionsProvider,
    symbol: str,
    as_of: date,
    *,
    fallback_spot: float,
    fallback_vol: float,
) -> tuple[pd.DataFrame, str]:
    getter = getattr(options_provider, "get_chain_with_source", None)
    if callable(getter):
        return getter(
            symbol,
            as_of,
            fallback_spot=fallback_spot,
            fallback_vol=fallback_vol,
        )
    return options_provider.get_chain(
        symbol,
        as_of,
        fallback_spot=fallback_spot,
        fallback_vol=fallback_vol,
    ), "real_chain"


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _bs_price(
    spot: float,
    strike: float,
    tte: float,
    vol: float,
    rf: float = 0.04,
    option_type: str = "call",
) -> float:
    if spot <= 0 or strike <= 0 or tte <= 0 or vol <= 0:
        return 0.0
    d1 = (math_log(spot / strike) + (rf + 0.5 * vol * vol) * tte) / (vol * sqrt(tte))
    d2 = d1 - vol * sqrt(tte)
    if option_type == "call":
        return spot * _normal_cdf(d1) - strike * exp(-rf * tte) * _normal_cdf(d2)
    return strike * exp(-rf * tte) * _normal_cdf(-d2) - spot * _normal_cdf(-d1)


@dataclass
class TradeLeg:
    symbol: str
    contract_symbol: str
    option_type: str
    strike: float
    expiry: str
    entry_date: date
    exit_date: date | None

    entry_spot: float          # underlying price at entry
    exit_spot: float | None    # underlying price at exit

    entry_price: float         # option premium paid (per share)
    exit_price: float          # option value at exit (per share)

    contracts: int             # number of contracts purchased
    cost_basis: float          # entry_price * 100 * contracts
    exit_value: float          # exit_price * 100 * contracts
    pnl: float                 # exit_value - cost_basis
    pnl_pct: float             # pnl / cost_basis

    expired_worthless: bool
    forge_score: float
    scout_score: float
    implied_volatility: float
    entry_data_source: str = "real_chain"
    exit_data_source: str = "real_chain"
    entry_quote_type: str = "ask"
    exit_quote_type: str = "bid"
    options_data_coverage_pct: float = 1.0
    entry_raw_price: float | None = None
    exit_raw_price: float | None = None
    entry_slippage_pct: float = 0.0
    exit_slippage_pct: float = 0.0
    entry_spread_pct: float | None = None
    exit_spread_pct: float | None = None
    entry_open_interest: int | None = None
    entry_volume: int | None = None
    exit_open_interest: int | None = None
    exit_volume: int | None = None


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _quote_spread_pct(row: pd.Series) -> float | None:
    bid = _safe_float(row.get("bid"), 0.0)
    ask = _safe_float(row.get("ask"), 0.0)
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return max(ask - bid, 0.0) / mid


def _passes_liquidity(
    *,
    spread_pct: float | None,
    max_spread_pct: float | None,
    open_interest: int | None,
    min_open_interest: int,
    volume: int | None,
    min_volume: int,
) -> bool:
    if max_spread_pct is not None and max_spread_pct > 0:
        if spread_pct is None or spread_pct > max_spread_pct:
            return False
    if min_open_interest > 0 and (open_interest is None or open_interest < min_open_interest):
        return False
    if min_volume > 0 and (volume is None or volume < min_volume):
        return False
    return True


def price_trade(
    candidate: ContractCandidate,
    monday: date,
    friday: date,
    history_df: pd.DataFrame,
    options_provider: HistoricalOptionsProvider,
    *,
    budget: float | None = None,
    hard_cost_ceiling: float | None = HARD_COST_CEILING_USD,
    strict_options_data: bool = False,
    entry_slippage_pct: float = 0.0,
    exit_slippage_pct: float = 0.0,
    max_entry_spread_pct: float | None = None,
    max_exit_spread_pct: float | None = None,
    min_entry_open_interest: int = 0,
    min_entry_volume: int = 0,
    min_exit_open_interest: int = 0,
    min_exit_volume: int = 0,
) -> TradeLeg | None:
    """
    Compute the P&L for entering a candidate on `monday` and exiting on `friday`.
    Entry price = candidate.ask (already computed by replay)
    Exit price  = Bid price from options provider on Friday
    """
    entry_date = nearest_trading_day(monday, history_df, direction="forward")
    exit_date = nearest_trading_day(friday, history_df, direction="back")

    if entry_date is None or exit_date is None:
        return None

    entry_spot = get_open_on(candidate.symbol, entry_date, history_df)
    exit_spot = get_price_on(candidate.symbol, exit_date, history_df)

    if entry_spot is None or exit_spot is None:
        return None

    # Entry: For spreads, use net debit. For naked, use ask.
    entry_raw_price = candidate.spread_cost if (candidate.is_spread and candidate.spread_cost) else candidate.ask
    if entry_raw_price <= 0:
        return None
    entry_spread_pct = _safe_float(getattr(candidate, "spread_pct", None), 0.0)
    entry_open_interest = _safe_int(getattr(candidate, "open_interest", None), 0)
    entry_volume = _safe_int(getattr(candidate, "volume", None), 0)
    if not _passes_liquidity(
        spread_pct=entry_spread_pct,
        max_spread_pct=max_entry_spread_pct,
        open_interest=entry_open_interest,
        min_open_interest=max(min_entry_open_interest, 0),
        volume=entry_volume,
        min_volume=max(min_entry_volume, 0),
    ):
        return None
    entry_slippage = max(entry_slippage_pct, 0.0)
    exit_slippage = max(exit_slippage_pct, 0.0)
    entry_price = entry_raw_price * (1.0 + entry_slippage)
    entry_data_source = getattr(candidate, "entry_data_source", "real_chain")
    entry_quote_type = getattr(candidate, "entry_quote_type", "ask")
    if strict_options_data and entry_data_source != "real_chain":
        return None

    # Contracts: how many fit in the dynamically volatility-scaled budget (minimum 1)
    # RUTHLESS REASONING: We now scale the $500 max by the ML scout_score (Confidence Sizing)
    # Map score [-1, 1] to a [0.2, 1.0] multiplier for the budget.
    confidence_scale = max(0.2, (candidate.scout_score + 1.0) / 2.0)
    base_budget = BUDGET_PER_TRADE if budget is None else max(float(budget), 0.0)
    effective_hard_ceiling = hard_cost_ceiling
    if effective_hard_ceiling is not None and effective_hard_ceiling <= 0:
        effective_hard_ceiling = None
    target_budget = base_budget * candidate.allocation_weight * confidence_scale
    actual_budget = min(target_budget, effective_hard_ceiling) if effective_hard_ceiling is not None else target_budget

    cost_per_contract = entry_price * 100.0
    contracts = int(actual_budget // cost_per_contract)
    if contracts < 1:
        return None
    cost_basis = cost_per_contract * contracts

    # Fetch Friday options chain to find the exit Bid
    # We fallback to 0.0 (expired worthless) if anything goes wrong
    exit_price = 0.0
    exit_raw_price = 0.0
    exit_data_source = "missing"
    exit_quote_type = "missing"
    exit_spread_pct: float | None = None
    exit_open_interest: int | None = None
    exit_volume: int | None = None
    chain, chain_source = _get_chain_with_source(
        options_provider,
        candidate.symbol,
        exit_date,
        fallback_spot=exit_spot,
        fallback_vol=candidate.implied_volatility,
    )
    if strict_options_data and chain_source != "real_chain":
        return None
    
    if not chain.empty:
        exit_data_source = chain_source
        opt_type_char = "C" if candidate.option_type == "call" else "P"
        expiry_date = pd.to_datetime(candidate.expiry).date()
        
        match = chain[
            (chain["option_type"] == opt_type_char) &
            (pd.to_datetime(chain["expire_date"]).dt.date == expiry_date) &
            (round(chain["strike"], 2) == round(candidate.strike, 2))
        ]
        if not match.empty:
            long_row = match.iloc[0]
            exit_spread_pct = _quote_spread_pct(long_row)
            exit_open_interest = _safe_int(long_row.get("open_interest", long_row.get("openInterest")), 0)
            exit_volume = _safe_int(long_row.get("trade_volume", long_row.get("volume")), 0)
            if not _passes_liquidity(
                spread_pct=exit_spread_pct,
                max_spread_pct=max_exit_spread_pct,
                open_interest=exit_open_interest,
                min_open_interest=max(min_exit_open_interest, 0),
                volume=exit_volume,
                min_volume=max(min_exit_volume, 0),
            ):
                return None
            exit_raw_price = _safe_float(long_row.get("bid"), 0.0)
            exit_price = exit_raw_price
            exit_quote_type = "bid" if chain_source == "real_chain" else "modeled"
            if candidate.is_spread and candidate.short_strike:
                short_match = chain[
                    (chain["option_type"] == opt_type_char) &
                    (pd.to_datetime(chain["expire_date"]).dt.date == expiry_date) &
                    (round(chain["strike"], 2) == round(float(candidate.short_strike), 2))
                ]
                if not short_match.empty:
                    exit_price = max(0.0, exit_price - _safe_float(short_match.iloc[0].get("ask"), 0.0))
                    exit_raw_price = exit_price
                    exit_quote_type = "net_bid_ask" if chain_source == "real_chain" else "modeled"
                elif strict_options_data:
                    return None
                else:
                    tte_exit = 1e-6
                    short_mid = _bs_price(
                        exit_spot,
                        float(candidate.short_strike),
                        tte_exit,
                        candidate.implied_volatility,
                        0.04,
                        candidate.option_type
                    )
                    exit_price = max(0.0, exit_price - (short_mid * 1.05))
                    exit_raw_price = exit_price
                    exit_data_source = "hybrid" if chain_source == "real_chain" else chain_source
                    exit_quote_type = "modeled"
            exit_price = max(0.0, exit_price * (1.0 - exit_slippage))
        else:
            if strict_options_data:
                return None
            # RUTHLESS REASONING: On Friday close, weeklys are essentially at zero TTE.
            # We use 1e-6 to avoid numerical division errors while stripping away the 'free' 2-day theta.
            tte_exit = 1e-6
            exit_price_mid = _bs_price(
                exit_spot, 
                float(candidate.strike), 
                tte_exit, 
                candidate.implied_volatility, 
                0.04, 
                candidate.option_type
            )
            exit_price_long = exit_price_mid * 0.95 # 5% Liquidity Haircut (Long Bid)

            exit_price_short = 0.0
            if candidate.is_spread and candidate.short_strike:
                # Value the short leg at Friday close (Exit Ask)
                short_mid = _bs_price(
                    exit_spot,
                    float(candidate.short_strike),
                    tte_exit,
                    candidate.implied_volatility,
                    0.04,
                    candidate.option_type
                )
                exit_price_short = short_mid * 1.05 # 5% Liquidity Premium (Short Ask to close)
                
            exit_price = max(0.0, exit_price_long - exit_price_short)
            exit_raw_price = exit_price
            exit_price = max(0.0, exit_price * (1.0 - exit_slippage))
            exit_data_source = "hybrid" if chain_source == "real_chain" else chain_source
            exit_quote_type = "modeled"

    expired_worthless = exit_price < 0.01
    exit_value = exit_price * 100.0 * contracts
    pnl = exit_value - cost_basis
    pnl_pct = pnl / cost_basis
    options_data_coverage_pct = _coverage_pct(entry_data_source, exit_data_source)

    return TradeLeg(
        symbol=candidate.symbol,
        contract_symbol=candidate.contract_symbol,
        option_type=candidate.option_type,
        strike=candidate.strike,
        expiry=candidate.expiry,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_spot=round(entry_spot, 4),
        exit_spot=round(exit_spot, 4),
        entry_price=round(entry_price, 4),
        exit_price=round(exit_price, 4),
        contracts=contracts,
        cost_basis=round(cost_basis, 2),
        exit_value=round(exit_value, 2),
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 4),
        expired_worthless=expired_worthless,
        forge_score=candidate.forge_score,
        scout_score=candidate.scout_score,
        implied_volatility=candidate.implied_volatility,
        entry_data_source=entry_data_source,
        exit_data_source=exit_data_source,
        entry_quote_type=entry_quote_type,
        exit_quote_type=exit_quote_type,
        options_data_coverage_pct=options_data_coverage_pct,
        entry_raw_price=round(entry_raw_price, 4),
        exit_raw_price=round(exit_raw_price, 4),
        entry_slippage_pct=round(entry_slippage, 4),
        exit_slippage_pct=round(exit_slippage, 4),
        entry_spread_pct=round(entry_spread_pct, 4),
        exit_spread_pct=round(exit_spread_pct, 4) if exit_spread_pct is not None else None,
        entry_open_interest=entry_open_interest,
        entry_volume=entry_volume,
        exit_open_interest=exit_open_interest,
        exit_volume=exit_volume,
    )
