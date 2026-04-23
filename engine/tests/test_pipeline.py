from __future__ import annotations

from datetime import date
import json
import tempfile
import unittest
from unittest import mock

import pandas as pd

from engine.orographic.forge import select_signals_for_forge
from engine.orographic.pipeline import (
    append_side_aware_shadow_ledger,
    build_forge_rejection_waterfall_artifact,
    build_promotion_readiness,
    build_side_aware_shadow_ledger_entry,
    load_universe,
    write_forge_rejection_waterfall_artifacts,
)
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

    def test_build_forge_rejection_waterfall_artifact_summarizes_rejections(self) -> None:
        payload = {
            "generated_at_utc": "2026-04-15T15:07:00+00:00",
            "product": "Orographic",
            "scout_signals": [
                {"symbol": "PLTR", "direction": "call", "scout_score": 0.65, "spot": 136.11},
                {"symbol": "MCD", "direction": "call", "scout_score": 0.63, "spot": 302.17},
            ],
            "council": {
                "abstain": True,
                "live_board": [],
                "shadow_board": [
                    {
                        "symbol": "PLTR",
                        "option_type": "call",
                        "expiry": "2026-04-17",
                        "strike": 135.0,
                        "forge_score": 0.82,
                        "contract_cost": 216.0,
                        "is_spread": True,
                    }
                ],
                "summary": {
                    "live_count": 0,
                    "shadow_count": 1,
                    "notes": [
                        "Council abstained because no contract cleared the live board threshold.",
                        "Council is operating under a risk-on market regime.",
                    ],
                },
            },
            "diagnostics": {
                "pre_forge": {
                    "selected_symbols": ["PLTR", "MCD"],
                    "settings": {"target_count": 2},
                    "rejections": [
                        {"symbol": "AAA", "reason": "liquidity_gate"},
                        {"symbol": "BBB", "reason": "liquidity_gate"},
                        {"symbol": "CCC", "reason": "no_expiry"},
                    ],
                },
                "forge": {
                    "waterfall": {"signals_considered": 2, "final_candidates": 1},
                    "settings": {"min_open_interest": 150},
                    "per_symbol": [
                        {"symbol": "PLTR", "final_candidates": 1},
                        {"symbol": "MCD", "final_candidates": 0, "rejection_reason": "delta"},
                    ],
                },
            },
            "summary": {
                "universe_size": 100,
                "scout_signal_count": 8,
                "pre_forge_signal_count": 2,
                "forge_candidate_count": 1,
                "abstain": True,
            },
        }

        artifact = build_forge_rejection_waterfall_artifact(payload)

        self.assertEqual(artifact["artifact"], "forge_rejection_waterfall")
        self.assertEqual(artifact["trading_day"], "2026-04-15")
        self.assertTrue(artifact["summary"]["abstain"])
        self.assertEqual(artifact["summary"]["passed_symbol_count"], 1)
        self.assertAlmostEqual(artifact["summary"]["forge_symbol_pass_rate"], 0.5)
        self.assertEqual(
            artifact["pre_forge"]["rejection_counts"],
            [
                {"reason": "liquidity_gate", "count": 2},
                {"reason": "no_expiry", "count": 1},
            ],
        )
        self.assertEqual(
            artifact["forge"]["rejection_counts"],
            [{"reason": "delta", "count": 1}],
        )
        self.assertEqual(
            artifact["final_board"]["abstain_reasons"],
            ["Council abstained because no contract cleared the live board threshold."],
        )
        self.assertEqual(artifact["promotion_readiness"]["decision"], "keep_shadow")

    def test_build_promotion_readiness_tracks_shadow_models(self) -> None:
        payload = {
            "scout_signals": [
                {"symbol": "AAA", "direction": "call"},
                {"symbol": "BBB", "direction": "put"},
            ],
            "council": {
                "live_board": [
                    {
                        "symbol": "AAA",
                        "council_risk_flags": ["sector_watch"],
                    }
                ],
                "shadow_board": [
                    {
                        "symbol": "BBB",
                        "council_risk_flags": [],
                    }
                ],
                "summary": {
                    "candidate_count": 4,
                    "avg_pairwise_correlation": 0.21,
                    "live_sector_counts": {"technology": 1},
                },
            },
            "diagnostics": {
                "scout": {
                    "side_aware_scores": [
                        {
                            "symbol": "AAA",
                            "model_mode": "trained_three_class",
                            "call_edge": 0.2,
                            "put_edge": 0.7,
                            "no_trade": 0.1,
                        },
                        {
                            "symbol": "BBB",
                            "model_mode": "trained_three_class",
                            "call_edge": 0.1,
                            "put_edge": 0.8,
                            "no_trade": 0.1,
                        },
                    ],
                    "sentinel_scores": [
                        {
                            "symbol": "AAA",
                            "mode": "shadow",
                            "shadow_multiplier": 0.8,
                            "event_type": "regulatory",
                        }
                    ],
                },
                "forge": {
                    "learned_ranker": {
                        "mode_counts": {"active": 4},
                        "scored_candidates": 4,
                        "avg_learned_rank_score": 0.61,
                    }
                },
            },
        }

        readiness = build_promotion_readiness(payload)
        models = {row["name"]: row for row in readiness["models"]}

        self.assertEqual(readiness["decision"], "keep_shadow")
        self.assertEqual(models["Side-Aware Scout"]["disagreements"], 1)
        self.assertEqual(models["Side-Aware Scout"]["side_mix"]["put"], 2)
        self.assertEqual(models["Sentinel Event Extractor"]["non_neutral_events"], 1)
        self.assertEqual(models["Payoff Ranker"]["mode"], "active")
        self.assertEqual(models["Council Risk Intelligence"]["live_risk_flags"], 1)
        self.assertEqual(len(readiness["gates"]), 6)

    def test_write_forge_rejection_waterfall_artifacts_creates_latest_and_dated_files(self) -> None:
        payload = {
            "generated_at_utc": "2026-04-15T15:07:00+00:00",
            "diagnostics": {"forge": {"waterfall": {}, "per_symbol": []}, "pre_forge": {"rejections": []}},
            "council": {"abstain": False, "summary": {"live_count": 1, "shadow_count": 0, "notes": []}},
            "summary": {"abstain": False},
            "scout_signals": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_forge_rejection_waterfall_artifacts(f"{tmpdir}/latest_run.json", payload)

            self.assertEqual(len(paths), 2)
            self.assertTrue(paths[0].name.endswith("_latest.json"))
            self.assertEqual(paths[1].name, "forge_rejection_waterfall_2026-04-15.json")
            self.assertTrue(paths[0].exists())
            self.assertTrue(paths[1].exists())

            rendered = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(rendered["trading_day"], "2026-04-15")

    def test_side_aware_shadow_ledger_records_disagreements(self) -> None:
        payload = {
            "generated_at_utc": "2026-04-22T15:07:00+00:00",
            "regime": {"mode": "risk_on", "bias": 0.4, "source_symbol": "SPY"},
            "model_artifacts": {"scout_side_model": {"present": True, "sha256": "abc"}},
            "forge_candidates": [{"symbol": "AAA"}],
            "council": {
                "live_board": [{"symbol": "AAA"}],
                "shadow_board": [{"symbol": "BBB"}],
            },
            "diagnostics": {
                "scout": {
                    "side_aware_scores": [
                        {
                            "symbol": "AAA",
                            "model_mode": "trained_option_payoff_three_class",
                            "active_direction": "call",
                            "active_scout_score": 0.6,
                            "call_edge": 0.2,
                            "put_edge": 0.7,
                            "no_trade": 0.1,
                        },
                        {
                            "symbol": "BBB",
                            "model_mode": "trained_option_payoff_three_class",
                            "active_direction": "put",
                            "active_scout_score": -0.5,
                            "call_edge": 0.1,
                            "put_edge": 0.2,
                            "no_trade": 0.7,
                        },
                    ]
                }
            },
        }

        entry = build_side_aware_shadow_ledger_entry(payload)

        self.assertEqual(entry["summary"]["observations"], 2)
        self.assertEqual(entry["summary"]["disagreements"], 2)
        self.assertEqual(entry["summary"]["directional_disagreements"], 1)
        self.assertEqual(entry["summary"]["no_trade_disagreements"], 1)
        self.assertTrue(entry["disagreements"][0]["was_live_symbol"])

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = append_side_aware_shadow_ledger(
                f"{tmpdir}/ledger.json",
                payload,
                max_entries=1,
            )
            rendered = json.loads(ledger_path.read_text(encoding="utf-8"))

        self.assertEqual(rendered["artifact"], "side_aware_scout_shadow_ledger")
        self.assertEqual(rendered["aggregate"]["runs"], 1)
        self.assertEqual(rendered["aggregate"]["disagreements"], 2)


if __name__ == "__main__":
    unittest.main()
