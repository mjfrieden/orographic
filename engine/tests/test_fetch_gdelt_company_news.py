from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.fetch_gdelt_company_news import _chunks, load_aliases, map_symbols


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


if __name__ == "__main__":
    unittest.main()
