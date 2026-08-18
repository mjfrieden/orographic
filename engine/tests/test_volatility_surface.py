from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from engine.orographic.volatility_surface import (
    compute_expiry_surface,
    compute_term_structure_slope,
)


class VolatilitySurfaceTests(unittest.TestCase):
    def test_surface_recovers_atm_skew_curvature_and_wing_difference(self) -> None:
        calls = pd.DataFrame({
            "strike": [100, 103, 105, 108, 110],
            "impliedVolatility": [0.24, 0.235, 0.23, 0.228, 0.23],
        })
        puts = pd.DataFrame({
            "strike": [90, 92, 95, 97, 100],
            "impliedVolatility": [0.34, 0.31, 0.285, 0.26, 0.245],
        })

        surface = compute_expiry_surface(
            calls,
            puts,
            spot=100.0,
            expiry="2026-08-21",
            as_of=date(2026, 8, 11),
        )

        self.assertEqual(surface["days_to_expiry"], 10)
        self.assertEqual(surface["observation_count"], 10)
        self.assertAlmostEqual(surface["atm_iv"], 0.245, places=4)
        self.assertIsNotNone(surface["skew_slope"])
        self.assertLess(surface["skew_slope"], 0)
        self.assertIsNotNone(surface["curvature"])
        self.assertGreater(surface["put_call_wing_skew"], 0)
        self.assertLess(surface["fit_rmse"], 0.03)

    def test_term_slope_is_normalized_to_thirty_days(self) -> None:
        surfaces = {
            "2026-08-21": {"days_to_expiry": 10, "atm_iv": 0.25},
            "2026-09-04": {"days_to_expiry": 24, "atm_iv": 0.278},
            "2026-09-18": {"days_to_expiry": 38, "atm_iv": 0.30},
        }

        slope = compute_term_structure_slope(surfaces, "2026-08-21")

        self.assertAlmostEqual(slope, 0.06, places=6)

    def test_sparse_or_invalid_chain_fails_closed_without_fabricated_fit(self) -> None:
        calls = pd.DataFrame({"strike": [100], "impliedVolatility": [0.25]})
        surface = compute_expiry_surface(
            calls,
            pd.DataFrame(),
            spot=100.0,
            expiry="bad-date",
            as_of=date(2026, 8, 11),
        )

        self.assertEqual(surface["atm_iv"], 0.25)
        self.assertIsNone(surface["skew_slope"])
        self.assertIsNone(surface["curvature"])
        self.assertIsNone(surface["days_to_expiry"])


if __name__ == "__main__":
    unittest.main()
