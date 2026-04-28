from __future__ import annotations

import unittest

import pandas as pd

from engine.train_scout_model import (
    _balanced_sample_weights,
    _class_balance_report,
    _directional_option_training_frame,
    _infer_regime_labels,
    _merge_option_outcome_labels,
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

    def test_directional_option_training_frame_uses_all_labeled_rows_and_builds_binary_label(self) -> None:
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

        self.assertEqual(len(directional), 3)
        self.assertEqual(directional["primary_label"].tolist(), [1, 0, 0])
        self.assertAlmostEqual(float(directional.iloc[0]["primary_outcome_value"]), 0.32, places=6)
        self.assertAlmostEqual(float(directional.iloc[1]["primary_outcome_value"]), -0.23, places=6)
        self.assertAlmostEqual(float(directional.iloc[2]["primary_outcome_value"]), -0.01, places=6)

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


if __name__ == "__main__":
    unittest.main()
