from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from engine.backtest.replay import forge_candidates_as_of
from engine.orographic.schemas import ScoutSignal


class _ReplayProvider:
    def __init__(self, chain: pd.DataFrame) -> None:
        self.chain = chain

    def get_chain(self, symbol: str, as_of: date, fallback_spot: float = 0, fallback_vol: float = 0.35) -> pd.DataFrame:
        return self.chain.copy()


def _signal() -> ScoutSignal:
    return ScoutSignal(
        symbol="TEST",
        direction="call",
        spot=100.0,
        momentum_5d=0.03,
        momentum_20d=0.06,
        rsi_14=58.0,
        realized_vol_20d=0.22,
        atr_pct_14d=0.02,
        technical_score=0.7,
        empirical_score=0.4,
        scout_score=0.8,
        notes=[],
    )


class ReplayTests(unittest.TestCase):
    def test_replay_constructs_debit_spread_candidate(self) -> None:
        chain = pd.DataFrame(
            [
                {
                    "option_type": "C",
                    "expire_date": "2026-04-10",
                    "strike": 100.0,
                    "bid": 5.8,
                    "ask": 6.0,
                    "delta": 0.55,
                    "implied_volatility": 0.30,
                    "open_interest": 1200,
                    "trade_volume": 400,
                },
                {
                    "option_type": "C",
                    "expire_date": "2026-04-10",
                    "strike": 105.0,
                    "bid": 4.2,
                    "ask": 4.4,
                    "delta": 0.24,
                    "implied_volatility": 0.29,
                    "open_interest": 900,
                    "trade_volume": 300,
                },
            ]
        )

        candidates = forge_candidates_as_of(
            _signal(),
            date(2026, 4, 6),
            _ReplayProvider(chain),
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertTrue(candidate.is_spread)
        self.assertAlmostEqual(candidate.spread_cost or 0.0, 1.8, places=4)
        self.assertEqual(candidate.short_strike, 105.0)
        self.assertEqual(candidate.contract_cost, 180.0)


if __name__ == "__main__":
    unittest.main()
