from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from engine.orographic.sentinel import fetch_ai_multiplier


class SentinelNetworkPolicyTests(unittest.TestCase):
    def test_disabled_network_returns_local_score_without_any_network_attempt(self) -> None:
        context = {
            "dataset_tags": "fnspid,edt",
            "fnspid_sentiment_mean": 0.8,
            "fnspid_novelty_score": 0.85,
            "fnspid_catalyst_density": 0.9,
            "edt_guidance_score": 1.0,
        }
        with (
            mock.patch.dict(os.environ, {
                "OROGRAPHIC_SENTINEL_DISABLE_NETWORK": "1",
                "OROGRAPHIC_SENTINEL_TOKEN": "synthetic-token",
                "OROGRAPHIC_SENTINEL_URL": "https://example.invalid/sentinel",
            }, clear=True),
            mock.patch("engine.orographic.sentinel.yf.Ticker") as ticker,
            mock.patch("engine.orographic.sentinel._fetch_remote_payload") as remote,
            mock.patch("engine.orographic.sentinel.urllib.request.urlopen") as http,
        ):
            score = fetch_ai_multiplier(
                "TEST", direction="call", scout_score=0.4, event_context=context,
            )
        ticker.assert_not_called()
        remote.assert_not_called()
        http.assert_not_called()
        self.assertEqual(score.multiplier, 1.0)
        self.assertEqual(score.mode, "shadow")
        self.assertEqual(score.event_type, "guidance")
        self.assertGreater(score.call_relevance, score.put_relevance)
        self.assertEqual(score.event_context, context)
        self.assertIn("network disabled", score.rationale)

    def test_disabled_network_does_not_depend_on_mode(self) -> None:
        for mode in ("active", "shadow", "off", "invalid"):
            with (
                self.subTest(mode=mode),
                mock.patch.dict(os.environ, {
                    "OROGRAPHIC_SENTINEL_DISABLE_NETWORK": "1",
                    "OROGRAPHIC_SENTINEL_MODE": mode,
                }, clear=True),
                mock.patch("engine.orographic.sentinel.yf.Ticker") as ticker,
                mock.patch("engine.orographic.sentinel.urllib.request.urlopen") as http,
            ):
                score = fetch_ai_multiplier("TEST", direction="put", scout_score=-0.3)
            ticker.assert_not_called()
            http.assert_not_called()
            self.assertIn("structured_event", score.model_mode)

    def test_unset_or_non_enabled_flag_preserves_default_http_behavior(self) -> None:
        for value in (None, "", "0"):
            env = {} if value is None else {"OROGRAPHIC_SENTINEL_DISABLE_NETWORK": value}
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({
                "ok": True,
                "event_type": "earnings",
                "source": "synthetic_remote",
            }).encode("utf-8")
            with (
                self.subTest(value=value),
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("engine.orographic.sentinel.yf.Ticker") as ticker,
                mock.patch("engine.orographic.sentinel.urllib.request.urlopen", return_value=response) as http,
            ):
                ticker.return_value.news = [{"title": "Synthetic earnings headline"}]
                score = fetch_ai_multiplier("TEST", direction="call", scout_score=0.4)
            ticker.assert_called_once_with("TEST")
            http.assert_called_once()
            request = http.call_args.args[0]
            self.assertEqual(request.full_url, "https://orographic.pages.dev/api/ai/sentinel")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(json.loads(request.data)["headlines"], ["Synthetic earnings headline"])
            self.assertEqual(score.event_type, "earnings")

    def test_default_http_failure_still_uses_existing_local_fallback(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("engine.orographic.sentinel.yf.Ticker") as ticker,
            mock.patch("engine.orographic.sentinel.urllib.request.urlopen", side_effect=OSError("offline")) as http,
        ):
            ticker.return_value.news = [{"title": "Synthetic headline"}]
            score = fetch_ai_multiplier("TEST", direction="call", scout_score=0.4)
        http.assert_called_once()
        self.assertEqual(score.multiplier, 1.0)
        self.assertEqual(score.rationale, "No Sentinel event intelligence gathered.")

    def test_default_without_news_still_does_not_call_remote(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("engine.orographic.sentinel.yf.Ticker") as ticker,
            mock.patch("engine.orographic.sentinel.urllib.request.urlopen") as http,
        ):
            ticker.return_value.news = []
            score = fetch_ai_multiplier("TEST")
        ticker.assert_called_once_with("TEST")
        http.assert_not_called()
        self.assertEqual(score.source, "default+structured_event_model")


if __name__ == "__main__":
    unittest.main()
