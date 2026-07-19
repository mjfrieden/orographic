from __future__ import annotations

import unittest

import pandas as pd

from scripts.evaluate_event_scout_ablation import evaluate


class EventScoutAblationTests(unittest.TestCase):
    def test_insufficient_event_linked_outcomes_hold_promotion(self) -> None:
        frame = pd.DataFrame([{
            "run_generated_at_utc": "2026-07-01T14:00:00Z", "outcome_status": "complete",
            "friday_close_pnl_pct_from_emission": 0.1, "event_observation_count_lookback": 1,
            "symbol": "AAPL", "option_type": "call", "regime_mode": "risk_on",
        }])
        report = evaluate(frame)
        self.assertEqual(report["status"], "hold")
        self.assertFalse(report["gates"]["completed_event_rows"]["passed"])
