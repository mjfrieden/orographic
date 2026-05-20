from __future__ import annotations

import unittest

from engine.orographic.moonshot import assess_candidate, select_moonshot_lane
from engine.orographic.schemas import ContractCandidate, MarketRegime


def _candidate(
    symbol: str = "AAPL",
    option_type: str = "call",
    *,
    cost: float = 120.0,
    delta: float = 0.34,
    iv: float = 0.48,
    forge_score: float = 0.72,
) -> ContractCandidate:
    return ContractCandidate(
        symbol=symbol,
        contract_symbol=f"{symbol}MOON",
        option_type=option_type,
        expiry="2026-04-17",
        strike=100.0,
        bid=1.1,
        ask=1.2,
        last=1.15,
        premium=cost / 100.0,
        contract_cost=cost,
        spread_pct=0.08,
        open_interest=600,
        volume=180,
        implied_volatility=iv,
        delta=delta,
        moneyness=0.03,
        projected_move_pct=0.05,
        breakeven_move_pct=0.03,
        expected_return_pct=1.2,
        extrinsic_ratio=0.7,
        scout_score=0.7,
        forge_score=forge_score,
        path_holding_quality_score=0.74,
        notes=[],
    )


class MoonshotTests(unittest.TestCase):
    def test_assess_candidate_rewards_nimrod_tail_profile(self) -> None:
        candidate = _candidate()
        assessment = assess_candidate(
            candidate,
            [candidate, _candidate(symbol="AAPL"), _candidate(symbol="AAPL")],
            MarketRegime(mode="risk_on", bias=0.4, source_symbol="SPY"),
        )

        self.assertTrue(assessment.eligible)
        self.assertGreaterEqual(assessment.tail_upside_score, 0.68)
        self.assertIn("cheap premium", " ".join(assessment.reasons))
        self.assertIn("medium delta", " ".join(assessment.reasons))

    def test_risk_off_call_is_not_eligible_for_satellite_slot(self) -> None:
        candidate = _candidate()
        assessment = assess_candidate(
            candidate,
            [candidate],
            MarketRegime(mode="risk_off", bias=-0.4, source_symbol="SPY"),
        )

        self.assertFalse(assessment.eligible)
        self.assertIn("risk-off", " ".join(assessment.reasons))

    def test_select_moonshot_lane_returns_capped_payload(self) -> None:
        good = _candidate("NVDA")
        weak = _candidate("CSCO", cost=420.0, delta=0.08, iv=0.18, forge_score=0.45)
        payload = select_moonshot_lane(
            [weak, good, _candidate("NVDA")],
            MarketRegime(mode="risk_on", bias=0.3, source_symbol="SPY"),
            slot_count=1,
        )

        self.assertEqual(payload["summary"]["pick_count"], 1)
        self.assertEqual(payload["picks"][0]["symbol"], "NVDA")
        self.assertIn("moonshot", payload["picks"][0])


if __name__ == "__main__":
    unittest.main()
