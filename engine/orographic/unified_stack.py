from __future__ import annotations

from dataclasses import dataclass

from .schemas import ContractCandidate


CURRENT_GATED = "current_gated"
UNIFIED_RND = "unified_rnd"
UNIFIED_NO_HIERARCHICAL = "unified_no_hierarchical"
UNIFIED_NO_PATH = "unified_no_path"
UNIFIED_NO_COST_AWARE = "unified_no_cost_aware"
UNIFIED_PRIMARY_ONLY = "unified_primary_only"

UNIFIED_PROFILES = {
    UNIFIED_RND,
    UNIFIED_NO_HIERARCHICAL,
    UNIFIED_NO_PATH,
    UNIFIED_NO_COST_AWARE,
    UNIFIED_PRIMARY_ONLY,
}


@dataclass(frozen=True)
class UnifiedRankWeights:
    primary: float
    path: float
    challenger_rank: float
    conservative_utility: float

    def normalized(self) -> "UnifiedRankWeights":
        total = self.primary + self.path + self.challenger_rank + self.conservative_utility
        if total <= 0:
            return UnifiedRankWeights(1.0, 0.0, 0.0, 0.0)
        return UnifiedRankWeights(
            self.primary / total,
            self.path / total,
            self.challenger_rank / total,
            self.conservative_utility / total,
        )


def is_unified_profile(profile: str) -> bool:
    return str(profile or "").strip().lower() in UNIFIED_PROFILES


def uses_hierarchical_scout(profile: str) -> bool:
    return is_unified_profile(profile) and profile not in {
        UNIFIED_NO_HIERARCHICAL,
        UNIFIED_PRIMARY_ONLY,
    }


def rank_weights(profile: str) -> UnifiedRankWeights:
    if profile == UNIFIED_PRIMARY_ONLY:
        return UnifiedRankWeights(1.0, 0.0, 0.0, 0.0)
    if profile == UNIFIED_NO_PATH:
        return UnifiedRankWeights(0.60, 0.0, 0.14, 0.08).normalized()
    if profile == UNIFIED_NO_COST_AWARE:
        return UnifiedRankWeights(0.60, 0.18, 0.0, 0.0).normalized()
    return UnifiedRankWeights(0.60, 0.18, 0.14, 0.08)


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _value(value: object, default: float) -> float:
    try:
        parsed = float(value) if value is not None else default
        return parsed if parsed == parsed else default
    except (TypeError, ValueError):
        return default


def apply_base_unified_rank(
    candidates: list[ContractCandidate],
    *,
    profile: str = UNIFIED_RND,
) -> list[ContractCandidate]:
    """Apply one auditable rank while allowing exact component ablations."""
    weights = rank_weights(profile)
    for candidate in candidates:
        primary = _clip(
            _value(
                candidate.learned_rank_score,
                _value(candidate.payoff_model_score, _value(candidate.forge_score, 0.0)),
            )
        )
        path = _clip(_value(candidate.path_holding_quality_score, 0.5))
        challenger_rank = _clip(_value(candidate.payoff_shadow_rank, primary))
        conservative_score = _clip(
            0.5 + _value(candidate.payoff_shadow_conservative_utility, 0.0) / 0.50
        )
        score = _clip(
            weights.primary * primary
            + weights.path * path
            + weights.challenger_rank * challenger_rank
            + weights.conservative_utility * conservative_score
        )
        candidate.forge_score = round(score, 4)
        candidate.final_candidate_score = round(score, 4)
        candidate.learned_rank_score = round(score, 4)
        candidate.ranker_mode = profile
        candidate.path_model_mode = profile
        candidate.notes.append(
            "Unified rank active "
            f"({profile}: primary={weights.primary:.3f}, path={weights.path:.3f}, "
            f"cost_rank={weights.challenger_rank:.3f}, conservative={weights.conservative_utility:.3f})"
        )
    candidates.sort(key=lambda candidate: candidate.forge_score, reverse=True)
    return candidates
