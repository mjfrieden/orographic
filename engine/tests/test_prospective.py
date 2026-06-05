from __future__ import annotations

from datetime import datetime, timezone
import unittest

from engine.orographic.prospective import due_fixed_exit_windows, mark_prospective_ledger


class ProspectiveLedgerTests(unittest.TestCase):
    def test_due_fixed_exit_windows_respect_market_schedule(self) -> None:
        run_time = "2026-05-11T15:00:00+00:00"

        before_one_hour = due_fixed_exit_windows(
            run_time,
            datetime(2026, 5, 11, 15, 30, tzinfo=timezone.utc),
        )
        after_one_hour = due_fixed_exit_windows(
            run_time,
            datetime(2026, 5, 11, 16, 5, tzinfo=timezone.utc),
        )
        after_close = due_fixed_exit_windows(
            run_time,
            datetime(2026, 5, 11, 21, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(before_one_hour["one_hour"])
        self.assertTrue(after_one_hour["one_hour"])
        self.assertFalse(after_one_hour["end_of_day"])
        self.assertTrue(after_close["end_of_day"])
        self.assertFalse(after_close["next_day_close"])

    def test_mark_prospective_ledger_fills_due_marks_and_path_summary(self) -> None:
        ledger = {
            "artifact": "prospective_pick_ledger",
            "entries": [
                {
                    "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
                    "picks": [
                        {
                            "contract_symbol": "AAA260515C00100000",
                            "emission_quote": {"mid": 1.0, "bid": 0.95, "ask": 1.05},
                            "outcomes": {
                                "status": "pending",
                                "quote_verification": {
                                    "emission_quote_captured": True,
                                    "outcome_quotes_captured": False,
                                },
                                "fixed_exit_marks": {
                                    "one_hour": None,
                                    "end_of_day": None,
                                    "next_day_close": None,
                                    "friday_close": None,
                                },
                                "path_rules": {
                                    "take_profit_40_pct_before_stop_50_pct": None,
                                    "take_profit_25_pct_before_stop_50_pct": None,
                                    "max_favorable_excursion_pct": None,
                                    "max_adverse_excursion_pct": None,
                                    "first_hit": None,
                                },
                                "realized_if_traded": {},
                            },
                        }
                    ],
                }
            ],
        }
        quotes = {
            "AAA260515C00100000": {
                "symbol": "AAA260515C00100000",
                "bid": 1.45,
                "ask": 1.55,
                "last": 1.52,
                "close": 1.4,
            }
        }

        updated, stats = mark_prospective_ledger(
            ledger,
            quotes,
            now_utc=datetime(2026, 5, 11, 21, 0, tzinfo=timezone.utc),
        )

        pick = updated["entries"][0]["picks"][0]
        self.assertEqual(stats["marks_written"], 2)
        self.assertEqual(pick["outcomes"]["status"], "partial")
        self.assertEqual(pick["outcomes"]["fixed_exit_marks"]["one_hour"]["mark"], 1.5)
        self.assertEqual(pick["outcomes"]["fixed_exit_marks"]["one_hour"]["pnl_pct_from_emission"], 0.5)
        self.assertEqual(pick["outcomes"]["fixed_exit_marks"]["end_of_day"]["mark_source"], "mid")
        self.assertIsNone(pick["outcomes"]["fixed_exit_marks"]["next_day_close"])
        self.assertEqual(pick["outcomes"]["path_rules"]["max_favorable_excursion_pct"], 0.5)
        self.assertEqual(
            pick["outcomes"]["path_rules"]["first_hit"],
            {"window": "one_hour", "rule": "take_profit_40_pct_fixed_mark_proxy"},
        )
        self.assertTrue(pick["outcomes"]["quote_verification"]["outcome_quotes_captured"])
        self.assertEqual(updated["outcome_summary"]["partial"], 1)
        self.assertEqual(stats["picks_partial"], 1)
        self.assertEqual(stats["picks_pending"], 0)

    def test_mark_prospective_ledger_counts_missing_due_quotes(self) -> None:
        ledger = {
            "entries": [
                {
                    "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
                    "picks": [{"contract_symbol": "MISS", "outcomes": {"fixed_exit_marks": {"one_hour": None}}}],
                }
            ]
        }

        _, stats = mark_prospective_ledger(
            ledger,
            {},
            now_utc=datetime(2026, 5, 11, 16, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(stats["quotes_missing"], 1)


if __name__ == "__main__":
    unittest.main()
