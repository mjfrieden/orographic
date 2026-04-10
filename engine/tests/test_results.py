from __future__ import annotations

from datetime import date
import unittest

from engine.backtest.pricer import TradeLeg
from engine.backtest.results import apply_coverage_policy, build_results


def _trade(
    symbol: str,
    entry_source: str,
    exit_source: str,
    coverage: float,
    *,
    exit_day: date = date(2026, 4, 10),
    cost_basis: float = 100.0,
    exit_value: float = 120.0,
    pnl: float = 20.0,
    pnl_pct: float = 0.2,
) -> TradeLeg:
    return TradeLeg(
        symbol=symbol,
        contract_symbol=f"{symbol}260410C00100000",
        option_type="call",
        strike=100.0,
        expiry="2026-04-10",
        entry_date=date(2026, 4, 6),
        exit_date=exit_day,
        entry_spot=100.0,
        exit_spot=101.0,
        entry_price=1.0,
        exit_price=1.2,
        contracts=1,
        cost_basis=cost_basis,
        exit_value=exit_value,
        pnl=pnl,
        pnl_pct=pnl_pct,
        expired_worthless=False,
        forge_score=0.6,
        scout_score=0.5,
        implied_volatility=0.25,
        entry_data_source=entry_source,
        exit_data_source=exit_source,
        entry_quote_type="ask",
        exit_quote_type="bid",
        options_data_coverage_pct=coverage,
    )


class ResultsTests(unittest.TestCase):
    def test_build_results_reports_coverage_breakdown(self) -> None:
        results = build_results(
            [
                _trade("REAL", "real_chain", "real_chain", 1.0),
                _trade("HYBRID", "real_chain", "hybrid", 0.75),
                _trade("SYN", "synthetic_chain", "synthetic_chain", 0.0),
            ],
            date(2026, 4, 1),
            date(2026, 4, 30),
        )

        coverage = results["options_data_coverage"]
        self.assertEqual(coverage["entry_source_counts"]["real_chain"], 2)
        self.assertEqual(coverage["exit_source_counts"]["synthetic_chain"], 1)
        self.assertAlmostEqual(coverage["entry_real_trade_pct"], 2 / 3, places=4)
        self.assertAlmostEqual(coverage["fully_real_trade_pct"], 1 / 3, places=4)

    def test_apply_coverage_policy_flags_shortfall(self) -> None:
        results = build_results(
            [_trade("SYN", "synthetic_chain", "synthetic_chain", 0.0)],
            date(2026, 4, 1),
            date(2026, 4, 30),
        )
        annotated = apply_coverage_policy(
            results,
            strict_options_data=False,
            min_real_coverage_pct=0.9,
        )
        self.assertTrue(annotated["coverage_policy"]["coverage_failed"])

    def test_build_results_uses_compounded_equity_for_drawdown(self) -> None:
        results = build_results(
            [
                _trade("UP", "real_chain", "real_chain", 1.0, exit_day=date(2026, 4, 10), pnl=100.0, pnl_pct=1.0, cost_basis=100.0, exit_value=200.0),
                _trade("DOWN", "real_chain", "real_chain", 1.0, exit_day=date(2026, 4, 17), pnl=-200.0, pnl_pct=-1.0, cost_basis=200.0, exit_value=0.0),
            ],
            date(2026, 4, 1),
            date(2026, 4, 30),
        )

        self.assertEqual(results["max_drawdown"], -1.0)


if __name__ == "__main__":
    unittest.main()
