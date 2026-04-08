from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from .market_data import (
    black_scholes_delta,
    compute_iv_rank,
    fetch_risk_free_rate,
    next_expiry,
    option_chain,
    option_expiries,
)
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


def _net_debit_cap(spot: float, base_cap: float) -> float:
    dynamic_cap = 2.25
    if spot >= 500:
        dynamic_cap = 4.5
    elif spot >= 300:
        dynamic_cap = 3.5
    elif spot >= 150:
        dynamic_cap = 2.75
    return max(base_cap, dynamic_cap)


def _long_leg_cap(net_debit_cap: float) -> float:
    return min(max(net_debit_cap * 4.0, 4.0), 20.0)


def _spread_cap(spot: float, base_cap: float) -> float:
    dynamic_cap = 0.18
    if spot >= 500:
        dynamic_cap = 0.28
    elif spot >= 300:
        dynamic_cap = 0.24
    return max(base_cap, dynamic_cap)


def _find_short_leg(
    short_pool: pd.DataFrame,
    *,
    option_type: str,
    long_strike: float,
    long_delta: float,
    spot: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    max_spread_pct: float,
) -> dict[str, float] | None:
    strike_filter = short_pool["strike"] > long_strike if option_type == "call" else short_pool["strike"] < long_strike
    candidates = short_pool[strike_filter].copy()
    if candidates.empty:
        return None

    mid = (candidates["bid"] + candidates["ask"]) / 2.0
    candidates = candidates[mid > 0].copy()
    if candidates.empty:
        return None

    candidates["spread_pct"] = (candidates["ask"] - candidates["bid"]) / ((candidates["bid"] + candidates["ask"]) / 2.0)
    candidates = candidates[candidates["spread_pct"] <= max_spread_pct].copy()
    if candidates.empty:
        return None

    candidates["delta"] = candidates.apply(
        lambda row: black_scholes_delta(
            spot=spot,
            strike=float(row["strike"]),
            time_to_expiry_years=time_to_expiry_years,
            risk_free_rate=risk_free_rate,
            volatility=max(float(row["impliedVolatility"]), 0.10),
            option_type=option_type,
        ),
        axis=1,
    )
    candidates = candidates[candidates["delta"].notna()].copy()
    if candidates.empty:
        return None

    target_abs_delta = max(0.10, min(0.30, abs(long_delta) * 0.55))
    candidates["target_distance"] = (candidates["delta"].abs() - target_abs_delta).abs()
    best = candidates.sort_values(["target_distance", "spread_pct", "ask"]).iloc[0]
    return {
        "strike": float(best["strike"]),
        "bid": float(best["bid"]),
        "ask": float(best["ask"]),
        "delta": float(best["delta"]),
    }


