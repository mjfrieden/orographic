from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from engine.orographic.schemas import MarketRegime
from engine.orographic.scout import build_signal
from engine.orographic.sentinel import SentinelScore


def _frame() -> pd.DataFrame:
    rows = 90
    close = pd.Series([100 + i * 0.2 for i in range(rows)], dtype=float)
    return pd.DataFrame(
        {
            "Close": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Volume": pd.Series([1_000_000 + i * 1000 for i in range(rows)], dtype=float),
        }
    )


class ScoutTests(unittest.TestCase):
    def test_strong_counter_regime_put_can_survive_risk_on(self) -> None:
        with (
            mock.patch("engine.orographic.scout._ml_scout_score", return_value=-0.6),
            mock.patch(
                "engine.orographic.scout.fetch_ai_multiplier",
                return_value=SentinelScore(multiplier=1.0, catalyst="none", rationale=""),
            ),
        ):
            signal, diagnostics = build_signal(
                "TEST",
                MarketRegime(mode="risk_on", bias=0.4, source_symbol="SPY"),
                _frame(),
                0.0,
                return_diagnostics=True,
            )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.direction, "put")
        self.assertAlmostEqual(signal.scout_score, -0.78, places=4)
        self.assertTrue(diagnostics["counter_regime_survivor"])
        self.assertEqual(diagnostics["reason"], "selected")

    def test_weak_counter_regime_put_is_rejected_in_risk_on(self) -> None:
        with (
            mock.patch("engine.orographic.scout._ml_scout_score", return_value=-0.2),
            mock.patch(
                "engine.orographic.scout.fetch_ai_multiplier",
                return_value=SentinelScore(multiplier=1.0, catalyst="none", rationale=""),
            ),
        ):
            signal, diagnostics = build_signal(
                "TEST",
                MarketRegime(mode="risk_on", bias=0.4, source_symbol="SPY"),
                _frame(),
                0.0,
                return_diagnostics=True,
            )

        self.assertIsNone(signal)
        self.assertEqual(diagnostics["pre_veto_direction"], "put")
        self.assertEqual(diagnostics["reason"], "counter_regime_weak_conviction")


if __name__ == "__main__":
    unittest.main()
