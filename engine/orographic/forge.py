from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from .market_data import black_scholes_delta, next_expiry, option_chain, option_expiries
from .schemas import ContractCandidate, MarketRegime, ScoutSignal


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _breakeven_move_pct(option_type: str, spot: float, strike: float, premium: float) -> float:
    if spot <= 0:
        return 1.0
    if option_type == "call":
        return max((strike + premium) / spot - 1.0, 0.0)
    return max(1.0 - (strike - premium) / spot, 0.0)


def _intrinsic(option_type: str, spot: float, strike: float) -> float:
    if option_type == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def _projected_move_pct(signal: ScoutSignal, regime: MarketRegime) -> float:
    base = max(abs(signal.momentum_5d), signal.atr_pct_14d * 1.15, 0.018)
    lift = max(abs(signal.scout_score), 0.05) * 0.06
    regime_lift = 0.01 if regime.mode != "neutral" else 0.0
    return min(base + lift + regime_lift, 0.18)


def _candidate_moneyness(option_type: str, spot: float, strike: float) -> float:
    if spot <= 0:
        return 0.0
    if option_type == "call":
        return strike / spot - 1.0
    return 1.0 - strike / spot


def rank_contracts(
    signals: Iterable[ScoutSignal],
    regime: MarketRegime,
    *,
    minimum_days_to_expiry: int = 2,
    maximum_days_to_expiry: int = 8,
    max_premium: float = 1.6,
    max_spread_pct: float = 0.18,
    min_open_interest: int = 150,
    min_volume: int = 25,
    min_abs_delta: float = 0.50,
    max_abs_delta: float = 0.85,
    risk_free_rate: float = 0.04,
) -> list[ContractCandidate]:
    candidates: list[ContractCandidate] = []
    today = date.today()

    for signal in signals:
        expiry = next_expiry(
            option_expiries(signal.symbol),
            minimum_days=minimum_days_to_expiry,
            maximum_days=maximum_days_to_expiry,
            today=today,
        )
        if not expiry:
            continue

        calls, puts = option_chain(signal.symbol, expiry)
        frame = calls if signal.direction == "call" else puts
        if frame.empty:
            continue

        clean = frame.copy()
        clean["bid"] = pd.to_numeric(clean.get("bid"), errors="coerce")
        clean["ask"] = pd.to_numeric(clean.get("ask"), errors="coerce")
        clean["lastPrice"] = pd.to_numeric(clean.get("lastPrice"), errors="coerce")
        clean["strike"] = pd.to_numeric(clean.get("strike"), errors="coerce")
        clean["openInterest"] = pd.to_numeric(clean.get("openInterest"), errors="coerce").fillna(0)
        clean["volume"] = pd.to_numeric(clean.get("volume"), errors="coerce").fillna(0)
        clean["impliedVolatility"] = pd.to_numeric(clean.get("impliedVolatility"), errors="coerce").fillna(0.45)
        clean = clean.dropna(subset=["bid", "ask", "strike"])
        clean = clean[(clean["bid"] > 0) & (clean["ask"] > 0)]
        if clean.empty:
            continue

        projected_move_pct = _projected_move_pct(signal, regime)
        projected_spot = signal.spot * (1 + projected_move_pct if signal.direction == "call" else 1 - projected_move_pct)
        days_to_expiry = max((date.fromisoformat(expiry) - today).days, 1)
        time_to_expiry_years = max(days_to_expiry / 365.0, 1.0 / 365.0)

        for _, row in clean.iterrows():
            bid = float(row["bid"])
            ask = float(row["ask"])
            premium = float(ask)
            if premium <= 0 or premium > max_premium:
                continue
            mid = (bid + ask) / 2.0
            if mid <= 0:
                continue
            spread_pct = (ask - bid) / mid
            if spread_pct > max_spread_pct:
                continue

            open_interest = int(float(row["openInterest"]))
            volume = int(float(row["volume"]))
            if open_interest < min_open_interest or volume < min_volume:
                continue

            strike = float(row["strike"])
            option_type = signal.direction
            # Structural shift: Target ITM to ATM options rather than OTM lotteries
            moneyness = _candidate_moneyness(option_type, signal.spot, strike)
            if moneyness < -0.05 or moneyness > 0.03:
                continue

            projected_value = _intrinsic(option_type, projected_spot, strike)
            expected_return_pct = projected_value / premium - 1.0
            breakeven_move_pct = _breakeven_move_pct(option_type, signal.spot, strike, premium)
            intrinsic_now = _intrinsic(option_type, signal.spot, strike)
            extrinsic_ratio = max(premium - intrinsic_now, 0.0) / premium if premium > 0 else 1.0
            iv = max(float(row["impliedVolatility"]), 0.10)
            target_iv = 0.35
            allocation_weight = round(min(max(target_iv / iv, 0.25), 3.0), 4)

            delta = black_scholes_delta(
                spot=signal.spot,
                strike=strike,
                time_to_expiry_years=time_to_expiry_years,
                risk_free_rate=risk_free_rate,
                volatility=iv,
                option_type=option_type,
            )
            if delta is None or abs(delta) < min_abs_delta or abs(delta) > max_abs_delta:
                continue
            liquidity_score = _clip(
                0.45
                + 0.18 * min(open_interest / 800.0, 1.0)
                + 0.18 * min(volume / 300.0, 1.0)
                - 0.35 * min(spread_pct / max_spread_pct, 1.0)
            )
            
            # Variance Risk Premium (VRP) Check
            # If the option's Implied Volatility is massively higher than the underlying's Realized Volatility, 
            # it means the market maker is charging a huge premium. We heavily penalize this.
            vrp_penalty = max(iv - signal.realized_vol_20d - 0.10, 0.0) * 2.0

            economics_score = _clip(
                0.50
                + 0.25 * min(expected_return_pct / 1.5, 1.0)
                + 0.15 * min((projected_move_pct - breakeven_move_pct) / 0.05, 1.0)
                + 0.10 * (1.0 - min(extrinsic_ratio, 1.0))
                - 0.15 * max(extrinsic_ratio - 0.90, 0.0) / 0.10
                - vrp_penalty
            )
            forge_score = _clip(
                0.45 * ((signal.scout_score + 1.0) / 2.0)
                + 0.30 * liquidity_score
                + 0.25 * economics_score
            )
            notes: list[str] = []
            if expected_return_pct > 1.0:
                notes.append("projected payoff is asymmetric")
            if extrinsic_ratio < 0.8:
                notes.append("time-value burden is acceptable")
            if vrp_penalty > 0.05:
                notes.append(f"VRP penalty applied: IV is highly elevated over RV")
            if delta is not None and 0.20 <= abs(delta) <= 0.45:
                notes.append("delta sits in the preferred weekly range")

            candidates.append(
                ContractCandidate(
                    symbol=signal.symbol,
                    contract_symbol=str(row.get("contractSymbol", "")),
                    option_type=option_type,
                    expiry=expiry,
                    strike=round(strike, 4),
                    bid=round(bid, 4),
                    ask=round(ask, 4),
                    last=round(float(row.get("lastPrice", 0.0) or 0.0), 4),
                    premium=round(premium, 4),
                    contract_cost=round(premium * 100.0, 2),
                    spread_pct=round(spread_pct, 4),
                    open_interest=open_interest,
                    volume=volume,
                    implied_volatility=round(float(row["impliedVolatility"]), 4),
                    delta=round(delta, 4) if delta is not None else None,
                    moneyness=round(moneyness, 4),
                    projected_move_pct=round(projected_move_pct, 4),
                    breakeven_move_pct=round(breakeven_move_pct, 4),
                    expected_return_pct=round(expected_return_pct, 4),
                    extrinsic_ratio=round(extrinsic_ratio, 4),
                    scout_score=signal.scout_score,
                    forge_score=round(forge_score, 4),
                    allocation_weight=allocation_weight,
                    notes=notes,
                )
            )

    candidates.sort(key=lambda row: row.forge_score, reverse=True)
    return candidates
