from __future__ import annotations

import unittest

from engine.backtest.risk_controls import apply_candidate_concentration_caps, sector_for_symbol
from engine.orographic.schemas import ContractCandidate


def _candidate(symbol: str, score: float) -> ContractCandidate:
    return ContractCandidate(
        symbol=symbol,
        contract_symbol=f"{symbol}260410C00100000",
        option_type="call",
        expiry="2026-04-10",
        strike=100.0,
        bid=1.0,
        ask=1.1,
        last=1.05,
        premium=1.1,
        contract_cost=110.0,
        spread_pct=0.095,
        open_interest=500,
        volume=100,
        implied_volatility=0.30,
        delta=0.5,
        moneyness=0.0,
        projected_move_pct=0.03,
        breakeven_move_pct=0.02,
        expected_return_pct=0.5,
        extrinsic_ratio=0.8,
        scout_score=0.6,
        forge_score=score,
        notes=[],
    )


class RiskControlsTests(unittest.TestCase):
    def test_sector_for_symbol_uses_default_bucket(self) -> None:
        self.assertEqual(sector_for_symbol("BAC"), "financials")
        self.assertEqual(sector_for_symbol("not-real"), "unknown")

    def test_apply_candidate_concentration_caps_keeps_highest_ranked(self) -> None:
        candidates = [
            _candidate("BAC", 0.90),
            _candidate("JPM", 0.80),
            _candidate("WFC", 0.70),
            _candidate("NVDA", 0.60),
        ]

        kept, diag = apply_candidate_concentration_caps(
            candidates,
            max_sector_candidates=2,
        )

        self.assertEqual([row.symbol for row in kept], ["BAC", "JPM", "NVDA"])
        self.assertEqual(diag["dropped_sector_cap"], 1)
        self.assertEqual(diag["kept"], 3)
        self.assertIn("sector_bucket=financials", kept[0].notes)

    def test_apply_candidate_concentration_caps_limits_symbol_clusters(self) -> None:
        kept, diag = apply_candidate_concentration_caps(
            [_candidate("BAC", 0.90), _candidate("BAC", 0.80), _candidate("BAC", 0.70)],
            max_symbol_candidates=1,
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(diag["dropped_symbol_cap"], 2)


if __name__ == "__main__":
    unittest.main()
