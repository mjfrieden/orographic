from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from engine import train_payoff_model as payoff_train_module
from engine.train_payoff_model import default_input_paths, load_examples, train


def _trade_row(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "AAA",
        "contract_symbol": "AAA260410C00100000",
        "option_type": "call",
        "strike": 100.0,
        "expiry": "2026-04-10",
        "entry_date": "2026-04-06",
        "exit_date": "2026-04-10",
        "entry_spot": 100.0,
        "exit_spot": 103.0,
        "entry_price": 1.2,
        "exit_price": 1.6,
        "entry_spread_pct": 0.08,
        "entry_open_interest": 500,
        "entry_volume": 120,
        "pnl_pct": 0.3333,
        "hold_period_return_after_friction_pct": 0.25,
        "positive_pnl_after_friction": True,
        "breakeven_after_friction": True,
        "implied_volatility": 0.35,
        "delta": 0.42,
        "moneyness": 0.01,
        "projected_move_pct": 0.04,
        "breakeven_move_pct": 0.03,
        "expected_return_pct": 0.2,
        "extrinsic_ratio": 0.8,
        "scout_score": 0.4,
        "forge_score": 0.6,
        "allocation_weight": 1.0,
        "iv_rank": 0.5,
        "regime_mode": "risk_on",
        "regime_bias": 0.2,
        "regime_alignment_score": 0.3,
    }
    payload.update(overrides)
    return payload


