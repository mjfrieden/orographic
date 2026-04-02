from __future__ import annotations

import unittest

from orographic.council import select_board
from orographic.schemas import ContractCandidate, MarketRegime


def _candidate(symbol: str, option_type: str, score: float) -> ContractCandidate:
    return ContractCandidate(
        symbol=symbol,
        contract_symbol=f"{symbol}TEST",
        option_type=option_type,
        expiry="2026-04-09",
        strike=100.0,
        bid=0.9,
        ask=1.0,
        last=0.95,
        premium=1.0,
        contract_cost=100.0,
        spread_pct=0.1,
        open_interest=500,
        volume=120,
        implied_volatility=0.35,
        delta=0.3,
        moneyness=0.03,
        projected_move_pct=0.05,
        breakeven_move_pct=0.03,
        expected_return_pct=1.1,
        extrinsic_ratio=0.7,
        scout_score=0.7,
        forge_score=score,
        notes=[],
    )


class CouncilTests(unittest.TestCase):
    def test_council_can_abstain(self) -> None:
        result = select_board(
            [_candidate("AAPL", "call", 0.4)],
            MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
        )
        self.assertTrue(result.abstain)
        self.assertEqual(result.live_board, [])

    def test_council_limits_side_concentration(self) -> None:
        candidates = [
            _candidate("AAPL", "call", 0.9),
            _candidate("MSFT", "call", 0.88),
            _candidate("NVDA", "put", 0.86),
        ]
        result = select_board(
            candidates,
            MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
            live_size=2,
        )
        self.assertEqual(len(result.live_board), 2)
        self.assertEqual({row.option_type for row in result.live_board}, {"call", "put"})


if __name__ == "__main__":
    unittest.main()

