from __future__ import annotations

import unittest

from engine.orographic.market_shock import MarketShockInput, classify_market_shock
from engine.orographic.schemas import MarketRegime


class MarketShockTests(unittest.TestCase):
    def test_extreme_vol_deleveraging_forces_abstain(self) -> None:
        shock = classify_market_shock(
            MarketShockInput(
                spy_bias_20d=-0.08,
                spy_return_5d=-0.05,
                vix_level=32.0,
                vix_change_5d=0.42,
                risk_off_score=1.0,
            ),
            MarketRegime(mode="extreme_vol", bias=-0.7, source_symbol="SPY"),
        )

        self.assertEqual(shock.label, "extreme_vol_deleveraging")
        self.assertTrue(shock.global_abstain)
        self.assertEqual(shock.stance, "abstain")
        self.assertIn("call", shock.blocked_sides)
        self.assertLessEqual(shock.max_extrinsic_ratio, 0.72)

    def test_good_market_still_tightens_melt_up_weeklies(self) -> None:
        shock = classify_market_shock(
            MarketShockInput(
                spy_bias_20d=0.05,
                spy_return_5d=0.025,
                vix_level=16.0,
                vix_change_5d=-0.08,
                risk_on_score=0.85,
            ),
            MarketRegime(mode="risk_on", bias=0.4, source_symbol="SPY"),
        )

        self.assertEqual(shock.label, "melt_up_fomo")
        self.assertFalse(shock.global_abstain)
        self.assertEqual(shock.stance, "tighten")
        self.assertIn("call", shock.preferred_sides)
        self.assertGreater(shock.live_score_buffer, 0.0)

    def test_ai_tech_unwind_tightens_calls_without_blanket_abstain(self) -> None:
        shock = classify_market_shock(
            MarketShockInput(
                spy_bias_20d=-0.02,
                spy_return_5d=-0.01,
                qqq_return_5d=-0.05,
                smh_return_5d=-0.07,
                vix_level=22.0,
                vix_change_5d=0.21,
            ),
            MarketRegime(mode="risk_off", bias=-0.3, source_symbol="SPY"),
        )

        self.assertEqual(shock.label, "ai_tech_unwind")
        self.assertFalse(shock.global_abstain)
        self.assertEqual(shock.stance, "tighten")
        self.assertIn("ai_semiconductor_unwind", shock.drivers)


if __name__ == "__main__":
    unittest.main()
