from __future__ import annotations

import unittest

from scripts.diagnose_payoff_skill import temporal_integrity_report


class PayoffSkillDiagnosticTests(unittest.TestCase):
    def test_temporal_integrity_accepts_signal_time_evidence(self) -> None:
        report = temporal_integrity_report([{
            "decision_at_utc": "2026-06-01T15:00:00Z",
            "entry_quote_observed_at_utc": "2026-06-01T14:59:55Z",
            "exit_quote_observed_at_utc": "2026-06-05T20:00:03Z",
            "executable_label_available_at_utc": "2026-06-05T20:00:05Z",
            "regime_observed_at_utc": "2026-06-01T15:00:00Z",
            "entry_bid": 1.0,
            "entry_ask": 1.2,
            "exit_bid": 1.3,
            "exit_ask": 1.5,
        }])

        self.assertTrue(report["passed"])
        self.assertEqual(report["violations"], {})

    def test_temporal_integrity_detects_future_regime_and_entry_quote(self) -> None:
        report = temporal_integrity_report([{
            "decision_at_utc": "2026-06-01T15:00:00Z",
            "entry_quote_observed_at_utc": "2026-06-01T15:00:05Z",
            "exit_quote_observed_at_utc": "2026-06-05T20:00:03Z",
            "executable_label_available_at_utc": "2026-06-05T20:00:05Z",
            "regime_observed_at_utc": "2026-06-01T15:01:00Z",
            "entry_bid": 1.0,
            "entry_ask": 1.2,
            "exit_bid": 1.3,
            "exit_ask": 1.5,
        }])

        self.assertFalse(report["passed"])
        self.assertEqual(report["violations"]["entry_quote_after_decision"], 1)
        self.assertEqual(report["violations"]["regime_observed_after_decision"], 1)


if __name__ == "__main__":
    unittest.main()
