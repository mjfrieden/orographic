from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

from engine.orographic.forge import _apply_pre_council_gate, _dedupe_candidates, rank_contracts_with_diagnostics, select_signals_for_forge
from engine.orographic.market_shock import MarketShockRegime
from engine.orographic.pipeline import (
    _build_manual_trade_pick,
    _configured_model_stack,
    _fail_closed_model_modes,
    _load_prior_live_board_symbols,
    _model_artifact_status,
    _model_mode_status,
    _partition_shadow_veto_signals,
    _promotion_gate_decision,
    append_board_recommendation_history,
    append_moonshot_prospective_ledger,
    append_prospective_pick_ledger,
    append_research_run_ledger,
    append_side_aware_shadow_ledger,
    build_board_recommendation_history_entry,
    build_live_shadow_attribution_artifact,
    build_moonshot_prospective_ledger_entry,
    build_prospective_pick_ledger_entry,
    build_research_run_ledger_entry,
    build_forge_rejection_waterfall_artifact,
    build_promotion_readiness,
    build_side_aware_shadow_ledger_entry,
    load_universe,
    PipelineConfig,
    run_scan,
    write_forge_rejection_waterfall_artifacts,
    write_live_shadow_attribution_artifacts,
)
from engine.orographic.schemas import ContractCandidate, MarketRegime, ScoutSignal


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


class UnifiedModelStackTests(unittest.TestCase):
    def test_pipeline_defaults_to_one_unified_lane(self) -> None:
        config = PipelineConfig(universe=["AAPL"])
        self.assertEqual(config.model_stack, "unified_rnd")
        self.assertEqual(config.shadow_size, 0)
        self.assertEqual(config.counterfactual_observation_size, 0)
        self.assertFalse(config.preserve_shadow_veto_live_policy)

    def test_current_gated_context_overrides_and_restores_external_stack(self) -> None:
        with mock.patch.dict(os.environ, {"OROGRAPHIC_MODEL_STACK": "unified_rnd"}, clear=False):
            with _configured_model_stack("current_gated", "not_ready"):
                self.assertEqual(os.environ["OROGRAPHIC_MODEL_STACK"], "current_gated")
                self.assertEqual(os.environ["OROGRAPHIC_SIDE_MODEL_MODE"], "shadow")
            self.assertEqual(os.environ["OROGRAPHIC_MODEL_STACK"], "unified_rnd")


