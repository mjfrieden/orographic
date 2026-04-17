from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from engine.backtest.replay import forge_candidates_as_of, select_expiry_from_chain
from engine.orographic.schemas import ScoutSignal


class _ReplayProvider:
    def __init__(self, chain: pd.DataFrame, source: str = "real_chain") -> None:
        self.chain = chain
        self.source = source

    def get_chain(self, symbol: str, as_of: date, fallback_spot: float = 0, fallback_vol: float = 0.35) -> pd.DataFrame:
        return self.chain.copy()

    def get_chain_with_source(
        self,
        symbol: str,
        as_of: date,
        fallback_spot: float = 0,
        fallback_vol: float = 0.35,
    ) -> tuple[pd.DataFrame, str]:
        return self.chain.copy(), self.source


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
    def test_replay_constructs_single_leg_candidate(self) -> None:
        chain = pd.DataFrame(
            [
                {
                    "option_type": "C",
                    "expire_date": "2026-04-10",
                    "strike": 100.0,
                    "bid": 1.7,
                    "ask": 1.8,
                    "delta": 0.55,
                    "implied_volatility": 0.30,
                    "open_interest": 1200,
                    "trade_volume": 400,
                },
                {
                    "option_type": "C",
                    "expire_date": "2026-04-10",
                    "strike": 105.0,
                    "bid": 0.7,
                    "ask": 0.8,
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
        self.assertFalse(candidate.is_spread)
        self.assertAlmostEqual(candidate.spread_cost or 0.0, 1.8, places=4)
        self.assertIsNone(candidate.short_strike)
        self.assertEqual(candidate.contract_cost, 180.0)
        self.assertEqual(candidate.entry_data_source, "real_chain")
        self.assertEqual(candidate.entry_quote_type, "ask")

    def test_replay_rejects_expensive_single_leg_instead_of_spreading(self) -> None:
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

        self.assertEqual(candidates, [])

    def test_replay_strict_mode_skips_synthetic_chain(self) -> None:
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
                }
            ]
        )
        candidates = forge_candidates_as_of(
            _signal(),
            date(2026, 4, 6),
            _ReplayProvider(chain, source="synthetic_chain"),
            strict_options_data=True,
        )
        self.assertEqual(candidates, [])

    def test_next_listed_weekly_uses_first_expiry_after_same_week_friday(self) -> None:
        chain = pd.DataFrame(
            [
                {
                    "option_type": "C",
                    "expire_date": "2026-04-17",
                    "strike": 100.0,
                    "bid": 1.7,
                    "ask": 1.8,
                    "delta": 0.55,
                    "implied_volatility": 0.30,
                    "open_interest": 1200,
                    "trade_volume": 400,
                }
            ]
        )

        candidates = forge_candidates_as_of(
            _signal(),
            date(2026, 4, 6),
            _ReplayProvider(chain),
            expiry_policy="next_listed_weekly",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].expiry, "2026-04-17")

    def test_target_dte_selects_expiry_inside_configured_window(self) -> None:
        chain = pd.DataFrame(
            [
                {"expire_date": "2026-04-10"},
                {"expire_date": "2026-04-17"},
                {"expire_date": "2026-04-24"},
            ]
        )

        expiry = select_expiry_from_chain(
            chain,
            date(2026, 4, 6),
            expiry_policy="target_dte",
            target_dte_min=7,
            target_dte_max=14,
        )

        self.assertEqual(expiry, date(2026, 4, 17))


if __name__ == "__main__":
    unittest.main()
