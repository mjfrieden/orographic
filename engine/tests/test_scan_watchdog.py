from __future__ import annotations

from datetime import UTC, datetime
import unittest

from scripts.orographic_scan_watchdog import decide_watchdog_action, expected_slot_start


class ScanWatchdogTests(unittest.TestCase):
    def test_expected_slot_start_returns_latest_weekday_slot(self) -> None:
        now = datetime(2026, 6, 29, 14, 25, tzinfo=UTC)
        slot = expected_slot_start(now)
        self.assertEqual(slot, datetime(2026, 6, 29, 14, 7, tzinfo=UTC))

    def test_watchdog_dispatches_when_slot_missing(self) -> None:
        now = datetime(2026, 6, 29, 14, 30, tzinfo=UTC)
        decision = decide_watchdog_action(now_utc=now, runs=[], grace_minutes=15)
        self.assertTrue(decision["should_dispatch"])
        self.assertEqual(decision["reason"], "missing_scan_run_for_slot")

    def test_watchdog_skips_when_run_exists_for_slot(self) -> None:
        now = datetime(2026, 6, 29, 14, 30, tzinfo=UTC)
        runs = [
            {
                "created_at": "2026-06-29T14:12:00Z",
                "head_branch": "main",
                "event": "schedule",
            }
        ]
        decision = decide_watchdog_action(now_utc=now, runs=runs, grace_minutes=15)
        self.assertFalse(decision["should_dispatch"])
        self.assertEqual(decision["reason"], "scan_run_present_for_slot")

    def test_watchdog_skips_within_grace_window(self) -> None:
        now = datetime(2026, 6, 29, 14, 15, tzinfo=UTC)
        decision = decide_watchdog_action(now_utc=now, runs=[], grace_minutes=15)
        self.assertFalse(decision["should_dispatch"])
        self.assertEqual(decision["reason"], "within_grace_window")


if __name__ == "__main__":
    unittest.main()
