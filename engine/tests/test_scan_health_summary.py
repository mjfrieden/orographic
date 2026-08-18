from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_scan_health_summary import _ledger_health, build_scan_health_summary


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class ScanHealthSummaryTests(unittest.TestCase):
    def test_ledger_health_exposes_trajectory_capture_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = _write_json(Path(tmpdir) / "ledger.json", {
                "outcome_summary": {
                    "trajectory_scored_picks": 3,
                    "trajectory_marks": 12,
                    "trajectory_picks_with_4_marks": 2,
                },
                "last_mark_summary": {
                    "trajectory_active_picks": 3,
                    "trajectory_marks_written": 3,
                    "trajectory_quotes_missing": 0,
                    "trajectory_quotes_stale": 0,
                },
            })
            health = _ledger_health(ledger)

        self.assertEqual(health["trajectory_scored_picks"], 3)
        self.assertEqual(health["trajectory_marks"], 12)
        self.assertEqual(health["trajectory_picks_with_4_marks"], 2)
        self.assertEqual(health["trajectory_active_picks_last_run"], 3)
        self.assertEqual(health["trajectory_marks_written_last_run"], 3)

    def test_build_scan_health_summary_passes_for_fresh_labeled_run(self) -> None:
        now = datetime(2026, 6, 23, 22, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot = _write_json(
                root / "latest_run.json",
                {
                    "generated_at_utc": (now - timedelta(minutes=20)).isoformat(),
                    "regime": {"mode": "risk_off", "bias": -0.2},
                    "summary": {
                        "scout_signal_count": 48,
                        "pre_forge_signal_count": 11,
                        "forge_candidate_count": 11,
                    },
                    "council": {
                        "abstain": True,
                        "summary": {"live_count": 0, "shadow_count": 3},
                    },
                },
            )
            prospective = _write_json(
                root / "prospective.json",
                {
                    "updated_at_utc": now.isoformat(),
                    "aggregate": {"runs": 3},
                    "outcome_summary": {
                        "picks": 20,
                        "pending": 2,
                        "partial": 3,
                        "complete": 15,
                        "with_any_mark": 18,
                        "with_all_fixed_marks": 15,
                        "missing_outcome_quotes": 0,
                        "capture_policy_v2_picks": 20,
                        "capture_windows_valid": 60,
                        "capture_windows_quote_missing": 0,
                        "capture_windows_missed": 0,
                    },
                    "last_mark_summary": {"marks_written": 4, "quotes_missing": 0},
                },
            )
            moonshot = _write_json(
                root / "moonshot.json",
                {
                    "updated_at_utc": now.isoformat(),
                    "aggregate": {"runs": 2},
                    "outcome_summary": {
                        "picks": 3,
                        "pending": 1,
                        "partial": 1,
                        "complete": 1,
                        "with_any_mark": 2,
                        "with_all_fixed_marks": 1,
                        "missing_outcome_quotes": 0,
                        "capture_policy_v2_picks": 3,
                        "capture_windows_valid": 5,
                        "capture_windows_quote_missing": 0,
                        "capture_windows_missed": 0,
                    },
                    "last_mark_summary": {"marks_written": 1, "quotes_missing": 0},
                },
            )
            audit = _write_json(
                root / "audit.json",
                {
                    "status": "passed",
                    "summary": {"recommendation_dataset_rows": 20, "moonshot_dataset_rows": 3},
                },
            )
            archive = _write_json(
                root / "archive.json",
                {"summary": {"rows_archived": 1200, "symbols_archived": 24}},
            )
            recommendations = _write_json(root / "recommendations.json", [{} for _ in range(20)])
            moonshots = _write_json(root / "moonshots.json", [{} for _ in range(3)])
            combined = _write_json(root / "combined.json", [{} for _ in range(23)])
            canonical = _write_json(
                root / "evidence_manifest.json",
                {
                    "bundle_id": "bundle-1",
                    "evidence": {
                        "cumulative_inventory": {"primary": {"recommendations": 100}},
                        "training_eligible": {"deduplicated_recommendation_outcomes": 20},
                        "current_model_cohort": {"resolved_recommendations": 7},
                    },
                    "checks": {
                        "recommendations_unique": True,
                        "quotes_unique": True,
                        "immutable_labels_preserved": True,
                        "inputs_readable": True,
                    },
                },
            )
            cirrus = _write_json(
                root / "canonical_materialization.json",
                {
                    "artifact": "cirrus_canonical_archive_materialization",
                    "schema_version": 2,
                    "status": "passed",
                    "canonical_bundle_id": "bundle-1",
                    "rows": 0,
                    "expected_rows": 0,
                    "partitions": 0,
                    "expected_partitions": 0,
                    "latest_quote_date": None,
                    "checks": {
                        "canonical_bundle_valid": True,
                        "row_count_matches": True,
                        "partition_count_matches": True,
                        "stale_partitions_replaced": True,
                    },
                },
            )
            output = root / "health.json"

            report = build_scan_health_summary(
                snapshot=snapshot,
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                research_audit=audit,
                archive_manifest=archive,
                recommendation_dataset=recommendations,
                moonshot_dataset=moonshots,
                combined_dataset=combined,
                canonical_manifest=canonical,
                cirrus_materialization=cirrus,
                output=output,
                now_utc=now,
                r2_status="success",
                dashboard_push_status="success",
                dashboard_deploy_status="success",
            )
            output_exists = output.exists()

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["labels"]["marked_picks"], 20)
        self.assertEqual(report["labels"]["quote_coverage_pct"], 1.0)
        self.assertEqual(report["labels"]["capture_policy_v2_picks"], 23)
        self.assertEqual(report["research"]["canonical_bundle_id"], "bundle-1")
        checks = {check["name"]: check for check in report["checks"]}
        self.assertTrue(checks["cirrus_materialization_valid"]["passed"])
        self.assertEqual(
            report["research"]["evidence_lifecycle"]["current_model_cohort"][
                "resolved_recommendations"
            ],
            7,
        )
        self.assertTrue(output_exists)

    def test_build_scan_health_summary_accepts_explicit_zero_signal_abstention(self) -> None:
        now = datetime(2026, 8, 11, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot = _write_json(
                root / "latest_run.json",
                {
                    "generated_at_utc": (now - timedelta(minutes=5)).isoformat(),
                    "regime": {"mode": "risk_on"},
                    "summary": {"scout_signal_count": 0, "forge_candidate_count": 0},
                    "council": {
                        "abstain": True,
                        "summary": {"live_count": 0, "shadow_count": 0},
                    },
                },
            )
            ledger = _write_json(
                root / "ledger.json",
                {
                    "aggregate": {"runs": 1},
                    "outcome_summary": {
                        "picks": 1,
                        "with_any_mark": 1,
                        "missing_outcome_quotes": 0,
                        "capture_policy_v2_picks": 0,
                    },
                },
            )
            audit = _write_json(root / "audit.json", {"status": "passed", "summary": {}})
            archive = _write_json(
                root / "archive.json",
                {"summary": {"rows_archived": 10, "symbols_archived": 1}},
            )
            recommendations = _write_json(root / "recommendations.json", [{}])
            moonshots = _write_json(root / "moonshots.json", [])
            combined = _write_json(root / "combined.json", [{}])

            report = build_scan_health_summary(
                snapshot=snapshot,
                prospective_ledger=ledger,
                moonshot_ledger=ledger,
                research_audit=audit,
                archive_manifest=archive,
                recommendation_dataset=recommendations,
                moonshot_dataset=moonshots,
                combined_dataset=combined,
                now_utc=now,
                r2_status="success",
                dashboard_push_status="success",
                dashboard_deploy_status="success",
            )

        self.assertEqual(report["status"], "passed")
        checks = {check["name"]: check for check in report["checks"]}
        self.assertTrue(checks["scan_emitted_scout_signals"]["accepted_abstention"])
        self.assertTrue(checks["strict_capture_policy_active"]["awaiting_next_pick"])

    def test_build_scan_health_summary_flags_stale_snapshot(self) -> None:
        now = datetime(2026, 6, 23, 22, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot = _write_json(
                root / "latest_run.json",
                {
                    "generated_at_utc": (now - timedelta(hours=9)).isoformat(),
                    "regime": {"mode": "risk_on"},
                    "summary": {"scout_signal_count": 10, "forge_candidate_count": 3},
                    "council": {"summary": {"live_count": 0, "shadow_count": 3}},
                },
            )
            ledger = _write_json(
                root / "ledger.json",
                {
                    "aggregate": {"runs": 1},
                    "outcome_summary": {
                        "picks": 1,
                        "with_any_mark": 1,
                        "missing_outcome_quotes": 0,
                    },
                },
            )
            audit = _write_json(root / "audit.json", {"status": "passed", "summary": {}})
            archive = _write_json(root / "archive.json", {"summary": {"rows_archived": 10, "symbols_archived": 1}})
            rows = _write_json(root / "rows.json", [{}])

            report = build_scan_health_summary(
                snapshot=snapshot,
                prospective_ledger=ledger,
                moonshot_ledger=ledger,
                research_audit=audit,
                archive_manifest=archive,
                recommendation_dataset=rows,
                moonshot_dataset=rows,
                combined_dataset=_write_json(root / "combined.json", [{}, {}]),
                now_utc=now,
                r2_status="success",
                dashboard_push_status="success",
                dashboard_deploy_status="success",
                max_run_age_minutes=240,
            )

        self.assertEqual(report["status"], "failed")
        self.assertIn("snapshot_is_fresh", {check["name"] for check in report["failed_checks"]})

    def test_historical_capture_debt_does_not_fail_a_clean_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_json(Path(tmpdir) / "ledger.json", {
                "outcome_summary": {
                    "capture_windows_missed": 31,
                    "capture_windows_quote_missing": 77,
                    "capture_windows_stale_quote": 4,
                },
                "last_mark_summary": {
                    "capture_windows_newly_missed": 0,
                    "capture_windows_quote_missing": 0,
                    "capture_windows_stale_quote": 0,
                },
            })
            health = _ledger_health(path)

        self.assertEqual(health["capture_windows_missed"], 31)
        self.assertEqual(health["capture_windows_newly_missed_last_run"], 0)
        self.assertEqual(health["capture_windows_quote_missing_last_run"], 0)


if __name__ == "__main__":
    unittest.main()