def rank_contracts_with_diagnostics(
    signals: Iterable[ScoutSignal],
    regime: MarketRegime,
    *,
    minimum_days_to_expiry: int = 2,
    maximum_days_to_expiry: int = 8,
    max_premium: float = 1.6,
    max_spread_pct: float = 0.18,
    min_open_interest: int = 150,
    min_volume: int = 25,
    min_abs_delta: float = 0.25,
    max_abs_delta: float = 0.75,
    ivr_gate: float = 0.70,
) -> tuple[list[ContractCandidate], dict[str, object]]:
    candidates: list[ContractCandidate] = []
    today = date.today()
    risk_free_rate = fetch_risk_free_rate()

    stage_totals = {
        "signals_considered": 0,
        "signals_with_expiry": 0,
        "signals_with_chain": 0,
        "rows_after_basic": 0,
        "rows_positive_bid_ask": 0,
        "rows_within_long_leg_cap": 0,
        "rows_within_spread_cap": 0,
        "rows_passing_liquidity": 0,
        "rows_passing_moneyness": 0,
        "rows_passing_delta": 0,
        "rows_passing_net_debit": 0,
        "final_candidates": 0,
    }
    per_symbol: list[dict[str, object]] = []

    for signal in signals:
        stage_totals["signals_considered"] += 1
        net_debit_cap = _net_debit_cap(signal.spot, max_premium)
        long_leg_cap = _long_leg_cap(net_debit_cap)
        effective_spread_cap = _spread_cap(signal.spot, max_spread_pct)
        symbol_diag: dict[str, object] = {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "spot": round(signal.spot, 4),
            "net_debit_cap": round(net_debit_cap, 4),
            "long_leg_cap": round(long_leg_cap, 4),
            "spread_cap": round(effective_spread_cap, 4),
            "rows_after_basic": 0,
            "rows_positive_bid_ask": 0,
            "rows_within_long_leg_cap": 0,
            "rows_within_spread_cap": 0,
            "rows_passing_liquidity": 0,
            "rows_passing_moneyness": 0,
            "rows_passing_delta": 0,
            "rows_passing_net_debit": 0,
            "final_candidates": 0,
        }

        expiry = next_expiry(
            option_expiries(signal.symbol),
            minimum_days=minimum_days_to_expiry,
            maximum_days=maximum_days_to_expiry,
            today=today,
        )
        if not expiry:
            symbol_diag["rejection_reason"] = "no_expiry"
            per_symbol.append(symbol_diag)
            continue

        symbol_diag["expiry"] = expiry
        stage_totals["signals_with_expiry"] += 1
        calls, puts = option_chain(signal.symbol, expiry)
        frame = calls if signal.direction == "call" else puts
        if frame.empty:
            symbol_diag["rejection_reason"] = "empty_chain"
            per_symbol.append(symbol_diag)
            continue

        stage_totals["signals_with_chain"] += 1
        clean = frame.copy()
        clean["bid"] = pd.to_numeric(clean.get("bid"), errors="coerce")
        clean["ask"] = pd.to_numeric(clean.get("ask"), errors="coerce")
        clean["lastPrice"] = pd.to_numeric(clean.get("lastPrice"), errors="coerce")
        clean["strike"] = pd.to_numeric(clean.get("strike"), errors="coerce")
        clean["openInterest"] = pd.to_numeric(clean.get("openInterest"), errors="coerce").fillna(0)
        clean["volume"] = pd.to_numeric(clean.get("volume"), errors="coerce").fillna(0)
        clean["impliedVolatility"] = pd.to_numeric(clean.get("impliedVolatility"), errors="coerce").fillna(0.45)
        clean = clean.dropna(subset=["bid", "ask", "strike"])
        symbol_diag["rows_after_basic"] = len(clean)
        stage_totals["rows_after_basic"] += len(clean)

        clean = clean[(clean["bid"] > 0) & (clean["ask"] > 0)].copy()
        symbol_diag["rows_positive_bid_ask"] = len(clean)
        stage_totals["rows_positive_bid_ask"] += len(clean)
        if clean.empty:
            symbol_diag["rejection_reason"] = "no_positive_bid_ask"
            per_symbol.append(symbol_diag)
            continue

        clean = clean[clean["ask"] <= long_leg_cap].copy()
        symbol_diag["rows_within_long_leg_cap"] = len(clean)
        stage_totals["rows_within_long_leg_cap"] += len(clean)
        if clean.empty:
            symbol_diag["rejection_reason"] = "long_leg_cap"
            per_symbol.append(symbol_diag)
            continue

        mid = (clean["bid"] + clean["ask"]) / 2.0
        clean = clean[mid > 0].copy()
        clean["spread_pct"] = (clean["ask"] - clean["bid"]) / ((clean["bid"] + clean["ask"]) / 2.0)
        clean = clean[clean["spread_pct"] <= effective_spread_cap].copy()
        symbol_diag["rows_within_spread_cap"] = len(clean)
        stage_totals["rows_within_spread_cap"] += len(clean)
        if clean.empty:
            symbol_diag["rejection_reason"] = "spread_cap"
            per_symbol.append(symbol_diag)
            continue

        clean = clean[
            (clean["openInterest"] >= min_open_interest)
            & (clean["volume"] >= min_volume)
        ].copy()
        symbol_diag["rows_passing_liquidity"] = len(clean)
        stage_totals["rows_passing_liquidity"] += len(clean)
        if clean.empty:
            symbol_diag["rejection_reason"] = "liquidity"
            per_symbol.append(symbol_diag)
            continue

        short_pool = clean.copy()
        clean["moneyness"] = clean["strike"].apply(
            lambda strike: _candidate_moneyness(signal.direction, signal.spot, float(strike))
        )
        clean = clean[(clean["moneyness"] >= -0.05) & (clean["moneyness"] <= 0.03)].copy()
        symbol_diag["rows_passing_moneyness"] = len(clean)
        stage_totals["rows_passing_moneyness"] += len(clean)
        if clean.empty:
            symbol_diag["rejection_reason"] = "moneyness"
            per_symbol.append(symbol_diag)
            continue

        projected_move_pct = _projected_move_pct(signal, regime)
        projected_spot = signal.spot * (1 + projected_move_pct if signal.direction == "call" else 1 - projected_move_pct)
        days_to_expiry = max((date.fromisoformat(expiry) - today).days, 1)
        time_to_expiry_years = max(days_to_expiry / 365.0, 1.0 / 365.0)
        clean["delta"] = clean.apply(
            lambda row: black_scholes_delta(
                spot=signal.spot,
                strike=float(row["strike"]),
                time_to_expiry_years=time_to_expiry_years,
                risk_free_rate=risk_free_rate,
                volatility=max(float(row["impliedVolatility"]), 0.10),
                option_type=signal.direction,
            ),
            axis=1,
        )
        clean = clean[clean["delta"].notna()].copy()
        clean = clean[clean["delta"].abs().between(min_abs_delta, max_abs_delta)].copy()
        symbol_diag["rows_passing_delta"] = len(clean)
        stage_totals["rows_passing_delta"] += len(clean)
        if clean.empty:
            symbol_diag["rejection_reason"] = "delta"
            per_symbol.append(symbol_diag)
            continue

        rows_passing_net_debit = 0
        symbol_candidates = 0
        for _, row in clean.iterrows():
            bid = float(row["bid"])
            ask = float(row["ask"])
            premium = float(ask)
            strike = float(row["strike"])
            option_type = signal.direction
            delta = float(row["delta"])
            spread_pct = float(row["spread_pct"])
            open_interest = int(float(row["openInterest"]))
            volume = int(float(row["volume"]))
            iv = max(float(row["impliedVolatility"]), 0.10)

            short_leg = _find_short_leg(
                short_pool,
                option_type=option_type,
                long_strike=strike,
                long_delta=delta,
                spot=signal.spot,
                time_to_expiry_years=time_to_expiry_years,
                risk_free_rate=risk_free_rate,
                max_spread_pct=effective_spread_cap,
            )

            is_spread = False
            actual_premium = premium
            if short_leg is not None:
                spread_debit = premium - short_leg["bid"]
                if 0.05 < spread_debit <= net_debit_cap:
                    is_spread = True
                    actual_premium = spread_debit
            if not is_spread and premium > net_debit_cap:
                continue

            rows_passing_net_debit += 1
            projected_value = _intrinsic(option_type, projected_spot, strike)
            intrinsic_now = _intrinsic(option_type, signal.spot, strike)
            if is_spread and short_leg is not None:
                projected_value = max(
                    projected_value - _intrinsic(option_type, projected_spot, short_leg["strike"]),
                    0.0,
                )
                intrinsic_now = max(
                    intrinsic_now - _intrinsic(option_type, signal.spot, short_leg["strike"]),
                    0.0,
                )

            expected_return_pct = projected_value / actual_premium - 1.0
            breakeven_move_pct = _breakeven_move_pct(option_type, signal.spot, strike, actual_premium)
            extrinsic_ratio = max(actual_premium - intrinsic_now, 0.0) / actual_premium if actual_premium > 0 else 1.0
            allocation_weight = round(min(max(0.35 / iv, 0.25), 3.0), 4)

            ivr = compute_iv_rank(signal.symbol, iv)
            ivr_penalty = max(ivr - ivr_gate, 0.0) * 0.4
            vrp_penalty = max(iv - signal.realized_vol_20d - 0.10, 0.0) * 2.0
            liquidity_score = _clip(
                0.45
                + 0.18 * min(open_interest / 800.0, 1.0)
                + 0.18 * min(volume / 300.0, 1.0)
                - 0.35 * min(spread_pct / effective_spread_cap, 1.0)
            )
            economics_score = _clip(
                0.50
                + 0.25 * min(expected_return_pct / 1.5, 1.0)
                + 0.15 * min((projected_move_pct - breakeven_move_pct) / 0.05, 1.0)
                + 0.10 * (1.0 - min(extrinsic_ratio, 1.0))
                - 0.15 * max(extrinsic_ratio - 0.90, 0.0) / 0.10
                - vrp_penalty
                - ivr_penalty
            )
            forge_score = _clip(
                0.45 * ((signal.scout_score + 1.0) / 2.0)
                + 0.30 * liquidity_score
                + 0.25 * economics_score
            )

            notes: list[str] = []
            if is_spread and short_leg is not None:
                notes.append(
                    f"debit spread selected: {strike:.2f}/{short_leg['strike']:.2f} for premium control"
                )
            if expected_return_pct > 1.0:
                notes.append("projected payoff is asymmetric")
            if extrinsic_ratio < 0.8:
                notes.append("time-value burden is acceptable")
            if vrp_penalty > 0.05:
                notes.append("VRP penalty applied: IV is highly elevated over RV")
            if ivr_penalty > 0.0:
                notes.append(f"IVR penalty applied: IV rank {ivr:.0%} above gate")
            if 0.20 <= abs(delta) <= 0.45:
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
                    contract_cost=round(actual_premium * 100.0, 2),
                    spread_pct=round(spread_pct, 4),
                    open_interest=open_interest,
                    volume=volume,
                    implied_volatility=round(float(row["impliedVolatility"]), 4),
                    delta=round(delta, 4),
                    moneyness=round(float(row["moneyness"]), 4),
                    projected_move_pct=round(projected_move_pct, 4),
                    breakeven_move_pct=round(breakeven_move_pct, 4),
                    expected_return_pct=round(expected_return_pct, 4),
                    extrinsic_ratio=round(extrinsic_ratio, 4),
                    scout_score=signal.scout_score,
                    forge_score=round(forge_score, 4),
                    short_strike=round(short_leg["strike"], 4) if short_leg else None,
                    short_ask=round(short_leg["ask"], 4) if short_leg else None,
                    short_bid=round(short_leg["bid"], 4) if short_leg else None,
                    is_spread=is_spread,
                    spread_cost=round(actual_premium, 4),
                    allocation_weight=allocation_weight,
                    iv_rank=round(ivr, 4),
                    notes=notes,
                )
            )
            symbol_candidates += 1

        symbol_diag["rows_passing_net_debit"] = rows_passing_net_debit
        symbol_diag["final_candidates"] = symbol_candidates
        stage_totals["rows_passing_net_debit"] += rows_passing_net_debit
        stage_totals["final_candidates"] += symbol_candidates
        if symbol_candidates == 0:
            symbol_diag["rejection_reason"] = "net_debit"
        per_symbol.append(symbol_diag)

    candidates.sort(key=lambda row: row.forge_score, reverse=True)
    return candidates, {
        "waterfall": stage_totals,
        "per_symbol": per_symbol,
        "settings": {
            "minimum_days_to_expiry": minimum_days_to_expiry,
            "maximum_days_to_expiry": maximum_days_to_expiry,
            "base_max_premium": max_premium,
            "base_max_spread_pct": max_spread_pct,
            "min_open_interest": min_open_interest,
            "min_volume": min_volume,
            "min_abs_delta": min_abs_delta,
            "max_abs_delta": max_abs_delta,
            "iv_rank_gate": ivr_gate,
        },
    }


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
    min_abs_delta: float = 0.25,
    max_abs_delta: float = 0.75,
    ivr_gate: float = 0.70,
) -> list[ContractCandidate]:
    candidates, _ = rank_contracts_with_diagnostics(
        signals,
        regime,
        minimum_days_to_expiry=minimum_days_to_expiry,
        maximum_days_to_expiry=maximum_days_to_expiry,
        max_premium=max_premium,
        max_spread_pct=max_spread_pct,
        min_open_interest=min_open_interest,
        min_volume=min_volume,
        min_abs_delta=min_abs_delta,
        max_abs_delta=max_abs_delta,
        ivr_gate=ivr_gate,
    )
    return candidates
