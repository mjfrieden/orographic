from __future__ import annotations

import unittest

import pandas as pd

from engine.orographic.headline_intelligence import classify_headline, normalize_headlines


class HeadlineIntelligenceTests(unittest.TestCase):
    def test_classifies_guidance_and_direction(self) -> None:
        result = classify_headline("Acme raises guidance after earnings beat")
        self.assertEqual(result["event_type"], "guidance")
        self.assertEqual(result["direction"], "bullish")
        self.assertFalse(result["requires_llm_review"])

    def test_ambiguous_or_unclassified_headlines_are_queued(self) -> None:
        frame = pd.DataFrame([
            {"symbol": "AAPL", "headline": "Apple holds annual developer event"},
            {"symbol": "MSFT", "headline": "Microsoft faces lawsuit after product launch"},
        ])
        normalized, review = normalize_headlines(frame, source="company_ir", default_source_quality=0.9)
        self.assertEqual(normalized.iloc[0]["event_type"], "unclassified")
        self.assertEqual(len(review), 2)
        self.assertEqual(set(review["reason"]), {"unclassified_headline", "ambiguous_event_type"})

    def test_duplicate_cluster_reduces_novelty_without_losing_raw_rows(self) -> None:
        frame = pd.DataFrame([
            {"symbol": "AAPL", "headline": "Apple raises guidance"},
            {"symbol": "AAPL", "headline": "Apple raises guidance"},
            {"symbol": "AAPL", "headline": "Apple launches product"},
        ])
        normalized, _ = normalize_headlines(frame, source="wire")
        self.assertEqual(len(normalized), 3)
        self.assertEqual(float(normalized.iloc[0]["novelty"]), 0.5)
        self.assertEqual(float(normalized.iloc[2]["novelty"]), 1.0)
        self.assertEqual(int(normalized.iloc[0]["duplicate_cluster_size"]), 2)
