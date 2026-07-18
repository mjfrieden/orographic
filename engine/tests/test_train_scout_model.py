from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from engine.train_scout_model import (
    _balanced_sample_weights,
    _class_balance_report,
    _directional_option_training_frame,
    _event_feature_activation_report,
    _infer_regime_labels,
    _load_option_outcome_labels,
    _merge_option_outcome_labels,
    _selected_event_feature_columns,
    build_feature_matrix,
)


class TrainScoutModelTests(unittest.TestCase):
    def test_merge_option_outcome_labels_aligns_symbol_and_date(self) -> None:
        combined = pd.DataFrame(
            {
                "mom_5d": [0.1, -0.1],
                "symbol": ["AAA", "BBB"],
            },
            index=pd.to_datetime(["2026-04-21", "2026-04-22"]),
        )
        option_labels = pd.DataFrame(
            [
                {"symbol": "AAA", "date": pd.Timestamp("2026-04-21"), "side_label": "call_edge"},
                {"symbol": "BBB", "date": pd.Timestamp("2026-04-23"), "side_label": "put_edge"},
            ]
        )

        merged = _merge_option_outcome_labels(combined, option_labels)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged.iloc[0]["symbol"], "AAA")
        self.assertEqual(merged.iloc[0]["side_label"], "call_edge")

    def test_load_option_outcome_labels_accepts_canonical_dataset_artifact(self) -> None:
        payload = {
            "artifact": "option_outcome_dataset",
            "rows": [
                {
                    "symbol": "AAA",
                    "option_type": "call",
                    "entry_date": "2026-04-21",
                    "exit_date": "2026-04-25",
                    "pnl_pct": 0.2,
                    "pnl": 20.0,
                },
                {
                    "symbol": "AAA",
                    "option_type": "put",
                    "entry_date": "2026-04-21",
                    "exit_date": "2026-04-25",
                    "pnl_pct": -0.1,
                    "pnl": -10.0,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "option_outcomes.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            labeled, metadata = _load_option_outcome_labels([path])

        self.assertEqual(metadata["trade_rows"], 2)
        self.assertEqual(metadata["labeled_symbol_dates"], 1)
        self.assertEqual(labeled.iloc[0]["side_label"], "call_edge")

    def test_directional_option_training_frame_filters_no_trade_rows_and_builds_binary_label(self) -> None:
        merged = pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "date": pd.Timestamp("2026-04-21"),
                    "side_label": "call_edge",
                    "call_avg_pnl_pct": 0.22,
                    "put_avg_pnl_pct": -0.10,
                },
                {
                    "symbol": "BBB",
                    "date": pd.Timestamp("2026-04-22"),
                    "side_label": "put_edge",
                    "call_avg_pnl_pct": -0.05,
                    "put_avg_pnl_pct": 0.18,
                },
                {
                    "symbol": "CCC",
                    "date": pd.Timestamp("2026-04-23"),
                    "side_label": "no_trade",
                    "call_avg_pnl_pct": -0.03,
                    "put_avg_pnl_pct": -0.02,
                },
            ]
        )

        directional = _directional_option_training_frame(merged)

        self.assertEqual(len(directional), 2)
        self.assertEqual(directional["primary_label"].tolist(), [1, 0])
        self.assertAlmostEqual(float(directional.iloc[0]["primary_outcome_value"]), 0.32, places=6)
        self.assertAlmostEqual(float(directional.iloc[1]["primary_outcome_value"]), -0.23, places=6)

    def test_infer_regime_labels_uses_spy_context(self) -> None:
        frame = pd.DataFrame(
            {
                "spy_mom_20d": [0.03, 0.0, -0.03],
            }
        )

        regimes = _infer_regime_labels(frame)

        self.assertEqual(regimes.tolist(), ["risk_on", "neutral", "risk_off"])

    def test_balanced_sample_weights_boost_minority_side_and_regime(self) -> None:
        y = pd.Series([1, 1, 1, 0]).to_numpy(dtype=int)
        regimes = pd.Series(["risk_on", "risk_on", "neutral", "risk_off"]).to_numpy(dtype=object)

        weights = _balanced_sample_weights(y, regimes)

        self.assertEqual(len(weights), 4)
        self.assertGreater(weights[3], weights[0])
        self.assertAlmostEqual(float(weights.mean()), 1.0, places=6)

    def test_class_balance_report_returns_minority_share_and_regime_mix(self) -> None:
        y = pd.Series([1, 1, 0, 0]).to_numpy(dtype=int)
        regimes = pd.Series(["risk_on", "neutral", "risk_off", "risk_off"]).to_numpy(dtype=object)

        report = _class_balance_report(y, regimes, class_names={0: "put_edge", 1: "call_edge"})

        self.assertEqual(report["class_counts"], {"put_edge": 2, "call_edge": 2})
        self.assertEqual(report["minority_share"], 0.5)
        self.assertEqual(report["class_regime_counts"]["put_edge"]["risk_off"], 2)

    def test_build_feature_matrix_includes_event_features_without_dropping_rows(self) -> None:
        rows = 90
        index = pd.date_range("2026-01-01", periods=rows, freq="D")
        close = pd.Series(
            [100 + i * 0.1 + (0.6 if i % 6 < 3 else -0.4) for i in range(rows)],
            dtype=float,
            index=index,
        )
        df = pd.DataFrame(
            {
                "Close": close,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Volume": pd.Series([1_000_000 + i * 1000 for i in range(rows)], dtype=float, index=index),
            },
            index=index,
        )
        event_feature_frame = pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "date": pd.Timestamp("2026-03-20"),
                    "fnspid_news_volume_1d": 7.0,
                    "edt_event_intensity": 0.6,
                    "dataset_tags": "fnspid,edt",
                }
            ]
        )

        features = build_feature_matrix(
            df,
            symbol="AAA",
            event_feature_frame=event_feature_frame,
        )

        self.assertIn("fnspid_news_volume_1d", features.columns)
        self.assertIn("edt_event_intensity", features.columns)
        self.assertGreater(len(features), 0)
        self.assertEqual(float(features.loc[pd.Timestamp("2026-03-20"), "fnspid_news_volume_1d"]), 7.0)
        self.assertEqual(float(features.iloc[-1]["edt_event_intensity"]), 0.0)

    def test_event_feature_activation_report_counts_nonzero_rows(self) -> None:
        frame = pd.DataFrame(
            {
                "fnspid_news_volume_1d": [0.0, 2.0, 0.0],
                "mirai_macro_shock_score": [0.0, 0.0, 0.5],
            }
        )

        report = _event_feature_activation_report(
            frame,
            ["fnspid_news_volume_1d", "mirai_macro_shock_score"],
        )

        self.assertEqual(report["rows_with_any_event_feature"], 2)
        self.assertEqual(report["row_coverage_pct"], 0.6667)
        self.assertEqual(report["by_feature"]["fnspid_news_volume_1d"]["nonzero_rows"], 1)
        self.assertEqual(report["by_feature"]["mirai_macro_shock_score"]["nonzero_rows"], 1)

    def test_selected_event_feature_columns_prefers_curated_sec_slice(self) -> None:
        selected = _selected_event_feature_columns(
            [
                "mom_5d",
                "fnspid_news_volume_1d",
                "sec_filing_count_1d",
                "sec_insider_count",
                "sec_signal_count_1d",
                "sec_signal_ratio",
                "sec_8k_flag",
                "narrative_attention_acceleration_3d",
                "narrative_confirmation_score_1d",
                "narrative_hype_pressure",
            ]
        )

        self.assertIn("fnspid_news_volume_1d", selected)
        self.assertIn("sec_signal_count_1d", selected)
        self.assertIn("sec_signal_ratio", selected)
        self.assertIn("sec_8k_flag", selected)
        self.assertNotIn("sec_filing_count_1d", selected)
        self.assertNotIn("sec_insider_count", selected)
        self.assertIn("narrative_attention_acceleration_3d", selected)
        self.assertIn("narrative_confirmation_score_1d", selected)
        self.assertNotIn("narrative_hype_pressure", selected)


if __name__ == "__main__":
    unittest.main()
