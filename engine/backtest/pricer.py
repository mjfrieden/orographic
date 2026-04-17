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
    entry_price = candidate.spread_cost if (candidate.is_spread and candidate.spread_cost) else candidate.ask
    if entry_price <= 0:
        return None
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
    exit_data_source = "missing"
    exit_quote_type = "missing"
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
            exit_price = float(match.iloc[0].get("bid", 0.0))
            exit_quote_type = "bid" if chain_source == "real_chain" else "modeled"
            if candidate.is_spread and candidate.short_strike:
                short_match = chain[
                    (chain["option_type"] == opt_type_char) &
                    (pd.to_datetime(chain["expire_date"]).dt.date == expiry_date) &
                    (round(chain["strike"], 2) == round(float(candidate.short_strike), 2))
                ]
                if not short_match.empty:
                    exit_price = max(0.0, exit_price - float(short_match.iloc[0].get("ask", 0.0)))
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
                    exit_data_source = "hybrid" if chain_source == "real_chain" else chain_source
                    exit_quote_type = "modeled"
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
    )
