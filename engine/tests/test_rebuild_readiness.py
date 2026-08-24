from __future__ import annotations

import unittest

from engine.orographic.rebuild_readiness import build_rebuild_readiness


class RebuildReadinessTests(unittest.TestCase):
    def test_fails_closed_when_current_evidence_is_incomplete(self) -> None:
        result = build_rebuild_readiness(
            {"coverage": {"complete_explicit_pairs": 0}, "fold_frozen_evaluation_plan": {"ready_folds": 0}},
            {"summary": {"live_by_policy": {"standing_limit_25": {"coverage_pct": 0.2}}}},
            {"decision": "not_ready"},
            {"research": {"evidence_lifecycle": {"current_model_cohort": {"resolved_recommendations": 0}}}},
        )

        self.assertFalse(result["production_model_change_allowed"])
        self.assertEqual(result["status"], "hold_collecting_executable_evidence")
        self.assertEqual(result["preferred_training_source"]["interface"], "shared research mart")

    def test_all_registered_gates_are_required(self) -> None:
        mart_consumer = {
            "artifact": "orographic_shared_mart_consumer_bundle",
            "status": "ready",
            "production_authority": "observation_only_never_used_for_routing",
            "source_systems": ["cirrus", "orographic"],
            "views": {
                name: {"rows": 1}
                for name in (
                    "orographic_training_v1",
                    "orographic_execution_quality_v1",
                    "orographic_exit_replay_v1",
                    "cirrus_orographic_disagreement_v1",
                    "orographic_model_monitoring_v1",
                )
            },
        }
        result = build_rebuild_readiness(
            {"coverage": {"complete_explicit_pairs": 150}, "fold_frozen_evaluation_plan": {"ready_folds": 3}},
            {"summary": {"live_by_policy": {"standing_limit_25": {"coverage_pct": 0.65}}}},
            {"decision": "ready"},
            {"research": {"evidence_lifecycle": {"current_model_cohort": {"resolved_recommendations": 100}}}},
            mart_consumer,
        )

        self.assertTrue(result["production_model_change_allowed"])
        self.assertEqual(result["status"], "ready_for_fold_frozen_challenger")
        self.assertEqual(
            result["preferred_training_source"]["tables"],
            ["recommendations", "execution_outcomes", "option_quotes", "feature_snapshots"],
        )


if __name__ == "__main__":
    unittest.main()
