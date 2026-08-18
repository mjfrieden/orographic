from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

from engine.orographic.live_options_archive import archive_live_option_chains
from scripts.archive_live_option_chains import select_rotating_symbols


class LiveOptionsArchiveTests(unittest.TestCase):
    def test_rotation_preserves_candidates_and_expands_universe_coverage(self) -> None:
        selected = select_rotating_symbols(
            ["AAA", "BBB", "CCC", "DDD", "EEE"],
            ["DDD", "AAA"],
            minimum_universe_symbols=4,
            rotation_offset=2,
        )

        self.assertEqual(selected[:2], ["DDD", "AAA"])
        self.assertEqual(len([symbol for symbol in selected if symbol in {"AAA", "BBB", "CCC", "DDD", "EEE"}]), 4)
        self.assertEqual(len(selected), len(set(selected)))
    def test_archive_live_option_chains_writes_empty_manifest_for_empty_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = archive_live_option_chains(
                [],
                output_dir=tmpdir,
                today=date(2026, 6, 9),
                run_started_at_utc="2026-06-09T16:40:00+00:00",
            )

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            latest = Path(tmpdir) / "coverage_manifest.json"
            latest_exists = latest.exists()

        self.assertTrue(latest_exists)
        self.assertEqual(manifest["summary"]["symbols_requested"], 0)
        self.assertEqual(manifest["summary"]["symbols_archived"], 0)
        self.assertEqual(manifest["summary"]["rows_archived"], 0)
        self.assertEqual(manifest["symbols"], {})

    def test_archive_live_option_chains_writes_partition_and_manifest(self) -> None:
        calls = pd.DataFrame(
            [
                {
                    "contractSymbol": "AAA260605C00100000",
                    "strike": 100.0,
                    "bid": 1.0,
                    "ask": 1.1,
                    "lastPrice": 1.05,
                    "impliedVolatility": 0.4,
                    "openInterest": 500,
                    "volume": 100,
                    "lastTradeDate": "2026-05-22T14:06:00Z",
                }
            ]
        )
        puts = pd.DataFrame(
            [
                {
                    "contractSymbol": "AAA260605P00100000",
                    "strike": 100.0,
                    "bid": 0.9,
                    "ask": 1.0,
                    "lastPrice": 0.95,
                    "impliedVolatility": 0.42,
                    "openInterest": 400,
                    "volume": 80,
                    "lastTradeDate": "2026-05-22T14:05:00Z",
                }
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                mock.patch("engine.orographic.live_options_archive.option_expiries", return_value=["2026-06-05"]),
                mock.patch("engine.orographic.live_options_archive.option_chain", return_value=(calls, puts)),
            ):
                result = archive_live_option_chains(
                    ["AAA"],
                    output_dir=tmpdir,
                    today=date(2026, 5, 22),
                    run_started_at_utc="2026-05-22T14:07:00+00:00",
                )

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"]["symbols_archived"], 1)
            self.assertEqual(manifest["summary"]["rows_archived"], 2)
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["summary"]["rows_with_two_sided_quotes"], 2)
            self.assertEqual(manifest["summary"]["rows_with_valid_iv"], 2)
            self.assertEqual(manifest["summary"]["rows_with_last_trade_timestamp"], 2)
            partition_path = Path(manifest["symbols"]["AAA"]["path"])
            self.assertTrue(partition_path.exists())
            archived = pd.read_parquet(partition_path)

        self.assertEqual(set(archived["option_type"].tolist()), {"C", "P"})
        self.assertEqual(archived["underlying_symbol"].iloc[0], "AAA")
        self.assertIn("quote_spread_pct", archived.columns)
        self.assertIn("chain_snapshot_at_utc", archived.columns)
        self.assertEqual(sorted(archived["last_trade_age_seconds"].astype(int).tolist()), [60, 120])


if __name__ == "__main__":
    unittest.main()
