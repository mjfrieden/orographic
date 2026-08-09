from __future__ import annotations

import unittest

import numpy as np

from scripts.train_payoff_shadow_challenger import (
    _apply_calibrator,
    _fit_sigmoid_calibrator,
    acceptance_gates,
)


class PayoffShadowChallengerTests(unittest.TestCase):
    def test_fold_local_calibration_preserves_ranking_and_matches_base_rate(self) -> None:
        raw = np.array([0.1, 0.2, 0.4, 0.8], dtype=float)
        y = np.array([0, 0, 1, 0], dtype=int)

        intercept = _fit_sigmoid_calibrator(raw, y)
        calibrated = _apply_calibrator(raw, intercept)

        self.assertEqual(np.argsort(raw).tolist(), np.argsort(calibrated).tolist())
        self.assertAlmostEqual(float(calibrated.mean()), float(y.mean()), places=6)

    def test_acceptance_remains_fail_closed_when_auc_passes_but_brier_fails(self) -> None:
        segment = {"rows": 60, "auc": 0.58, "brier": 0.27, "baseline_brier": 0.23}
        report = acceptance_gates({
            "calibrated_auc": 0.57,
            "calibrated_brier": 0.27,
            "raw_brier": 0.31,
            "baseline_brier": 0.23,
            "by_side": {"call": segment, "put": segment},
            "by_regime": {"risk_on": segment, "risk_off": segment},
        })

        self.assertEqual(report["status"], "hold")
        self.assertTrue(report["gates"]["aggregate_discrimination"])
        self.assertFalse(report["gates"]["aggregate_brier_skill"])


if __name__ == "__main__":
    unittest.main()
