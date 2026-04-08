from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from engine.backtest.pricer import price_trade
from engine.orographic.schemas import ContractCandidate


class _EmptyChainProvider:
    def get_chain(self, symbol: str, as_of: date, fallback_spot: float = 0, fallback_vol: float = 0.35) -> pd.DataFrame:
        return pd.DataFrame()


class _SpreadQuoteProvider:
    def __init__(self, chain: pd.DataFrame) -> None:
        self.chain = chain

    def get_chain(self, symbol: str, as_of: date, fallback_spot: float = 0, fallback_vol: float = 0.35) -> pd.DataFrame:
        return self.chain.copy()


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [100.0, 109.0],
            "Close": [101.0, 110.0],
        },
        index=pd.to_datetime(["2026-04-06", "2026-04-10"]),
    )


def _candidate(**overrides: object) -> ContractCandidate:
    payload = {
        "symbol": "TEST",
        "contract_symbol": "TEST260410C00100000",
        "option_type": "call",
        "expiry": "2026-04-10",
        "strike": 100.0,
        "bid": 5.8,
        "ask": 6.0,
        "last": 5.9,
        "premium": 6.0,
        "contract_cost": 600.0,
        "spread_pct": 0.03,
        "open_interest": 500,
        "volume": 300,
        "implied_volatility": 0.35,
        "delta": 0.55,
        "moneyness": 0.0,
        "projected_move_pct": 0.05,
        "breakeven_move_pct": 0.03,
        "expected_return_pct": 0.8,
        "extrinsic_ratio": 0.7,
        "scout_score": 1.0,
        "forge_score": 0.7,
        "notes": [],
    }
    payload.update(overrides)
    return ContractCandidate(**payload)


class PricerTests(unittest.TestCase):
    def test_price_trade_skips_over_budget_contract(self) -> None:
        leg = price_trade(
            _candidate(),
            date(2026, 4, 6),
            date(2026, 4, 10),
            _history(),
            _EmptyChainProvider(),
        )
        self.assertIsNone(leg)

    def test_price_trade_marks_spread_exit_net_of_short_leg(self) -> None:
        chain = pd.DataFrame(
            [
                {
                    "option_type": "C",
                    "expire_date": "2026-04-10",
                    "strike": 100.0,
                    "bid": 10.0,
                    "ask": 10.3,
                },
                {
                    "option_type": "C",
                    "expire_date": "2026-04-10",
                    "strike": 105.0,
                    "bid": 5.0,
                    "ask": 5.5,
                },
            ]
        )
        leg = price_trade(
            _candidate(
                ask=4.0,
                premium=4.0,
                contract_cost=150.0,
                is_spread=True,
                spread_cost=1.5,
                short_strike=105.0,
                short_bid=5.0,
                short_ask=5.5,
            ),
            date(2026, 4, 6),
            date(2026, 4, 10),
            _history(),
            _SpreadQuoteProvider(chain),
        )
        self.assertIsNotNone(leg)
        assert leg is not None
        self.assertEqual(leg.contracts, 3)
        self.assertAlmostEqual(leg.exit_price, 4.5, places=4)
        self.assertAlmostEqual(leg.exit_value, 1350.0, places=2)


if __name__ == "__main__":
    unittest.main()
