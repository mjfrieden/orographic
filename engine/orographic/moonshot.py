from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.backtest.risk_controls import sector_for_symbol
from engine.orographic.call_contract_selector import blend_call_selector_score

from .schemas import ContractCandidate, MarketRegime


DEFAULT_MOONSHOT_THRESHOLD = 0.68
DEFAULT_MAX_COST_BASIS = 225.0


@dataclass(frozen=True)
class MoonshotAssessment:
    candidate: ContractCandidate
    tail_upside_score: float
    eligible: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = self.candidate.to_dict()
        payload["moonshot"] = {
            "tail_upside_score": self.tail_upside_score,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
        }
        return payload


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
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
    if cost_basis <= 150.0:
        return 1.0
    if cost_basis <= 225.0:
        return 0.65
    if cost_basis <= 300.0:
        return 0.35
    return 0.05


def _delta_score(delta: float | None) -> float:
    absolute_delta = abs(_safe_float(delta))
    if 0.25 <= absolute_delta <= 0.40:
        return 1.0
    if 0.18 <= absolute_delta < 0.25 or 0.40 < absolute_delta <= 0.55:
        return 0.55
    if 0.55 < absolute_delta <= 0.70:
        return 0.15
    return 0.0


def _iv_score(implied_volatility: float) -> float:
    if 0.40 <= implied_volatility <= 0.55:
        return 1.0
    if 0.30 <= implied_volatility < 0.40 or 0.55 < implied_volatility <= 0.70:
        return 0.55
    return 0.2


def _threshold_score(value: object, threshold: float, boost: float) -> float:
    parsed = _safe_float(value, default=-1.0)
    return boost if parsed >= threshold else boost * 0.35


def _theme_agreement(candidate: ContractCandidate, peers: list[ContractCandidate]) -> float:
    same_theme = [
        row
        for row in peers
        if row.symbol == candidate.symbol and row.option_type == candidate.option_type
    ]
    return _clamp(len(same_theme) / 3.0)


def _directional_bias(candidate: ContractCandidate, regime: MarketRegime | None) -> tuple[float, list[str]]:
    side = str(candidate.option_type or "").lower()
    regime_mode = str(getattr(regime, "mode", "") or "").lower()
    sector = str(candidate.sector or sector_for_symbol(candidate.symbol) or "").lower()
    reasons: list[str] = []

    if side == "call":
        if regime_mode == "risk_off":
            reasons.append("risk-off regime weakens call moonshot prior")
            return 0.25, reasons
        reasons.append("Nimrod prior: calls dominated the observed extreme-upside sample")
        return 1.0, reasons

    if side == "put" and sector in {"financials", "banks", "banking"} and regime_mode in {
        "neutral",
        "neutral_shock",
        "shock",
        "",
    }:
        reasons.append("Nimrod prior: financial puts can work in neutral-shock tape")
        return 0.9, reasons

    if side == "put":
        reasons.append("Nimrod prior: puts were a minority of observed moonshots")
        return 0.35, reasons

    return 0.1, ["unknown option side reduces tail confidence"]


