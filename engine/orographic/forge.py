from __future__ import annotations

from datetime import date, datetime, timezone
import logging
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
from .volatility_surface import compute_expiry_surface, compute_term_structure_slope

log = logging.getLogger(__name__)


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


def _last_trade_age_seconds(value: object, captured_at: datetime) -> float | None:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        numeric = None
    try:
        parsed = (
            pd.to_datetime(numeric, unit="ms" if numeric > 1e12 else "s", utc=True)
            if numeric is not None
            else pd.Timestamp(value)
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return round(max((captured_at - parsed.to_pydatetime()).total_seconds(), 0.0), 3)


def _net_debit_cap(spot: float, base_cap: float) -> float:
    dynamic_cap = 2.25
    if spot >= 500:
        dynamic_cap = 4.5
    elif spot >= 300:
        dynamic_cap = 3.5
    elif spot >= 150:
        dynamic_cap = 2.75
    return max(base_cap, dynamic_cap)


def _spread_cap(spot: float, base_cap: float) -> float:
    dynamic_cap = 0.18
    if spot >= 500:
        dynamic_cap = 0.28
    elif spot >= 300:
        dynamic_cap = 0.24
    return max(base_cap, dynamic_cap)


def _windowed_expiries(
    symbol: str,
    *,
    minimum_days_to_expiry: int,
    maximum_days_to_expiry: int,
    today: date,
) -> list[str]:
    candidates: list[tuple[str, int]] = []
    for raw in option_expiries(symbol):
        try:
            expiry_date = date.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
        days = (expiry_date - today).days
        if minimum_days_to_expiry <= days <= maximum_days_to_expiry:
            candidates.append((raw, days))
    candidates.sort(key=lambda item: item[1])
    return [raw for raw, _ in candidates]


def _empty_chain_stage_counts() -> dict[str, int]:
    return {
        "rows_after_basic": 0,
        "rows_positive_bid_ask": 0,
        "rows_within_long_leg_cap": 0,
        "rows_within_spread_cap": 0,
        "rows_passing_liquidity": 0,
        "rows_passing_moneyness": 0,
    }


def _prepare_chain_frame(
    signal: ScoutSignal,
    frame: pd.DataFrame,
    *,
    long_leg_cap: float,
    effective_spread_cap: float,
    min_open_interest: int,
    min_volume: int,
) -> tuple[pd.DataFrame, dict[str, int], str | None]:
    counts = _empty_chain_stage_counts()
    clean = frame.copy()
    empty_series = pd.Series(index=clean.index, dtype=float)
    clean["bid"] = pd.to_numeric(clean.get("bid"), errors="coerce")
    clean["ask"] = pd.to_numeric(clean.get("ask"), errors="coerce")
    clean["lastPrice"] = pd.to_numeric(clean.get("lastPrice"), errors="coerce")
    clean["strike"] = pd.to_numeric(clean.get("strike"), errors="coerce")
    clean["openInterest"] = pd.to_numeric(clean.get("openInterest", empty_series), errors="coerce").fillna(0)
    clean["volume"] = pd.to_numeric(clean.get("volume", empty_series), errors="coerce").fillna(0)
    clean["impliedVolatility"] = pd.to_numeric(clean.get("impliedVolatility", empty_series), errors="coerce").fillna(0.45)
    clean = clean.dropna(subset=["bid", "ask", "strike"])
    counts["rows_after_basic"] = len(clean)
    if clean.empty:
        return clean, counts, "no_basic_rows"

    clean = clean[(clean["bid"] > 0) & (clean["ask"] > 0)].copy()
    counts["rows_positive_bid_ask"] = len(clean)
    if clean.empty:
        return clean, counts, "no_positive_bid_ask"

    clean = clean[clean["ask"] <= long_leg_cap].copy()
    counts["rows_within_long_leg_cap"] = len(clean)
    if clean.empty:
        return clean, counts, "long_leg_cap"

    mid = (clean["bid"] + clean["ask"]) / 2.0
    clean = clean[mid > 0].copy()
    clean["spread_pct"] = (clean["ask"] - clean["bid"]) / ((clean["bid"] + clean["ask"]) / 2.0)
    clean = clean[clean["spread_pct"] <= effective_spread_cap].copy()
    counts["rows_within_spread_cap"] = len(clean)
    if clean.empty:
        return clean, counts, "spread_cap"

    clean = clean[
        (clean["openInterest"] >= min_open_interest)
        & (clean["volume"] >= min_volume)
    ].copy()
    counts["rows_passing_liquidity"] = len(clean)
    if clean.empty:
        return clean, counts, "liquidity"

    clean["moneyness"] = clean["strike"].apply(
        lambda strike: _candidate_moneyness(signal.direction, signal.spot, float(strike))
    )
    clean = clean[(clean["moneyness"] >= -0.05) & (clean["moneyness"] <= 0.03)].copy()
    counts["rows_passing_moneyness"] = len(clean)
    if clean.empty:
        return clean, counts, "moneyness"

    return clean, counts, None


def _stage_progress_key(stage_counts: dict[str, int]) -> tuple[int, ...]:
    return (
        int(stage_counts.get("rows_passing_moneyness", 0)),
        int(stage_counts.get("rows_passing_liquidity", 0)),
        int(stage_counts.get("rows_within_spread_cap", 0)),
        int(stage_counts.get("rows_within_long_leg_cap", 0)),
        int(stage_counts.get("rows_positive_bid_ask", 0)),
        int(stage_counts.get("rows_after_basic", 0)),
    )


def _candidate_sort_score(candidate: ContractCandidate) -> float:
    learned = candidate.learned_rank_score
    if learned is not None:
        return float(learned)
    return float(candidate.forge_score)


def _friction_buffer_pct(
    candidate: ContractCandidate,
    *,
    entry_slippage_pct: float = 0.03,
    exit_slippage_pct: float = 0.03,
) -> float:
    spread_drag = min(max(candidate.spread_pct, 0.0) * 0.60, 0.25)
    extrinsic_drag = max(candidate.extrinsic_ratio - 0.45, 0.0) * 0.45
    return round(spread_drag + entry_slippage_pct + exit_slippage_pct + extrinsic_drag, 4)


def _expected_edge_after_friction_pct(candidate: ContractCandidate) -> float:
    gross_edge = candidate.expected_option_return_pct_model
    if gross_edge is None:
        gross_edge = candidate.expected_return_pct
    return round(float(gross_edge) - _friction_buffer_pct(candidate), 4)


def _sentinel_effect_flags(effect: object) -> tuple[float, float]:
    label = str(effect or "").strip().lower()
    if label == "spot":
        return 1.0, 0.0
    if label == "iv":
        return 0.0, 1.0
    if label == "mixed":
        return 1.0, 1.0
    return 0.0, 0.0


def _sentinel_holding_window_fit(signal: ScoutSignal, days_to_expiry: int) -> tuple[float | None, str | None]:
    sentinel = getattr(signal, "sentinel_event", None)
    if not isinstance(sentinel, dict) or not sentinel:
        return None, None

    horizon = str(sentinel.get("time_horizon") or "unknown")
    decay = str(sentinel.get("decay_half_life") or "unknown")
    confidence = float(sentinel.get("confidence") or 0.0)
    if confidence <= 0:
        return None, None

    horizon_days = {
        "intraday": 1,
        "one_to_three_days": 3,
        "one_to_two_weeks": 10,
        "longer": 20,
        "unknown": None,
    }.get(horizon)
    decay_days = {
        "intraday": 1,
        "one_day": 1,
        "three_days": 3,
        "one_week": 7,
        "longer": 14,
        "unknown": None,
    }.get(decay)
    anchor_days = decay_days or horizon_days
    if anchor_days is None:
        return None, None

    gap = abs(days_to_expiry - anchor_days)
    fit_score = _clip(1.0 - gap / max(anchor_days, 1), 0.0, 1.0)
    fit_score = round(fit_score * min(max(confidence, 0.25), 1.0), 4)
    if fit_score >= 0.7:
        label = "well_matched"
    elif fit_score >= 0.4:
        label = "acceptable"
    else:
        label = "mismatch"
    return fit_score, label


def _dedupe_candidates(
    candidates: list[ContractCandidate],
    *,
    max_structures_per_symbol_side: int = 2,
    min_moneyness_gap: float = 0.01,
    max_structures_per_symbol: int = 1,
    strong_ticker_moneyness_gap: float = 0.035,
    strong_ticker_delta_gap: float = 0.20,
    strong_ticker_min_score: float = 0.68,
    strong_ticker_min_edge_after_friction_pct: float = 0.15,
) -> tuple[list[ContractCandidate], int]:
    side_kept: list[ContractCandidate] = []
    removed = 0
    grouped: dict[tuple[str, str], list[ContractCandidate]] = {}
    for candidate in sorted(candidates, key=_candidate_sort_score, reverse=True):
        grouped.setdefault((candidate.symbol, candidate.option_type), []).append(candidate)

    for group_rows in grouped.values():
        selected: list[ContractCandidate] = []
        for candidate in group_rows:
            if len(selected) >= max_structures_per_symbol_side:
                removed += 1
                continue
            if any(abs(candidate.moneyness - row.moneyness) < min_moneyness_gap for row in selected):
                removed += 1
                continue
            selected.append(candidate)
        side_kept.extend(selected)

    kept: list[ContractCandidate] = []
    by_symbol: dict[str, list[ContractCandidate]] = {}
    for candidate in sorted(side_kept, key=_candidate_sort_score, reverse=True):
        symbol_rows = by_symbol.setdefault(candidate.symbol, [])
        if len(symbol_rows) < max_structures_per_symbol:
            symbol_rows.append(candidate)
            kept.append(candidate)
            continue
        if _has_strong_ticker_differentiation(
            candidate,
            symbol_rows,
            min_moneyness_gap=strong_ticker_moneyness_gap,
            min_delta_gap=strong_ticker_delta_gap,
            min_score=strong_ticker_min_score,
            min_edge_after_friction_pct=strong_ticker_min_edge_after_friction_pct,
        ):
            symbol_rows.append(candidate)
            kept.append(candidate)
            continue
        removed += 1

    kept.sort(key=_candidate_sort_score, reverse=True)
    return kept, removed


def _has_strong_ticker_differentiation(
    candidate: ContractCandidate,
    selected: list[ContractCandidate],
    *,
    min_moneyness_gap: float,
    min_delta_gap: float,
    min_score: float,
    min_edge_after_friction_pct: float,
) -> bool:
    score = _candidate_sort_score(candidate)
    edge_after_friction = candidate.expected_edge_after_friction_pct
    if edge_after_friction is None:
        edge_after_friction = _expected_edge_after_friction_pct(candidate)
    if score < min_score or edge_after_friction < min_edge_after_friction_pct:
        return False

    for row in selected:
        expiry_gap = abs((pd.Timestamp(candidate.expiry) - pd.Timestamp(row.expiry)).days)
        moneyness_gap = abs(candidate.moneyness - row.moneyness)
        delta_gap = (
            abs(float(candidate.delta) - float(row.delta))
            if candidate.delta is not None and row.delta is not None
            else 0.0
        )
        structurally_distinct = (
            candidate.option_type != row.option_type
            or expiry_gap >= 7
            or moneyness_gap >= min_moneyness_gap
            or delta_gap >= min_delta_gap
        )
        if not structurally_distinct:
            return False
    return True


def _apply_pre_council_gate(
    candidates: list[ContractCandidate],
    *,
    min_expected_edge_after_friction_pct: float = 0.05,
    enforced: bool = True,
) -> tuple[list[ContractCandidate], dict[str, object]]:
    kept: list[ContractCandidate] = []
    dropped: list[dict[str, object]] = []
    for candidate in candidates:
        friction_buffer = _friction_buffer_pct(candidate)
        edge_after_friction = _expected_edge_after_friction_pct(candidate)
        candidate.friction_buffer_pct = friction_buffer
        candidate.expected_edge_after_friction_pct = edge_after_friction
        candidate.friction_gate_passed = edge_after_friction >= min_expected_edge_after_friction_pct
        if not candidate.friction_gate_passed:
            if enforced:
                candidate.council_risk_flags = sorted(set([*candidate.council_risk_flags, "friction_veto"]))
                candidate.notes = [*candidate.notes, "Pre-Council friction veto"]
            dropped.append(
                {
                    "symbol": candidate.symbol,
                    "contract_symbol": candidate.contract_symbol,
                    "reason": "friction_gate",
                    "expected_edge_after_friction_pct": edge_after_friction,
                    "friction_buffer_pct": friction_buffer,
                    "extrinsic_ratio": candidate.extrinsic_ratio,
                    "forge_score": candidate.forge_score,
                    "learned_rank_score": candidate.learned_rank_score,
                    "contract_cost": candidate.contract_cost,
                    "spread_pct": candidate.spread_pct,
                    "delta": candidate.delta,
                }
            )
            continue
        kept.append(candidate)

    kept.sort(key=_candidate_sort_score, reverse=True)
    return kept, {
        "kept": len(kept),
        "dropped": len(dropped),
        "enforced": enforced,
        "min_expected_edge_after_friction_pct": min_expected_edge_after_friction_pct,
        "rejections": dropped,
    }


def select_signals_for_forge(
    signals: Iterable[ScoutSignal],
    *,
    target_count: int = 6,
    minimum_days_to_expiry: int = 2,
    maximum_days_to_expiry: int = 8,
    max_premium: float = 1.6,
    max_spread_pct: float = 0.18,
    min_open_interest: int = 150,
    min_volume: int = 25,
    today: date | None = None,
) -> tuple[list[ScoutSignal], dict[str, object]]:
    selected: list[ScoutSignal] = []
    rejections: list[dict[str, object]] = []
    evaluated = 0
    signals = list(signals)
    today = today or date.today()
    selected_expiries: list[dict[str, str]] = []

    for signal in signals:
        if len(selected) >= target_count:
            break

        evaluated += 1
        net_debit_cap = _net_debit_cap(signal.spot, max_premium)
        long_leg_cap = net_debit_cap
        effective_spread_cap = _spread_cap(signal.spot, max_spread_pct)
        try:
            expiries = _windowed_expiries(
                signal.symbol,
                minimum_days_to_expiry=minimum_days_to_expiry,
                maximum_days_to_expiry=maximum_days_to_expiry,
                today=today,
            )
            if not expiries:
                rejections.append({"symbol": signal.symbol, "reason": "no_expiry"})
                continue
            best_success: dict[str, object] | None = None
            best_attempt: dict[str, object] | None = None
            expiry_attempts: list[dict[str, object]] = []
            for expiry in expiries:
                calls, puts = option_chain(signal.symbol, expiry)
                frame = calls if signal.direction == "call" else puts
                if frame.empty:
                    attempt = {
                        "expiry": expiry,
                        "reason": "empty_chain",
                        **_empty_chain_stage_counts(),
                    }
                    expiry_attempts.append(attempt)
                    if best_attempt is None:
                        best_attempt = attempt
                    continue

                clean, stage_counts, rejection_reason = _prepare_chain_frame(
                    signal,
                    frame,
                    long_leg_cap=long_leg_cap,
                    effective_spread_cap=effective_spread_cap,
                    min_open_interest=min_open_interest,
                    min_volume=min_volume,
                )
                attempt = {
                    "expiry": expiry,
                    "reason": rejection_reason or "selected",
                    **stage_counts,
                }
                expiry_attempts.append(attempt)
                if rejection_reason is None:
                    candidate_attempt = {
                        "expiry": expiry,
                        "tradable_rows": len(clean),
                        "stage_counts": stage_counts,
                    }
                    if best_success is None or (
                        int(candidate_attempt["tradable_rows"]),
                        _stage_progress_key(stage_counts),
                        -int((date.fromisoformat(expiry) - today).days),
                    ) > (
                        int(best_success["tradable_rows"]),
                        _stage_progress_key(best_success["stage_counts"]),
                        -int((date.fromisoformat(str(best_success["expiry"])) - today).days),
                    ):
                        best_success = candidate_attempt
                    continue
                if best_attempt is None or _stage_progress_key(stage_counts) > _stage_progress_key(best_attempt):
                    best_attempt = {
                        "expiry": expiry,
                        "reason": rejection_reason,
                        **stage_counts,
                    }
            if best_success is None:
                rejection_reason = str((best_attempt or {}).get("reason") or "liquidity")
                mapped_reason = "premium_cap" if rejection_reason == "long_leg_cap" else "liquidity_gate"
                rejections.append(
                    {
                        "symbol": signal.symbol,
                        "reason": mapped_reason,
                        "expiry": (best_attempt or {}).get("expiry"),
                        "long_leg_cap": round(long_leg_cap, 4),
                        "spread_cap": round(effective_spread_cap, 4),
                        "expiry_attempts": expiry_attempts,
                    }
                )
                continue
        except Exception as exc:
            rejections.append({"symbol": signal.symbol, "reason": "chain_error", "error": str(exc)})
            continue

        selected.append(signal)
        selected_expiries.append({"symbol": signal.symbol, "expiry": str(best_success["expiry"])})

    diagnostics = {
        "signals_available": len(signals),
        "signals_evaluated": evaluated,
        "signals_selected": len(selected),
        "selected_symbols": [signal.symbol for signal in selected],
        "selected_expiries": selected_expiries,
        "rejections": rejections,
        "settings": {
            "target_count": target_count,
            "minimum_days_to_expiry": minimum_days_to_expiry,
            "maximum_days_to_expiry": maximum_days_to_expiry,
            "base_max_premium": max_premium,
            "base_max_spread_pct": max_spread_pct,
            "min_open_interest": min_open_interest,
            "min_volume": min_volume,
        },
    }
    return selected, diagnostics


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
    min_expected_edge_after_friction_pct: float = 0.05,
    enforce_pre_council_friction_gate: bool = False,
    max_structures_per_symbol_side: int = 2,
    min_moneyness_gap: float = 0.01,
    prior_live_board_symbols: list[str] | None = None,
    turnover_switch_penalty: float = 0.03,
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
        "candidates_after_friction_gate": 0,
        "candidates_after_deduplication": 0,
    }
    per_symbol: list[dict[str, object]] = []

    for signal in signals:
        stage_totals["signals_considered"] += 1
        net_debit_cap = _net_debit_cap(signal.spot, max_premium)
        long_leg_cap = net_debit_cap
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
        sentinel_event = signal.sentinel_event if isinstance(signal.sentinel_event, dict) else {}
        if sentinel_event:
            symbol_diag["sentinel_event_type"] = sentinel_event.get("event_type")
            symbol_diag["sentinel_time_horizon"] = sentinel_event.get("time_horizon")
            symbol_diag["sentinel_decay_half_life"] = sentinel_event.get("decay_half_life")
            symbol_diag["sentinel_direction_3d"] = sentinel_event.get("direction_3d")
        expiries = _windowed_expiries(
            signal.symbol,
            minimum_days_to_expiry=minimum_days_to_expiry,
            maximum_days_to_expiry=maximum_days_to_expiry,
            today=today,
        )
        if not expiries:
            symbol_diag["rejection_reason"] = "no_expiry"
            per_symbol.append(symbol_diag)
            continue
        stage_totals["signals_with_expiry"] += 1
        best_symbol_diag: dict[str, object] | None = None
        best_candidates_for_symbol: list[ContractCandidate] = []
        best_candidate_key: tuple[int, float, int] | None = None
        saw_non_empty_chain = False
        expiry_attempts: list[dict[str, object]] = []
        expiry_surfaces: dict[str, dict[str, object]] = {}

        for expiry in expiries:
            current_diag = dict(symbol_diag)
            current_diag["expiry"] = expiry
            chain_snapshot_at = datetime.now(timezone.utc)
            calls, puts = option_chain(signal.symbol, expiry)
            surface = compute_expiry_surface(
                calls,
                puts,
                spot=signal.spot,
                expiry=expiry,
                as_of=today,
            )
            expiry_surfaces[expiry] = surface
            current_diag["volatility_surface"] = surface
            frame = calls if signal.direction == "call" else puts
            if frame.empty:
                current_diag["rejection_reason"] = "empty_chain"
                expiry_attempts.append({"expiry": expiry, "rejection_reason": "empty_chain"})
                if best_symbol_diag is None:
                    best_symbol_diag = current_diag
                continue

            saw_non_empty_chain = True
            clean, stage_counts, rejection_reason = _prepare_chain_frame(
                signal,
                frame,
                long_leg_cap=long_leg_cap,
                effective_spread_cap=effective_spread_cap,
                min_open_interest=min_open_interest,
                min_volume=min_volume,
            )
            current_diag.update(stage_counts)
            if rejection_reason is not None:
                current_diag["rejection_reason"] = rejection_reason
                expiry_attempts.append({"expiry": expiry, "rejection_reason": rejection_reason, **stage_counts})
                if best_symbol_diag is None or _stage_progress_key(stage_counts) > _stage_progress_key(best_symbol_diag):
                    best_symbol_diag = current_diag
                continue

            projected_move_pct = _projected_move_pct(signal, regime)
            projected_spot = signal.spot * (1 + projected_move_pct if signal.direction == "call" else 1 - projected_move_pct)
            days_to_expiry = max((date.fromisoformat(expiry) - today).days, 1)
            time_to_expiry_years = max(days_to_expiry / 365.0, 1.0 / 365.0)
            holding_window_fit, holding_window_label = _sentinel_holding_window_fit(signal, days_to_expiry)
            if holding_window_fit is not None:
                current_diag["sentinel_holding_window_fit"] = holding_window_fit
                current_diag["sentinel_holding_window_label"] = holding_window_label
                if holding_window_label == "mismatch":
                    current_diag["sentinel_shadow_note"] = (
                        f"Sentinel horizon mismatch: {sentinel_event.get('time_horizon', 'unknown')} catalyst vs {days_to_expiry} DTE"
                    )
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
            current_diag["rows_passing_delta"] = len(clean)
            if clean.empty:
                current_diag["rejection_reason"] = "delta"
                expiry_attempts.append({"expiry": expiry, "rejection_reason": "delta", **stage_counts, "rows_passing_delta": 0})
                if best_symbol_diag is None or (
                    int(current_diag.get("rows_passing_delta", 0)),
                    _stage_progress_key(current_diag),
                ) > (
                    int(best_symbol_diag.get("rows_passing_delta", 0)),
                    _stage_progress_key(best_symbol_diag),
                ):
                    best_symbol_diag = current_diag
                continue

            local_candidates: list[ContractCandidate] = []
            rows_passing_net_debit = 0
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

                actual_premium = premium
                if premium > net_debit_cap:
                    continue

                rows_passing_net_debit += 1
                projected_value = _intrinsic(option_type, projected_spot, strike)
                intrinsic_now = _intrinsic(option_type, signal.spot, strike)

                expected_return_pct = projected_value / actual_premium - 1.0
                breakeven_move_pct = _breakeven_move_pct(option_type, signal.spot, strike, actual_premium)
                extrinsic_ratio = max(actual_premium - intrinsic_now, 0.0) / actual_premium if actual_premium > 0 else 1.0
                allocation_weight = round(min(max(0.35 / iv, 0.25), 3.0), 4)

                ivr = compute_iv_rank(signal.symbol, iv)
                ivr_penalty = max(ivr - ivr_gate, 0.0) * 0.4
                vrp_gap = max(iv - signal.realized_vol_20d, 0.0)
                vrp_penalty = max(vrp_gap - 0.10, 0.0) * 2.0
                premium_pct_of_spot = actual_premium / signal.spot if signal.spot > 0 else 0.0
                sentinel_confidence = _clip(float(sentinel_event.get("confidence") or 0.0))
                sentinel_call_relevance = _clip(float(sentinel_event.get("call_relevance") or 0.0))
                sentinel_put_relevance = _clip(float(sentinel_event.get("put_relevance") or 0.0))
                sentinel_no_trade_relevance = _clip(float(sentinel_event.get("no_trade_relevance") or 0.0))
                sentinel_spot_effect, sentinel_iv_effect = _sentinel_effect_flags(
                    sentinel_event.get("spot_vs_iv_effect")
                )
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
                if holding_window_label == "mismatch":
                    notes.append(
                        f"Sentinel shadow mismatch: {sentinel_event.get('time_horizon', 'unknown')} catalyst vs {days_to_expiry} DTE"
                    )
                elif holding_window_label == "well_matched":
                    notes.append(
                        f"Sentinel shadow fit: {sentinel_event.get('time_horizon', 'unknown')} catalyst aligns with {days_to_expiry} DTE"
                    )

                local_candidates.append(
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
                        scout_call_edge_prob=round(float(getattr(signal, "call_edge_prob", 0.0) or 0.0), 4),
                        scout_put_edge_prob=round(float(getattr(signal, "put_edge_prob", 0.0) or 0.0), 4),
                        scout_no_trade_prob=round(float(getattr(signal, "no_trade_prob", 0.0) or 0.0), 4),
                        short_strike=None,
                        short_ask=None,
                        short_bid=None,
                        is_spread=False,
                        spread_cost=round(actual_premium, 4),
                        allocation_weight=allocation_weight,
                        iv_rank=round(ivr, 4),
                        entry_data_source=str(row.get("dataSource") or "market_adapter"),
                        surface_atm_iv=surface.get("atm_iv"),
                        surface_skew_slope=surface.get("skew_slope"),
                        surface_curvature=surface.get("curvature"),
                        surface_put_call_wing_skew=surface.get("put_call_wing_skew"),
                        surface_fit_rmse=surface.get("fit_rmse"),
                        surface_observation_count=int(surface.get("observation_count") or 0),
                        iv_relative_to_atm=(
                            round(iv - float(surface["atm_iv"]), 6)
                            if surface.get("atm_iv") is not None
                            else None
                        ),
                        iv_minus_realized_vol=round(iv - signal.realized_vol_20d, 6),
                        quote_mid=round((bid + ask) / 2.0, 4),
                        quote_spread_dollars=round(ask - bid, 4),
                        chain_snapshot_at_utc=chain_snapshot_at.replace(microsecond=0).isoformat(),
                        last_trade_age_seconds=_last_trade_age_seconds(row.get("lastTradeDate"), chain_snapshot_at),
                        realized_vol_20d=round(signal.realized_vol_20d, 4),
                        atr_pct_14d=round(signal.atr_pct_14d, 4),
                        premium_pct_of_spot=round(premium_pct_of_spot, 4),
                        vrp_gap=round(vrp_gap, 4),
                        sentinel_holding_window_fit=holding_window_fit,
                        sentinel_holding_window_label=holding_window_label,
                        sentinel_event_type=sentinel_event.get("event_type"),
                        sentinel_decay_half_life=sentinel_event.get("decay_half_life"),
                        sentinel_time_horizon=sentinel_event.get("time_horizon"),
                        sentinel_confidence=round(sentinel_confidence, 4),
                        sentinel_source_reliability=sentinel_event.get("source_reliability"),
                        sentinel_novelty=sentinel_event.get("novelty"),
                        sentinel_call_relevance=round(sentinel_call_relevance, 4),
                        sentinel_put_relevance=round(sentinel_put_relevance, 4),
                        sentinel_no_trade_relevance=round(sentinel_no_trade_relevance, 4),
                        sentinel_spot_effect=round(sentinel_spot_effect, 4),
                        sentinel_iv_effect=round(sentinel_iv_effect, 4),
                        notes=notes,
                    )
                )

            current_diag["rows_passing_net_debit"] = rows_passing_net_debit
            current_diag["final_candidates"] = len(local_candidates)
            if not local_candidates:
                current_diag["rejection_reason"] = "net_debit"
                expiry_attempts.append(
                    {
                        "expiry": expiry,
                        "rejection_reason": "net_debit",
                        **stage_counts,
                        "rows_passing_delta": int(current_diag["rows_passing_delta"]),
                        "rows_passing_net_debit": rows_passing_net_debit,
                        "final_candidates": 0,
                    }
                )
                if best_symbol_diag is None or (
                    int(current_diag.get("rows_passing_net_debit", 0)),
                    int(current_diag.get("rows_passing_delta", 0)),
                    _stage_progress_key(current_diag),
                ) > (
                    int(best_symbol_diag.get("rows_passing_net_debit", 0)),
                    int(best_symbol_diag.get("rows_passing_delta", 0)),
                    _stage_progress_key(best_symbol_diag),
                ):
                    best_symbol_diag = current_diag
                continue

            best_local_score = max(_candidate_sort_score(candidate) for candidate in local_candidates)
            candidate_key = (len(local_candidates), round(best_local_score, 6), -days_to_expiry)
            expiry_attempts.append(
                {
                    "expiry": expiry,
                    "rejection_reason": "selected",
                    **stage_counts,
                    "rows_passing_delta": int(current_diag["rows_passing_delta"]),
                    "rows_passing_net_debit": rows_passing_net_debit,
                    "final_candidates": len(local_candidates),
                }
            )
            if best_candidate_key is None or candidate_key > best_candidate_key:
                best_candidate_key = candidate_key
                best_candidates_for_symbol = local_candidates
                best_symbol_diag = current_diag

        if saw_non_empty_chain:
            stage_totals["signals_with_chain"] += 1
        if best_symbol_diag is None:
            per_symbol.append(symbol_diag)
            continue

        selected_expiry = str(best_symbol_diag.get("expiry") or "")
        term_slope = compute_term_structure_slope(expiry_surfaces, selected_expiry)
        for candidate in best_candidates_for_symbol:
            candidate.surface_term_slope_30d = term_slope
        best_symbol_diag["volatility_surface_term_slope_30d"] = term_slope
        best_symbol_diag["volatility_surface_expiries_observed"] = len(expiry_surfaces)

        best_symbol_diag["expiry_attempts"] = expiry_attempts
        stage_totals["rows_after_basic"] += int(best_symbol_diag.get("rows_after_basic", 0))
        stage_totals["rows_positive_bid_ask"] += int(best_symbol_diag.get("rows_positive_bid_ask", 0))
        stage_totals["rows_within_long_leg_cap"] += int(best_symbol_diag.get("rows_within_long_leg_cap", 0))
        stage_totals["rows_within_spread_cap"] += int(best_symbol_diag.get("rows_within_spread_cap", 0))
        stage_totals["rows_passing_liquidity"] += int(best_symbol_diag.get("rows_passing_liquidity", 0))
        stage_totals["rows_passing_moneyness"] += int(best_symbol_diag.get("rows_passing_moneyness", 0))
        stage_totals["rows_passing_delta"] += int(best_symbol_diag.get("rows_passing_delta", 0))
        stage_totals["rows_passing_net_debit"] += int(best_symbol_diag.get("rows_passing_net_debit", 0))
        stage_totals["final_candidates"] += int(best_symbol_diag.get("final_candidates", 0))
        candidates.extend(best_candidates_for_symbol)
        per_symbol.append(best_symbol_diag)

    try:
        from engine.orographic.payoff_model import score_candidates, summarize_path_heads

        score_candidates(
            candidates,
            regime,
            as_of=today,
            prior_live_board_symbols=prior_live_board_symbols,
            turnover_switch_penalty=turnover_switch_penalty,
        )
        if any(candidate.path_holding_quality_score is not None for candidate in candidates):
            path_model_summary = summarize_path_heads(candidates)
        else:
            path_model_summary = {
                "mode_counts": {},
                "scored_candidates": 0,
                "avg_holding_quality_score": None,
                "avg_early_profit_take_prob": None,
                "avg_decay_risk": None,
            }
    except Exception as exc:
        log.warning("Payoff model scoring skipped: %s", exc)
        path_model_summary = {
            "mode_counts": {},
            "scored_candidates": 0,
            "avg_holding_quality_score": None,
            "avg_early_profit_take_prob": None,
            "avg_decay_risk": None,
        }

    if not any(candidate.path_holding_quality_score is not None for candidate in candidates):
        try:
            from engine.orographic.path_model import score_candidates as score_path_candidates
            from engine.orographic.path_model import summarize_candidates as summarize_path_candidates

            score_path_candidates(
                candidates,
                regime,
                as_of=today,
            )
            path_model_summary = summarize_path_candidates(candidates)
        except Exception as exc:
            log.warning("Path model scoring skipped: %s", exc)

    candidates.sort(key=_candidate_sort_score, reverse=True)
    friction_passed_candidates, friction_diag = _apply_pre_council_gate(
        candidates,
        min_expected_edge_after_friction_pct=min_expected_edge_after_friction_pct,
        enforced=enforce_pre_council_friction_gate,
    )
    stage_totals["candidates_after_friction_gate"] = len(friction_passed_candidates)
    if enforce_pre_council_friction_gate:
        candidates = friction_passed_candidates
    candidates, dedupe_removed = _dedupe_candidates(
        candidates,
        max_structures_per_symbol_side=max_structures_per_symbol_side,
        min_moneyness_gap=min_moneyness_gap,
    )
    stage_totals["candidates_after_deduplication"] = len(candidates)
    ranker_modes: dict[str, int] = {}
    learned_scores = []
    for candidate in candidates:
        mode = str(getattr(candidate, "ranker_mode", "heuristic") or "heuristic")
        ranker_modes[mode] = ranker_modes.get(mode, 0) + 1
        if candidate.learned_rank_score is not None:
            learned_scores.append(float(candidate.learned_rank_score))
    return candidates, {
        "waterfall": stage_totals,
        "per_symbol": per_symbol,
        "learned_ranker": {
            "mode_counts": ranker_modes,
            "active_default": True,
            "shadow_env": "OROGRAPHIC_PAYOFF_MODEL_MODE=shadow",
            "scored_candidates": len(learned_scores),
            "avg_learned_rank_score": round(sum(learned_scores) / len(learned_scores), 4) if learned_scores else None,
        },
        "path_model": path_model_summary,
        "pre_council_gate": friction_diag,
        "deduplication": {
            "max_structures_per_symbol_side": max_structures_per_symbol_side,
            "min_moneyness_gap": min_moneyness_gap,
            "max_structures_per_symbol": 1,
            "strong_ticker_moneyness_gap": 0.035,
            "strong_ticker_delta_gap": 0.20,
            "strong_ticker_min_score": 0.68,
            "strong_ticker_min_edge_after_friction_pct": 0.15,
            "removed_candidates": dedupe_removed,
            "kept_candidates": len(candidates),
        },
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
            "min_expected_edge_after_friction_pct": min_expected_edge_after_friction_pct,
            "enforce_pre_council_friction_gate": enforce_pre_council_friction_gate,
            "max_structures_per_symbol_side": max_structures_per_symbol_side,
            "min_moneyness_gap": min_moneyness_gap,
            "max_structures_per_symbol": 1,
            "turnover_switch_penalty": turnover_switch_penalty,
            "prior_live_board_symbols": list(prior_live_board_symbols or []),
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
    min_expected_edge_after_friction_pct: float = 0.05,
    enforce_pre_council_friction_gate: bool = False,
    max_structures_per_symbol_side: int = 2,
    min_moneyness_gap: float = 0.01,
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
        min_expected_edge_after_friction_pct=min_expected_edge_after_friction_pct,
        enforce_pre_council_friction_gate=enforce_pre_council_friction_gate,
        max_structures_per_symbol_side=max_structures_per_symbol_side,
        min_moneyness_gap=min_moneyness_gap,
    )
    return candidates
