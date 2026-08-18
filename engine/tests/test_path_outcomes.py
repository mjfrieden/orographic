from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from engine.orographic.path_outcomes import apply_archived_quote_path_labels, build_archived_quote_path_label


class PathOutcomeTests(unittest.TestCase):
    def test_prospective_trajectory_marks_are_used_without_archive_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pick = {
                "run_generated_at_utc": "2026-05-29T14:07:00+00:00",
                "symbol": "AAA",
                "contract_symbol": "AAA260605C00100000",
                "option_type": "call",
                "expiry": "2026-06-05",
                "strike": 100.0,
                "emission_quote": {"mid": 1.00},
                "outcomes": {"trajectory_marks": [
                    {"captured_at_utc": "2026-05-29T15:07:00+00:00", "mark": 1.30, "bid": 1.25, "ask": 1.35},
                    {"captured_at_utc": "2026-05-29T16:07:00+00:00", "mark": 0.45, "bid": 0.40, "ask": 0.50},
                ]},
            }
            label = build_archived_quote_path_label(pick, archive_dir=Path(tmpdir))

        self.assertEqual(label["status"], "observed")
        self.assertEqual(label["observation_count"], 2)
        self.assertEqual(label["first_hit"]["rule"], "take_profit_25_pct")
        self.assertEqual(label["marks"][0]["source_path"], "prospective_trajectory_marks")
    def test_build_archived_quote_path_label_computes_mfe_mae_and_first_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir)
            first = archive / "partitioned/quote_date=2026-05-29/run_time_utc=150700/underlying_symbol=AAA/chain.parquet"
            second = archive / "partitioned/quote_date=2026-05-29/run_time_utc=200700/underlying_symbol=AAA/chain.parquet"
            first.parent.mkdir(parents=True, exist_ok=True)
            second.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {
                        "quote_date": "2026-05-29",
                        "run_started_at_utc": "2026-05-29T15:07:00+00:00",
                        "underlying_symbol": "AAA",
                        "expire_date": "2026-06-05",
                        "option_type": "C",
                        "contract_symbol": "AAA260605C00100000",
                        "strike": 100.0,
                        "bid": 1.20,
                        "ask": 1.30,
                        "last": 1.25,
                    }
                ]
            ).to_parquet(first, index=False)
            pd.DataFrame(
                [
                    {
                        "quote_date": "2026-05-29",
                        "run_started_at_utc": "2026-05-29T20:07:00+00:00",
                        "underlying_symbol": "AAA",
                        "expire_date": "2026-06-05",
                        "option_type": "C",
                        "contract_symbol": "AAA260605C00100000",
                        "strike": 100.0,
                        "bid": 0.45,
                        "ask": 0.55,
                        "last": 0.50,
                    }
                ]
            ).to_parquet(second, index=False)
            pick = {
                "run_generated_at_utc": "2026-05-29T14:07:00+00:00",
                "symbol": "AAA",
                "contract_symbol": "AAA260605C00100000",
                "option_type": "call",
                "expiry": "2026-06-05",
                "strike": 100.0,
                "emission_quote": {"mid": 1.00},
            }

            label = build_archived_quote_path_label(pick, archive_dir=archive)

        self.assertEqual(label["status"], "observed")
        self.assertEqual(label["observation_count"], 2)
        self.assertEqual(label["max_favorable_excursion_pct"], 0.25)
        self.assertEqual(label["max_adverse_excursion_pct"], -0.5)
        self.assertEqual(label["first_hit"]["rule"], "take_profit_25_pct")
        self.assertTrue(label["take_profit_25_pct_before_stop_50_pct"])

    def test_apply_archived_quote_path_labels_updates_ledger_picks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir)
            path = archive / "partitioned/quote_date=2026-05-29/run_time_utc=150700/underlying_symbol=AAA/chain.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {
                        "quote_date": "2026-05-29",
                        "run_started_at_utc": "2026-05-29T15:07:00+00:00",
                        "underlying_symbol": "AAA",
                        "expire_date": "2026-06-05",
                        "option_type": "C",
                        "contract_symbol": "AAA1",
                        "strike": 100.0,
                        "bid": 1.40,
                        "ask": 1.60,
                    }
                ]
            ).to_parquet(path, index=False)
            ledger = {
                "entries": [
                    {
                        "run_generated_at_utc": "2026-05-29T14:07:00+00:00",
                        "picks": [
                            {
                                "symbol": "AAA",
                                "contract_symbol": "AAA1",
                                "option_type": "call",
                                "expiry": "2026-06-05",
                                "strike": 100.0,
                                "emission_quote": {"mid": 1.00},
                            }
                        ],
                    }
                ]
            }

            updated, stats = apply_archived_quote_path_labels(ledger, archive_dir=archive)

        self.assertEqual(stats["labels_observed"], 1)
        label = updated["entries"][0]["picks"][0]["outcomes"]["archived_quote_path"]
        self.assertEqual(label["max_favorable_excursion_pct"], 0.5)


if __name__ == "__main__":
    unittest.main()
