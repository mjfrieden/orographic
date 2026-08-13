from __future__ import annotations

from datetime import UTC, datetime
import unittest

from engine.orographic.model_governance import build_model_governance_summary


class ModelGovernanceTests(unittest.TestCase):
    def test_summary_separates_capture_health_from_challenger_authority(self) -> None:
        report = build_model_governance_summary(
            scan_health={
                "labels": {
                    "trajectory_active_picks_last_run": 2,
                    "trajectory_marks_written_last_run": 2,
                    "trajectory_marks": 18,
                    "trajectory_scored_picks": 3,
                },
                "checks": [{"name": "trajectory_capture_health", "passed": True}],
            },
            scout_card={"status": "hold", "rows": 283, "paired_direction_counts": {"call": 8, "put": 3}},
            payoff_card={"training_examples": 409, "promotion_gates": {"status": "hold", "gates": {}}},
            payoff_evidence={"decision": "collecting_evidence", "coverage": {"resolved_recommendations": 12}},
            veto_evidence={"decision": "collecting_evidence", "coverage": {}, "readiness": {}},
            path_evidence={"status": "hold", "promotion_gates": {"minimum_exact_paths": {"actual": 4, "required_min": 150}}},
            now_utc=datetime(2026, 8, 12, 16, 0, tzinfo=UTC),
        )

        self.assertEqual(report["status"], "hold")
        self.assertEqual(report["data_capture"]["status"], "pass")
        self.assertFalse(report["live_authority"]["challenger_order_routing"])
        self.assertEqual(report["summary"]["held"], 4)
        self.assertEqual(report["challengers"][3]["progress"]["current"], 4)

    def test_missing_trajectory_contract_is_hold_not_false_pass(self) -> None:
        report = build_model_governance_summary(
            scan_health={},
            scout_card={},
            payoff_card={},
            payoff_evidence={},
            veto_evidence={},
            path_evidence={},
        )
        self.assertEqual(report["data_capture"]["status"], "hold")
        self.assertEqual(report["status"], "hold")

    def test_failed_active_capture_blocks_overall_readiness(self) -> None:
        report = build_model_governance_summary(
            scan_health={
                "labels": {"trajectory_active_picks_last_run": 3, "trajectory_marks_written_last_run": 0},
                "checks": [{"name": "trajectory_capture_health", "passed": False}],
            },
            scout_card={}, payoff_card={}, payoff_evidence={}, veto_evidence={}, path_evidence={},
        )
        self.assertEqual(report["data_capture"]["status"], "fail")
        self.assertEqual(report["status"], "fail")

    def test_latest_capture_health_overrides_stale_scan_capture_metrics(self) -> None:
        report = build_model_governance_summary(
            scan_health={
                "generated_at_utc": "2026-08-12T15:00:00Z",
                "labels": {"trajectory_active_picks_last_run": 0},
                "checks": [{"name": "trajectory_capture_health", "passed": True}],
            },
            capture_health={
                "generated_at_utc": "2026-08-12T15:15:00Z",
                "labels": {
                    "trajectory_active_picks_last_run": 2,
                    "trajectory_marks_written_last_run": 0,
                    "trajectory_quotes_missing_last_run": 2,
                },
                "checks": [{"name": "trajectory_capture_health", "passed": False}],
            },
            scout_card={}, payoff_card={}, payoff_evidence={}, veto_evidence={}, path_evidence={},
        )

        self.assertEqual(report["data_capture"]["status"], "fail")
        self.assertEqual(report["data_capture"]["active_contracts_last_run"], 2)
        self.assertEqual(report["data_capture"]["missing_quotes_last_run"], 2)

    def test_newer_scan_health_wins_over_stale_capture_health(self) -> None:
        report = build_model_governance_summary(
            scan_health={
                "generated_at_utc": "2026-08-12T15:30:00Z",
                "labels": {"trajectory_active_picks_last_run": 1, "trajectory_marks_written_last_run": 1},
                "checks": [{"name": "trajectory_capture_health", "passed": True}],
            },
            capture_health={
                "generated_at_utc": "2026-08-12T15:15:00Z",
                "labels": {"trajectory_active_picks_last_run": 2, "trajectory_marks_written_last_run": 0},
                "checks": [{"name": "trajectory_capture_health", "passed": False}],
            },
            scout_card={}, payoff_card={}, payoff_evidence={}, veto_evidence={}, path_evidence={},
        )

        self.assertEqual(report["data_capture"]["status"], "pass")
        self.assertEqual(report["data_capture"]["active_contracts_last_run"], 1)


if __name__ == "__main__":
    unittest.main()
