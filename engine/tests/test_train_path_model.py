from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from engine import train_path_model as path_train_module


class TrainPathModelTests(unittest.TestCase):
    def test_train_report_includes_shadow_activation_and_family_bakeoff(self) -> None:
        examples = []
        for idx in range(60):
            side = "call" if idx % 2 == 0 else "put"
            regime_bucket = "risk_on" if idx < 20 else ("risk_off" if idx < 40 else "neutral")
            candidate = path_train_module.ContractCandidate(
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
                realized_vol_20d=0.22,
                atr_pct_14d=0.02,
                premium_pct_of_spot=0.012,
                vrp_gap=0.08,
                expected_edge_after_friction_pct=0.12,
                sentinel_holding_window_fit=0.7,
                sentinel_confidence=0.6,
                sentinel_call_relevance=0.8 if side == "call" else 0.2,
                sentinel_put_relevance=0.8 if side == "put" else 0.2,
                sentinel_no_trade_relevance=0.05,
                sentinel_spot_effect=1.0,
                sentinel_iv_effect=0.0,
            )
            examples.append(
                path_train_module.TradeExample(
                    candidate=candidate,
                    entry_date=path_train_module.date(2026, 1 + (idx % 3), 3 + (idx % 20)),
                    exit_date=path_train_module.date(2026, 1 + (idx % 3), 10 + (idx % 10)),
                    entry_spot=100.0,
                    exit_spot=102.0,
                    regime_bucket=regime_bucket,
                    pnl_pct=0.2 if idx % 3 else -0.1,
                    prob_positive_option_pnl=0 if idx % 3 == 0 else 1,
                    expected_option_return_pct=0.2 if idx % 3 else -0.1,
                    prob_exceeds_breakeven=0 if idx % 4 == 0 else 1,
                    max_favorable_excursion_before_expiry=0.35 if idx % 2 == 0 else 0.12,
                    adverse_excursion_risk=-0.18 if idx % 2 == 0 else -0.08,
                )
            )

        source_metadata = {
            "primary_training_source_artifact": "option_outcome_dataset",
            "primary_training_source_files": ["output/option_outcomes_latest.json"],
            "canonical_dataset_files": ["output/option_outcomes_latest.json"],
            "legacy_result_files": [],
            "input_artifact_by_file": {"output/option_outcomes_latest.json": "option_outcome_dataset"},
            "regime_dataset_summary": {
                "risk_on": {"rows": 20},
                "risk_off": {"rows": 20},
                "neutral": {"rows": 20},
            },
            "exact_quote_marks_used": 30,
        }

        cv_report = {
            "selected_family": "linear",
            "folds": 3,
            "early_take_profit_auc_mean": 0.58,
            "early_take_profit_brier_mean": 0.21,
            "path_expected_mfe_mae_mean": 0.11,
            "path_decay_risk_mae_mean": 0.07,
            "probability_buckets": {},
            "family_bakeoff": {
                "linear": {"early_take_profit_brier_mean": 0.21},
                "tree": {"early_take_profit_brier_mean": 0.23},
                "ensemble": {"early_take_profit_brier_mean": 0.22},
            },
            "by_segment": {
                "side": {"path_early_profit_take_prob": {}},
                "regime": {"path_early_profit_take_prob": {}},
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "path.pkl"
            report_path = Path(tmpdir) / "report.json"
            card_path = Path(tmpdir) / "card.json"
            with (
                mock.patch.object(path_train_module, "load_examples", return_value=(examples, source_metadata)),
                mock.patch.object(path_train_module, "feature_matrix", return_value=np.ones((60, len(path_train_module.FEATURE_COLS)))),
                mock.patch.object(path_train_module, "_fit_bundle", return_value={"stub": True}),
                mock.patch.object(path_train_module, "_cv_report", return_value=cv_report),
                mock.patch.object(path_train_module.joblib, "dump"),
                mock.patch.object(path_train_module, "_sha256_file", return_value="abc123"),
            ):
                report = path_train_module.train(
                    [Path("output/option_outcomes_latest.json")],
                    output_model=model_path,
                    output_report=report_path,
                    output_model_card=card_path,
                    options_data_dir=None,
                    min_side_examples=20,
                )

        self.assertEqual(report["artifact"], "path_model")
        self.assertEqual(report["selected_family"], "linear")
        self.assertEqual(report["cross_validation"]["selected_family"], "linear")
        self.assertIn("family_bakeoff", report["cross_validation"])
        self.assertEqual(report["activation_policy"]["default"], "shadow")
        self.assertEqual(report["promotion_gates"]["status"], "hold")


if __name__ == "__main__":
    unittest.main()
