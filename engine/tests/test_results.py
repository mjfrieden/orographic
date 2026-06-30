from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from engine.backtest.pricer import TradeLeg
from engine.backtest.results import (
    DEFAULT_OUTPUT,
    apply_coverage_policy,
    build_option_outcome_dataset,
    build_option_outcome_dataset_summary,
    build_results,
    default_option_outcome_output_path,
    option_outcome_dataset_payload_from_results_payload,
    save_option_outcome_dataset,
)


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
    entry_price: float = 1.0,
    exit_price: float = 1.2,
    entry_raw_price: float | None = None,
    exit_raw_price: float | None = None,
    entry_slippage_pct: float = 0.0,
    exit_slippage_pct: float = 0.0,
    regime_mode: str | None = "risk_on",
    regime_bias: float | None = 0.25,
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
        entry_price=entry_price,
        exit_price=exit_price,
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
        entry_raw_price=entry_raw_price,
        exit_raw_price=exit_raw_price,
        entry_slippage_pct=entry_slippage_pct,
        exit_slippage_pct=exit_slippage_pct,
        regime_mode=regime_mode,
        regime_bias=regime_bias,
        regime_source_symbol="SPY",
    )


class ResultsTests(unittest.TestCase):
    def test_default_option_outcome_output_path_uses_canonical_latest_for_default_results(self) -> None:
        self.assertEqual(
            default_option_outcome_output_path(DEFAULT_OUTPUT),
            Path("output/option_outcomes_latest.json"),
        )

    def test_default_option_outcome_output_path_rewrites_named_result_paths(self) -> None:
        self.assertEqual(
            default_option_outcome_output_path(Path("output/backtest_results_custom.json")),
            Path("output/option_outcomes_custom.json"),
        )
        self.assertEqual(
            default_option_outcome_output_path(Path("docs/research_run.json")),
            Path("docs/research_run_option_outcomes.json"),
        )

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
        self.assertEqual(results["side_breakdown"][0]["option_type"], "call")
        self.assertEqual(results["side_breakdown"][0]["trades"], 3)

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

    def test_build_results_reports_put_side_breakdown(self) -> None:
        put_trade = _trade("HEDGE", "real_chain", "real_chain", 1.0, pnl=30.0, pnl_pct=0.3)
        put_trade.option_type = "put"
        results = build_results(
            [
                _trade("LONG", "real_chain", "real_chain", 1.0, pnl=-10.0, pnl_pct=-0.1),
                put_trade,
            ],
            date(2026, 4, 1),
            date(2026, 4, 30),
        )

        self.assertEqual(
            results["side_breakdown"],
            [
                {
                    "option_type": "call",
                    "trades": 1,
                    "win_rate": 0.0,
                    "expired_worthless": 0,
                    "total_pnl": -10.0,
                    "avg_pnl_pct": -0.1,
                    "avg_cost_basis": 100.0,
                },
                {
                    "option_type": "put",
                    "trades": 1,
                    "win_rate": 1.0,
                    "expired_worthless": 0,
                    "total_pnl": 30.0,
                    "avg_pnl_pct": 0.3,
                    "avg_cost_basis": 100.0,
                },
            ],
        )

    def test_build_results_reports_regime_breakdown(self) -> None:
        risk_on = _trade("RON", "real_chain", "real_chain", 1.0, pnl=20.0, pnl_pct=0.2, regime_mode="risk_on", regime_bias=0.3)
        neutral = _trade("NEU", "real_chain", "real_chain", 1.0, pnl=-5.0, pnl_pct=-0.05, regime_mode="neutral", regime_bias=0.0)
        risk_off = _trade("ROFF", "real_chain", "real_chain", 1.0, pnl=15.0, pnl_pct=0.15, regime_mode="risk_off", regime_bias=-0.25)
        results = build_results(
            [risk_on, neutral, risk_off],
            date(2026, 4, 1),
            date(2026, 4, 30),
        )

        self.assertEqual(
            results["regime_breakdown"],
            [
                {
                    "regime_mode": "risk_on",
                    "trades": 1,
                    "win_rate": 1.0,
                    "expired_worthless": 0,
                    "total_pnl": 20.0,
                    "avg_pnl_pct": 0.2,
                    "avg_cost_basis": 100.0,
                    "avg_regime_bias": 0.3,
                },
                {
                    "regime_mode": "neutral",
                    "trades": 1,
                    "win_rate": 0.0,
                    "expired_worthless": 0,
                    "total_pnl": -5.0,
                    "avg_pnl_pct": -0.05,
                    "avg_cost_basis": 100.0,
                    "avg_regime_bias": 0.0,
                },
                {
                    "regime_mode": "risk_off",
                    "trades": 1,
                    "win_rate": 1.0,
                    "expired_worthless": 0,
                    "total_pnl": 15.0,
                    "avg_pnl_pct": 0.15,
                    "avg_cost_basis": 100.0,
                    "avg_regime_bias": -0.25,
                },
            ],
        )

    def test_option_outcome_dataset_tracks_friction_adjusted_labels(self) -> None:
        trade = _trade(
            "FRIC",
            "real_chain",
            "real_chain",
            1.0,
            cost_basis=120.0,
            exit_value=100.0,
            pnl=-20.0,
            pnl_pct=-0.1667,
            entry_price=1.2,
            exit_price=1.0,
            entry_raw_price=1.0,
            exit_raw_price=1.2,
            entry_slippage_pct=0.1,
            exit_slippage_pct=0.1,
        )

        dataset = build_option_outcome_dataset([trade])
        row = dataset[0]

        self.assertAlmostEqual(row["raw_pnl_pct"], 0.2, places=4)
        self.assertAlmostEqual(row["hold_period_return_after_friction_pct"], -0.1667, places=4)
        self.assertTrue(row["positive_pnl_before_friction"])
        self.assertFalse(row["positive_pnl_after_friction"])
        self.assertTrue(row["friction_flipped_winner_to_loser"])
        self.assertGreater(row["total_friction_cost_usd"], 0.0)

        summary = build_option_outcome_dataset_summary(dataset)
        self.assertEqual(summary["rows"], 1)
        self.assertEqual(summary["friction_flip_count"], 1)
        self.assertAlmostEqual(summary["positive_pnl_before_friction_rate"], 1.0, places=4)
        self.assertAlmostEqual(summary["positive_pnl_after_friction_rate"], 0.0, places=4)

    def test_option_outcome_dataset_preserves_option_native_and_sentinel_fields(self) -> None:
        trade = _trade("EVNT", "real_chain", "real_chain", 1.0)
        trade.realized_vol_20d = 0.22
        trade.atr_pct_14d = 0.026
        trade.premium_pct_of_spot = 0.011
        trade.vrp_gap = 0.08
        trade.sentinel_holding_window_fit = 0.75
        trade.sentinel_holding_window_label = "well_matched"
        trade.sentinel_decay_half_life = "three_days"
        trade.sentinel_time_horizon = "one_to_three_days"
        trade.sentinel_confidence = 0.72
        trade.sentinel_call_relevance = 0.88
        trade.sentinel_put_relevance = 0.12
        trade.sentinel_no_trade_relevance = 0.04
        trade.sentinel_spot_effect = 1.0
        trade.sentinel_iv_effect = 0.0
        trade.sentinel_status = "ai_success_event"
        trade.sentinel_options_impact_label = "spot_up_iv_down"
        trade.sentinel_recommended_use = "tie_breaker"
        trade.sentinel_veto_reason = None
        trade.sentinel_tie_breaker_score = 0.025
        trade.sentinel_size_multiplier = 0.9
        trade.path_early_profit_take_prob = 0.66
        trade.path_expected_mfe_pct = 0.24
        trade.path_decay_risk = 0.31
        trade.path_holding_quality_score = 0.62
        trade.path_model_mode = "shadow"

        row = build_option_outcome_dataset([trade])[0]
        round_tripped = option_outcome_dataset_payload_from_results_payload({"all_trades": [row]})["rows"][0]

        self.assertAlmostEqual(row["realized_vol_20d"], 0.22, places=4)
        self.assertAlmostEqual(row["premium_pct_of_spot"], 0.011, places=4)
        self.assertAlmostEqual(row["sentinel_confidence"], 0.72, places=4)
        self.assertEqual(row["sentinel_holding_window_label"], "well_matched")
        self.assertEqual(row["sentinel_status"], "ai_success_event")
        self.assertEqual(row["sentinel_options_impact_label"], "spot_up_iv_down")
        self.assertEqual(row["sentinel_recommended_use"], "tie_breaker")
        self.assertAlmostEqual(row["sentinel_tie_breaker_score"], 0.025, places=4)
        self.assertAlmostEqual(row["sentinel_size_multiplier"], 0.9, places=4)
        self.assertAlmostEqual(round_tripped["vrp_gap"], 0.08, places=4)
        self.assertAlmostEqual(round_tripped["sentinel_call_relevance"], 0.88, places=4)
        self.assertEqual(round_tripped["sentinel_status"], "ai_success_event")
        self.assertEqual(round_tripped["sentinel_options_impact_label"], "spot_up_iv_down")
        self.assertAlmostEqual(round_tripped["path_early_profit_take_prob"], 0.66, places=4)
        self.assertAlmostEqual(round_tripped["path_holding_quality_score"], 0.62, places=4)

    def test_build_results_uses_option_outcome_dataset_rows(self) -> None:
        trade = _trade(
            "DATA",
            "real_chain",
            "real_chain",
            1.0,
            entry_raw_price=1.0,
            exit_raw_price=1.1,
            entry_slippage_pct=0.05,
            exit_slippage_pct=0.05,
        )
        results = build_results([trade], date(2026, 4, 1), date(2026, 4, 30))

        self.assertEqual(results["option_outcome_dataset_summary"]["rows"], 1)
        self.assertIn("positive_pnl_after_friction", results["all_trades"][0])
        self.assertIn("hold_period_return_after_friction_pct", results["all_trades"][0])
        self.assertIn("raw_pnl_pct", results["all_trades"][0])
        self.assertEqual(results["all_trades"][0]["regime_mode"], "risk_on")
        self.assertAlmostEqual(results["all_trades"][0]["regime_bias"], 0.25, places=4)

    def test_build_results_empty_payload_includes_regime_breakdown(self) -> None:
        results = build_results([], date(2026, 4, 1), date(2026, 4, 30))
        self.assertEqual(results["regime_breakdown"], [])

    def test_save_option_outcome_dataset_writes_artifact(self) -> None:
        trade = _trade(
            "SAVE",
            "real_chain",
            "real_chain",
            1.0,
            entry_raw_price=1.0,
            exit_raw_price=1.05,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/option_outcomes.json"
            save_option_outcome_dataset([trade], output_path=Path(output), start_date=date(2026, 4, 1), end_date=date(2026, 4, 30))
            rendered = json.loads(Path(output).read_text(encoding="utf-8"))

        self.assertEqual(rendered["artifact"], "option_outcome_dataset")
        self.assertEqual(rendered["summary"]["rows"], 1)
        self.assertEqual(rendered["rows"][0]["symbol"], "SAVE")

    def test_option_outcome_dataset_payload_from_legacy_results(self) -> None:
        payload = {
            "generated_at": "2026-05-05",
            "backtest_start": "2026-04-01",
            "backtest_end": "2026-04-30",
            "all_trades": [
                {
                    "symbol": "LEG",
                    "contract_symbol": "LEG260410C00100000",
                    "option_type": "call",
                    "strike": 100.0,
                    "expiry": "2026-04-10",
                    "entry_date": "2026-04-06",
                    "exit_date": "2026-04-10",
                    "entry_price": 1.2,
                    "exit_price": 1.0,
                    "contracts": 1,
                    "cost_basis": 120.0,
                    "exit_value": 100.0,
                    "pnl": -20.0,
                    "pnl_pct": -0.1667,
                    "entry_raw_price": 1.0,
                    "exit_raw_price": 1.2,
                }
            ],
        }

        converted = option_outcome_dataset_payload_from_results_payload(payload)

        self.assertEqual(converted["artifact"], "option_outcome_dataset")
        self.assertEqual(converted["summary"]["rows"], 1)
        self.assertFalse(converted["rows"][0]["positive_pnl_after_friction"])
        self.assertTrue(converted["rows"][0]["positive_pnl_before_friction"])


if __name__ == "__main__":
    unittest.main()
