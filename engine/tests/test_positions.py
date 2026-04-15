from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine.orographic.positions import append_position_history, enrich_positions


class PositionTrackingTests(unittest.TestCase):
    def test_enrich_positions_uses_mid_quote_when_broker_value_missing(self) -> None:
        positions = [
            {
                "symbol": "ABBV260417C00207500",
                "quantity": 1,
                "cost_basis": 217.0,
                "current_value": None,
                "date_acquired": "2026-04-14",
            }
        ]
        quotes = {
            "ABBV260417C00207500": {
                "bid": 2.0,
                "ask": 2.4,
                "last": 2.3,
                "close": 2.1,
            }
        }

        enriched = enrich_positions(positions, quotes)

        self.assertEqual(enriched[0]["mark_price"], 2.2)
        self.assertEqual(enriched[0]["mark_source"], "mid")
        self.assertEqual(enriched[0]["current_value"], 220.0)
        self.assertEqual(enriched[0]["current_value_source"], "quote_mid")
        self.assertEqual(enriched[0]["open_pl"], 3.0)

    def test_enrich_positions_preserves_broker_value_when_present(self) -> None:
        positions = [
            {
                "symbol": "NKE260417C00043000",
                "quantity": 1,
                "cost_basis": 98.0,
                "current_value": 105.0,
                "date_acquired": "2026-04-14",
            }
        ]

        enriched = enrich_positions(positions, {})

        self.assertEqual(enriched[0]["current_value"], 105.0)
        self.assertEqual(enriched[0]["current_value_source"], "broker")
        self.assertEqual(enriched[0]["open_pl"], 7.0)

    def test_append_position_history_trims_to_max_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "position_history.json"
            for idx in range(3):
                append_position_history(
                    path,
                    {
                        "captured_at_utc": f"2026-04-14T0{idx}:00:00+00:00",
                        "run_generated_at_utc": f"2026-04-14T0{idx}:00:00+00:00",
                        "configured": True,
                        "positions_count": idx,
                        "positions": [],
                        "status": "ok",
                    },
                    max_entries=2,
                )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["entries"]), 2)
            self.assertEqual(payload["entries"][0]["positions_count"], 1)
            self.assertEqual(payload["entries"][1]["positions_count"], 2)


if __name__ == "__main__":
    unittest.main()
