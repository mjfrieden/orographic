from __future__ import annotations

from dataclasses import dataclass

from engine.orographic.schemas import ContractCandidate


MODEL_WEIGHT = 0.70
CONTRACT_SIDE_WEIGHT = 0.30


@dataclass(frozen=True)
class CallContractSelectorScore:
    model_score: float
    contract_side_score: float
    blended_score: float
    mode: str


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        parsed = float(value)
        if parsed != parsed:
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _cost_score(cost_basis: float) -> float:
    if cost_basis <= 125.0:
        return 1.0
    if cost_basis <= 175.0:
        return 0.82
    if cost_basis <= 225.0:
        return 0.62
    if cost_basis <= 300.0:
        return 0.30
    return 0.08


def _delta_score(delta: float | None) -> float:
    absolute_delta = abs(_safe_float(delta))
    if 0.28 <= absolute_delta <= 0.42:
        return 1.0
    if 0.22 <= absolute_delta < 0.28 or 0.42 < absolute_delta <= 0.52:
        return 0.70
    if 0.18 <= absolute_delta < 0.22 or 0.52 < absolute_delta <= 0.62:
        return 0.35
    return 0.05


def _iv_score(implied_volatility: float) -> float:
    if 0.32 <= implied_volatility <= 0.58:
        return 1.0
    if 0.24 <= implied_volatility < 0.32 or 0.58 < implied_volatility <= 0.72:
        return 0.62
    return 0.22


def _liquidity_score(candidate: ContractCandidate) -> float:
    spread_pct = max(_safe_float(candidate.spread_pct), 0.0)
    open_interest = max(_safe_float(candidate.open_interest), 0.0)
    volume = max(_safe_float(candidate.volume), 0.0)
    spread_score = 1.0 - min(spread_pct / 0.18, 1.0)
    oi_score = min(open_interest / 1000.0, 1.0)
    volume_score = min(volume / 350.0, 1.0)
    return _clip(0.55 * spread_score + 0.25 * oi_score + 0.20 * volume_score)


def _breakeven_edge_score(candidate: ContractCandidate) -> float:
    projected = _safe_float(candidate.projected_move_pct)
    breakeven = _safe_float(candidate.breakeven_move_pct)
    return _clip(0.45 + ((projected - breakeven) / 0.10))


def contract_side_score(candidate: ContractCandidate) -> float:
    """Score call-contract quality independent of the model-side learner."""
    cost_basis = _safe_float(candidate.contract_cost, _safe_float(candidate.premium) * 100.0)
    expected_return = _clip(0.45 + _safe_float(candidate.expected_return_pct) / 3.0)
    return round(
        _clip(
            0.24 * _cost_score(cost_basis)
            + 0.22 * _delta_score(candidate.delta)
            + 0.17 * _iv_score(_safe_float(candidate.implied_volatility))
            + 0.17 * _breakeven_edge_score(candidate)
            + 0.12 * _liquidity_score(candidate)
            + 0.08 * expected_return
        ),
        4,
    )


def blend_call_selector_score(
    candidate: ContractCandidate,
    *,
    model_score: float,
    mode: str = "active",
) -> CallContractSelectorScore | None:
    """Apply Nimrod's call-contract selector blend to call candidates only."""
    if str(candidate.option_type).lower() != "call":
        return None
    normalized_model_score = _clip(_safe_float(model_score))
    contract_score = contract_side_score(candidate)
    blended = _clip((MODEL_WEIGHT * normalized_model_score) + (CONTRACT_SIDE_WEIGHT * contract_score))
    return CallContractSelectorScore(
        model_score=round(normalized_model_score, 4),
        contract_side_score=round(contract_score, 4),
        blended_score=round(blended, 4),
        mode=mode,
    )
