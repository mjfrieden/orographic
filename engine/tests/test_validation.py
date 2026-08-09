from __future__ import annotations

import unittest

import pandas as pd

from engine.orographic.validation import purged_date_splits


class PurgedDateSplitTests(unittest.TestCase):
    def test_groups_same_day_rows_and_purges_overlapping_outcomes(self) -> None:
        feature_dates = pd.to_datetime(
            [
                "2026-01-02", "2026-01-02",
                "2026-01-05", "2026-01-05",
                "2026-01-06", "2026-01-06",
                "2026-01-07", "2026-01-07",
                "2026-01-08", "2026-01-08",
                "2026-01-09", "2026-01-09",
            ]
        )
        label_dates = feature_dates + pd.to_timedelta(
            [1, 1, 3, 3, 1, 1, 1, 1, 1, 1, 1, 1],
            unit="D",
        )

        splits = list(purged_date_splits(feature_dates, label_dates, n_splits=2))

        self.assertEqual(len(splits), 2)
        for train_idx, validation_idx in splits:
            validation_start = feature_dates[validation_idx].min()
            self.assertLess(label_dates[train_idx].max(), validation_start)
            self.assertTrue(set(feature_dates[train_idx]).isdisjoint(set(feature_dates[validation_idx])))
            for observed_date in feature_dates.unique():
                rows = {idx for idx, value in enumerate(feature_dates) if value == observed_date}
                self.assertFalse(rows & set(train_idx) and rows & set(validation_idx))

    def test_rejects_mismatched_date_vectors(self) -> None:
        with self.assertRaises(ValueError):
            list(purged_date_splits(["2026-01-01"], ["2026-01-02", "2026-01-03"]))


if __name__ == "__main__":
    unittest.main()
