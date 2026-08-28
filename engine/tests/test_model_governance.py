from __future__ import annotations

from datetime import UTC, datetime
import unittest

from engine.orographic.model_governance import build_model_governance_summary


class ModelGovernanceTests(unittest.TestCase):
    def test_summary_exposes_one_production_model_and_no_experiment_lanes(self) -> None:
        report = build_model_governance_summary(
            scan_health={
                "research": {"canonical_bundle_id": "bundle-1"},
                "labels": {
                    "trajectory_active_picks_last_run": 2,
                    "trajectory_marks_written_last_run": 2,
                    "trajectory_marks": 18,
                    "trajectory_scored_picks": 3,
                },
                "checks": [{"name": "trajectory_capture_health", "passed": True}],
            },
            now_utc=datetime(2026, 8, 27, 16, 0, tzinfo=UTC),
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["production_model"]["profile"], "production_v2")
        self.assertFalse(report["production_model"]["probability_sizing"])
        self.assertFalse(report["production_model"]["legacy_model_authority"])
        self.assertNotIn("challengers", report)
        self.assertEqual(report["summary"]["experiment_lanes"], 0)
        self.assertEqual(report["data_capture"]["canonical_bundle_id"], "bundle-1")

    def test_missing_trajectory_health_is_hold(self) -> None:
        report = build_model_governance_summary(scan_health={})
        self.assertEqual(report["data_capture"]["status"], "hold")
        self.assertEqual(report["status"], "pass")

    def test_failed_active_capture_blocks_overall_health(self) -> None:
        report = build_model_governance_summary(
            scan_health={
                "labels": {"trajectory_active_picks_last_run": 3, "trajectory_marks_written_last_run": 0},
                "checks": [{"name": "trajectory_capture_health", "passed": False}],
            }
        )
        self.assertEqual(report["data_capture"]["status"], "fail")
        self.assertEqual(report["status"], "fail")

    def test_latest_capture_health_wins(self) -> None:
        report = build_model_governance_summary(
            scan_health={
                "generated_at_utc": "2026-08-27T15:00:00Z",
                "labels": {"trajectory_active_picks_last_run": 0},
                "checks": [{"name": "trajectory_capture_health", "passed": True}],
            },
            capture_health={
                "generated_at_utc": "2026-08-27T15:15:00Z",
                "labels": {
                    "trajectory_active_picks_last_run": 2,
                    "trajectory_marks_written_last_run": 0,
                    "trajectory_quotes_missing_last_run": 2,
                },
                "checks": [{"name": "trajectory_capture_health", "passed": False}],
            },
        )
        self.assertEqual(report["data_capture"]["status"], "fail")
        self.assertEqual(report["data_capture"]["missing_quotes_last_run"], 2)


if __name__ == "__main__":
    unittest.main()