class PipelineTests(unittest.TestCase):
    def test_manual_trade_pick_uses_only_council_approved_contract(self) -> None:
        lower = {"symbol": "AAA", "contract_symbol": "AAA260821C00100000", "forge_score": 0.61}
        best = {"symbol": "BBB", "contract_symbol": "BBB260821P00200000", "forge_score": 0.79}

        pick = _build_manual_trade_pick(
            council_payload={"live_board": [lower, best], "shadow_board": []},
            forge_candidates=[lower, best],
            counterfactual_candidates=[],
        )

        self.assertEqual(pick["candidate"]["contract_symbol"], best["contract_symbol"])
        self.assertEqual(pick["model_recommendation"], "trade")
        self.assertTrue(pick["manual_entry_available"])
        self.assertTrue(pick["automatic_routing_eligible"])
        self.assertFalse(pick["requires_manual_override_confirmation"])

    def test_manual_trade_pick_rejects_every_non_council_candidate(self) -> None:
        research = {"symbol": "CCC", "contract_symbol": "CCC260821C00300000", "forge_score": 0.55}
        pick = _build_manual_trade_pick(
            council_payload={"live_board": [], "shadow_board": []},
            forge_candidates=[],
            counterfactual_candidates=[research],
        )
        self.assertIsNone(pick)

    def test_payoff_volatility_challenger_is_optional_and_observation_only(self) -> None:
        artifacts = _model_artifact_status()

        self.assertTrue(artifacts["payoff_volatility_shadow"]["present"])
        self.assertFalse(artifacts["payoff_volatility_shadow"]["required"])
        self.assertFalse(artifacts["payoff_volatility_shadow_card"]["required"])
        self.assertEqual(
            _model_mode_status(artifacts)["payoff_volatility_challenger"],
            "observation_only",
        )

    def test_canonical_promotion_gate_requires_exact_pass_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            comparison_path = Path(tmpdir) / "comparison.json"
            for decision, expected in (
                ("pass", "pass"),
                ("PASS", "pass"),
                ("passed", "passed"),
                ("approved", "approved"),
                ("hold", "hold"),
            ):
                comparison_path.write_text(json.dumps({"decision": decision}), encoding="utf-8")
                self.assertEqual(_promotion_gate_decision(comparison_path), expected)

    def test_nonpassing_promotion_gate_forces_learned_overlays_to_shadow(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "OROGRAPHIC_SIDE_MODEL_MODE": "active",
                "OROGRAPHIC_SENTINEL_MODE": "active",
                "OROGRAPHIC_PAYOFF_MODEL_MODE": "active",
            },
            clear=False,
        ):
            with _fail_closed_model_modes("not_ready"):
                self.assertEqual(os.environ["OROGRAPHIC_SIDE_MODEL_MODE"], "shadow")
                self.assertEqual(os.environ["OROGRAPHIC_SENTINEL_MODE"], "shadow")
                self.assertEqual(os.environ["OROGRAPHIC_PAYOFF_MODEL_MODE"], "shadow")
            self.assertEqual(os.environ["OROGRAPHIC_SIDE_MODEL_MODE"], "active")
            self.assertEqual(os.environ["OROGRAPHIC_SENTINEL_MODE"], "active")
            self.assertEqual(os.environ["OROGRAPHIC_PAYOFF_MODEL_MODE"], "active")

    def test_run_scan_restores_requested_active_modes_after_fail_closed_scan(self) -> None:
        comparison = {"artifact": "promotion_shadow_active_comparison", "decision": "not_ready", "windows": []}
        council_payload = {
            "live_board": [],
            "shadow_board": [],
            "abstain": True,
            "summary": {"candidate_count": 0, "live_count": 0, "shadow_count": 0, "notes": []},
        }

        def scan_in_shadow(_universe, **_kwargs):
            self.assertEqual(os.environ["OROGRAPHIC_SIDE_MODEL_MODE"], "shadow")
            self.assertEqual(os.environ["OROGRAPHIC_SENTINEL_MODE"], "shadow")
            self.assertEqual(os.environ["OROGRAPHIC_PAYOFF_MODEL_MODE"], "shadow")
            return (
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                [],
                {"pre_veto_direction_counts": {}, "final_direction_counts": {}, "counter_regime_survivors": 0},
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            comparison_path = Path(tmpdir) / "comparison.json"
            comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "OROGRAPHIC_SIDE_MODEL_MODE": "active",
                        "OROGRAPHIC_SENTINEL_MODE": "active",
                        "OROGRAPHIC_PAYOFF_MODEL_MODE": "active",
                    },
                    clear=False,
                ),
                mock.patch("engine.orographic.pipeline.scan_symbols_with_diagnostics", side_effect=scan_in_shadow),
                mock.patch("engine.orographic.pipeline.select_signals_for_forge", return_value=([], {})),
                mock.patch("engine.orographic.pipeline.rank_contracts_with_diagnostics", return_value=([], {"waterfall": {}, "learned_ranker": {}})),
                mock.patch(
                    "engine.orographic.pipeline.select_board",
                    return_value=mock.Mock(to_dict=lambda: council_payload, live_board=[], abstain=True),
                ),
            ):
                payload = run_scan(PipelineConfig(
                    universe=["AAA"],
                    model_stack="current_gated",
                    board_history_path=None,
                    promotion_comparison_path=comparison_path,
                ))
                self.assertEqual(os.environ["OROGRAPHIC_SIDE_MODEL_MODE"], "active")
                self.assertEqual(os.environ["OROGRAPHIC_SENTINEL_MODE"], "active")
                self.assertEqual(os.environ["OROGRAPHIC_PAYOFF_MODEL_MODE"], "active")

        self.assertEqual(payload["model_modes"]["side_aware_scout"], "shadow")
        self.assertEqual(payload["model_modes"]["sentinel"], "shadow")
        self.assertEqual(payload["model_modes"]["payoff_ranker"], "shadow")
        self.assertEqual(payload["promotion_readiness"]["decision"], "promotion_hold")

    def setUp(self) -> None:
        self.market_shock = MarketShockRegime(
            label="normal_crosscurrents",
            severity=0.0,
            stance="allow",
            global_abstain=False,
        )
        self.market_shock_patcher = mock.patch(
            "engine.orographic.pipeline.classify_current_market_shock",
            return_value=self.market_shock,
        )
        self.market_shock_mock = self.market_shock_patcher.start()
        self.addCleanup(self.market_shock_patcher.stop)

    def test_load_prior_live_board_symbols_reads_latest_entry(self) -> None:
        payload = {
            "entries": [
                {"live_board": [{"symbol": "OLD"}]},
                {"live_board": [{"symbol": "AAPL"}, {"symbol": "MSFT"}]},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/board_history.json"
            Path(path).write_text(json.dumps(payload), encoding="utf-8")
            symbols = _load_prior_live_board_symbols(path)

        self.assertEqual(symbols, ["AAPL", "MSFT"])

    def test_default_universe_expands_to_100_symbols(self) -> None:
        universe = load_universe(None)
        self.assertEqual(len(universe), 100)
        self.assertEqual(universe[:4], ["SPY", "QQQ", "IWM", "DIA"])

    def test_shadow_veto_signals_are_partitioned_into_research_only_cohort(self) -> None:
        live = _signal("AAA")
        research = _signal("BBB")
        policy_hold = _signal("CCC")
        diagnostics = {
            "side_aware_scores": [
                {"symbol": "AAA", "shadow_guard_would_veto": False},
                {"symbol": "BBB", "shadow_guard_would_veto": True},
                {"symbol": "CCC", "policy_held_candidate": True},
            ]
        }

        live_rows, research_rows = _partition_shadow_veto_signals([live, research, policy_hold], diagnostics)

        self.assertEqual([row.symbol for row in live_rows], ["AAA"])
        self.assertEqual([row.symbol for row in research_rows], ["BBB", "CCC"])

        live_rows, research_rows = _partition_shadow_veto_signals(
            [live, research, policy_hold],
            diagnostics,
            include_shadow_veto=False,
        )
        self.assertEqual([row.symbol for row in live_rows], ["AAA", "BBB"])
        self.assertEqual([row.symbol for row in research_rows], ["CCC"])

    def test_deprecated_alternate_lane_settings_cannot_split_the_candidate_surface(self) -> None:
        research_signal = _signal("BBB")
        candidate_payload = {
            "symbol": "BBB",
            "contract_symbol": "BBB260821C00100000",
            "option_type": "call",
            "expiry": "2026-08-21",
            "strike": 100.0,
            "bid": 1.0,
            "ask": 1.2,
            "last": 1.1,
            "contract_cost": 120.0,
            "spread_pct": 0.1667,
            "open_interest": 400,
            "volume": 100,
            "forge_score": 0.72,
            "friction_gate_passed": True,
            "notes": [],
        }
        candidate = mock.Mock(
            forge_score=0.72,
            to_dict=lambda: candidate_payload,
        )
        council_payload = {
            "live_board": [],
            "shadow_board": [],
            "abstain": True,
            "summary": {"candidate_count": 1, "live_count": 0, "shadow_count": 0, "notes": []},
        }

        def rank(signals, *_args, **_kwargs):
            return ([candidate], {"waterfall": {}, "learned_ranker": {}}) if signals else (
                [],
                {"waterfall": {}, "learned_ranker": {}},
            )

        def council(candidates, *_args, **_kwargs):
            self.assertEqual(candidates, [candidate])
            return mock.Mock(to_dict=lambda: council_payload, live_board=[], abstain=True)

        with (
            mock.patch(
                "engine.orographic.pipeline.scan_symbols_with_diagnostics",
                return_value=(
                    MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                    [research_signal],
                    {
                        "pre_veto_direction_counts": {"call": 1, "put": 0},
                        "final_direction_counts": {"call": 1, "put": 0},
                        "counter_regime_survivors": 0,
                        "shadow_side_veto_observations": 1,
                        "shadow_side_veto_rejections": 0,
                        "side_aware_scores": [
                            {"symbol": "BBB", "shadow_guard_would_veto": True}
                        ],
                    },
                ),
            ),
            mock.patch(
                "engine.orographic.pipeline.select_signals_for_forge",
                side_effect=lambda signals, **_kwargs: (list(signals), {}),
            ),
            mock.patch("engine.orographic.pipeline.rank_contracts_with_diagnostics", side_effect=rank),
            mock.patch("engine.orographic.pipeline.select_board", side_effect=council),
            mock.patch(
                "engine.orographic.pipeline.select_moonshot_lane",
                return_value={"picks": [], "shadow": [], "summary": {"pick_count": 0, "eligible_count": 0}},
            ),
        ):
            payload = run_scan(
                PipelineConfig(
                    universe=["BBB"],
                    counterfactual_observation_size=1,
                    model_stack="current_gated",
                    board_history_path=None,
                )
            )

        lane = payload["counterfactual_observation_lane"]
        self.assertFalse(lane["council_eligible"])
        self.assertFalse(lane["tradier_routing_eligible"])
        self.assertEqual(lane["mode"], "deprecated_empty_schema_compatibility")
        self.assertEqual(lane["candidates"], [])
        self.assertEqual(payload["forge_candidates"], [candidate_payload])
        self.assertEqual(payload["council"]["live_board"], [])
        ledger_entry = build_prospective_pick_ledger_entry(payload)
        self.assertEqual(ledger_entry["summary"]["counterfactual_observation"], 0)

    def test_run_scan_defaults_to_recovered_dte_window(self) -> None:
        signal = _signal("AAA")
        council_payload = {
            "live_board": [],
            "shadow_board": [],
            "abstain": True,
            "summary": {
                "candidate_count": 0,
                "live_count": 0,
                "shadow_count": 0,
                "notes": [],
            },
        }

        with (
            mock.patch(
                "engine.orographic.pipeline.scan_symbols_with_diagnostics",
                return_value=(
                    MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                    [signal],
                    {"pre_veto_direction_counts": {"call": 1}, "final_direction_counts": {"call": 1}, "counter_regime_survivors": 0},
                ),
            ),
            mock.patch(
                "engine.orographic.pipeline.select_signals_for_forge",
                return_value=([signal], {}),
            ) as select_signals_mock,
            mock.patch(
                "engine.orographic.pipeline.rank_contracts_with_diagnostics",
                return_value=([], {"waterfall": {}, "learned_ranker": {}}),
            ) as rank_contracts_mock,
            mock.patch(
                "engine.orographic.pipeline.select_board",
                return_value=mock.Mock(to_dict=lambda: council_payload, live_board=[], abstain=True),
            ) as select_board_mock,
        ):
            payload = run_scan(PipelineConfig(universe=["AAA"], board_history_path=None))

        self.assertEqual(payload["scan_settings"]["minimum_days_to_expiry"], 7)
        self.assertEqual(payload["scan_settings"]["maximum_days_to_expiry"], 14)
        self.assertEqual(payload["scan_settings"]["forge_intake"], 12)
        self.assertEqual(payload["scan_settings"]["live_size"], 1)
        self.assertEqual(payload["scan_settings"]["minimum_live_score"], 0.86)
        self.assertEqual(payload["scan_settings"]["minimum_put_live_score"], 0.84)
        self.assertEqual(payload["scan_settings"]["max_live_extrinsic_ratio"], 0.90)
        self.assertEqual(payload["scan_settings"]["moonshot_size"], 1)
        self.assertEqual(payload["scan_settings"]["moonshot_threshold"], 0.68)
        self.assertEqual(payload["scan_settings"]["moonshot_max_cost_basis"], 225.0)
        self.assertEqual(payload["scan_settings"]["moonshot_lane_role"], "visible_experimental_side_pick")
        self.assertEqual(payload["scan_settings"]["moonshot_primary_ensemble_effect"], "none")
        self.assertEqual(payload["scan_settings"]["moonshot_outcome_tracking"], "moonshot_prospective_ledger")
        self.assertEqual(payload["moonshot_lane"]["summary"]["pick_count"], 0)
        self.assertEqual(select_signals_mock.call_args.kwargs["target_count"], 12)
        self.assertEqual(select_signals_mock.call_args.kwargs["minimum_days_to_expiry"], 7)
        self.assertEqual(select_signals_mock.call_args.kwargs["maximum_days_to_expiry"], 14)
        self.assertEqual(rank_contracts_mock.call_args.kwargs["minimum_days_to_expiry"], 7)
        self.assertEqual(rank_contracts_mock.call_args.kwargs["maximum_days_to_expiry"], 14)
        self.assertFalse(rank_contracts_mock.call_args.kwargs["enforce_pre_council_friction_gate"])
        self.assertEqual(select_board_mock.call_args.kwargs["minimum_live_score"], 0.86)
        self.assertEqual(select_board_mock.call_args.kwargs["minimum_put_live_score"], 0.84)
        self.assertEqual(select_board_mock.call_args.kwargs["max_live_extrinsic_ratio"], 0.90)
        self.assertEqual(payload["scan_settings"]["market_shock_control_mode"], "active")
        self.assertEqual(payload["market_shock"]["label"], "normal_crosscurrents")
        self.assertEqual(payload["diagnostics"]["market_shock"]["mode"], "active")
        self.assertEqual(select_board_mock.call_args.kwargs["market_shock"], self.market_shock)

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

    def test_pre_forge_uses_best_tradable_expiry_within_window(self) -> None:
        signal = _signal("AAA")
        liquid_chain = _chain(bid=1.0, ask=1.08, open_interest=400, volume=120)
        illiquid_chain = _chain(bid=0.05, ask=0.50, open_interest=10, volume=5)

        def fake_option_chain(symbol: str, expiry: str) -> tuple[pd.DataFrame, pd.DataFrame]:
            frame = illiquid_chain if expiry == "2026-04-17" else liquid_chain
            return frame.copy(), pd.DataFrame()

        with (
            mock.patch("engine.orographic.forge.option_expiries", return_value=["2026-04-17", "2026-04-24"]),
            mock.patch("engine.orographic.forge.option_chain", side_effect=fake_option_chain),
        ):
            selected, diagnostics = select_signals_for_forge(
                [signal],
                target_count=1,
                minimum_days_to_expiry=7,
                maximum_days_to_expiry=14,
                today=date(2026, 4, 10),
            )

        self.assertEqual([row.symbol for row in selected], ["AAA"])
        self.assertEqual(diagnostics["selected_expiries"], [{"symbol": "AAA", "expiry": "2026-04-24"}])

    def test_rank_contracts_uses_best_expiry_with_candidates_in_window(self) -> None:
        signal = _signal("AAA")
        illiquid_chain = pd.DataFrame(
            [
                {
                    "contractSymbol": "AAA260417C00100000",
                    "bid": 0.05,
                    "ask": 0.50,
                    "lastPrice": 0.25,
                    "strike": 100.0,
                    "openInterest": 10,
                    "volume": 5,
                    "impliedVolatility": 0.25,
                }
            ]
        )
        liquid_chain = pd.DataFrame(
            [
                {
                    "contractSymbol": "AAA260424C00100000",
                    "bid": 1.0,
                    "ask": 1.08,
                    "lastPrice": 1.04,
                    "strike": 100.0,
                    "openInterest": 400,
                    "volume": 120,
                    "impliedVolatility": 0.25,
                }
            ]
        )

        def fake_option_chain(symbol: str, expiry: str) -> tuple[pd.DataFrame, pd.DataFrame]:
            frame = illiquid_chain if expiry == "2026-04-17" else liquid_chain
            return frame.copy(), pd.DataFrame()

        with (
            mock.patch("engine.orographic.forge.option_expiries", return_value=["2026-04-17", "2026-04-24"]),
            mock.patch("engine.orographic.forge.option_chain", side_effect=fake_option_chain),
            mock.patch("engine.orographic.forge.fetch_risk_free_rate", return_value=0.04),
            mock.patch("engine.orographic.forge.compute_iv_rank", return_value=0.35),
            mock.patch(
                "engine.orographic.payoff_model.score_candidates",
                side_effect=lambda candidates, regime, as_of=None, prior_live_board_symbols=None, turnover_switch_penalty=0.03: None,
            ),
            mock.patch("engine.orographic.forge.date") as date_mock,
        ):
            date_mock.today.return_value = date(2026, 4, 10)
            date_mock.fromisoformat.side_effect = date.fromisoformat
            candidates, diagnostics = rank_contracts_with_diagnostics(
                [signal],
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                minimum_days_to_expiry=7,
                maximum_days_to_expiry=14,
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].expiry, "2026-04-24")
        self.assertEqual(diagnostics["per_symbol"][0]["expiry"], "2026-04-24")

    def test_forge_logs_sentinel_horizon_mismatch_in_shadow_diagnostics(self) -> None:
        signal = _signal("AAA")
        signal.sentinel_event = {
            "event_type": "analyst",
            "time_horizon": "one_to_three_days",
            "decay_half_life": "one_day",
            "confidence": 0.8,
            "direction_3d": "up",
        }
        liquid_chain = pd.DataFrame(
            [
                {
                    "contractSymbol": "AAA260417C00100000",
                    "bid": 1.0,
                    "ask": 1.08,
                    "lastPrice": 1.04,
                    "strike": 100.0,
                    "openInterest": 400,
                    "volume": 120,
                    "impliedVolatility": 0.25,
                }
            ]
        )

        with (
            mock.patch("engine.orographic.forge.option_expiries", return_value=["2026-04-17"]),
            mock.patch("engine.orographic.forge.option_chain", return_value=(liquid_chain.copy(), pd.DataFrame())),
            mock.patch("engine.orographic.forge.fetch_risk_free_rate", return_value=0.04),
            mock.patch("engine.orographic.forge.compute_iv_rank", return_value=0.35),
            mock.patch(
                "engine.orographic.payoff_model.score_candidates",
                side_effect=lambda candidates, regime, as_of=None, prior_live_board_symbols=None, turnover_switch_penalty=0.03: None,
            ),
            mock.patch("engine.orographic.forge.date") as date_mock,
        ):
            date_mock.today.return_value = date(2026, 4, 10)
            date_mock.fromisoformat.side_effect = date.fromisoformat
            candidates, diagnostics = rank_contracts_with_diagnostics(
                [signal],
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                minimum_days_to_expiry=2,
                maximum_days_to_expiry=8,
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].sentinel_holding_window_label, "mismatch")
        self.assertEqual(diagnostics["per_symbol"][0]["sentinel_holding_window_label"], "mismatch")
        self.assertIn("Sentinel shadow mismatch", " ".join(candidates[0].notes))

    def test_forge_scores_complete_chain_and_attaches_surface_telemetry(self) -> None:
        signal = _signal("AAA")
        calls = pd.DataFrame([
            {"contractSymbol": f"AAA260417C{strike:08d}", "bid": bid, "ask": ask, "lastPrice": (bid + ask) / 2,
             "strike": strike / 1000, "openInterest": 600, "volume": 150, "impliedVolatility": iv,
             "lastTradeDate": "2026-04-10T14:00:00Z"}
            for strike, bid, ask, iv in [
                (98000, 1.30, 1.38, 0.29),
                (100000, 1.00, 1.08, 0.25),
                (102000, 0.72, 0.79, 0.24),
            ]
        ])
        puts = pd.DataFrame([
            {"contractSymbol": f"AAA260417P{strike:08d}", "bid": 0.8, "ask": 0.88, "lastPrice": 0.84,
             "strike": strike / 1000, "openInterest": 600, "volume": 150, "impliedVolatility": iv}
            for strike, iv in [(94000, 0.34), (97000, 0.29), (100000, 0.26)]
        ])

        with (
            mock.patch("engine.orographic.forge.option_expiries", return_value=["2026-04-17"]),
            mock.patch("engine.orographic.forge.option_chain", return_value=(calls, puts)),
            mock.patch("engine.orographic.forge.fetch_risk_free_rate", return_value=0.04),
            mock.patch("engine.orographic.forge.compute_iv_rank", return_value=0.35),
            mock.patch(
                "engine.orographic.payoff_model.score_candidates",
                side_effect=lambda candidates, regime, as_of=None, prior_live_board_symbols=None, turnover_switch_penalty=0.03: None,
            ),
            mock.patch("engine.orographic.forge.date") as date_mock,
        ):
            date_mock.today.return_value = date(2026, 4, 10)
            date_mock.fromisoformat.side_effect = date.fromisoformat
            candidates, diagnostics = rank_contracts_with_diagnostics(
                [signal],
                MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                minimum_days_to_expiry=7,
                maximum_days_to_expiry=7,
            )

        self.assertEqual(diagnostics["per_symbol"][0]["final_candidates"], 3)
        # Council-facing deduplication keeps one structure, but Forge must
        # score all three eligible contracts before that policy is applied.
        self.assertEqual(len(candidates), 1)
        self.assertTrue(all(candidate.surface_atm_iv is not None for candidate in candidates))
        self.assertTrue(all(candidate.surface_observation_count == 5 for candidate in candidates))
        self.assertTrue(all(candidate.quote_mid is not None for candidate in candidates))
        self.assertTrue(all(candidate.quote_spread_dollars is not None for candidate in candidates))
        self.assertTrue(all(candidate.chain_snapshot_at_utc for candidate in candidates))

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
        self.assertEqual(artifact["promotion_readiness"]["decision"], "promotion_hold")

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

        self.assertEqual(readiness["decision"], "promotion_hold")
        self.assertEqual(models["Side-Aware Scout"]["disagreements"], 1)
        self.assertEqual(models["Side-Aware Scout"]["side_mix"]["put"], 2)
        self.assertEqual(models["Sentinel Event Extractor"]["non_neutral_events"], 1)
        self.assertEqual(models["Payoff Ranker"]["mode"], "shadow")
        self.assertEqual(models["Payoff Ranker"]["configured_mode"], "active")
        self.assertEqual(models["Path Quality Model"]["mode"], "shadow")
        self.assertEqual(models["Council Risk Intelligence"]["live_risk_flags"], 1)
        self.assertEqual(len(readiness["gates"]), 6)

    def test_build_promotion_readiness_uses_tracked_profitability_evidence(self) -> None:
        prospective = {
            "artifact": "prospective_pick_ledger",
            "aggregate": {"runs": 1},
            "outcome_summary": {
                "complete": 2,
                "partial": 0,
                "pending": 1,
                "capture_policy_v2_picks": 3,
                "capture_windows_valid": 8,
                "capture_windows_quote_missing": 1,
                "capture_windows_stale_quote": 0,
                "capture_windows_missed": 1,
            },
            "entries": [
                {
                    "regime": {"mode": "risk_on"},
                    "picks": [
                        {
                            "lane": "live",
                            "outcomes": {
                                "status": "complete",
                                "quote_verification": {"outcome_quotes_captured": True},
                                "fixed_exit_marks": {"friday_close": {"pnl_pct_from_emission": 0.10}},
                                "realized_if_traded": {"pnl": 25.0, "pnl_pct": 0.10},
                            },
                        },
                        {
                            "lane": "shadow",
                            "outcomes": {
                                "status": "complete",
                                "quote_verification": {"outcome_quotes_captured": True},
                                "fixed_exit_marks": {"friday_close": {"pnl_pct_from_emission": 0.15}},
                                "realized_if_traded": {"pnl": 30.0, "pnl_pct": 0.15},
                            },
                        },
                        {
                            "lane": "council_holdout",
                            "outcomes": {
                                "status": "pending",
                                "quote_verification": {"outcome_quotes_captured": True},
                                "fixed_exit_marks": {"friday_close": {"pnl_pct_from_emission": 0.25}},
                                "realized_if_traded": {},
                            },
                        },
                    ],
                }
            ],
        }
        moonshot = {
            "artifact": "moonshot_prospective_ledger",
            "aggregate": {"runs": 1},
            "outcome_summary": {"complete": 0, "partial": 0, "pending": 0},
            "entries": [],
        }
        shadow = {
            "artifact": "side_aware_scout_shadow_ledger",
            "aggregate": {"runs": 35, "disagreements": 42, "directional_disagreements": 18, "no_trade_disagreements": 24},
            "entries": [],
        }
        research = {"artifact": "research_run_ledger", "aggregate": {"runs": 12, "abstain_runs": 5}}
        board = {"artifact": "board_recommendation_history", "aggregate": {"runs": 12, "abstain_runs": 5}}
        comparison = {
            "artifact": "promotion_shadow_active_comparison",
            "decision": "not_ready",
            "windows": [
                {"window": "3_month", "status": "insufficient_data", "coverage_complete": False, "checks": {}},
                {"window": "6_month", "status": "insufficient_data", "coverage_complete": False, "checks": {}},
                {"window": "12_month", "status": "insufficient_data", "coverage_complete": False, "checks": {}},
            ],
        }
        challenger_evidence = {
            "artifact": "payoff_challenger_prospective_evidence",
            "decision": "collecting_evidence",
            "coverage": {
                "scored_recommendations": 18,
                "resolved_recommendations": 12,
                "decision_disagreements": 7,
            },
            "rank_replay": {"eligible_complete_runs": 4},
            "readiness": {"next_action": "Capture strict Friday-close executable labels."},
        }
        veto_evidence = {
            "artifact": "counterfactual_scout_veto_evidence",
            "decision": "collecting_evidence",
            "coverage": {
                "independent_recommendations": 44,
                "resolved_current_rule_vetoes": 9,
                "independent_veto_trading_days": 6,
            },
            "current_rule": {"veto_benefit": {"mean_avoided_net_return": 0.07}},
            "readiness": {"next_action": "Capture more strict Friday-close outcomes."},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            prospective_path = Path(tmpdir) / "prospective.json"
            moonshot_path = Path(tmpdir) / "moonshot.json"
            shadow_path = Path(tmpdir) / "shadow.json"
            research_path = Path(tmpdir) / "research.json"
            board_path = Path(tmpdir) / "board.json"
            comparison_path = Path(tmpdir) / "comparison.json"
            challenger_evidence_path = Path(tmpdir) / "challenger.json"
            veto_evidence_path = Path(tmpdir) / "veto.json"
            prospective_path.write_text(json.dumps(prospective), encoding="utf-8")
            moonshot_path.write_text(json.dumps(moonshot), encoding="utf-8")
            shadow_path.write_text(json.dumps(shadow), encoding="utf-8")
            research_path.write_text(json.dumps(research), encoding="utf-8")
            board_path.write_text(json.dumps(board), encoding="utf-8")
            comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
            challenger_evidence_path.write_text(json.dumps(challenger_evidence), encoding="utf-8")
            veto_evidence_path.write_text(json.dumps(veto_evidence), encoding="utf-8")

            payload = {
                "model_modes": {"payoff_ranker": "active"},
                "diagnostic_sources": {
                    "prospective_ledger": str(prospective_path),
                    "moonshot_ledger": str(moonshot_path),
                    "shadow_ledger": str(shadow_path),
                    "research_ledger": str(research_path),
                    "board_history": str(board_path),
                    "promotion_comparison": str(comparison_path),
                    "payoff_challenger_evidence": str(challenger_evidence_path),
                    "counterfactual_veto_evidence": str(veto_evidence_path),
                },
                "diagnostics": {
                    "scout": {"side_aware_scores": [], "sentinel_scores": []},
                    "forge": {"learned_ranker": {"mode_counts": {}}, "path_model": {"mode_counts": {}}},
                },
                "council": {"live_board": [], "shadow_board": [], "summary": {"candidate_count": 0}},
            }

            readiness = build_promotion_readiness(payload)

        models = {row["name"]: row for row in readiness["models"]}
        gates = {row["name"]: row for row in readiness["gates"]}

        self.assertEqual(models["Payoff Ranker"]["mode"], "shadow")
        self.assertEqual(models["Payoff Ranker"]["live_realized_pnl"], 25.0)
        self.assertEqual(models["Side-Aware Scout"]["veto_evidence_decision"], "collecting_evidence")
        self.assertEqual(models["Side-Aware Scout"]["veto_independent_recommendations"], 44)
        self.assertEqual(models["Side-Aware Scout"]["veto_resolved_current_rule"], 9)
        self.assertEqual(models["Side-Aware Scout"]["veto_independent_trading_days"], 6)
        self.assertEqual(models["Side-Aware Scout"]["veto_mean_avoided_net_return"], 0.07)
        self.assertEqual(models["Council Risk Intelligence"]["holdout_friday_close_avg_pnl_pct"], 0.25)
        self.assertEqual(readiness["profitability_summary"]["tracked_recommendations"], 3)
        self.assertEqual(readiness["profitability_summary"]["quote_coverage_pct"], 1.0)
        self.assertEqual(readiness["profitability_summary"]["payoff_challenger_decision"], "collecting_evidence")
        self.assertEqual(readiness["profitability_summary"]["payoff_challenger_scored"], 18)
        self.assertEqual(readiness["profitability_summary"]["payoff_challenger_resolved"], 12)
        self.assertEqual(readiness["profitability_summary"]["payoff_challenger_replay_runs"], 4)
        self.assertEqual(readiness["profitability_summary"]["payoff_challenger_disagreements"], 7)
        self.assertEqual(readiness["profitability_summary"]["counterfactual_veto_decision"], "collecting_evidence")
        self.assertEqual(readiness["profitability_summary"]["counterfactual_veto_resolved"], 9)
        self.assertEqual(
            readiness["profitability_summary"]["payoff_challenger_next_action"],
            "Capture strict Friday-close executable labels.",
        )
        self.assertEqual(readiness["profitability_summary"]["capture_policy_v2_picks"], 3)
        self.assertEqual(readiness["profitability_summary"]["capture_windows_valid"], 8)
        self.assertEqual(readiness["profitability_summary"]["capture_windows_missed"], 1)
        self.assertEqual(gates["Live Shadow Window"]["status"], "pass")
        self.assertIn("35 shadow runs", gates["Live Shadow Window"]["progress"])
        self.assertEqual(gates["Backtest Windows"]["status"], "collecting_evidence")
        self.assertEqual(readiness["promotion_gate_decision"], "not_ready")
        self.assertEqual(readiness["decision"], "promotion_hold")
        self.assertTrue(all(row["mode"] != "active" for row in readiness["models"][:3]))

    def test_build_promotion_readiness_allows_active_only_after_canonical_pass(self) -> None:
        comparison = {
            "artifact": "promotion_shadow_active_comparison",
            "decision": "pass",
            "windows": [
                {
                    "window": window,
                    "status": "pass",
                    "coverage_complete": True,
                    "checks": {
                        "calibration_non_worse": True,
                        "sharpe_non_worse": True,
                        "drawdown_non_worse": True,
                    },
                }
                for window in ("3_month", "6_month", "12_month")
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            comparison_path = Path(tmpdir) / "comparison.json"
            comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
            readiness = build_promotion_readiness({
                "model_modes": {
                    "side_aware_scout": "active",
                    "sentinel": "active",
                    "payoff_ranker": "active",
                },
                "diagnostic_sources": {"promotion_comparison": str(comparison_path)},
                "diagnostics": {
                    "scout": {"side_aware_scores": [], "sentinel_scores": []},
                    "forge": {"learned_ranker": {"mode_counts": {"active": 1}}},
                },
                "council": {"live_board": [], "shadow_board": [], "summary": {}},
            })

        models = {row["name"]: row for row in readiness["models"]}
        self.assertEqual(readiness["decision"], "production_active")
        self.assertEqual(readiness["promotion_gate_decision"], "pass")
        self.assertEqual(models["Side-Aware Scout"]["mode"], "active")
        self.assertEqual(models["Sentinel Event Extractor"]["mode"], "active")
        self.assertEqual(models["Payoff Ranker"]["mode"], "active")

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

    def test_build_live_shadow_attribution_artifact_tracks_layer_decisions(self) -> None:
        payload = {
            "generated_at_utc": "2026-04-25T15:07:00+00:00",
            "summary": {
                "scout_signal_count": 5,
                "pre_forge_signal_count": 3,
                "forge_candidate_count": 3,
                "scout_pre_veto_direction_counts": {"call": 4, "put": 1},
                "scout_final_direction_counts": {"call": 3, "put": 1},
                "scout_counter_regime_survivors": 1,
            },
            "forge_candidates": [
                {
                    "symbol": "SLV",
                    "contract_symbol": "SLV1",
                    "option_type": "call",
                    "expiry": "2026-04-27",
                    "strike": 69.0,
                    "forge_score": 0.74,
                    "risk_adjusted_score": 0.79,
                    "final_candidate_score": 0.77,
                    "prob_fill_quality_ok": 0.81,
                    "prob_no_trade": 0.18,
                    "scout_no_trade_prob": 0.12,
                    "sentinel_confidence": 0.6,
                    "sentinel_no_trade_relevance": 0.1,
                    "expected_edge_after_friction_pct": 0.18,
                    "contract_cost": 145.0,
                    "council_risk_flags": [],
                    "notes": [],
                },
                {
                    "symbol": "GLD",
                    "contract_symbol": "GLD1",
                    "option_type": "call",
                    "expiry": "2026-04-27",
                    "strike": 435.0,
                    "forge_score": 0.69,
                    "expected_edge_after_friction_pct": 0.11,
                    "contract_cost": 182.0,
                    "council_risk_flags": ["high_extrinsic"],
                    "notes": ["Shadow only"],
                },
                {
                    "symbol": "SPY",
                    "contract_symbol": "SPY1",
                    "option_type": "call",
                    "expiry": "2026-04-27",
                    "strike": 717.0,
                    "forge_score": 0.61,
                    "expected_edge_after_friction_pct": 0.09,
                    "contract_cost": 202.0,
                    "council_risk_flags": ["wide_spread"],
                    "notes": ["Risk-adjusted demotion"],
                },
            ],
            "council": {
                "abstain": False,
                "live_board": [
                    {
                        "symbol": "SLV",
                        "contract_symbol": "SLV1",
                        "option_type": "call",
                        "expiry": "2026-04-27",
                        "strike": 69.0,
                        "forge_score": 0.74,
                        "risk_adjusted_score": 0.79,
                        "final_candidate_score": 0.77,
                        "prob_fill_quality_ok": 0.81,
                        "prob_no_trade": 0.18,
                        "scout_no_trade_prob": 0.12,
                        "sentinel_confidence": 0.6,
                        "sentinel_no_trade_relevance": 0.1,
                        "expected_edge_after_friction_pct": 0.18,
                        "contract_cost": 145.0,
                        "council_risk_flags": [],
                        "notes": [],
                    }
                ],
                "shadow_board": [
                    {
                        "symbol": "GLD",
                        "contract_symbol": "GLD1",
                        "option_type": "call",
                        "expiry": "2026-04-27",
                        "strike": 435.0,
                        "forge_score": 0.69,
                        "expected_edge_after_friction_pct": 0.11,
                        "contract_cost": 182.0,
                        "council_risk_flags": ["high_extrinsic"],
                        "notes": ["Shadow only"],
                    }
                ],
                "summary": {
                    "candidate_count": 3,
                    "live_count": 1,
                    "shadow_count": 1,
                    "avg_pairwise_correlation": 0.22,
                    "abstain_audit": {
                        "primary_reason": "live_board_available",
                        "primary_reason_label": "Live board available",
                    },
                    "notes": ["Council is operating under a neutral market regime."],
                },
            },
            "diagnostics": {
                "pre_forge": {
                    "rejections": [{"symbol": "QQQ", "reason": "liquidity_gate"}],
                },
                "forge": {
                    "waterfall": {"signals_considered": 3, "final_candidates": 3},
                    "pre_council_gate": {
                        "kept": 3,
                        "dropped": 1,
                        "min_expected_edge_after_friction_pct": 0.05,
                        "rejections": [
                            {
                                "symbol": "DIA",
                                "contract_symbol": "DIA1",
                                "reason": "friction_gate",
                                "expected_edge_after_friction_pct": 0.03,
                                "friction_buffer_pct": 0.07,
                            }
                        ],
                    },
                    "deduplication": {
                        "removed_candidates": 2,
                        "kept_candidates": 3,
                        "max_structures_per_symbol_side": 2,
                    },
                },
            },
        }

        artifact = build_live_shadow_attribution_artifact(payload)

        self.assertEqual(artifact["artifact"], "live_shadow_attribution")
        self.assertEqual(artifact["summary"]["friction_veto_count"], 1)
        self.assertEqual(artifact["summary"]["dedupe_removed_count"], 2)
        self.assertEqual(artifact["summary"]["council_holdout_count"], 1)
        self.assertEqual(artifact["summary"]["live_side_mix"], {"call": 1, "put": 0})
        self.assertEqual(artifact["summary"]["abstain_primary_reason"], "live_board_available")
        self.assertEqual(artifact["summary"]["live_avg_policy_score"], 0.79)
        self.assertEqual(artifact["council_holdouts"][0]["symbol"], "SPY")
        self.assertEqual(artifact["friction_vetoes"][0]["symbol"], "DIA")
        self.assertEqual(artifact["top_live_board"][0]["policy_score"], 0.79)
        self.assertEqual(artifact["top_live_board"][0]["prob_fill_quality_ok"], 0.81)
        self.assertEqual(artifact["top_live_board"][0]["no_trade_pressure"], 0.18)
        self.assertEqual(
            artifact["layer_breakdown"]["council"]["abstain_audit"]["primary_reason"],
            "live_board_available",
        )

    def test_write_live_shadow_attribution_artifacts_creates_latest_and_dated_files(self) -> None:
        payload = {
            "generated_at_utc": "2026-04-25T15:07:00+00:00",
            "summary": {
                "scout_signal_count": 1,
                "pre_forge_signal_count": 1,
                "forge_candidate_count": 1,
            },
            "forge_candidates": [],
            "council": {
                "abstain": False,
                "live_board": [],
                "shadow_board": [],
                "summary": {"candidate_count": 0, "live_count": 0, "shadow_count": 0, "notes": []},
            },
            "diagnostics": {
                "pre_forge": {"rejections": []},
                "forge": {"pre_council_gate": {"rejections": []}, "deduplication": {}, "waterfall": {}},
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_live_shadow_attribution_artifacts(f"{tmpdir}/latest_run.json", payload)

            self.assertEqual(len(paths), 2)
            self.assertTrue(paths[0].name.endswith("_latest.json"))
            self.assertEqual(paths[1].name, "live_shadow_attribution_2026-04-25.json")
            self.assertTrue(paths[0].exists())
            self.assertTrue(paths[1].exists())

            rendered = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(rendered["trading_day"], "2026-04-25")

    def test_run_scan_keeps_snapshot_counts_truthful(self) -> None:
        signal = _signal("AAA")
        live_candidate = {
            "symbol": "AAA",
            "contract_symbol": "AAATEST",
            "option_type": "call",
            "expiry": "2026-04-17",
            "strike": 100.0,
            "bid": 1.0,
            "ask": 1.1,
            "last": 1.05,
            "premium": 1.1,
            "contract_cost": 110.0,
            "spread_pct": 0.05,
            "open_interest": 400,
            "volume": 120,
            "implied_volatility": 0.25,
            "delta": 0.4,
            "moneyness": 0.0,
            "projected_move_pct": 0.05,
            "breakeven_move_pct": 0.02,
            "expected_return_pct": 0.8,
            "extrinsic_ratio": 0.6,
            "scout_score": 0.6,
            "forge_score": 0.72,
            "payoff_edge_score": 0.64,
            "prob_positive_option_pnl": 0.64,
            "expected_edge_after_friction_pct": 0.22,
            "friction_gate_passed": True,
            "learned_rank_score": 0.72,
            "notes": [],
        }
        council_payload = {
            "live_board": [live_candidate],
            "shadow_board": [],
            "abstain": False,
            "summary": {
                "candidate_count": 1,
                "live_count": 1,
                "shadow_count": 0,
                "notes": [],
            },
        }

        with (
            mock.patch(
                "engine.orographic.pipeline.scan_symbols_with_diagnostics",
                return_value=(
                    MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                    [signal],
                    {"pre_veto_direction_counts": {"call": 1}, "final_direction_counts": {"call": 1}, "counter_regime_survivors": 0},
                ),
            ),
            mock.patch(
                "engine.orographic.pipeline.select_signals_for_forge",
                return_value=([signal], {}),
            ),
            mock.patch(
                "engine.orographic.pipeline.rank_contracts_with_diagnostics",
                return_value=([mock.Mock(to_dict=lambda: live_candidate, forge_score=0.72)], {"waterfall": {}, "learned_ranker": {}}),
            ),
            mock.patch(
                "engine.orographic.pipeline.select_board",
                return_value=mock.Mock(to_dict=lambda: council_payload, live_board=[mock.Mock(forge_score=0.72)], abstain=False),
            ),
        ):
            payload = run_scan(mock.Mock(universe=["AAA"], forge_intake=1, live_size=1, shadow_size=1))

        self.assertEqual(payload["summary"]["scout_signal_count"], len(payload["scout_signals"]))
        self.assertEqual(payload["summary"]["forge_candidate_count"], len(payload["forge_candidates"]))
        self.assertEqual(payload["council"]["summary"]["candidate_count"], len(payload["forge_candidates"]))
        self.assertEqual(payload["council"]["summary"]["live_count"], len(payload["council"]["live_board"]))
        self.assertEqual(payload["attribution"]["artifact"], "live_shadow_attribution")
        self.assertEqual(payload["scan_settings"]["live_size"], 1)
        self.assertEqual(payload["scan_settings"]["forge_intake"], 1)
        self.assertIn("directional_scout", payload["model_modes"])
        self.assertIn("payoff_ranker", payload["model_modes"])
        self.assertIn("model_artifacts", payload["attribution"])
        self.assertEqual(payload["attribution"]["scan_settings"]["shadow_size"], 0)
        self.assertEqual(payload["scan_settings"]["production_lane_count"], 1)
        self.assertEqual(payload["scan_settings"]["production_lane"], "council.live_board")

    def test_run_scan_passes_prior_live_board_symbols_into_council(self) -> None:
        signal = _signal("AAA")
        live_candidate = {
            "symbol": "AAA",
            "contract_symbol": "AAA1",
            "option_type": "call",
            "expiry": "2026-04-17",
            "strike": 100.0,
            "forge_score": 0.72,
            "contract_cost": 110.0,
            "notes": [],
        }
        council_payload = {
            "live_board": [live_candidate],
            "shadow_board": [],
            "abstain": False,
            "summary": {
                "candidate_count": 1,
                "live_count": 1,
                "shadow_count": 0,
                "notes": [],
            },
        }

        with (
            mock.patch(
                "engine.orographic.pipeline.scan_symbols_with_diagnostics",
                return_value=(
                    MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
                    [signal],
                    {"pre_veto_direction_counts": {"call": 1}, "final_direction_counts": {"call": 1}, "counter_regime_survivors": 0},
                ),
            ),
            mock.patch(
                "engine.orographic.pipeline.select_signals_for_forge",
                return_value=([signal], {}),
            ),
            mock.patch(
                "engine.orographic.pipeline.rank_contracts_with_diagnostics",
                return_value=([mock.Mock(to_dict=lambda: live_candidate, forge_score=0.72)], {"waterfall": {}, "learned_ranker": {}}),
            ) as rank_contracts_mock,
            mock.patch("engine.orographic.pipeline._load_prior_live_board_symbols", return_value=["AAPL", "MSFT"]),
            mock.patch(
                "engine.orographic.pipeline.select_board",
                return_value=mock.Mock(
                    to_dict=lambda: council_payload,
                    live_board=[mock.Mock(forge_score=0.72)],
                    abstain=False,
                    summary={"candidate_count": 1, "live_count": 1, "shadow_count": 0, "notes": []},
                ),
            ) as select_board_mock,
        ):
            payload = run_scan(
                mock.Mock(
                    universe=["AAA"],
                    forge_intake=1,
                    live_size=1,
                    shadow_size=1,
                    board_history_path="board_history.json",
                )
            )

        self.assertEqual(payload["summary"]["prior_live_board_symbols"], ["AAPL", "MSFT"])
        self.assertEqual(
            rank_contracts_mock.call_args.kwargs["prior_live_board_symbols"],
            ["AAPL", "MSFT"],
        )
        self.assertEqual(
            select_board_mock.call_args.kwargs["prior_live_board_symbols"],
            ["AAPL", "MSFT"],
        )

    def test_rank_contracts_filters_friction_and_deduplicates_symbol_structures(self) -> None:
        candidates = [
            ContractCandidate(
                symbol="AAA",
                contract_symbol="AAA1",
                option_type="call",
                expiry="2026-04-17",
                strike=100.0,
                bid=1.0,
                ask=1.1,
                last=1.05,
                premium=1.1,
                contract_cost=110.0,
                spread_pct=0.095,
                open_interest=500,
                volume=120,
                implied_volatility=0.30,
                delta=0.46,
                moneyness=-0.005,
                projected_move_pct=0.05,
                breakeven_move_pct=0.02,
                expected_return_pct=0.8,
                extrinsic_ratio=0.55,
                scout_score=0.6,
                forge_score=0.72,
                expected_option_return_pct_model=0.22,
                learned_rank_score=0.72,
                payoff_edge_score=0.60,
                breakeven_edge_score=0.58,
            ),
            ContractCandidate(
                symbol="AAA",
                contract_symbol="AAA2",
                option_type="call",
                expiry="2026-04-17",
                strike=99.8,
                bid=1.05,
                ask=1.15,
                last=1.10,
                premium=1.15,
                contract_cost=115.0,
                spread_pct=0.091,
                open_interest=520,
                volume=140,
                implied_volatility=0.30,
                delta=0.41,
                moneyness=-0.008,
                projected_move_pct=0.05,
                breakeven_move_pct=0.02,
                expected_return_pct=0.7,
                extrinsic_ratio=0.50,
                scout_score=0.6,
                forge_score=0.68,
                expected_option_return_pct_model=0.20,
                learned_rank_score=0.68,
                payoff_edge_score=0.58,
                breakeven_edge_score=0.56,
            ),
            ContractCandidate(
                symbol="AAA",
                contract_symbol="AAA3",
                option_type="call",
                expiry="2026-04-17",
                strike=99.3,
                bid=1.1,
                ask=1.2,
                last=1.15,
                premium=1.2,
                contract_cost=120.0,
                spread_pct=0.087,
                open_interest=540,
                volume=160,
                implied_volatility=0.30,
                delta=0.36,
                moneyness=-0.012,
                projected_move_pct=0.05,
                breakeven_move_pct=0.02,
                expected_return_pct=0.4,
                extrinsic_ratio=0.35,
                scout_score=0.6,
                forge_score=0.66,
                expected_option_return_pct_model=-0.02,
                learned_rank_score=0.66,
                payoff_edge_score=0.46,
                breakeven_edge_score=0.44,
            ),
            ContractCandidate(
                symbol="AAA",
                contract_symbol="AAA4",
                option_type="call",
                expiry="2026-04-17",
                strike=99.0,
                bid=1.45,
                ask=1.55,
                last=1.50,
                premium=1.55,
                contract_cost=155.0,
                spread_pct=0.066,
                open_interest=560,
                volume=180,
                implied_volatility=0.30,
                delta=0.31,
                moneyness=-0.020,
                projected_move_pct=0.05,
                breakeven_move_pct=0.02,
                expected_return_pct=0.5,
                extrinsic_ratio=0.03,
                scout_score=0.6,
                forge_score=0.64,
                expected_option_return_pct_model=0.16,
                learned_rank_score=0.64,
                payoff_edge_score=0.54,
                breakeven_edge_score=0.52,
            ),
        ]

        gated, diagnostics = _apply_pre_council_gate(
            candidates,
            min_expected_edge_after_friction_pct=0.05,
        )
        deduped, removed = _dedupe_candidates(gated)

        self.assertEqual([row.contract_symbol for row in deduped], ["AAA1"])
        self.assertEqual(diagnostics["dropped"], 1)
        self.assertEqual(removed, 2)

    def test_symbol_deduplication_keeps_strong_distinct_second_structure(self) -> None:
        primary = ContractCandidate(
            symbol="AAA",
            contract_symbol="AAA1",
            option_type="call",
            expiry="2026-04-17",
            strike=100.0,
            bid=1.0,
            ask=1.1,
            last=1.05,
            premium=1.1,
            contract_cost=110.0,
            spread_pct=0.05,
            open_interest=500,
            volume=120,
            implied_volatility=0.30,
            delta=0.46,
            moneyness=-0.005,
            projected_move_pct=0.05,
            breakeven_move_pct=0.02,
            expected_return_pct=0.8,
            extrinsic_ratio=0.40,
            scout_score=0.6,
            forge_score=0.74,
            expected_option_return_pct_model=0.30,
            learned_rank_score=0.74,
        )
        distinct = ContractCandidate(
            symbol="AAA",
            contract_symbol="AAA2",
            option_type="call",
            expiry="2026-04-17",
            strike=104.0,
            bid=0.7,
            ask=0.8,
            last=0.75,
            premium=0.8,
            contract_cost=80.0,
            spread_pct=0.05,
            open_interest=500,
            volume=120,
            implied_volatility=0.30,
            delta=0.22,
            moneyness=0.04,
            projected_move_pct=0.05,
            breakeven_move_pct=0.02,
            expected_return_pct=0.7,
            extrinsic_ratio=0.35,
            scout_score=0.6,
            forge_score=0.70,
            expected_option_return_pct_model=0.28,
            learned_rank_score=0.70,
        )
        adjacent = ContractCandidate(
            symbol="AAA",
            contract_symbol="AAA3",
            option_type="call",
            expiry="2026-04-17",
            strike=101.0,
            bid=0.9,
            ask=1.0,
            last=0.95,
            premium=1.0,
            contract_cost=100.0,
            spread_pct=0.05,
            open_interest=500,
            volume=120,
            implied_volatility=0.30,
            delta=0.40,
            moneyness=0.004,
            projected_move_pct=0.05,
            breakeven_move_pct=0.02,
            expected_return_pct=0.7,
            extrinsic_ratio=0.35,
            scout_score=0.6,
            forge_score=0.69,
            expected_option_return_pct_model=0.28,
            learned_rank_score=0.69,
        )

        deduped, removed = _dedupe_candidates([primary, distinct, adjacent])

        self.assertEqual([row.contract_symbol for row in deduped], ["AAA1", "AAA2"])
        self.assertEqual(removed, 1)

    def test_pre_council_gate_can_observe_without_vetoing_candidates(self) -> None:
        weak_edge = ContractCandidate(
            symbol="AAA",
            contract_symbol="AAA1",
            option_type="call",
            expiry="2026-04-17",
            strike=100.0,
            bid=1.0,
            ask=1.1,
            last=1.05,
            premium=1.1,
            contract_cost=110.0,
            spread_pct=0.05,
            open_interest=400,
            volume=120,
            implied_volatility=0.25,
            delta=0.4,
            moneyness=0.0,
            projected_move_pct=0.05,
            breakeven_move_pct=0.02,
            expected_return_pct=0.01,
            extrinsic_ratio=0.7,
            scout_score=0.6,
            forge_score=0.72,
            expected_option_return_pct_model=0.01,
            learned_rank_score=0.72,
        )

        observed, diagnostics = _apply_pre_council_gate(
            [weak_edge],
            min_expected_edge_after_friction_pct=0.05,
            enforced=False,
        )

        self.assertEqual(observed, [])
        self.assertEqual(diagnostics["dropped"], 1)
        self.assertFalse(diagnostics["enforced"])
        self.assertFalse(weak_edge.friction_gate_passed)
        self.assertNotIn("friction_veto", weak_edge.council_risk_flags)

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

    def test_board_recommendation_history_records_live_and_shadow_boards(self) -> None:
        payload = {
            "generated_at_utc": "2026-04-27T15:16:47+00:00",
            "regime": {"mode": "risk_off", "bias": -0.4, "source_symbol": "SPY"},
            "summary": {
                "universe_size": 100,
                "scout_signal_count": 66,
                "pre_forge_signal_count": 6,
                "forge_candidate_count": 5,
            },
            "council": {
                "abstain": False,
                "live_board": [
                    {
                        "symbol": "IWM",
                        "contract_symbol": "IWM1",
                        "option_type": "put",
                        "expiry": "2026-04-27",
                        "strike": 201.0,
                        "forge_score": 0.57,
                        "contract_cost": 144.0,
                    }
                ],
                "shadow_board": [
                    {
                        "symbol": "XLF",
                        "contract_symbol": "XLF1",
                        "option_type": "put",
                        "expiry": "2026-04-27",
                        "strike": 48.0,
                        "forge_score": 0.82,
                        "contract_cost": 133.0,
                    }
                ],
                "summary": {
                    "live_count": 1,
                    "shadow_count": 1,
                    "abstain_audit": {
                        "primary_reason": "live_board_available",
                        "primary_reason_label": "Live board available",
                    },
                },
            },
        }

        entry = build_board_recommendation_history_entry(payload)

        self.assertEqual(entry["summary"]["live_count"], 1)
        self.assertEqual(entry["summary"]["shadow_side_mix"], {"call": 0, "put": 1})
        self.assertEqual(
            entry["summary"]["abstain_audit"]["primary_reason"],
            "live_board_available",
        )
        self.assertEqual(entry["live_board"][0]["symbol"], "IWM")

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = append_board_recommendation_history(
                f"{tmpdir}/board_history.json",
                payload,
                max_entries=2,
            )
            rendered = json.loads(ledger_path.read_text(encoding="utf-8"))

        self.assertEqual(rendered["artifact"], "board_recommendation_history")
        self.assertEqual(rendered["aggregate"]["runs"], 1)
        self.assertEqual(rendered["aggregate"]["live_picks_emitted"], 1)
        self.assertEqual(rendered["aggregate"]["shadow_picks_emitted"], 1)
        self.assertEqual(rendered["aggregate"]["abstain_primary_reasons"], [])

    def test_research_run_ledger_records_vetoes_holdouts_and_metadata(self) -> None:
        payload = {
            "generated_at_utc": "2026-05-05T13:45:00+00:00",
            "regime": {"mode": "risk_on", "bias": 0.3, "source_symbol": "SPY"},
            "scan_settings": {"live_size": 3, "shadow_size": 3, "forge_intake": 6, "universe_size": 100},
            "model_modes": {
                "directional_scout": "artifact",
                "side_aware_scout": "shadow",
                "sentinel": "shadow",
                "payoff_ranker": "active",
                "side_model_source": "artifact",
            },
            "model_artifacts": {
                "scout_model": {"present": True, "sha256": "abc"},
                "payoff_model": {"present": True, "sha256": "def"},
            },
            "summary": {
                "universe_size": 100,
                "scout_signal_count": 12,
                "pre_forge_signal_count": 4,
                "forge_candidate_count": 3,
                "abstain": False,
            },
            "council": {
                "abstain": False,
                "live_board": [
                    {
                        "symbol": "AAA",
                        "contract_symbol": "AAA1",
                        "option_type": "call",
                        "expiry": "2026-05-09",
                        "strike": 100.0,
                        "forge_score": 0.72,
                        "payoff_edge_score": 0.61,
                        "expected_edge_after_friction_pct": 0.14,
                        "contract_cost": 120.0,
                        "council_risk_flags": [],
                        "notes": [],
                    }
                ],
                "shadow_board": [
                    {
                        "symbol": "BBB",
                        "contract_symbol": "BBB1",
                        "option_type": "put",
                        "expiry": "2026-05-09",
                        "strike": 80.0,
                        "forge_score": 0.66,
                        "payoff_edge_score": 0.55,
                        "expected_edge_after_friction_pct": 0.09,
                        "contract_cost": 95.0,
                        "council_risk_flags": ["wide_spread"],
                        "notes": ["Shadow only"],
                    }
                ],
                "summary": {
                    "candidate_count": 3,
                    "live_count": 1,
                    "shadow_count": 1,
                    "abstain_audit": {
                        "primary_reason": "live_board_available",
                        "primary_reason_label": "Live board available",
                    },
                },
            },
            "attribution": {
                "friction_vetoes": [{"symbol": "CCC", "reason": "friction_gate"}],
                "council_holdouts": [{"symbol": "DDD", "contract_symbol": "DDD1"}],
                "pre_forge_rejections": [{"symbol": "EEE", "reason": "liquidity_gate"}],
            },
        }

        entry = build_research_run_ledger_entry(payload)

        self.assertEqual(entry["summary"]["friction_veto_count"], 1)
        self.assertEqual(entry["summary"]["council_holdout_count"], 1)
        self.assertEqual(entry["summary"]["pre_forge_rejection_count"], 1)
        self.assertEqual(entry["scan_settings"]["forge_intake"], 6)
        self.assertEqual(entry["model_modes"]["payoff_ranker"], "active")
        self.assertEqual(entry["live_board"][0]["symbol"], "AAA")

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = append_research_run_ledger(
                f"{tmpdir}/research_run_ledger.json",
                payload,
                max_entries=2,
            )
            rendered = json.loads(ledger_path.read_text(encoding="utf-8"))

        self.assertEqual(rendered["artifact"], "research_run_ledger")
        self.assertEqual(rendered["aggregate"]["runs"], 1)
        self.assertEqual(rendered["aggregate"]["friction_vetoes"], 1)
        self.assertEqual(rendered["aggregate"]["council_holdouts"], 1)
        self.assertEqual(rendered["aggregate"]["pre_forge_rejections"], 1)

    def test_prospective_pick_ledger_records_all_scored_contracts_and_outcome_slots(self) -> None:
        def candidate(symbol: str, contract: str, option_type: str, score: float, *, passed: bool = True) -> dict[str, object]:
            return {
                "symbol": symbol,
                "contract_symbol": contract,
                "option_type": option_type,
                "expiry": "2026-05-15",
                "strike": 100.0,
                "bid": 1.0,
                "ask": 1.2,
                "last": 1.1,
                "spread_pct": 0.09,
                "open_interest": 500,
                "volume": 120,
                "contract_cost": 110.0,
                "forge_score": score,
                "learned_rank_score": score + 0.01,
                "expected_edge_after_friction_pct": 0.12,
                "friction_buffer_pct": 0.04,
                "extrinsic_ratio": 0.72,
                "friction_gate_passed": passed,
                "entry_quote_type": "mid",
                "entry_data_source": "scan_quote",
                "notes": ["test row"],
            }

        live = candidate("AAA", "AAA1", "call", 0.86)
        live.update({
            "payoff_shadow_prob_positive": 0.61,
            "payoff_shadow_rank": 0.75,
            "payoff_shadow_probability_delta": -0.09,
            "payoff_shadow_rank_delta": 0.12,
            "payoff_shadow_disagreement": True,
            "payoff_shadow_mode": "observation_only",
            "payoff_shadow_artifact_sha256": "shadow-sha",
        })
        shadow = candidate("BBB", "BBB1", "put", 0.78)
        veto = candidate("CCC", "CCC1", "call", 0.74, passed=False)
        holdout = candidate("DDD", "DDD1", "call", 0.71)
        payload = {
            "generated_at_utc": "2026-05-05T13:45:00+00:00",
            "regime": {"mode": "neutral", "bias": 0.0, "source_symbol": "SPY"},
            "scan_settings": {
                "live_size": 1,
                "shadow_size": 1,
                "forge_intake": 4,
                "minimum_live_score": 0.76,
                "minimum_put_live_score": 0.84,
                "max_live_extrinsic_ratio": 0.90,
            },
            "model_modes": {"payoff_ranker": "active"},
            "model_artifacts": {"payoff_model": {"present": True, "sha256": "abc"}},
            "scout_signals": [
                {"symbol": "AAA", "spot": 101.25},
                {"symbol": "BBB", "spot": 99.5},
                {"symbol": "CCC", "spot": 88.0},
                {"symbol": "DDD", "spot": 77.0},
            ],
            "forge_candidates": [live, shadow, veto, holdout],
            "council": {
                "abstain": False,
                "live_board": [live],
                "shadow_board": [shadow],
                "summary": {"live_count": 1, "shadow_count": 1},
            },
            "attribution": {
                "friction_vetoes": [{"symbol": "CCC", "contract_symbol": "CCC1"}],
                "council_holdouts": [{"symbol": "DDD", "contract_symbol": "DDD1"}],
            },
        }

        entry = build_prospective_pick_ledger_entry(payload)

        self.assertEqual(
            entry["summary"],
            {
                "pick_rows": 4,
                "live": 1,
                "shadow": 1,
                "council_holdout": 1,
                "friction_veto": 1,
                "counterfactual_observation": 0,
            },
        )
        lanes = {row["contract_symbol"]: row["lane"] for row in entry["picks"]}
        self.assertEqual(lanes, {"AAA1": "live", "BBB1": "shadow", "CCC1": "friction_veto", "DDD1": "council_holdout"})
        self.assertEqual(entry["picks"][0]["recommendation_id"], "2026-05-05T13:45:00+00:00|AAA1|live")
        self.assertEqual(entry["picks"][0]["lane_reason"], "selected_live_board")
        self.assertEqual(entry["picks"][0]["days_to_expiry"], 10)
        self.assertEqual(entry["picks"][0]["underlying"]["spot"], 101.25)
        self.assertEqual(entry["picks"][0]["emission_quote"]["captured_at_utc"], "2026-05-05T13:45:00+00:00")
        self.assertEqual(entry["picks"][0]["emission_quote"]["spread"], 0.2)
        self.assertEqual(entry["picks"][0]["emission_quote"]["mid"], 1.1)
        self.assertEqual(entry["picks"][0]["outcomes"]["status"], "pending")
        self.assertIsNone(entry["picks"][0]["outcomes"]["fixed_exit_marks"]["friday_close"])
        self.assertEqual(entry["picks"][0]["outcomes"]["quote_verification"]["capture_policy_version"], 2)
        self.assertIsNone(entry["picks"][0]["outcomes"]["capture_attempts"]["friday_close"])
        self.assertEqual(entry["picks"][0]["context"]["scan_settings"]["minimum_put_live_score"], 0.84)
        self.assertEqual(entry["picks"][0]["scores"]["payoff_shadow_prob_positive"], 0.61)
        self.assertTrue(entry["picks"][0]["scores"]["payoff_shadow_disagreement"])

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = append_prospective_pick_ledger(
                f"{tmpdir}/prospective_pick_ledger.json",
                payload,
                max_entries=2,
            )
            rendered = json.loads(ledger_path.read_text(encoding="utf-8"))

        self.assertEqual(rendered["artifact"], "prospective_pick_ledger")
        self.assertEqual(rendered["schema_version"], 3)
        self.assertEqual(rendered["payoff_shadow_policy"]["mode"], "observation_only")
        self.assertFalse(rendered["payoff_shadow_policy"]["affects_tradier_routing"])
        self.assertEqual(rendered["aggregate"]["runs"], 1)
        self.assertEqual(rendered["aggregate"]["pick_rows"], 4)
        self.assertEqual(rendered["aggregate"]["friction_veto"], 1)
        self.assertEqual(rendered["outcome_summary"]["pending"], 4)
        self.assertEqual(rendered["outcome_summary"]["payoff_shadow_scored"], 1)
        self.assertEqual(rendered["outcome_summary"]["payoff_shadow_disagreements"], 1)

    def test_moonshot_prospective_ledger_records_pick_and_shadow_candidates(self) -> None:
        def moonshot_candidate(contract: str, *, eligible: bool, score: float) -> dict[str, object]:
            return {
                "symbol": "AAA",
                "contract_symbol": contract,
                "option_type": "call",
                "expiry": "2026-05-15",
                "strike": 100.0,
                "bid": 1.0,
                "ask": 1.2,
                "last": 1.1,
                "spread_pct": 0.09,
                "open_interest": 500,
                "volume": 120,
                "contract_cost": 110.0,
                "forge_score": 0.76,
                "learned_rank_score": 0.80,
                "delta": 0.34,
                "implied_volatility": 0.48,
                "iv_rank": 0.44,
                "extrinsic_ratio": 0.72,
                "moneyness": 0.01,
                "friction_gate_passed": True,
                "moonshot": {
                    "tail_upside_score": score,
                    "eligible": eligible,
                    "reasons": ["medium delta matches tail bucket"],
                },
            }

        payload = {
            "generated_at_utc": "2026-05-05T13:45:00+00:00",
            "regime": {"mode": "neutral", "bias": 0.0, "source_symbol": "SPY"},
            "scan_settings": {"moonshot_size": 1, "moonshot_threshold": 0.68},
            "model_modes": {"payoff_ranker": "active"},
            "model_artifacts": {"payoff_model": {"present": True, "sha256": "abc"}},
            "moonshot_lane": {
                "policy": {"name": "nimrod_inspired_moonshot_satellite"},
                "picks": [moonshot_candidate("AAA1", eligible=True, score=0.74)],
                "shadow": [moonshot_candidate("AAA2", eligible=False, score=0.61)],
            },
        }

        entry = build_moonshot_prospective_ledger_entry(payload)

        self.assertEqual(entry["summary"]["candidate_rows"], 2)
        self.assertEqual(entry["summary"]["moonshot_pick"], 1)
        self.assertEqual(entry["summary"]["moonshot_shadow"], 1)
        lanes = {row["contract_symbol"]: row["lane"] for row in entry["picks"]}
        self.assertEqual(lanes, {"AAA1": "moonshot_pick", "AAA2": "moonshot_shadow"})
        self.assertEqual(entry["picks"][0]["moonshot"]["tail_upside_score"], 0.74)
        self.assertIsNone(entry["picks"][0]["outcomes"]["fixed_exit_marks"]["next_day_close"])

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = append_moonshot_prospective_ledger(
                f"{tmpdir}/moonshot_prospective_ledger.json",
                payload,
            )
            rendered = json.loads(ledger_path.read_text(encoding="utf-8"))

        self.assertEqual(rendered["artifact"], "moonshot_prospective_ledger")
        self.assertEqual(rendered["aggregate"]["runs"], 1)
        self.assertEqual(rendered["aggregate"]["candidate_rows"], 2)


if __name__ == "__main__":
    unittest.main()
