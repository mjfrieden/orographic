from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from engine.orographic.promotion_comparison import build_promotion_comparison


def _pick(lane: str, timestamp: str, ask: float, bid: float, probability: float) -> dict:
    return {
        "lane": lane,
        "run_generated_at_utc": timestamp,
        "contract_symbol": f"{lane}-{timestamp}",
        "emission_quote": {"ask": ask, "mid": ask - 0.05},
        "scores": {"prob_positive_option_pnl": probability},
        "outcomes": {"fixed_exit_marks": {"friday_close": {"bid": bid, "mark": bid + 0.05}}},
    }


class PromotionComparisonTests(unittest.TestCase):
    def test_replays_costs_calibration_and_canonical_windows(self) -> None:
        entries = []
        for timestamp in ("2025-07-01T15:00:00+00:00", "2026-06-01T15:00:00+00:00", "2026-07-01T15:00:00+00:00"):
            entries.append({
                "run_generated_at_utc": timestamp,
                "picks": [
                    _pick("live", timestamp, 1.00, 0.80, 0.70),
                    _pick("shadow", timestamp, 1.00, 1.30, 0.70),
                ],
            })
        artifact = build_promotion_comparison(
            {"updated_at_utc": "2026-07-01T15:00:00+00:00", "entries": entries},
            {"updated_at_utc": "2026-07-01T15:00:00+00:00", "aggregate": {"runs": 40, "disagreements": 50}},
            as_of=datetime(2026, 7, 1, 15, tzinfo=timezone.utc),
        )

        self.assertEqual([row["window"] for row in artifact["windows"]], ["3_month", "6_month", "12_month"])
        three_month = artifact["windows"][0]
        self.assertTrue(three_month["coverage_complete"])
        self.assertEqual(three_month["active"]["net_pnl"], -40.0)
        self.assertEqual(three_month["shadow"]["net_pnl"], 60.0)
        self.assertEqual(three_month["shadow"]["spread_cost"], 20.0)
        self.assertLess(three_month["shadow"]["calibration"]["brier_score"], three_month["active"]["calibration"]["brier_score"])
        self.assertEqual(artifact["source_summary"]["shadow_disagreements"], 50)
        self.assertEqual(artifact["decision"], "not_ready")

    def test_ignores_unmarked_and_non_comparison_lanes(self) -> None:
        artifact = build_promotion_comparison(
            {"entries": [{"run_generated_at_utc": "2026-07-01T00:00:00+00:00", "picks": [
                {"lane": "live", "emission_quote": {"ask": 1.0}, "outcomes": {}},
                _pick("council_holdout", "2026-07-01T00:00:00+00:00", 1.0, 2.0, 0.5),
            ]}]},
            {"aggregate": {}},
            as_of=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(artifact["source_summary"]["eligible_marked_recommendations"], 0)
        self.assertTrue(all(row["status"] == "insufficient_data" for row in artifact["windows"]))

    def test_repeated_intraday_contracts_are_cluster_adjusted(self) -> None:
        entries = [{
            "run_generated_at_utc": "2026-06-01T15:00:00+00:00",
            "picks": [
                _pick("live", "2026-06-01T15:00:00+00:00", 1.0, 0.8, 0.7),
                _pick("live", "2026-06-01T16:00:00+00:00", 1.0, 0.8, 0.7),
                _pick("shadow", "2026-06-01T15:00:00+00:00", 1.0, 1.2, 0.7),
                _pick("shadow", "2026-06-01T16:00:00+00:00", 1.0, 1.2, 0.7),
            ],
        }]
        # Force both scans in each lane to represent the same contract.
        for pick in entries[0]["picks"]:
            pick["contract_symbol"] = f"{pick['lane']}-same-contract"
        artifact = build_promotion_comparison(
            {"entries": entries},
            {"aggregate": {}},
            as_of=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        clustered = artifact["windows"][0]["cluster_adjusted"]
        self.assertEqual(clustered["raw_recommendations"], 4)
        self.assertEqual(clustered["independent_daily_contracts"], 2)
        self.assertEqual(clustered["active"]["trades"], 1)
        self.assertEqual(clustered["shadow"]["trades"], 1)
        self.assertFalse(artifact["windows"][0]["checks"]["minimum_evidence"])

    def test_less_negative_challenger_does_not_count_as_profitable(self) -> None:
        timestamp = "2026-06-01T15:00:00+00:00"
        artifact = build_promotion_comparison(
            {"entries": [{"run_generated_at_utc": timestamp, "picks": [
                _pick("live", timestamp, 1.0, 0.70, 0.7),
                _pick("shadow", timestamp, 1.0, 0.80, 0.7),
            ]}]},
            {"aggregate": {}},
            as_of=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        checks = artifact["windows"][0]["checks"]
        self.assertTrue(checks["after_cost_pnl_lift"])
        self.assertFalse(checks["absolute_profitability"])
        self.assertEqual(artifact["schema_version"], 2)

    def test_paired_day_bootstrap_requires_repeatable_positive_lift(self) -> None:
        entries = [{
            "run_generated_at_utc": "2026-01-01T15:00:00+00:00",
            "picks": [
                _pick("live", "2026-01-01T15:00:00+00:00", 1.0, 0.8, 0.7),
                _pick("shadow", "2026-01-01T15:00:00+00:00", 1.0, 1.2, 0.7),
            ],
        }]
        start = datetime(2026, 5, 1, 15, tzinfo=timezone.utc)
        for offset in range(35):
            timestamp = (start + timedelta(days=offset)).isoformat()
            entries.append({
                "run_generated_at_utc": timestamp,
                "picks": [
                    _pick("live", timestamp, 1.0, 0.8, 0.7),
                    _pick("shadow", timestamp, 1.0, 1.2, 0.7),
                ],
            })
        artifact = build_promotion_comparison(
            {"entries": entries},
            {"aggregate": {}},
            as_of=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        window = artifact["windows"][0]
        inference = window["paired_day_inference"]
        self.assertEqual(inference["paired_days"], 35)
        self.assertGreater(inference["confidence_interval_95"]["lower"], 0)
        self.assertEqual(inference["probability_positive"], 1.0)
        self.assertTrue(window["checks"]["uncertainty_robustness"])
        self.assertTrue(window["checks"]["absolute_profitability"])

    def test_window_coverage_must_be_complete_in_both_lanes(self) -> None:
        artifact = build_promotion_comparison(
            {"entries": [
                {
                    "run_generated_at_utc": "2026-01-01T15:00:00+00:00",
                    "picks": [_pick("shadow", "2026-01-01T15:00:00+00:00", 1.0, 1.2, 0.7)],
                },
                {
                    "run_generated_at_utc": "2026-06-01T15:00:00+00:00",
                    "picks": [
                        _pick("live", "2026-06-01T15:00:00+00:00", 1.0, 0.8, 0.7),
                        _pick("shadow", "2026-06-01T15:00:00+00:00", 1.0, 1.2, 0.7),
                    ],
                },
            ]},
            {"aggregate": {}},
            as_of=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        window = artifact["windows"][0]
        self.assertFalse(window["coverage_complete"])
        self.assertFalse(window["coverage_by_lane"]["active"])
        self.assertTrue(window["coverage_by_lane"]["shadow"])
        self.assertEqual(window["status"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()
