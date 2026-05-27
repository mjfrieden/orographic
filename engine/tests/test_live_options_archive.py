from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

from engine.orographic.live_options_archive import archive_live_option_chains


class LiveOptionsArchiveTests(unittest.TestCase):
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
            partition_path = Path(manifest["symbols"]["AAA"]["path"])
            self.assertTrue(partition_path.exists())
            archived = pd.read_parquet(partition_path)

        self.assertEqual(set(archived["option_type"].tolist()), {"C", "P"})
        self.assertEqual(archived["underlying_symbol"].iloc[0], "AAA")


if __name__ == "__main__":
    unittest.main()
