from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from scripts.shared_source_freshness import assess_source_freshness


class SourceFreshnessTests(unittest.TestCase):
    def assess(self, **changes):
        args = {
            "source_identity": {
                "source_system": "cirrus", "bundle_id": "bundle-a", "manifest_sha256": "a" * 64,
            },
            "export_generated_at": "2026-10-12T17:21:35+00:00",
            "decision_timestamps": ["2026-07-03T17:10:00Z", "2026-10-12T17:20:00Z"],
            "quote_observation_timestamps": ["2026-10-12T17:21:00Z"],
            "as_of_utc": "2026-10-13T01:00:00Z",
            "required_not_before_utc": "2026-10-12T17:00:00Z",
        }
        args.update(changes)
        before = copy.deepcopy(args)
        result = assess_source_freshness(**args)
        self.assertEqual(args, before, "assessment must not mutate historical evidence")
        return result

    def test_identity_export_and_observation_ranges_are_separate(self):
        result = self.assess()
        self.assertTrue(result["source_is_fresh"])
        self.assertEqual(result["source_identity"]["bundle_id"], "bundle-a")
        self.assertEqual(result["export_generated_at_utc"], "2026-10-12T17:21:35+00:00")
        self.assertEqual(result["model_run_decision_range"]["min_at_utc"], "2026-07-03T17:10:00+00:00")
        self.assertEqual(result["model_run_decision_range"]["max_at_utc"], "2026-10-12T17:20:00+00:00")
        self.assertEqual(result["quote_observation_range"]["max_at_utc"], "2026-10-12T17:21:00+00:00")
        self.assertEqual(result["session_completeness"], "not_assessed")
        self.assertEqual(result["provider_quote_freshness"], "not_assessed")

    def test_new_export_cannot_make_old_decisions_fresh(self):
        result = self.assess(decision_timestamps=["2026-10-08T17:20:00Z"])
        self.assertEqual(result["export_recency"]["status"], "fresh")
        self.assertEqual(result["source_freshness"], "stale")
        self.assertFalse(result["source_is_fresh"])

    def test_new_export_and_decisions_cannot_make_old_quotes_fresh(self):
        result = self.assess(quote_observation_timestamps=["2026-10-08T17:20:00Z"])
        self.assertEqual(result["model_run_decision_range"]["status"], "fresh")
        self.assertEqual(result["quote_observation_range"]["status"], "stale")
        self.assertFalse(result["source_is_fresh"])

    def test_unknown_quote_coverage_cannot_be_replaced_by_decisions(self):
        result = self.assess(quote_observation_timestamps=None)
        self.assertEqual(result["quote_observation_range"]["reason"], "coverage_not_supplied")
        self.assertIsNone(result["quote_observation_range"]["row_count"])
        self.assertEqual(result["source_freshness"], "unknown")
        self.assertFalse(result["source_is_fresh"])

    def test_missing_empty_and_invalid_population_fail_closed(self):
        for field in ("decision_timestamps", "quote_observation_timestamps"):
            for values in (None, [], [None], [""], ["not-a-time"], ["2026-10-12"], ["2026-10-12T17:20:00"]):
                with self.subTest(field=field, values=values):
                    result = self.assess(**{field: values})
                    self.assertEqual(result["source_freshness"], "unknown")
                    self.assertFalse(result["source_is_fresh"])

    def test_invalid_row_is_not_dropped_when_another_row_is_recent(self):
        result = self.assess(decision_timestamps=[None, "2026-10-12T17:20:00Z"])
        self.assertEqual(result["model_run_decision_range"]["row_count"], 2)
        self.assertEqual(result["model_run_decision_range"]["invalid_timestamp_count"], 1)
        self.assertFalse(result["source_is_fresh"])

    def test_missing_naive_and_invalid_export_clock_fail_closed(self):
        for value in (None, "", "invalid", "2026-10-12", "2026-10-12T17:21:35"):
            with self.subTest(value=value):
                result = self.assess(export_generated_at=value)
                self.assertEqual(result["export_recency"]["status"], "unknown")
                self.assertFalse(result["source_is_fresh"])

    def test_missing_or_invalid_explicit_clock_and_cutoff_fail_closed(self):
        for field in ("as_of_utc", "required_not_before_utc"):
            for value in (None, "", "invalid", "2026-10-12", "2026-10-12T17:00:00"):
                with self.subTest(field=field, value=value):
                    result = self.assess(**{field: value})
                    self.assertFalse(result["context_valid"])
                    self.assertFalse(result["source_is_fresh"])
        result = self.assess(required_not_before_utc="2026-10-14T00:00:00Z")
        self.assertFalse(result["context_valid"])
        self.assertFalse(result["source_is_fresh"])

    def test_future_export_decision_and_quote_clocks_fail_closed(self):
        for field, value in (
            ("export_generated_at", "2026-10-14T00:00:00Z"),
            ("decision_timestamps", ["2026-10-14T00:00:00Z"]),
            ("quote_observation_timestamps", ["2026-10-14T00:00:00Z"]),
        ):
            with self.subTest(field=field):
                result = self.assess(**{field: value})
                self.assertFalse(result["source_is_fresh"])

    def test_observation_after_export_is_not_accepted(self):
        result = self.assess(export_generated_at="2026-10-12T17:00:00Z")
        for field in ("model_run_decision_range", "quote_observation_range"):
            self.assertEqual(result[field]["reason"], "observation_after_export")
        self.assertFalse(result["source_is_fresh"])

    def test_missing_identity_blocks_a_positive_claim(self):
        result = self.assess(source_identity={"source_system": "cirrus"})
        self.assertFalse(result["identity_known"])
        self.assertFalse(result["source_is_fresh"])

    def test_known_staleness_remains_visible_with_unknown_quote_coverage(self):
        result = self.assess(decision_timestamps=["2026-10-08T17:20:00Z"], quote_observation_timestamps=None)
        self.assertEqual(result["source_freshness"], "stale")
        self.assertEqual(result["quote_observation_range"]["status"], "unknown")

    def test_offset_conversion_and_inclusive_cutoff(self):
        result = self.assess(
            decision_timestamps=["2026-10-12T13:00:00-04:00"],
            quote_observation_timestamps=["2026-10-12T19:00:00+02:00"],
            export_generated_at=datetime(2026, 10, 12, 17, tzinfo=timezone.utc),
        )
        self.assertTrue(result["source_is_fresh"])
        self.assertEqual(result["quote_observation_range"]["max_at_utc"], "2026-10-12T17:00:00+00:00")

    def test_weekend_uses_supplied_friday_cutoff_without_weekday_guessing(self):
        result = self.assess(
            as_of_utc="2026-10-11T12:00:00Z", required_not_before_utc="2026-10-09T19:00:00Z",
            export_generated_at="2026-10-09T21:00:00Z",
            decision_timestamps=["2026-10-09T19:30:00Z"],
            quote_observation_timestamps=["2026-10-09T20:00:00Z"],
        )
        self.assertTrue(result["source_is_fresh"])

    def test_holiday_uses_supplied_session_cutoff_without_calendar_inference(self):
        # Caller supplies the previous expected session for this synthetic case.
        result = self.assess(
            as_of_utc="2026-12-25T18:00:00Z", required_not_before_utc="2026-12-24T17:00:00Z",
            export_generated_at="2026-12-24T19:00:00Z",
            decision_timestamps=["2026-12-24T17:00:00Z"],
            quote_observation_timestamps=["2026-12-24T18:00:00Z"],
        )
        self.assertTrue(result["source_is_fresh"])
        self.assertFalse(self.assess(
            as_of_utc="2026-12-25T18:00:00Z", required_not_before_utc=None,
        )["source_is_fresh"])


if __name__ == "__main__":
    unittest.main()
