from __future__ import annotations

from datetime import date
import unittest
from unittest import mock

import pandas as pd

from engine.orographic.forge import select_signals_for_forge
from engine.orographic.pipeline import load_universe
from engine.orographic.schemas import ScoutSignal


def _signal(symbol: str, spot: float = 100.0) -> ScoutSignal:
    return ScoutSignal(
        symbol=symbol,
        direction="call",
        spot=spot,
        momentum_5d=0.03,
        momentum_20d=0.05,
        rsi_14=58.0,
        realized_vol_20d=0.22,
        atr_pct_14d=0.02,
        technical_score=0.4,
        empirical_score=0.2,
        scout_score=0.6,
        notes=[],
    )


def _chain(*, bid: float, ask: float, open_interest: int, volume: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "bid": bid,
                "ask": ask,
                "strike": 100.0,
                "openInterest": open_interest,
                "volume": volume,
            },
            {
                "bid": bid * 0.8,
                "ask": ask * 0.8,
                "strike": 101.0,
                "openInterest": open_interest,
                "volume": volume,
            },
        ]
    )


class PipelineTests(unittest.TestCase):
    def test_default_universe_expands_to_100_symbols(self) -> None:
        universe = load_universe(None)
        self.assertEqual(len(universe), 100)
        self.assertEqual(universe[:4], ["SPY", "QQQ", "IWM", "DIA"])

    def test_pre_forge_gate_skips_illiquid_signals_and_backfills_next_names(self) -> None:
        signals = [_signal("AAA"), _signal("BBB"), _signal("CCC")]
        liquid_chain = _chain(bid=1.0, ask=1.08, open_interest=400, volume=120)
        illiquid_chain = _chain(bid=0.05, ask=0.50, open_interest=10, volume=5)

        def fake_option_chain(symbol: str, expiry: str) -> tuple[pd.DataFrame, pd.DataFrame]:
            frame = illiquid_chain if symbol == "AAA" else liquid_chain
            return frame.copy(), pd.DataFrame()

        with (
            mock.patch("engine.orographic.forge.option_expiries", return_value=["2026-04-17"]),
            mock.patch("engine.orographic.forge.option_chain", side_effect=fake_option_chain),
        ):
            selected, diagnostics = select_signals_for_forge(
                signals,
                target_count=2,
                today=date(2026, 4, 13),
            )

        self.assertEqual([signal.symbol for signal in selected], ["BBB", "CCC"])
        self.assertEqual(diagnostics["signals_selected"], 2)
        self.assertIn("AAA", [row["symbol"] for row in diagnostics["rejections"]])


if __name__ == "__main__":
    unittest.main()