class TrainPayoffModelLoaderTests(unittest.TestCase):
    def test_family_selection_penalizes_a_weak_put_segment(self) -> None:
        aggregate_winner_with_weak_put = {
            "positive_pnl_brier_mean": 0.18,
            "breakeven_brier_mean": 0.18,
            "expected_return_mae_mean": 0.20,
            "positive_pnl_auc_mean": 0.60,
            "breakeven_auc_mean": 0.58,
            "by_segment": {
                "side": {
                    "prob_positive_option_pnl": {
                        "call": {"rows": 60, "brier": 0.16, "baseline_brier": 0.24, "auc": 0.62},
                        "put": {"rows": 60, "brier": 0.34, "baseline_brier": 0.24, "auc": 0.49},
                    }
                }
            },
        }
        balanced_family = {
            "positive_pnl_brier_mean": 0.20,
            "breakeven_brier_mean": 0.19,
            "expected_return_mae_mean": 0.22,
            "positive_pnl_auc_mean": 0.57,
            "breakeven_auc_mean": 0.56,
            "by_segment": {
                "side": {
                    "prob_positive_option_pnl": {
                        "call": {"rows": 60, "brier": 0.19, "baseline_brier": 0.24, "auc": 0.57},
                        "put": {"rows": 60, "brier": 0.20, "baseline_brier": 0.24, "auc": 0.55},
                    }
                }
            },
        }

        self.assertLess(
            payoff_train_module._family_sort_key(balanced_family),
            payoff_train_module._family_sort_key(aggregate_winner_with_weak_put),
        )

    def test_promotion_gate_blocks_weak_put_and_weak_regime_quality(self) -> None:
        cv = {
            "positive_pnl_auc_mean": 0.58,
            "breakeven_auc_mean": 0.57,
            "positive_pnl_brier_mean": 0.20,
            "breakeven_brier_mean": 0.19,
            "by_segment": {
                "side": {
                    "prob_positive_option_pnl": {
                        "call": {"rows": 50, "auc": 0.60, "brier": 0.19, "baseline_brier": 0.24},
                        "put": {"rows": 50, "auc": 0.49, "brier": 0.28, "baseline_brier": 0.24},
                    }
                },
                "regime": {
                    "prob_positive_option_pnl": {
                        "risk_on": {"rows": 40, "auc": 0.58, "brier": 0.20, "baseline_brier": 0.24},
                        "risk_off": {"rows": 40, "auc": 0.50, "brier": 0.27, "baseline_brier": 0.24},
                        "neutral": {"rows": 40, "auc": 0.52, "brier": 0.23, "baseline_brier": 0.24},
                    }
                },
            },
        }

        report = payoff_train_module._promotion_gate_report(
            training_examples=200,
            min_side_examples=75,
            side_counts={"call": 100, "put": 100},
            dataset_summary={"friction_flip_count": 10},
            regime_dataset_summary={"risk_on": {"rows": 70}, "risk_off": {"rows": 70}, "neutral": {"rows": 60}},
            cv=cv,
            positive_baseline_brier=0.24,
            breakeven_baseline_brier=0.24,
            primary_artifact="option_outcome_dataset",
            observed_path_examples=180,
            observed_path_coverage_ratio=0.9,
        )

        self.assertEqual(report["status"], "hold")
        self.assertFalse(report["gates"]["side_walk_forward_quality"]["passed"])
        self.assertFalse(report["gates"]["side_walk_forward_quality"]["segments"]["put"]["passed"])
        self.assertFalse(report["gates"]["regime_walk_forward_quality"]["passed"])
        self.assertEqual(report["gates"]["regime_walk_forward_quality"]["qualified_segments"], 1)

    def test_segment_metrics_include_naive_brier_baseline(self) -> None:
        y = np.array([0, 1] * 15, dtype=int)
        probs = np.array([0.2, 0.8] * 15, dtype=float)
        segments = np.array(["put"] * 30, dtype=object)

        report = payoff_train_module._segment_metric_report(y, probs, segments)

        self.assertEqual(report["put"]["baseline_brier"], 0.25)
        self.assertLess(report["put"]["brier"], report["put"]["baseline_brier"])

    def test_default_input_paths_returns_existing_canonical_candidates_in_priority_order(self) -> None:
        live = Path("output/option_outcomes_live_recommendations.json")
        canonical = Path("output/option_outcomes_latest.json")
        legacy = Path("output/backtest_results_2026-04-17_blended_target_dte_7_14_strict_real_execution_stress_12mo.json")

        original_exists = Path.exists

        def fake_exists(path: Path) -> bool:
            return path in {live, canonical, legacy}

        with (
            mock.patch.object(Path, "exists", fake_exists),
            mock.patch.object(payoff_train_module, "_is_strict_executable_dataset", return_value=False),
        ):
            resolved = default_input_paths()

        self.assertEqual(resolved, [live, canonical, legacy])

    def test_default_input_paths_excludes_legacy_when_strict_dataset_exists(self) -> None:
        live = Path("output/option_outcomes_live_recommendations.json")

        with (
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(
                payoff_train_module,
                "_is_strict_executable_dataset",
                side_effect=lambda path: path == live,
            ),
        ):
            resolved = default_input_paths()

        self.assertEqual(resolved, [live])

    def test_load_examples_accepts_canonical_option_outcome_dataset(self) -> None:
        payload = {
            "artifact": "option_outcome_dataset",
            "rows": [
                _trade_row(
                    realized_vol_20d=0.21,
                    atr_pct_14d=0.024,
                    premium_pct_of_spot=0.012,
                    vrp_gap=0.14,
                    sentinel_holding_window_fit=0.8,
                    sentinel_confidence=0.7,
                    sentinel_call_relevance=0.85,
                    sentinel_put_relevance=0.1,
                    sentinel_no_trade_relevance=0.05,
                    sentinel_spot_effect=1.0,
                    sentinel_iv_effect=0.0,
                ),
                _trade_row(symbol="BBB", contract_symbol="BBB260410P00100000", option_type="put", scout_score=-0.4, hold_period_return_after_friction_pct=-0.12, positive_pnl_after_friction=False, breakeven_after_friction=False, regime_mode="risk_off", regime_bias=-0.25, regime_alignment_score=-0.3),
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "option_outcomes.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            examples, metadata = load_examples([path], options_data_dir=None)

        self.assertEqual(len(examples), 2)
        self.assertEqual(metadata["input_artifacts"], {"option_outcome_dataset": 1})
        self.assertEqual(metadata["primary_training_source_artifact"], "option_outcome_dataset")
        self.assertEqual(metadata["canonical_dataset_files"], [str(path)])
        self.assertEqual(metadata["legacy_result_files"], [])
        self.assertEqual(metadata["dataset_summary"]["rows"], 2)
        self.assertEqual(metadata["dataset_summary"]["friction_flip_count"], 0)
        self.assertIn("call", metadata["side_dataset_summary"])
        self.assertIn("put", metadata["side_dataset_summary"])
        self.assertIn("risk_on", metadata["regime_dataset_summary"])
        self.assertIn("risk_off", metadata["regime_dataset_summary"])
        self.assertEqual(metadata["input_rows_by_file"][str(path)], 2)
        self.assertAlmostEqual(examples[0].pnl_pct, 0.25, places=4)
        self.assertEqual(examples[0].prob_positive_option_pnl, 1)
        self.assertEqual(examples[1].prob_positive_option_pnl, 0)
        self.assertEqual(examples[1].prob_exceeds_breakeven, 0)
        self.assertEqual(examples[0].regime_bucket, "risk_on")
        self.assertEqual(examples[1].regime_bucket, "risk_off")
        self.assertAlmostEqual(examples[0].candidate.realized_vol_20d or 0.0, 0.21, places=4)
        self.assertAlmostEqual(examples[0].candidate.vrp_gap or 0.0, 0.14, places=4)
        self.assertAlmostEqual(examples[0].candidate.sentinel_confidence or 0.0, 0.7, places=4)
        self.assertAlmostEqual(examples[0].candidate.sentinel_call_relevance or 0.0, 0.85, places=4)

    def test_training_features_use_entry_date_dte_and_signal_time_regime(self) -> None:
        payload = {"artifact": "option_outcome_dataset", "rows": [_trade_row()]}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "outcomes.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            examples, _ = load_examples([path], options_data_dir=None)

        matrix = payoff_train_module._training_feature_matrix(examples)
        dte_idx = payoff_train_module.FEATURE_COLS.index("dte")
        risk_on_idx = payoff_train_module.FEATURE_COLS.index("regime_is_risk_on")
        risk_off_idx = payoff_train_module.FEATURE_COLS.index("regime_is_risk_off")

        self.assertEqual(matrix.shape[0], 1)
        self.assertEqual(matrix[0, dte_idx], 4.0)
        self.assertEqual(matrix[0, risk_on_idx], 1.0)
        self.assertEqual(matrix[0, risk_off_idx], 0.0)

    def test_load_examples_falls_back_to_legacy_backtest_results(self) -> None:
        payload = {
            "all_trades": [_trade_row(pnl_pct=-0.1, hold_period_return_after_friction_pct=None, positive_pnl_after_friction=None, breakeven_after_friction=None)],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "backtest_results.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            examples, metadata = load_examples([path], options_data_dir=None)

        self.assertEqual(len(examples), 1)
        self.assertEqual(metadata["input_artifacts"], {"backtest_results": 1})
        self.assertEqual(metadata["primary_training_source_artifact"], "backtest_results")
        self.assertEqual(metadata["canonical_dataset_files"], [])
        self.assertEqual(metadata["legacy_result_files"], [str(path)])
        self.assertAlmostEqual(examples[0].pnl_pct, -0.1, places=4)
        self.assertEqual(examples[0].prob_positive_option_pnl, 0)
        self.assertEqual(examples[0].prob_exceeds_breakeven, 1)

    def test_load_examples_deduplicates_across_input_types(self) -> None:
        canonical = {
            "artifact": "option_outcome_dataset",
            "rows": [_trade_row()],
        }
        legacy = {
            "all_trades": [_trade_row()],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            canonical_path = Path(tmpdir) / "canonical.json"
            legacy_path = Path(tmpdir) / "legacy.json"
            canonical_path.write_text(json.dumps(canonical), encoding="utf-8")
            legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
            examples, metadata = load_examples([canonical_path, legacy_path], options_data_dir=None)

        self.assertEqual(len(examples), 1)
        self.assertEqual(metadata["deduplicated_examples"], 1)
        self.assertEqual(metadata["primary_training_source_artifact"], "option_outcome_dataset")
        self.assertEqual(metadata["canonical_dataset_files"], [str(canonical_path)])
        self.assertEqual(metadata["legacy_result_files"], [str(legacy_path)])
        self.assertEqual(metadata["input_rows_by_file"][str(canonical_path)], 1)
        self.assertEqual(metadata["input_rows_by_file"][str(legacy_path)], 1)

    def test_load_examples_uses_archived_quote_path_marks_for_mfe_and_mae(self) -> None:
        canonical = {
            "artifact": "option_outcome_dataset",
            "rows": [
                _trade_row(
                    archived_quote_path={
                        "status": "observed",
                        "marks": [
                            {"pnl_pct_from_emission": -0.15},
                            {"pnl_pct_from_emission": 0.35},
                        ],
                    }
                )
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            canonical_path = Path(tmpdir) / "canonical.json"
            canonical_path.write_text(json.dumps(canonical), encoding="utf-8")
            examples, metadata = load_examples([canonical_path], options_data_dir=None)

        self.assertEqual(len(examples), 1)
        self.assertAlmostEqual(examples[0].max_favorable_excursion_before_expiry, 0.35, places=6)
        self.assertAlmostEqual(examples[0].adverse_excursion_risk, -0.15, places=6)
        self.assertEqual(metadata["exact_quote_marks_used"], 2)
        self.assertEqual(metadata["examples_with_exact_quote_path"], 1)
        self.assertEqual(metadata["exact_quote_path_coverage_ratio"], 1.0)

    def test_train_report_includes_dataset_observability_and_promotion_gates(self) -> None:
        examples = []
        for idx in range(60):
            side = "call" if idx % 2 == 0 else "put"
            regime_bucket = "risk_on" if idx < 20 else ("risk_off" if idx < 40 else "neutral")
            candidate = payoff_train_module.ContractCandidate(
                symbol=f"S{idx}",
                contract_symbol=f"S{idx}260410{'C' if side == 'call' else 'P'}00100000",
                option_type=side,
                expiry="2026-04-10",
                strike=100.0,
                bid=1.0,
                ask=1.2,
                last=1.1,
                premium=1.2,
                contract_cost=120.0,
                spread_pct=0.08,
                open_interest=500,
                volume=100,
                implied_volatility=0.3,
                delta=0.4,
                moneyness=0.01,
                projected_move_pct=0.04,
                breakeven_move_pct=0.03,
                expected_return_pct=0.2,
                extrinsic_ratio=0.8,
                scout_score=0.3 if side == "call" else -0.3,
                forge_score=0.6,
            )
            examples.append(
                payoff_train_module.TradeExample(
                    candidate=candidate,
                    entry_date=payoff_train_module.date(2026, 1 + (idx % 3), 3 + (idx % 20)),
                    exit_date=payoff_train_module.date(2026, 1 + (idx % 3), 10 + (idx % 10)),
                    entry_spot=100.0,
                    exit_spot=102.0,
                    regime_bucket=regime_bucket,
                    pnl_pct=0.2 if idx % 3 else -0.1,
                    prob_positive_option_pnl=0 if idx % 3 == 0 else 1,
                    prob_no_trade=1 if idx % 3 == 0 else 0,
                    prob_fill_quality_ok=1,
                    expected_option_return_pct=0.2 if idx % 3 else -0.1,
                    prob_exceeds_breakeven=0 if idx % 4 == 0 else 1,
                    max_favorable_excursion_before_expiry=0.35,
                    adverse_excursion_risk=-0.15,
                )
            )

        source_metadata = {
            "primary_training_source_artifact": "option_outcome_dataset",
            "primary_training_source_files": ["output/option_outcomes_latest.json"],
            "canonical_dataset_files": ["output/option_outcomes_latest.json"],
            "legacy_result_files": [],
            "input_artifact_by_file": {"output/option_outcomes_latest.json": "option_outcome_dataset"},
            "dataset_summary": {
                "rows": 60,
                "positive_pnl_after_friction_rate": 0.6667,
                "positive_pnl_before_friction_rate": 0.75,
                "breakeven_after_friction_rate": 0.75,
                "breakeven_before_friction_rate": 0.8,
                "friction_flip_count": 8,
                "avg_friction_drag_pct": 0.08,
                "avg_total_friction_cost_usd": 12.5,
            },
            "side_dataset_summary": {
                "call": {"rows": 30, "friction_flip_count": 4},
                "put": {"rows": 30, "friction_flip_count": 4},
            },
            "regime_dataset_summary": {
                "risk_on": {"rows": 20, "friction_flip_count": 3},
                "risk_off": {"rows": 20, "friction_flip_count": 3},
                "neutral": {"rows": 20, "friction_flip_count": 2},
            },
            "exact_quote_marks_used": 0,
        }

        cv_report = {
            "selected_family": "linear",
            "folds": 3,
            "positive_pnl_auc_mean": 0.58,
            "breakeven_auc_mean": 0.57,
            "positive_pnl_brier_mean": 0.21,
            "breakeven_brier_mean": 0.18,
            "probability_buckets": {},
            "family_bakeoff": {
                "linear": {"positive_pnl_brier_mean": 0.21},
                "tree": {"positive_pnl_brier_mean": 0.23},
                "ensemble": {"positive_pnl_brier_mean": 0.22},
            },
            "by_segment": {
                "side": {"prob_positive_option_pnl": {}, "prob_exceeds_breakeven": {}},
                "regime": {"prob_positive_option_pnl": {}, "prob_exceeds_breakeven": {}},
            },
            "by_side": {"prob_positive_option_pnl": {}, "prob_exceeds_breakeven": {}},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "payoff.pkl"
            report_path = Path(tmpdir) / "report.json"
            card_path = Path(tmpdir) / "card.json"
            with (
                mock.patch.object(payoff_train_module, "load_examples", return_value=(examples, source_metadata)),
                mock.patch.object(payoff_train_module, "feature_matrix", return_value=np.ones((60, len(payoff_train_module.FEATURE_COLS)))),
                mock.patch.object(payoff_train_module, "_fit_bundle", return_value={"stub": True}),
                mock.patch.object(payoff_train_module, "_cv_report", return_value=cv_report),
                mock.patch.object(payoff_train_module.joblib, "dump"),
                mock.patch.object(payoff_train_module, "_sha256_file", return_value="abc123"),
            ):
                report = train(
                    [Path("output/option_outcomes_latest.json")],
                    output_model=model_path,
                    output_report=report_path,
                    output_model_card=card_path,
                    options_data_dir=None,
                    min_side_examples=20,
                )

        self.assertEqual(report["observability"]["dataset_summary"]["friction_flip_count"], 8)
        self.assertIn("by_regime", report["observability"])
        self.assertIn("by_regime_dataset", report["observability"])
        self.assertEqual(report["training_data"]["primary_artifact"], "option_outcome_dataset")
        self.assertEqual(report["selected_family"], "linear")
        self.assertEqual(report["cross_validation"]["selected_family"], "linear")
        self.assertRegex(report["trained_at"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("prob_no_trade", report["integrated_heads"])
        self.assertIn("path_decay_risk", report["integrated_heads"])
        self.assertIn("family_bakeoff", report["cross_validation"])
        self.assertEqual(report["promotion_gates"]["status"], "hold")
        self.assertTrue(report["promotion_gates"]["gates"]["friction_flip_observability"]["passed"])
        self.assertFalse(report["promotion_gates"]["gates"]["minimum_training_examples"]["passed"])
        self.assertFalse(report["promotion_gates"]["gates"]["regime_segment_coverage"]["passed"])
        self.assertEqual(report["calibration"]["brier"]["baseline_prob_positive_option_pnl"], 0.2222)


if __name__ == "__main__":
    unittest.main()
