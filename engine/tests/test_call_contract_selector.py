from __future__ import annotations

import unittest

from engine.orographic.call_contract_selector import (
    blend_call_selector_score,
    contract_side_score,
)
from engine.orographic.schemas import ContractCandidate


def _candidate(option_type: str = "call") -> ContractCandidate:
    return ContractCandidate(
        symbol="UBER",
        contract_symbol="UBER260529C00076000",
        option_type=option_type,
        expiry="2026-05-29",
        strike=76.0,
        bid=1.05,
        ask=1.12,
        last=1.08,
        premium=1.12,
        contract_cost=112.0,
        spread_pct=0.065,
        open_interest=900,
        volume=240,
        implied_volatility=0.44,
        delta=0.34 if option_type == "call" else -0.34,
        moneyness=0.018,
        projected_move_pct=0.075,
        breakeven_move_pct=0.032,
        expected_return_pct=1.15,
        extrinsic_ratio=1.0,
        scout_score=0.72,
        forge_score=0.82,
        spread_cost=1.12,
        allocation_weight=1.0,
        iv_rank=0.52,
    )


class CallContractSelectorTests(unittest.TestCase):
    def test_contract_side_score_rewards_nimrod_call_shape(self) -> None:
        good = _candidate()
        weak = _candidate()
        weak.contract_cost = 340.0
        weak.delta = 0.08
        weak.implied_volatility = 0.85
        weak.projected_move_pct = 0.02
        weak.breakeven_move_pct = 0.07
        weak.spread_pct = 0.22

        self.assertGreater(contract_side_score(good), contract_side_score(weak))
        self.assertGreater(contract_side_score(good), 0.75)

    def test_blend_is_call_only_and_uses_70_30_weights(self) -> None:
        call = _candidate("call")
        score = blend_call_selector_score(call, model_score=0.80, mode="active")

        self.assertIsNotNone(score)
        assert score is not None
        expected = round((0.70 * 0.80) + (0.30 * contract_side_score(call)), 4)
        self.assertEqual(score.blended_score, expected)
        self.assertEqual(score.mode, "active")
        self.assertIsNone(blend_call_selector_score(_candidate("put"), model_score=0.80))


if __name__ == "__main__":
    unittest.main()
