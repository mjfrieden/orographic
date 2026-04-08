from __future__ import annotations

from datetime import date
import unittest

from engine.backtest.alpha_experiment import (
    apply_symbol_priors,
    build_symbol_priors,
    estimated_cost_basis,
    filter_by_cost_basis,
)
from engine.backtest.pricer import TradeLeg
from engine.orographic.schemas import ContractCandidate


def _candidate(symbol: str = "TEST", **overrides: object) -> ContractCandidate:
    payload = {
        "symbol": symbol,
        "contract_symbol": f"{symbol}260410C00100000",
        "option_type": "call",
        "expiry": "2026-04-10",
        "strike": 100.0,
        "bid": 1.4,
        "ask": 1.5,
        "last": 1.45,
        "premium": 1.5,
        "contract_cost": 150.0,
        "spread_pct": 0.04,
        "open_interest": 500,
        "volume": 300,
        "implied_volatility": 0.25,
        "delta": 0.45,
        "moneyness": 0.0,
        "projected_move_pct": 0.03,
        "breakeven_move_pct": 0.02,
        "expected_return_pct": 0.6,
        "extrinsic_ratio": 0.7,
        "scout_score": 0.6,
        "forge_score": 0.6,
        "allocation_weight": 1.0,
        "notes": [],
    }
    payload.update(overrides)
    return ContractCandidate(**payload)


def _trade(symbol: str, exit_day: date, pnl: float, pnl_pct: float) -> TradeLeg:
    return TradeLeg(
        symbol=symbol,
        contract_symbol=f"{symbol}260410C00100000",
        option_type="call",
        strike=100.0,
        expiry="2026-04-10",
        entry_date=exit_day,
        exit_date=exit_day,
        entry_spot=100.0,
        exit_spot=101.0,
        entry_price=1.0,
        exit_price=1.2,
        contracts=1,
        cost_basis=100.0,
        exit_value=120.0,
        pnl=pnl,
        pnl_pct=pnl_pct,
        expired_worthless=False,
        forge_score=0.6,
        scout_score=0.5,
        implied_volatility=0.25,
    )


class AlphaExperimentTests(unittest.TestCase):
    def test_filter_by_cost_basis_drops_expensive_candidates(self) -> None:
        cheap = _candidate(symbol="CHEAP", ask=1.5, premium=1.5, contract_cost=150.0)
        expensive = _candidate(
            symbol="EXP",
            ask=3.0,
            premium=3.0,
            contract_cost=300.0,
            allocation_weight=3.0,
            scout_score=1.0,
        )

        kept, diag = filter_by_cost_basis([cheap, expensive], 500.0)

        self.assertEqual([row.symbol for row in kept], ["CHEAP"])
        self.assertEqual(diag["dropped"], 1)
        self.assertEqual(estimated_cost_basis(expensive), 1500.0)

    def test_apply_symbol_priors_boosts_winners_and_excludes_losers(self) -> None:
        monday = date(2026, 4, 14)
        trades = [
            _trade("WIN", date(2026, 3, 3), 80.0, 0.80),
            _trade("WIN", date(2026, 3, 10), 70.0, 0.70),
            _trade("WIN", date(2026, 3, 17), 60.0, 0.60),
            _trade("WIN", date(2026, 3, 24), 65.0, 0.65),
            _trade("WIN", date(2026, 3, 31), 55.0, 0.55),
            _trade("LOSE", date(2026, 3, 3), -90.0, -0.90),
            _trade("LOSE", date(2026, 3, 10), -80.0, -0.80),
            _trade("LOSE", date(2026, 3, 17), -75.0, -0.75),
            _trade("LOSE", date(2026, 3, 24), -70.0, -0.70),
            _trade("LOSE", date(2026, 3, 31), -65.0, -0.65),
        ]
        priors = build_symbol_priors(trades, monday, lookback_weeks=12, min_trades=5)

        adjusted, diag = apply_symbol_priors(
            [_candidate("WIN"), _candidate("MID"), _candidate("LOSE")],
            priors,
            top_n=1,
            bottom_n=1,
            boost=0.03,
        )

        self.assertEqual([row.symbol for row in adjusted], ["WIN", "MID"])
        win_candidate = adjusted[0]
        self.assertAlmostEqual(win_candidate.forge_score, 0.63, places=4)
        self.assertIn("WIN", diag["boosted_symbols"])
        self.assertIn("LOSE", diag["excluded_symbols"])


if __name__ == "__main__":
    unittest.main()
