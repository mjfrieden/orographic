from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_outcome_capture_health import build_outcome_capture_health


class OutcomeCaptureHealthTests(unittest.TestCase):
    def test_flags_late_scheduler_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prospective = self._ledger(root, "prospective.json", {})
            moonshot = self._ledger(root, "moonshot.json", {})
            report = build_outcome_capture_health(
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                token_configured=True,
                prospective_step_status="success",
                moonshot_step_status="success",
                evidence_step_status="success",
                scheduled_at_utc="2026-08-18T13:30:00Z",
                scheduler="cloudflare_cron",
                max_scheduler_delay_seconds=600,
                now_utc=datetime(2026, 8, 18, 13, 41, tzinfo=UTC),
            )

        failed = {row["name"] for row in report["failed_checks"]}
        self.assertIn("scheduler_delivery_fresh", failed)
        self.assertEqual(report["scheduler"]["delivery_delay_seconds"], 660.0)

    def test_scheduler_delivery_uses_workflow_start_not_health_build_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prospective = self._ledger(root, "prospective.json", {})
            moonshot = self._ledger(root, "moonshot.json", {})
            report = build_outcome_capture_health(
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                token_configured=True,
                prospective_step_status="success",
                moonshot_step_status="success",
                evidence_step_status="success",
                scheduled_at_utc="2026-08-21T14:10:45Z",
                workflow_started_at_utc="2026-08-21T14:20:24Z",
                scheduler="cloudflare_cron",
                max_scheduler_delay_seconds=600,
                now_utc=datetime(2026, 8, 21, 14, 21, 25, tzinfo=UTC),
            )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["scheduler"]["delivery_delay_seconds"], 579.0)
        self.assertEqual(
            report["scheduler"]["workflow_started_at_utc"],
            "2026-08-21T14:20:24Z",
        )

    def _ledger(self, root: Path, name: str, stats: dict) -> Path:
        path = root / name
        path.write_text(json.dumps({"last_capture_attempt_at_utc": "2026-08-12T15:15:00Z", "last_mark_summary": stats}))
        return path

    def test_fresh_marks_for_active_contracts_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prospective = self._ledger(root, "prospective.json", {
                "trajectory_active_picks": 2,
                "trajectory_marks_written": 2,
            })
            moonshot = self._ledger(root, "moonshot.json", {})
            report = build_outcome_capture_health(
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                token_configured=True,
                prospective_step_status="success",
                moonshot_step_status="success",
                evidence_step_status="success",
                now_utc=datetime(2026, 8, 12, 15, 16, tzinfo=UTC),
            )

        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["alert_required"])
        self.assertEqual(report["labels"]["trajectory_marks_written_last_run"], 2)

    def test_missing_active_quotes_raise_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prospective = self._ledger(root, "prospective.json", {
                "trajectory_active_picks": 2,
                "trajectory_marks_written": 0,
                "trajectory_quotes_missing": 2,
            })
            moonshot = self._ledger(root, "moonshot.json", {})
            report = build_outcome_capture_health(
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                token_configured=True,
                prospective_step_status="success",
                moonshot_step_status="success",
                evidence_step_status="success",
            )

        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["alert_required"])
        failed = {row["name"] for row in report["failed_checks"]}
        self.assertIn("trajectory_capture_health", failed)

    def test_isolated_stale_quote_degrades_without_paging(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prospective = self._ledger(root, "prospective.json", {
                "trajectory_active_picks": 61,
                "trajectory_marks_written": 60,
                "trajectory_quotes_stale": 1,
            })
            moonshot = self._ledger(root, "moonshot.json", {})
            report = build_outcome_capture_health(
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                token_configured=True,
                prospective_step_status="success",
                moonshot_step_status="success",
                evidence_step_status="success",
            )

        self.assertEqual(report["status"], "degraded")
        self.assertFalse(report["alert_required"])
        self.assertEqual(report["labels"]["trajectory_capture_ratio_last_run"], 0.9836)
        self.assertEqual(
            {row["name"] for row in report["failed_checks"]},
            {"trajectory_capture_health"},
        )
        self.assertEqual(report["alert_checks"], [])

    def test_capture_coverage_below_thirty_percent_still_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prospective = self._ledger(root, "prospective.json", {
                "trajectory_active_picks": 61,
                "trajectory_marks_written": 18,
                "trajectory_quotes_stale": 43,
            })
            moonshot = self._ledger(root, "moonshot.json", {})
            report = build_outcome_capture_health(
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                token_configured=True,
                prospective_step_status="success",
                moonshot_step_status="success",
                evidence_step_status="success",
            )

        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["alert_required"])
        self.assertEqual(
            {row["name"] for row in report["alert_checks"]},
            {"trajectory_capture_health"},
        )

    def test_capture_coverage_at_thirty_percent_still_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prospective = self._ledger(root, "prospective.json", {
                "trajectory_active_picks": 10,
                "trajectory_marks_written": 3,
                "trajectory_quotes_stale": 7,
            })
            moonshot = self._ledger(root, "moonshot.json", {})
            report = build_outcome_capture_health(
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                token_configured=True,
                prospective_step_status="success",
                moonshot_step_status="success",
                evidence_step_status="success",
            )

        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["alert_required"])

    def test_capture_coverage_above_thirty_percent_does_not_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prospective = self._ledger(root, "prospective.json", {
                "trajectory_active_picks": 100,
                "trajectory_marks_written": 31,
                "trajectory_quotes_stale": 69,
            })
            moonshot = self._ledger(root, "moonshot.json", {})
            report = build_outcome_capture_health(
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                token_configured=True,
                prospective_step_status="success",
                moonshot_step_status="success",
                evidence_step_status="success",
            )

        self.assertEqual(report["status"], "degraded")
        self.assertFalse(report["alert_required"])

    def test_missing_token_and_failed_step_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = build_outcome_capture_health(
                prospective_ledger=root / "missing-a.json",
                moonshot_ledger=root / "missing-b.json",
                token_configured=False,
                prospective_step_status="skipped",
                moonshot_step_status="failure",
                evidence_step_status="skipped",
            )

        failed = {row["name"] for row in report["failed_checks"]}
        self.assertEqual(report["status"], "failed")
        self.assertIn("tradier_capture_configured", failed)
        self.assertNotIn("capture_steps_completed", failed)


if __name__ == "__main__":
    unittest.main()
