from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from engine.backtest.options_provider import HistoricalOptionsProvider
from engine.backtest.options_store import (
    _standardize_optionsdx_wide_frame,
    build_partitioned_store,
    manifest_path,
    partition_file_path,
)


SAMPLE_CSV = """quote_date,underlying_symbol,expire_date,strike,option_type,bid,ask,implied_volatility,delta,open_interest,trade_volume
2026-01-05,SPY,2026-01-09,595,C,1.10,1.20,0.18,0.42,1000,300
2026-01-05,SPY,2026-01-09,600,C,0.80,0.90,0.19,0.33,800,250
2026-01-09,SPY,2026-01-09,595,C,4.50,4.60,0.20,0.88,900,500
"""


class OptionsStoreTests(unittest.TestCase):
    def test_standardize_optionsdx_wide_frame_explodes_calls_and_puts(self) -> None:
        wide = pd.DataFrame(
            [
                {
                    "QUOTE_DATE": "2020-03-06",
                    "UNDERLYING_LAST": 292.97,
                    "EXPIRE_DATE": "2020-03-06",
                    "STRIKE": 200,
                    "C_BID": 91.52,
                    "C_ASK": 93.18,
                    "C_IV": None,
                    "C_DELTA": 1.0,
                    "C_VOLUME": 0,
                    "C_LAST": 0,
                    "P_BID": 0.0,
                    "P_ASK": 0.05,
                    "P_IV": 2.6103,
                    "P_DELTA": -0.00259,
                    "P_VOLUME": 0,
                    "P_LAST": 0.01,
                }
            ]
        )

        normalized = _standardize_optionsdx_wide_frame(wide, "spy_sample-1.csv")
        self.assertEqual(len(normalized), 2)
        self.assertEqual(sorted(normalized["option_type"].unique().tolist()), ["C", "P"])
        self.assertEqual(sorted(normalized["underlying_symbol"].unique().tolist()), ["SPY"])

    def test_build_partitioned_store_writes_manifest_and_partitions(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "sample.csv").write_text(SAMPLE_CSV)

            manifest = build_partitioned_store(data_dir)

            self.assertTrue(manifest_path(data_dir).exists())
            self.assertTrue(partition_file_path(data_dir, date(2026, 1, 5), "SPY").exists())
            self.assertEqual(manifest["summary"]["partition_count"], 2)
            self.assertEqual(manifest["summary"]["symbol_count"], 1)
            self.assertEqual(manifest["symbols"]["SPY"]["row_count"], 3)

    def test_provider_reads_partitioned_store_and_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "sample.csv").write_text(SAMPLE_CSV)
            build_partitioned_store(data_dir)

            provider = HistoricalOptionsProvider(data_dir)
            chain, source = provider.get_chain_with_source("SPY", date(2026, 1, 5))

            self.assertEqual(source, "real_chain")
            self.assertEqual(len(chain), 2)
            self.assertTrue(provider.has_real_coverage("SPY", date(2026, 1, 5)))
            self.assertFalse(provider.has_real_coverage("QQQ", date(2026, 1, 5)))


if __name__ == "__main__":
    unittest.main()
