from __future__ import annotations

from datetime import date
import unittest
from unittest.mock import patch

import pandas as pd

from engine.backtest.replay import forge_candidates_as_of, replay_week, select_expiry_from_chain
from engine.orographic.schemas import MarketRegime, ScoutSignal


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


def _history(rows: int = 90, *, end: str = "2026-04-06") -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=rows)
    close = pd.Series([100 + i * 0.15 for i in range(rows)], index=index, dtype=float)
    return pd.DataFrame(
        {
            "Close": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Volume": pd.Series([1_000_000 + i * 1000 for i in range(rows)], index=index, dtype=float),
        },
        index=index,
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

    def test_replay_week_applies_path_model_scoring(self) -> None:
        signal = _signal()
        candidate = forge_candidates_as_of(
            signal,
            date(2026, 4, 6),
            _ReplayProvider(
                pd.DataFrame(
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
                        }
                    ]
                )
            ),
        )[0]

        def _fake_path_score(candidates: list[object], regime: object, *, as_of: date | None = None) -> None:
            self.assertEqual(as_of, date(2026, 4, 6))
            self.assertEqual(len(candidates), 1)
            candidates[0].path_holding_quality_score = 0.77
            candidates[0].path_model_mode = "shadow"

        with (
            patch("engine.backtest.replay.infer_regime_as_of", return_value=MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY")),
            patch("engine.backtest.replay.build_signal_as_of", return_value=signal),
            patch("engine.backtest.replay.forge_candidates_as_of", return_value=[candidate]),
            patch("engine.orographic.payoff_model.score_candidates", return_value=None),
            patch("engine.orographic.path_model.score_candidates", side_effect=_fake_path_score),
        ):
            result = replay_week(
                date(2026, 4, 6),
                ["TEST"],
                {"TEST": pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2026-04-06"]))},
                pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2026-04-06"])),
                pd.DataFrame({"Close": [20.0]}, index=pd.to_datetime(["2026-04-06"])),
                _ReplayProvider(pd.DataFrame()),
            )

        self.assertEqual(len(result.candidates), 1)
        self.assertAlmostEqual(result.candidates[0].path_holding_quality_score or 0.0, 0.77, places=4)
        self.assertEqual(result.candidates[0].path_model_mode, "shadow")

    def test_replay_week_attaches_historical_sentinel_event_features(self) -> None:
        chain = pd.DataFrame(
            [
                {
                    "option_type": "C",
                    "expire_date": "2026-04-10",
                    "strike": 112.0,
                    "bid": 1.7,
                    "ask": 1.8,
                    "delta": 0.55,
                    "implied_volatility": 0.30,
                    "open_interest": 1200,
                    "trade_volume": 400,
                }
            ]
        )
        event_store = pd.DataFrame(
            [
                {
                    "symbol": "TEST",
                    "date": pd.Timestamp("2026-04-06"),
                    "fnspid_news_volume_1d": 4.0,
                    "fnspid_news_volume_3d": 7.0,
                    "fnspid_sentiment_mean": 0.6,
                    "fnspid_catalyst_density": 0.5,
                    "dataset_tags": "fnspid",
                }
            ]
        )
        hist = _history()
        with (
            patch("engine.backtest.replay._ml_scout_score", return_value=0.55),
            patch("engine.orographic.payoff_model.score_candidates", return_value=None),
            patch("engine.orographic.path_model.score_candidates", return_value=None),
        ):
            result = replay_week(
                date(2026, 4, 6),
                ["TEST"],
                {"TEST": hist},
                hist,
                _history(end="2026-04-06"),
                _ReplayProvider(chain),
                event_feature_store=event_store,
            )

        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.sentinel_status, "ai_success_event")
        self.assertGreater(candidate.sentinel_confidence or 0.0, 0.0)
        self.assertIn(candidate.sentinel_options_impact_label, {"spot_up_iv_down", "pre_event_premium_risk"})
        self.assertIn(candidate.sentinel_recommended_use, {"observe", "tie_breaker", "reduce_size", "veto_candidate"})
        self.assertEqual(candidate.sentinel_event["event_context"]["dataset_tags"], "fnspid")


if __name__ == "__main__":
    unittest.main()