def assess_candidate(
    candidate: ContractCandidate,
    peers: list[ContractCandidate],
    regime: MarketRegime | None,
    *,
    threshold: float = DEFAULT_MOONSHOT_THRESHOLD,
    max_cost_basis: float = DEFAULT_MAX_COST_BASIS,
) -> MoonshotAssessment:
    cost_basis = _safe_float(candidate.contract_cost, _safe_float(candidate.premium) * 100.0)
    delta = _safe_float(candidate.delta)
    implied_volatility = _safe_float(candidate.implied_volatility)
    direction, reasons = _directional_bias(candidate, regime)
    agreement = _theme_agreement(candidate, peers)

    if cost_basis <= 150.0:
        reasons.append("cheap premium fits Nimrod's highest-yield tail bucket")
    if 0.25 <= abs(delta) <= 0.40:
        reasons.append("medium delta matches Nimrod's convexity sweet spot")
    if 0.40 <= implied_volatility <= 0.55:
        reasons.append("IV sits in Nimrod's elevated-but-not-extreme window")
    if _safe_float(candidate.forge_score) >= 0.65:
        reasons.append("Forge score clears the moonshot quality floor")
    if agreement >= 2.0 / 3.0:
        reasons.append("nearby same-symbol contracts agree with the setup")

    score = (
        _cost_score(cost_basis) * 0.28
        + _delta_score(candidate.delta) * 0.22
        + _iv_score(implied_volatility) * 0.15
        + _threshold_score(candidate.forge_score, 0.65, 0.9) * 0.15
        + _threshold_score(candidate.path_holding_quality_score, 0.70, 0.7) * 0.08
        + agreement * 0.12
        + direction * 0.12
    )
    call_selector = blend_call_selector_score(
        candidate,
        model_score=_safe_float(candidate.learned_rank_score, _safe_float(candidate.forge_score)),
        mode="moonshot_only_nimrod_call_selector",
    )
    if call_selector is not None:
        candidate.call_selector_model_score = call_selector.model_score
        candidate.call_selector_contract_score = call_selector.contract_side_score
        candidate.call_contract_selector_score = call_selector.blended_score
        candidate.call_contract_selector_mode = call_selector.mode
        score = (score * 0.65) + (call_selector.blended_score * 0.35)
        reasons.append("Nimrod call contract selector applied inside moonshot lane only")
    else:
        candidate.call_selector_model_score = None
        candidate.call_selector_contract_score = None
        candidate.call_contract_selector_score = None
        candidate.call_contract_selector_mode = None

    tail_upside_score = round(_clamp(score), 4)
    eligible = (
        tail_upside_score >= threshold
        and cost_basis <= max_cost_basis
        and 0.25 <= abs(delta) <= 0.55
        and not (str(getattr(regime, "mode", "")).lower() == "risk_off" and candidate.option_type == "call")
        and not candidate.is_spread
    )
    if not eligible:
        reasons.append("fails moonshot satellite slot gate")

    return MoonshotAssessment(
        candidate=candidate,
        tail_upside_score=tail_upside_score,
        eligible=eligible,
        reasons=reasons,
    )


def select_moonshot_lane(
    candidates: list[ContractCandidate],
    regime: MarketRegime | None,
    *,
    slot_count: int = 1,
    threshold: float = DEFAULT_MOONSHOT_THRESHOLD,
    max_cost_basis: float = DEFAULT_MAX_COST_BASIS,
) -> dict[str, Any]:
    assessments = [
        assess_candidate(
            candidate,
            candidates,
            regime,
            threshold=threshold,
            max_cost_basis=max_cost_basis,
        )
        for candidate in candidates
    ]
    assessments.sort(
        key=lambda row: (
            row.eligible,
            row.tail_upside_score,
            -_safe_float(row.candidate.contract_cost),
            _safe_float(row.candidate.forge_score),
        ),
        reverse=True,
    )
    picks = [row for row in assessments if row.eligible][:slot_count]
    shadow = [row for row in assessments if not row.eligible][: max(slot_count * 3, 3)]
    return {
        "policy": {
            "name": "nimrod_inspired_moonshot_satellite",
            "slot_count": slot_count,
            "threshold": threshold,
            "max_cost_basis": max_cost_basis,
            "capital_mode": "research_telemetry_non_routable",
            "execution_effect": "none",
            "council_eligible": False,
            "tradier_routing_eligible": False,
            "notes": [
                "Tail-upside research telemetry only; it is not a second production lane and has no order authority.",
                "Scores cheap premium, medium delta, elevated-but-not-extreme IV, Forge quality, path quality, theme agreement, and side/regime prior.",
            ],
        },
        "picks": [row.to_dict() for row in picks],
        "shadow": [row.to_dict() for row in shadow],
        "summary": {
            "candidate_count": len(candidates),
            "eligible_count": sum(1 for row in assessments if row.eligible),
            "pick_count": len(picks),
            "top_score": assessments[0].tail_upside_score if assessments else None,
            "threshold": threshold,
            "max_cost_basis": max_cost_basis,
        },
    }
