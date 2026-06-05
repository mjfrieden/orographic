from __future__ import annotations

import unittest
from unittest import mock

from engine.orographic.council import select_board
from engine.orographic.schemas import ContractCandidate, MarketRegime


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
        self.assertEqual(
            result.summary["abstain_audit"]["primary_reason"],
            "below_live_score",
        )

    def test_council_limits_side_concentration(self) -> None:
        candidates = [
            _candidate("AAPL", "call", 0.9),
            _candidate("MSFT", "call", 0.88),
            _candidate("NVDA", "put", 0.86),
        ]
        with mock.patch("engine.orographic.council._fetch_corr_matrix", return_value=None):
            result = select_board(
                candidates,
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                live_size=2,
            )
        self.assertEqual(len(result.live_board), 2)
        self.assertEqual({row.option_type for row in result.live_board}, {"call", "put"})

    def test_council_audits_extrinsic_abstentions(self) -> None:
        candidate = _candidate("AAPL", "put", 0.82)
        candidate.extrinsic_ratio = 1.0
        result = select_board(
            [candidate],
            MarketRegime(mode="risk_on", bias=0.3, source_symbol="SPY"),
        )
        self.assertTrue(result.abstain)
        self.assertEqual(
            result.summary["abstain_audit"]["primary_reason"],
            "extrinsic_limit",
        )
        self.assertEqual(
            result.summary["abstain_audit"]["blocked_symbols"]["extrinsic_only"],
            ["AAPL"],
        )

    def test_council_prefers_prior_board_when_replacement_uplift_is_small(self) -> None:
        candidates = [
            _candidate("AAPL", "call", 0.90),
            _candidate("NVDA", "put", 0.89),
            _candidate("XOM", "put", 0.88),
        ]
        with mock.patch("engine.orographic.council._fetch_corr_matrix", return_value=None):
            result = select_board(
                candidates,
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                live_size=2,
                prior_live_board_symbols=["AAPL", "XOM"],
                turnover_switch_penalty=0.03,
            )

        self.assertEqual([row.symbol for row in result.live_board], ["AAPL", "XOM"])
        self.assertTrue(result.summary["turnover"]["applied"])
        self.assertEqual(result.summary["turnover"]["reason"], "retained_prior_board")
        self.assertIn("Turnover penalty kept the prior live board", " ".join(result.summary["notes"]))

    def test_council_live_blocks_probation_symbols_but_keeps_them_shadow_visible(self) -> None:
        candidates = [
            _candidate("NFLX", "call", 0.95),
            _candidate("TLT", "call", 0.94),
            _candidate("AAPL", "put", 0.93),
        ]
        with mock.patch("engine.orographic.council._fetch_corr_matrix", return_value=None):
            result = select_board(
                candidates,
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                live_size=2,
                shadow_size=3,
            )

        self.assertEqual([row.symbol for row in result.live_board], ["AAPL"])
        self.assertEqual({row.symbol for row in result.shadow_board}, {"NFLX", "TLT"})
        for row in result.shadow_board:
            self.assertIn("symbol_probation", row.council_risk_flags)
        self.assertEqual(
            result.summary["abstain_audit"]["blocked_symbols"]["symbol_probation"],
            ["NFLX", "TLT"],
        )

    def test_council_does_not_probation_block_bac(self) -> None:
        result = select_board(
            [_candidate("BAC", "put", 0.95)],
            MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
            live_size=1,
        )

        self.assertFalse(result.abstain)
        self.assertEqual([row.symbol for row in result.live_board], ["BAC"])
        self.assertNotIn("symbol_probation", result.live_board[0].council_risk_flags)


if __name__ == "__main__":
    unittest.main()
