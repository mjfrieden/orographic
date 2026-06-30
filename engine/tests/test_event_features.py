from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from engine.orographic.event_features import (
    build_event_feature_history,
    latest_event_feature_snapshot,
    load_event_feature_frame,
    write_event_feature_frame,
)


class EventFeatureTests(unittest.TestCase):
    def test_load_event_feature_frame_normalizes_numeric_columns(self) -> None:
        rows = [
            {
                "symbol": "aapl",
                "date": "2026-04-21T14:30:00Z",
                "fnspid_news_volume_1d": "5",
                "fnspid_sentiment_mean": "0.4",
                "dataset_tags": "fnspid,edt",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "daily_event_features.csv"
            write_event_feature_frame(rows, path)
            loaded = load_event_feature_frame(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded.iloc[0]["symbol"], "AAPL")
        self.assertEqual(float(loaded.iloc[0]["fnspid_news_volume_1d"]), 5.0)
        self.assertEqual(float(loaded.iloc[0]["edt_event_intensity"]), 0.0)
        self.assertEqual(loaded.iloc[0]["dataset_tags"], "fnspid,edt")

    def test_build_event_feature_history_aligns_by_symbol_and_date(self) -> None:
        rows = [
            {
                "symbol": "AAPL",
                "date": "2026-04-21",
                "fnspid_news_volume_1d": 4.0,
                "stocktwits_message_count": 20.0,
                "dataset_tags": "fnspid,stocktwits",
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "daily_event_features.csv"
            write_event_feature_frame(rows, path)
            store = load_event_feature_frame(path)
        history = build_event_feature_history(
            "AAPL",
            pd.to_datetime(["2026-04-21", "2026-04-22"]),
            store,
        )

        self.assertEqual(float(history.iloc[0]["fnspid_news_volume_1d"]), 4.0)
        self.assertEqual(float(history.iloc[1]["fnspid_news_volume_1d"]), 0.0)
        self.assertEqual(history.iloc[0]["dataset_tags"], "fnspid,stocktwits")

    def test_latest_event_feature_snapshot_uses_last_available_row_on_or_before_as_of(self) -> None:
        rows = [
            {
                "symbol": "AAPL",
                "date": "2026-04-20",
                "fnspid_news_volume_1d": 2.0,
                "dataset_tags": "fnspid",
            },
            {
                "symbol": "AAPL",
                "date": "2026-04-21",
                "fnspid_news_volume_1d": 6.0,
                "dataset_tags": "fnspid,edt",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "daily_event_features.csv"
            write_event_feature_frame(rows, path)
            store = load_event_feature_frame(path)

        snapshot = latest_event_feature_snapshot("AAPL", store, as_of=pd.Timestamp("2026-04-21T20:00:00"))

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.as_of.isoformat(), "2026-04-21")
        self.assertEqual(snapshot.dataset_tags, "fnspid,edt")
        self.assertEqual(snapshot.to_feature_dict()["fnspid_news_volume_1d"], 6.0)

    def test_latest_event_feature_snapshot_can_reject_stale_rows(self) -> None:
        rows = [
            {
                "symbol": "AAPL",
                "date": "2026-04-01",
                "sec_signal_count_1d": 1.0,
                "dataset_tags": "sec_filings",
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "daily_event_features.csv"
            write_event_feature_frame(rows, path)
            store = load_event_feature_frame(path)

        stale = latest_event_feature_snapshot("AAPL", store, as_of=pd.Timestamp("2026-04-10"), max_age_days=5)
        fresh = latest_event_feature_snapshot("AAPL", store, as_of=pd.Timestamp("2026-04-05"), max_age_days=5)

        self.assertIsNone(stale)
        self.assertIsNotNone(fresh)

    def test_global_macro_rows_are_combined_with_symbol_specific_rows(self) -> None:
        rows = [
            {
                "symbol": "__GLOBAL__",
                "date": "2026-04-21",
                "mirai_macro_shock_score": 0.7,
                "dataset_tags": "mirai",
            },
            {
                "symbol": "AAPL",
                "date": "2026-04-21",
                "fnspid_news_volume_1d": 3,
                "dataset_tags": "fnspid",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "daily_event_features.csv"
            write_event_feature_frame(rows, path)
            store = load_event_feature_frame(path)

        history = build_event_feature_history("AAPL", pd.to_datetime(["2026-04-21"]), store)
        snapshot = latest_event_feature_snapshot("AAPL", store, as_of=pd.Timestamp("2026-04-21"))

        self.assertEqual(float(history.iloc[0]["fnspid_news_volume_1d"]), 3.0)
        self.assertEqual(float(history.iloc[0]["mirai_macro_shock_score"]), 0.7)
        self.assertEqual(set(history.iloc[0]["dataset_tags"].split(",")), {"mirai", "fnspid"})
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.to_feature_dict()["mirai_macro_shock_score"], 0.7)
        self.assertEqual(snapshot.to_feature_dict()["fnspid_news_volume_1d"], 3.0)


if __name__ == "__main__":
    unittest.main()
