from __future__ import annotations

import unittest

from engine.orographic.market_data import black_scholes_delta


class MarketDataTests(unittest.TestCase):
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
