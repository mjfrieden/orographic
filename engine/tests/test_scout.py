from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from engine.orographic.schemas import MarketRegime
from engine.orographic.scout import _MODEL_PATH, _SCALER_PATH, _load_model, build_signal
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
    def test_scout_model_loader_returns_trained_artifact_when_available(self) -> None:
        if not _MODEL_PATH.exists() or not _SCALER_PATH.exists():
            self.skipTest("Scout model artifacts are not present in this checkout.")
        _load_model.cache_clear()
        loaded = _load_model()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(len(loaded), 8)

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

    def test_option_payoff_side_model_can_override_directional_scout(self) -> None:
        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "active"}),
            mock.patch("engine.orographic.scout._ml_scout_score", return_value=0.5),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.15, "put_edge": 0.70, "no_trade": 0.15},
                    "trained_option_payoff_three_class",
                ),
            ),
            mock.patch(
                "engine.orographic.scout.fetch_ai_multiplier",
                return_value=SentinelScore(multiplier=1.0, catalyst="none", rationale=""),
            ),
        ):
            signal, diagnostics = build_signal(
                "TEST",
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                _frame(),
                0.0,
                return_diagnostics=True,
            )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.direction, "put")
        self.assertAlmostEqual(signal.scout_score, -0.55, places=4)
        self.assertEqual(signal.scout_model_mode, "trained_option_payoff_three_class")
        self.assertEqual(diagnostics["side_model_override"]["target"], "strict_real_option_payoff")

    def test_option_payoff_side_model_is_shadow_by_default(self) -> None:
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("engine.orographic.scout._ml_scout_score", return_value=0.5),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.15, "put_edge": 0.70, "no_trade": 0.15},
                    "trained_option_payoff_three_class",
                ),
            ),
            mock.patch(
                "engine.orographic.scout.fetch_ai_multiplier",
                return_value=SentinelScore(multiplier=1.0, catalyst="none", rationale=""),
            ),
        ):
            signal, diagnostics = build_signal(
                "TEST",
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                _frame(),
                0.0,
                return_diagnostics=True,
            )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.direction, "call")
        self.assertAlmostEqual(signal.scout_score, 0.5, places=4)
        self.assertEqual(diagnostics["side_model_override"]["mode"], "shadow")

    def test_option_direction_target_updates_ml_note(self) -> None:
        with (
            mock.patch(
                "engine.orographic.scout._load_model",
                return_value=(object(), object(), [], None, "none", "strict_real_option_direction", "call-side edge target", "call_edge"),
            ),
            mock.patch("engine.orographic.scout._ml_scout_score", return_value=0.4),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.60, "put_edge": 0.25, "no_trade": 0.15},
                    "trained_option_payoff_three_class",
                ),
            ),
            mock.patch(
                "engine.orographic.scout.fetch_ai_multiplier",
                return_value=SentinelScore(multiplier=1.0, catalyst="none", rationale=""),
            ),
        ):
            signal, diagnostics = build_signal(
                "TEST",
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                _frame(),
                0.0,
                return_diagnostics=True,
            )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(diagnostics["primary_target"], "strict_real_option_direction")
        self.assertIn("p_call_edge", signal.notes[0])


if __name__ == "__main__":
    unittest.main()
