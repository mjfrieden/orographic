from __future__ import annotations

import unittest

import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from engine.orographic.path_hazard import (
    FEATURE_COLS,
    cumulative_incidence,
    record_from_trade,
    train_and_evaluate,
)


def _trade(*, mark_time: str, mark_return: float) -> dict[str, object]:
    return {
        "symbol": "AAA",
        "contract_symbol": "AAA260410C00100000",
        "option_type": "call",
        "strike": 100.0,
        "expiry": "2026-04-10",
        "entry_date": "2026-04-06",
        "exit_date": "2026-04-10",
        "entry_spot": 100.0,
        "exit_spot": 102.0,
        "entry_price": 1.0,
        "pnl_pct": 0.2,
        "hold_period_return_after_friction_pct": 0.18,
        "archived_quote_path": {
            "status": "observed",
            "marks": [{"captured_at_utc": mark_time, "pnl_pct_from_emission": mark_return}],
        },
    }


class PathHazardTests(unittest.TestCase):
    def test_post_exit_marks_are_excluded_from_competing_risk_label(self) -> None:
        record = record_from_trade(_trade(mark_time="2026-04-11T15:00:00+00:00", mark_return=0.5))
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.event, "expiry")
        self.assertFalse(record.exact_path)
        self.assertEqual(record.valid_marks, 0)
        self.assertEqual(record.invalid_post_exit_marks, 1)
        self.assertEqual(record.mechanical_return, 0.18)

    def test_valid_pre_exit_target_sets_mechanical_return(self) -> None:
        record = record_from_trade(_trade(mark_time="2026-04-08T15:00:00+00:00", mark_return=0.31))
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.event, "target")
        self.assertTrue(record.exact_path)
        self.assertEqual(record.mechanical_return, 0.25)

    def test_insufficient_exact_paths_fail_closed_without_artifact(self) -> None:
        record = record_from_trade(_trade(mark_time="2026-04-08T15:00:00+00:00", mark_return=0.31))
        artifact, report = train_and_evaluate([record] if record else [])
        self.assertIsNone(artifact)
        self.assertEqual(report["status"], "insufficient_exact_paths")

    def test_cumulative_incidence_is_bounded_and_exhaustive(self) -> None:
        width = len(FEATURE_COLS) + 2
        X = np.zeros((2, width), dtype=float)
        target_model = Pipeline([
            ("scale", RobustScaler()),
            ("model", DummyClassifier(strategy="constant", constant=1)),
        ]).fit(X, np.ones(2, dtype=int))
        stop_model = Pipeline([
            ("scale", RobustScaler()),
            ("model", DummyClassifier(strategy="constant", constant=0)),
        ]).fit(X, np.zeros(2, dtype=int))
        target, stop, expiry = cumulative_incidence(
            {"target_hazard_model": target_model, "stop_hazard_model": stop_model},
            np.zeros(len(FEATURE_COLS), dtype=float),
            5,
        )
        self.assertAlmostEqual(target, 1.0)
        self.assertAlmostEqual(stop, 0.0)
        self.assertAlmostEqual(expiry, 0.0)


if __name__ == "__main__":
    unittest.main()
