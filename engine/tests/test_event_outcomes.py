from __future__ import annotations

import unittest

import pandas as pd

from engine.orographic.event_outcomes import enrich_outcomes_with_events


class EventOutcomeCoverageTests(unittest.TestCase):
    def test_enrichment_uses_only_events_known_before_recommendation(self) -> None:
        outcomes = pd.DataFrame(
            [
                {
                    "run_generated_at_utc": "2026-04-22T14:00:00Z",
                    "symbol": "AAPL",
                    "contract_symbol": "AAPL1",
                    "outcome_status": "complete",
                }
            ]
        )
        events = pd.DataFrame(
            [
                {
                    "event_id": "prior-symbol",
                    "symbol": "AAPL",
                    "effective_at": "2026-04-22T13:00:00Z",
                    "source": "wire",
                    "source_kind": "news",
                    "event_type": "guidance",
                    "sentiment": 0.0,
                    "novelty": 1.0,
                    "confidence": 1.0,
                    "headline": "Apple raises guidance",
                },
                {
                    "event_id": "prior-global",
                    "symbol": "__GLOBAL__",
                    "effective_at": "2026-04-21T13:00:00Z",
                    "source": "gdelt",
                    "source_kind": "macro",
                    "event_type": "geopolitical",
                    "sentiment": -0.5,
                    "novelty": 0.8,
                    "confidence": 0.7,
                    "headline": "Global conflict expands",
                },
                {
                    "event_id": "future",
                    "symbol": "AAPL",
                    "effective_at": "2026-04-22T15:00:00Z",
                    "source": "wire",
                    "source_kind": "news",
                    "event_type": "guidance",
                    "sentiment": 1.0,
                    "novelty": 1.0,
                    "confidence": 1.0,
                    "headline": "Apple raises guidance again",
                },
            ]
        )

        enriched, report = enrich_outcomes_with_events(outcomes, events, lookback_days=5)

        row = enriched.iloc[0]
        self.assertEqual(int(row["event_observation_count_lookback"]), 2)
        self.assertEqual(int(row["event_symbol_specific_count"]), 1)
        self.assertEqual(int(row["event_global_count"]), 1)
        self.assertNotIn("future", row["event_ids"])
        self.assertEqual(float(row["narrative_attention_1d_at_entry"]), 1.0)
        self.assertEqual(float(row["narrative_attention_3d_at_entry"]), 1.0)
        self.assertGreater(float(row["narrative_hype_pressure_at_entry"]), 0.0)
        self.assertEqual(report["summary"]["complete_outcome_event_coverage_pct"], 1.0)

    def test_enrichment_excludes_events_outside_lookback(self) -> None:
        outcomes = pd.DataFrame(
            [{"run_generated_at_utc": "2026-04-22T14:00:00Z", "symbol": "AAPL", "outcome_status": "pending"}]
        )
        events = pd.DataFrame(
            [{
                "event_id": "old", "symbol": "AAPL", "effective_at": "2026-04-10T14:00:00Z",
                "source": "sec", "source_kind": "sec", "event_type": "10-k",
                "sentiment": 0.0, "novelty": 1.0, "confidence": 1.0,
                "headline": "Old filing",
            }]
        )

        enriched, report = enrich_outcomes_with_events(outcomes, events, lookback_days=5)

        self.assertEqual(int(enriched.iloc[0]["event_observation_count_lookback"]), 0)
        self.assertEqual(report["summary"]["recommendation_event_coverage_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
