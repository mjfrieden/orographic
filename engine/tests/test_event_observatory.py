from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from engine.orographic.event_feature_builders import build_observatory_daily_features
from engine.orographic.event_observatory import (
    ObservatoryConflictError,
    assess_observatory_quality,
    merge_observations,
    normalize_observations,
    write_observatory,
)


class EventObservatoryTests(unittest.TestCase):
    def test_late_observation_uses_first_seen_as_effective_time(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "id": "story-1",
                    "ticker": "AAPL",
                    "published_at": "2026-04-21T20:00:00Z",
                    "first_seen_at": "2026-04-22T13:00:00Z",
                    "headline": "Apple raises guidance",
                }
            ]
        )

        normalized, invalid = normalize_observations(raw, source="wire", source_kind="news")

        self.assertEqual(invalid, 0)
        self.assertEqual(normalized.iloc[0]["effective_at"], pd.Timestamp("2026-04-22T13:00:00Z"))
        self.assertIn('"headline":"Apple raises guidance"', normalized.iloc[0]["raw_payload_json"])

    def test_normalization_explodes_symbols_and_deduplicates_stable_ids(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "id": "story-1",
                    "ticker": "AAPL,MSFT",
                    "published_at": "2026-04-21T20:00:00Z",
                    "first_seen_at": "2026-04-21T20:01:00Z",
                    "headline": "Technology demand accelerates",
                },
                {
                    "id": "story-1",
                    "ticker": "AAPL,MSFT",
                    "published_at": "2026-04-21T20:00:00Z",
                    "first_seen_at": "2026-04-21T20:01:00Z",
                    "headline": "Technology demand accelerates",
                },
            ]
        )
        normalized, _ = normalize_observations(raw, source="wire", source_kind="news")

        merged, duplicates = merge_observations(pd.DataFrame(), normalized)

        self.assertEqual(len(merged), 2)
        self.assertEqual(duplicates, 2)
        self.assertEqual(set(merged["symbol"]), {"AAPL", "MSFT"})

    def test_repeated_collection_timestamp_does_not_change_immutable_payload(self) -> None:
        original = pd.DataFrame(
            [{"id": "story-1", "ticker": "AAPL", "published_at": "2026-04-21T20:00:00Z", "headline": "Guidance raised"}]
        )
        first, _ = normalize_observations(
            original, source="wire", source_kind="news", observed_at="2026-04-21T20:01:00Z"
        )
        second, _ = normalize_observations(
            original, source="wire", source_kind="news", observed_at="2026-04-21T20:15:00Z"
        )

        merged, duplicates = merge_observations(first, second)

        self.assertEqual(len(merged), 1)
        self.assertEqual(duplicates, 1)
        self.assertEqual(merged.iloc[0]["first_seen_at"], pd.Timestamp("2026-04-21T20:01:00Z"))

    def test_immutable_merge_rejects_changed_payload_for_stable_event(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "id": "filing-1",
                    "symbol": "AAPL",
                    "acceptance_datetime": "2026-04-21T12:00:00Z",
                    "form": "8-K",
                }
            ]
        )
        existing, _ = normalize_observations(
            raw, source="sec", source_kind="sec", observed_at="2026-04-21T12:01:00Z"
        )
        changed = existing.copy()
        changed.loc[:, "raw_payload_hash"] = "different"

        with self.assertRaises(ObservatoryConflictError):
            merge_observations(existing, changed)

    def test_immutable_merge_rejects_conflicting_payloads_within_one_input(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "id": "story-1",
                    "symbol": "AAPL",
                    "published_at": "2026-04-21T12:00:00Z",
                    "headline": "Original headline",
                },
                {
                    "id": "story-1",
                    "symbol": "AAPL",
                    "published_at": "2026-04-21T12:00:00Z",
                    "headline": "Changed headline",
                },
            ]
        )
        incoming, _ = normalize_observations(
            raw, source="wire", source_kind="news", observed_at="2026-04-21T12:01:00Z"
        )

        with self.assertRaises(ObservatoryConflictError):
            merge_observations(pd.DataFrame(), incoming)

    def test_quality_report_measures_coverage_and_collection_delay(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "id": "story-1",
                    "ticker": "AAPL",
                    "published_at": "2026-04-21T20:00:00Z",
                    "first_seen_at": "2026-04-21T20:10:00Z",
                    "headline": "Apple raises guidance",
                    "url": "https://example.test/story-1",
                },
                {
                    "id": "story-2",
                    "ticker": "MSFT",
                    "published_at": "2026-04-21T20:00:00Z",
                    "first_seen_at": "2026-04-21T20:20:00Z",
                    "headline": "",
                    "url": "",
                },
            ]
        )
        normalized, _ = normalize_observations(raw, source="wire", source_kind="news")

        report = assess_observatory_quality(normalized)

        self.assertEqual(report.rows, 2)
        self.assertEqual(report.symbols, 2)
        self.assertEqual(report.delayed_rows, 2)
        self.assertEqual(report.missing_headline_pct, 0.5)
        self.assertEqual(report.missing_url_pct, 0.5)
        self.assertEqual(report.mean_delay_minutes, 15.0)

    def test_daily_features_use_effective_date_and_preserve_source_families(self) -> None:
        news_raw = pd.DataFrame(
            [
                {
                    "id": "story-1",
                    "ticker": "AAPL",
                    "published_at": "2026-04-21T20:00:00Z",
                    "first_seen_at": "2026-04-22T13:00:00Z",
                    "headline": "Apple raises guidance",
                    "sentiment": 0.8,
                    "novelty": 0.9,
                }
            ]
        )
        sec_raw = pd.DataFrame(
            [
                {
                    "accession_number": "filing-1",
                    "symbol": "AAPL",
                    "acceptance_datetime": "2026-04-22T14:00:00Z",
                    "first_seen_at": "2026-04-22T14:01:00Z",
                    "form": "8-K",
                }
            ]
        )
        news, _ = normalize_observations(news_raw, source="wire", source_kind="news")
        sec, _ = normalize_observations(sec_raw, source="sec", source_kind="sec")
        observatory, _ = merge_observations(news, sec)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "observatory.parquet"
            write_observatory(observatory, path)
            daily = build_observatory_daily_features(path)

        row = daily.loc[
            (daily["symbol"] == "AAPL") & (daily["date"] == pd.Timestamp("2026-04-22"))
        ].iloc[0]
        self.assertEqual(float(row["fnspid_news_volume_1d"]), 1.0)
        self.assertEqual(float(row["sec_8k_flag"]), 1.0)
        self.assertEqual(row["dataset_tags"], "narrative_expectations,fnspid,sec_filings")

    def test_narrative_expectations_measure_attention_duplication_and_confirmation(self) -> None:
        first_day, _ = normalize_observations(
            pd.DataFrame(
                [{
                    "id": "one", "ticker": "AAPL", "published_at": "2026-04-20T13:00:00Z",
                    "first_seen_at": "2026-04-20T13:01:00Z", "headline": "Apple product demand rises",
                    "sentiment": 0.4, "novelty": 1.0,
                }]
            ),
            source="wire-a",
            source_kind="news",
        )
        burst_a, _ = normalize_observations(
            pd.DataFrame(
                [
                    {
                        "id": "two", "ticker": "AAPL", "published_at": "2026-04-22T13:00:00Z",
                        "first_seen_at": "2026-04-22T13:01:00Z", "headline": "Apple shares surge on product hype",
                        "sentiment": 0.9, "novelty": 0.3,
                    },
                    {
                        "id": "three", "ticker": "AAPL", "published_at": "2026-04-22T13:02:00Z",
                        "first_seen_at": "2026-04-22T13:03:00Z", "headline": "Apple valuation reaches record",
                        "sentiment": 0.8, "novelty": 0.5,
                    },
                ]
            ),
            source="wire-a",
            source_kind="news",
        )
        burst_b, _ = normalize_observations(
            pd.DataFrame(
                [{
                    "id": "four", "ticker": "AAPL", "published_at": "2026-04-22T13:04:00Z",
                    "first_seen_at": "2026-04-22T13:05:00Z", "headline": "Apple shares surge on product hype",
                    "sentiment": 0.9, "novelty": 0.3,
                }]
            ),
            source="wire-b",
            source_kind="news",
        )
        observatory, _ = merge_observations(first_day, burst_a)
        observatory, _ = merge_observations(observatory, burst_b)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "observatory.parquet"
            write_observatory(observatory, path)
            daily = build_observatory_daily_features(path)

        narrative_rows = daily.loc[daily["symbol"] == "AAPL"].sort_values("date")
        quiet = narrative_rows.loc[narrative_rows["date"] == pd.Timestamp("2026-04-21")].iloc[0]
        burst = narrative_rows.loc[narrative_rows["date"] == pd.Timestamp("2026-04-22")].iloc[0]
        self.assertEqual(float(quiet["narrative_attention_1d"]), 0.0)
        self.assertEqual(float(quiet["narrative_hype_pressure"]), 0.0)
        self.assertEqual(float(burst["narrative_attention_1d"]), 3.0)
        self.assertEqual(float(burst["narrative_attention_3d"]), 4.0)
        self.assertAlmostEqual(float(burst["narrative_duplicate_ratio_1d"]), 1 / 3, places=6)
        self.assertAlmostEqual(float(burst["narrative_source_diversity_1d"]), 0.5, places=6)
        self.assertAlmostEqual(float(burst["narrative_confirmation_score_1d"]), 0.5, places=6)
        self.assertGreater(float(burst["narrative_hype_pressure"]), 0.3)
        self.assertIn("narrative_expectations", burst["dataset_tags"])


if __name__ == "__main__":
    unittest.main()
