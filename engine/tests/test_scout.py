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
    def test_hierarchical_challenger_is_observed_without_changing_live_signal(self) -> None:
        observation = {
            "mode": "observation_only",
            "execution_effect": "none_observation_only",
            "trade_probability": 0.82,
            "conditional_call_probability": 0.10,
            "call_edge": 0.082,
            "put_edge": 0.738,
            "no_trade": 0.18,
            "preferred_side": "put",
            "would_abstain": False,
        }
        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "shadow"}, clear=True),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(0.5, 0.75)),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=({"call_edge": 0.70, "put_edge": 0.15, "no_trade": 0.15}, "trained_option_payoff_three_class"),
            ),
            mock.patch("engine.orographic.scout._hierarchical_side_observation", return_value=observation),
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
        self.assertEqual(signal.scout_score, 0.5)
        self.assertEqual(diagnostics["hierarchical_side_challenger"]["preferred_side"], "put")
        self.assertEqual(diagnostics["hierarchical_side_challenger"]["execution_effect"], "none_observation_only")

    def test_scout_model_loader_returns_trained_artifact_when_available(self) -> None:
        if not _MODEL_PATH.exists() or not _SCALER_PATH.exists():
            self.skipTest("Scout model artifacts are not present in this checkout.")
        _load_model.cache_clear()
        loaded = _load_model()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(len(loaded), 9)

    def test_strong_counter_regime_put_can_survive_risk_on(self) -> None:
        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "shadow"}),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(-0.6, 0.2)),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.10, "put_edge": 0.75, "no_trade": 0.15},
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
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "shadow"}),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(-0.2, 0.4)),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.10, "put_edge": 0.60, "no_trade": 0.30},
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
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(0.5, 0.75)),
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
        self.assertTrue(diagnostics["side_aware"]["active_policy"]["applied"])
        self.assertEqual(diagnostics["side_aware"]["active_policy"]["policy"], "canonical_three_class")

    def test_option_payoff_side_model_shadow_conflict_is_logged_but_not_blocked(self) -> None:
        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "shadow"}, clear=True),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(0.5, 0.75)),
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
        self.assertEqual(diagnostics["side_model_override"]["mode"], "shadow")
        self.assertEqual(diagnostics["side_aware"]["shadow_guard"]["reason"], "shadow_direction_conflict")
        self.assertEqual(diagnostics["side_aware"]["shadow_guard"]["preferred_side"], "put")
        self.assertTrue(diagnostics["side_aware"]["shadow_guard"]["passed"])

    def test_option_payoff_side_model_active_no_trade_blocks_trade(self) -> None:
        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "active"}),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(0.5, 0.75)),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.20, "put_edge": 0.10, "no_trade": 0.70},
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

        self.assertIsNone(signal)
        self.assertEqual(diagnostics["reason"], "scout_model_no_trade")
        self.assertEqual(diagnostics["side_aware"]["active_policy"]["reason"], "scout_model_no_trade")
        self.assertTrue(diagnostics["side_aware"]["active_policy"]["applied"])
        self.assertFalse(diagnostics["side_aware"]["active_policy"]["passed"])

    def test_option_payoff_side_model_shadow_no_trade_is_observation_only(self) -> None:
        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "shadow"}, clear=True),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(0.5, 0.75)),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.20, "put_edge": 0.10, "no_trade": 0.70},
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
        self.assertEqual(diagnostics["reason"], "selected")
        guard = diagnostics["side_aware"]["shadow_guard"]
        self.assertTrue(guard["passed"])
        self.assertTrue(guard["would_veto"])
        self.assertEqual(guard["reason"], "shadow_no_trade_veto")
        self.assertEqual(guard["execution_effect"], "none_observation_only")

    def test_option_payoff_side_model_shadow_allows_small_disagreement(self) -> None:
        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "shadow"}, clear=True),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(0.5, 0.75)),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.46, "put_edge": 0.36, "no_trade": 0.18},
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
        self.assertTrue(diagnostics["side_aware"]["shadow_guard"]["passed"])

    def test_option_direction_target_updates_ml_note(self) -> None:
        with (
            mock.patch(
                "engine.orographic.scout._load_model",
                return_value=(object(), object(), [], None, "none", 0.5, "strict_real_option_direction", "call-side edge target", "call_edge"),
            ),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(0.4, 0.7)),
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

    def test_sentinel_v2_fields_are_captured_in_diagnostics_and_signal(self) -> None:
        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "shadow"}),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(0.45, 0.725)),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.62, "put_edge": 0.18, "no_trade": 0.20},
                    "trained_option_payoff_three_class",
                ),
            ),
            mock.patch(
                "engine.orographic.scout.fetch_ai_multiplier",
                return_value=SentinelScore(
                    multiplier=1.0,
                    catalyst="earnings beat",
                    rationale="Short horizon upside catalyst.",
                    event_type="earnings",
                    event_polarity=0.7,
                    directional_relevance="call",
                    time_horizon="one_to_three_days",
                    direction_1d="up",
                    direction_3d="up",
                    direction_5d="neutral",
                    magnitude_bucket="medium",
                    decay_half_life="three_days",
                    spot_vs_iv_effect="spot",
                    call_relevance=0.8,
                    put_relevance=0.1,
                    no_trade_relevance=0.2,
                    confidence=0.75,
                    headlines=["Company beats estimates"],
                ),
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
        self.assertEqual(diagnostics["sentinel"]["direction_3d"], "up")
        self.assertEqual(diagnostics["sentinel"]["decay_half_life"], "three_days")
        self.assertAlmostEqual(diagnostics["sentinel"]["call_relevance"], 0.8, places=4)
        self.assertEqual(signal.sentinel_event["magnitude_bucket"], "medium")
        self.assertEqual(signal.sentinel_event["spot_vs_iv_effect"], "spot")
        self.assertIn("Sentinel horizon one_to_three_days", " ".join(signal.notes))

    def test_dataset_backed_event_features_flow_into_diagnostics_and_sentinel_context(self) -> None:
        event_store = pd.DataFrame(
            [
                {
                    "symbol": "TEST",
                    "date": pd.Timestamp("2026-04-21"),
                    "fnspid_news_volume_1d": 9.0,
                    "fnspid_catalyst_density": 0.8,
                    "edt_event_intensity": 0.6,
                    "dataset_tags": "fnspid,edt",
                }
            ]
        )
        sentinel_mock = mock.Mock(
            return_value=SentinelScore(multiplier=1.0, catalyst="none", rationale="")
        )
        frame = _frame()
        frame.index = pd.date_range("2026-01-22", periods=len(frame), freq="D")
        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SIDE_MODEL_MODE": "shadow"}),
            mock.patch("engine.orographic.scout._ml_scout_signal", return_value=(0.45, 0.725)),
            mock.patch(
                "engine.orographic.scout._ml_side_probabilities",
                return_value=(
                    {"call_edge": 0.62, "put_edge": 0.18, "no_trade": 0.20},
                    "trained_option_payoff_three_class",
                ),
            ),
            mock.patch("engine.orographic.scout.fetch_ai_multiplier", sentinel_mock),
        ):
            signal, diagnostics = build_signal(
                "TEST",
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                frame,
                0.0,
                event_feature_store=event_store,
                return_diagnostics=True,
            )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(diagnostics["event_dataset_features"]["dataset_tags"], "fnspid,edt")
        self.assertEqual(diagnostics["event_dataset_features"]["fnspid_news_volume_1d"], 9.0)
        self.assertEqual(diagnostics["sentinel"]["event_context"]["edt_event_intensity"], 0.6)
        self.assertIn("Dataset-backed event context active", " ".join(signal.notes))
        self.assertEqual(
            sentinel_mock.call_args.kwargs["event_context"]["fnspid_catalyst_density"],
            0.8,
        )


if __name__ == "__main__":
    unittest.main()
