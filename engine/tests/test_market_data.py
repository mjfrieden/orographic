from __future__ import annotations

import unittest
from unittest import mock
import json
import os

import pandas as pd

from engine.orographic.market_data import black_scholes_delta, option_chain, option_expiries


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class MarketDataTests(unittest.TestCase):
    def test_tradier_expirations_are_preferred_when_configured(self) -> None:
        captured = {}

        def fake_open(request, timeout=0):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["timeout"] = timeout
            return _FakeResponse({"expirations": {"date": ["2026-08-21", "2026-08-28"]}})

        with (
            mock.patch.dict(os.environ, {"TRADIER_ACCESS_TOKEN": "secret", "TRADIER_BASE_URL": "https://api.tradier.test/v1"}, clear=True),
            mock.patch("engine.orographic.market_data.urlopen", side_effect=fake_open),
            mock.patch("engine.orographic.market_data.yf.Ticker") as ticker,
        ):
            expiries = option_expiries("aapl")

        self.assertEqual(expiries, ["2026-08-21", "2026-08-28"])
        self.assertIn("/markets/options/expirations?", captured["url"])
        self.assertIn("symbol=AAPL", captured["url"])
        self.assertEqual(captured["authorization"], "Bearer secret")
        self.assertEqual(captured["timeout"], 20)
        ticker.assert_not_called()

    def test_tradier_chain_normalizes_greeks_and_source_provenance(self) -> None:
        payload = {
            "options": {"option": [
                {"symbol": "AAPL260821C00200000", "option_type": "call", "strike": 200,
                 "bid": 1.1, "ask": 1.2, "last": 1.15, "volume": 100, "open_interest": 500,
                 "trade_date": 1787320800000, "greeks": {"mid_iv": 0.31, "delta": 0.42, "updated_at": 1787320800000}},
                {"symbol": "AAPL260821P00200000", "option_type": "put", "strike": 200,
                 "bid": 1.0, "ask": 1.1, "last": 1.05, "volume": 80, "open_interest": 450,
                 "greeks": {"smv_vol": 0.33, "delta": -0.48}},
            ]},
        }
        with (
            mock.patch.dict(os.environ, {"TRADIER_ACCESS_TOKEN": "secret"}, clear=True),
            mock.patch("engine.orographic.market_data.urlopen", return_value=_FakeResponse(payload)),
        ):
            calls, puts = option_chain("AAPL", "2026-08-21")

        self.assertEqual(calls.iloc[0]["contractSymbol"], "AAPL260821C00200000")
        self.assertEqual(puts.iloc[0]["contractSymbol"], "AAPL260821P00200000")
        self.assertEqual(calls.iloc[0]["impliedVolatility"], 0.31)
        self.assertEqual(puts.iloc[0]["impliedVolatility"], 0.33)
        self.assertEqual(calls.iloc[0]["tradierDelta"], 0.42)
        self.assertTrue(str(calls.iloc[0]["lastTradeDate"]).startswith("2026-08-21"))
        self.assertEqual(set(calls["dataSource"]), {"tradier"})

    def test_chain_falls_back_to_yfinance_when_tradier_is_unavailable(self) -> None:
        calls = pd.DataFrame([{"contractSymbol": "AAPL-C"}])
        puts = pd.DataFrame([{"contractSymbol": "AAPL-P"}])
        chain = mock.Mock(calls=calls, puts=puts)
        ticker = mock.Mock()
        ticker.option_chain.return_value = chain
        with (
            mock.patch.dict(os.environ, {"TRADIER_ACCESS_TOKEN": "secret"}, clear=True),
            mock.patch("engine.orographic.market_data.urlopen", side_effect=OSError("offline")),
            mock.patch("engine.orographic.market_data.yf.Ticker", return_value=ticker),
        ):
            normalized_calls, normalized_puts = option_chain("AAPL", "2026-08-21")

        self.assertEqual(normalized_calls.iloc[0]["dataSource"], "yfinance_fallback")
        self.assertEqual(normalized_puts.iloc[0]["dataSource"], "yfinance_fallback")

    def test_call_delta_is_positive(self) -> None:
        delta = black_scholes_delta(
            spot=100.0,
            strike=102.0,
            time_to_expiry_years=7 / 365,
            risk_free_rate=0.04,
            volatility=0.35,
            option_type="call",
        )
        self.assertIsNotNone(delta)
        self.assertGreater(delta, 0.0)
        self.assertLess(delta, 1.0)

    def test_put_delta_is_negative(self) -> None:
        delta = black_scholes_delta(
            spot=100.0,
            strike=98.0,
            time_to_expiry_years=7 / 365,
            risk_free_rate=0.04,
            volatility=0.35,
            option_type="put",
        )
        self.assertIsNotNone(delta)
        self.assertLess(delta, 0.0)
        self.assertGreater(delta, -1.0)


if __name__ == "__main__":
    unittest.main()
