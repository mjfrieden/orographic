from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.fetch_gdelt_company_news import _chunks, load_aliases, map_symbols
from scripts.gdelt_cooldown import load_cooldown, record_rate_limit


class GdeltCompanyNewsTests(unittest.TestCase):
    def test_load_aliases_filters_to_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "aliases.json"
            path.write_text(json.dumps({"AAPL": ["Apple"], "MSFT": ["Microsoft"]}), encoding="utf-8")
            aliases = load_aliases(path, {"AAPL"})

        self.assertEqual(aliases, {"AAPL": ["Apple"]})

    def test_map_symbols_requires_explicit_alias_boundaries(self) -> None:
        aliases = {"AAPL": ["Apple"], "MSFT": ["Microsoft"], "C": ["Citigroup"]}

        self.assertEqual(map_symbols("Apple and Microsoft announce new products", aliases), ["AAPL", "MSFT"])
        self.assertEqual(map_symbols("Pineapple demand rises", aliases), [])
        self.assertEqual(map_symbols("Citigroup reports earnings", aliases), ["C"])

    def test_map_symbols_supports_punctuation_and_case(self) -> None:
        aliases = {"T": ["AT&T"], "LOW": ["Lowe's"]}

        self.assertEqual(map_symbols("AT&T expands fiber near LOWE'S stores", aliases), ["T", "LOW"])

    def test_default_sized_batches_reduce_request_count(self) -> None:
        aliases = [(f"S{i}", f"Company {i}") for i in range(78)]

        batches = list(_chunks(aliases, 20))

        self.assertEqual(len(batches), 4)
        self.assertEqual(sum(len(batch) for batch in batches), 78)

    def test_rate_limit_cooldown_persists_and_expires(self) -> None:
        now = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmpdir:
            state = Path(tmpdir) / "gdelt_cooldown.json"
            written = record_rate_limit(state, source="test", cooldown_hours=6, now=now)
            active = load_cooldown(state, now=now + timedelta(hours=5))
            expired = load_cooldown(state, now=now + timedelta(hours=7))

        self.assertEqual(active, written)
        self.assertIsNone(expired)

    def test_retry_after_extends_default_cooldown(self) -> None:
        now = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmpdir:
            state = Path(tmpdir) / "gdelt_cooldown.json"
            written = record_rate_limit(
                state, source="test", cooldown_hours=1, retry_after_seconds=7200, now=now
            )

        self.assertEqual(written["cooldown_seconds"], 7200.0)

    def test_scheduled_workflow_shares_cooldown_and_keeps_ir_collection(self) -> None:
        workflow = (Path(__file__).parents[2] / ".github/workflows/orographic_scan.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(workflow.count("--cooldown-state engine/data/event_observatory/gdelt_cooldown.json"), 2)
        self.assertGreater(workflow.index("python scripts/fetch_company_ir_feeds.py"), workflow.index("python scripts/fetch_gdelt_company_news.py"))
        self.assertEqual(workflow.count("--max-retries 0"), 2)


if __name__ == "__main__":
    unittest.main()
