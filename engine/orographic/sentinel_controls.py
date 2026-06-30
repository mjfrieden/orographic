from __future__ import annotations

from dataclasses import replace
from typing import Any

from .schemas import ContractCandidate


SENTINEL_CONTROL_MODES = {"off", "shadow", "veto", "size_down", "tiebreaker"}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_sentinel_control_mode(value: object, *, default: str = "shadow") -> str:
    mode = str(value or default).strip().lower()
    return mode if mode in SENTINEL_CONTROL_MODES else default


def apply_sentinel_controls(
    candidates: list[ContractCandidate],
    *,
    mode: str = "shadow",
    no_trade_penalty_weight: float = 0.04,
) -> tuple[list[ContractCandidate], dict[str, Any]]:
    control_mode = normalize_sentinel_control_mode(mode)
    adjusted: list[ContractCandidate] = []
    vetoed: list[dict[str, Any]] = []
    resized: list[dict[str, Any]] = []
    reranked: list[dict[str, Any]] = []

    for candidate in candidates:
        recommended_use = str(candidate.sentinel_recommended_use or "observe").strip().lower()
        impact_label = str(candidate.sentinel_options_impact_label or "unknown").strip().lower()
        veto_reason = str(candidate.sentinel_veto_reason or impact_label or "sentinel_veto")
        would_veto = recommended_use == "veto_candidate"
        would_size_down = recommended_use in {"veto_candidate", "reduce_size"}

        if would_veto:
            vetoed.append(
                {
                    "symbol": candidate.symbol,
                    "contract_symbol": candidate.contract_symbol,
                    "sentinel_status": candidate.sentinel_status,
                    "sentinel_options_impact_label": impact_label,
                    "sentinel_veto_reason": veto_reason,
                    "forge_score": candidate.forge_score,
                }
            )
            if control_mode == "veto":
                continue

        updated = candidate
        if would_size_down:
            multiplier = _safe_float(candidate.sentinel_size_multiplier, 1.0)
            multiplier = max(0.1, min(multiplier, 1.0))
            new_weight = round(float(candidate.allocation_weight or 1.0) * multiplier, 4)
            resized.append(
                {
                    "symbol": candidate.symbol,
                    "contract_symbol": candidate.contract_symbol,
                    "sentinel_recommended_use": recommended_use,
                    "sentinel_size_multiplier": round(multiplier, 4),
                    "allocation_weight": new_weight,
                }
            )
            if control_mode == "size_down":
                updated = replace(
                    updated,
                    allocation_weight=new_weight,
                    notes=[*updated.notes, f"Sentinel production size-down x{multiplier:.2f}"],
                )

        tie_breaker = _safe_float(updated.sentinel_tie_breaker_score, 0.0)
        no_trade = _safe_float(updated.sentinel_no_trade_relevance, 0.0)
        action_penalty = 0.03 if recommended_use == "veto_candidate" else 0.015 if recommended_use == "reduce_size" else 0.0
        adjustment = tie_breaker - (no_trade * no_trade_penalty_weight) - action_penalty
        if abs(adjustment) > 0.0001:
            new_score = round(max(0.0, min(float(updated.forge_score or 0.0) + adjustment, 0.9999)), 4)
            reranked.append(
                {
                    "symbol": updated.symbol,
                    "contract_symbol": updated.contract_symbol,
                    "adjustment": round(adjustment, 4),
                    "forge_score": new_score,
                    "sentinel_recommended_use": recommended_use,
                }
            )
            if control_mode == "tiebreaker":
                updated = replace(
                    updated,
                    forge_score=new_score,
                    notes=[*updated.notes, f"Sentinel production tie-breaker adjustment {adjustment:+.4f}"],
                )

        adjusted.append(updated)

    adjusted.sort(key=lambda row: float(getattr(row, "forge_score", 0.0) or 0.0), reverse=True)
    return adjusted, {
        "mode": control_mode,
        "applied": control_mode not in {"off", "shadow"},
        "input_candidates": len(candidates),
        "output_candidates": len(adjusted),
        "vetoed": len(vetoed) if control_mode == "veto" else 0,
        "resized": len(resized) if control_mode == "size_down" else 0,
        "reranked": len(reranked) if control_mode == "tiebreaker" else 0,
        "would_veto": len(vetoed),
        "would_resize": len(resized),
        "would_rerank": len(reranked),
        "veto_details": vetoed[:10],
        "resize_details": resized[:10],
        "rerank_details": reranked[:10],
    }
