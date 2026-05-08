from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from engine.orographic.sentinel import fetch_ai_multiplier


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buffer.read()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class SentinelTests(unittest.TestCase):
    def test_fetch_ai_multiplier_returns_structured_event_fields(self) -> None:
        payload = {
            "ok": True,
            "multiplier": 1.08,
            "shadow_multiplier": 1.08,
            "catalyst": "earnings beat",
            "rationale": "Immediate upside catalyst.",
            "event_type": "earnings",
            "event_polarity": 0.7,
            "directional_relevance": "call",
            "time_horizon": "one_to_three_days",
            "direction_1d": "up",
            "direction_3d": "up",
            "direction_5d": "neutral",
            "magnitude_bucket": "medium",
            "decay_half_life": "three_days",
            "spot_vs_iv_effect": "spot",
            "call_relevance": 0.85,
            "put_relevance": 0.1,
            "no_trade_relevance": 0.15,
            "confidence": 0.8,
            "source": "cloudflare_ai",
            "mode": "shadow",
        }
        fake_news = [{"title": "Company beats earnings estimates"}]

        with (
            mock.patch.dict("os.environ", {"OROGRAPHIC_SENTINEL_MODE": "shadow"}, clear=False),
            mock.patch("engine.orographic.sentinel.yf.Ticker") as ticker_mock,
            mock.patch("engine.orographic.sentinel.urllib.request.urlopen", return_value=_FakeResponse(payload)),
        ):
            ticker_mock.return_value.news = fake_news
            score = fetch_ai_multiplier("TEST", direction="call", scout_score=0.4)

        self.assertEqual(score.direction_1d, "up")
        self.assertEqual(score.direction_3d, "up")
        self.assertEqual(score.direction_5d, "neutral")
        self.assertEqual(score.magnitude_bucket, "medium")
        self.assertEqual(score.decay_half_life, "three_days")
        self.assertEqual(score.spot_vs_iv_effect, "spot")
        self.assertAlmostEqual(score.call_relevance, 0.85, places=4)
        self.assertAlmostEqual(score.put_relevance, 0.1, places=4)
        self.assertAlmostEqual(score.no_trade_relevance, 0.15, places=4)
        self.assertEqual(score.headlines, ["Company beats earnings estimates"])
        self.assertEqual(score.multiplier, 1.0)
        self.assertAlmostEqual(score.shadow_multiplier, 1.08, places=4)

    def test_fetch_ai_multiplier_defaults_missing_structured_fields_to_neutral(self) -> None:
        payload = {
            "ok": True,
            "multiplier": 1.0,
            "shadow_multiplier": 1.0,
            "catalyst": "none",
            "rationale": "No strong event edge.",
        }
        fake_news = [{"title": "Routine corporate update"}]

        with (
            mock.patch("engine.orographic.sentinel.yf.Ticker") as ticker_mock,
            mock.patch("engine.orographic.sentinel.urllib.request.urlopen", return_value=_FakeResponse(payload)),
        ):
            ticker_mock.return_value.news = fake_news
            score = fetch_ai_multiplier("TEST", direction="put", scout_score=-0.3)

        self.assertEqual(score.direction_1d, "neutral")
        self.assertEqual(score.direction_3d, "neutral")
        self.assertEqual(score.direction_5d, "neutral")
        self.assertEqual(score.magnitude_bucket, "unknown")
        self.assertEqual(score.decay_half_life, "unknown")
        self.assertEqual(score.spot_vs_iv_effect, "unknown")
        self.assertEqual(score.call_relevance, 0.0)
        self.assertEqual(score.put_relevance, 0.0)
        self.assertEqual(score.no_trade_relevance, 1.0)


if __name__ == "__main__":
    unittest.main()
