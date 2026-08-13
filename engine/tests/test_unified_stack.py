from __future__ import annotations

import unittest

from engine.orographic.schemas import ContractCandidate
from engine.orographic.unified_stack import (
    UNIFIED_NO_COST_AWARE,
    UNIFIED_NO_HIERARCHICAL,
    UNIFIED_NO_PATH,
    UNIFIED_PRIMARY_ONLY,
    UNIFIED_RND,
    apply_base_unified_rank,
    rank_weights,
    uses_hierarchical_scout,
)


def _candidate() -> ContractCandidate:
    return ContractCandidate(
        symbol="TEST",
        contract_symbol="TEST260821C00100000",
        option_type="call",
        expiry="2026-08-21",
        strike=100.0,
        bid=1.0,
        ask=1.1,
        last=1.05,
        premium=1.1,
        contract_cost=110.0,
        spread_pct=0.05,
        open_interest=500,
        volume=200,
        implied_volatility=0.35,
        delta=0.40,
        moneyness=0.01,
        projected_move_pct=0.04,
        breakeven_move_pct=0.03,
        expected_return_pct=0.7,
        extrinsic_ratio=0.8,
        scout_score=0.7,
        forge_score=0.5,
        learned_rank_score=0.8,
        payoff_model_score=0.75,
        path_holding_quality_score=0.6,
        payoff_shadow_rank=0.4,
        payoff_shadow_conservative_utility=0.1,
        notes=[],
    )


class UnifiedStackTests(unittest.TestCase):
    def test_full_rank_has_auditable_component_weights(self) -> None:
        candidate = _candidate()
        apply_base_unified_rank([candidate], profile=UNIFIED_RND)

        expected = 0.60 * 0.8 + 0.18 * 0.6 + 0.14 * 0.4 + 0.08 * 0.7
        self.assertAlmostEqual(candidate.forge_score, expected, places=4)
        self.assertEqual(candidate.ranker_mode, UNIFIED_RND)

    def test_component_ablations_remove_and_renormalize_weights(self) -> None:
        self.assertEqual(rank_weights(UNIFIED_NO_PATH).path, 0.0)
        cost_weights = rank_weights(UNIFIED_NO_COST_AWARE)
        self.assertEqual(cost_weights.challenger_rank, 0.0)
        self.assertEqual(cost_weights.conservative_utility, 0.0)
        self.assertAlmostEqual(sum(cost_weights.__dict__.values()), 1.0)

    def test_primary_only_disables_optional_hierarchy(self) -> None:
        self.assertFalse(uses_hierarchical_scout(UNIFIED_NO_HIERARCHICAL))
        self.assertFalse(uses_hierarchical_scout(UNIFIED_PRIMARY_ONLY))

        candidate = _candidate()
        apply_base_unified_rank([candidate], profile=UNIFIED_PRIMARY_ONLY)
        self.assertEqual(candidate.forge_score, 0.8)


if __name__ == "__main__":
    unittest.main()
