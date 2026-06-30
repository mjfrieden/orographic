from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from engine.orographic.prospective import (
    TRADIER_QUOTE_BATCH_SIZE,
    due_fixed_exit_windows,
    fetch_tradier_quotes,
    mark_prospective_ledger,
    mark_prospective_ledger_file,
)


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

    def test_mark_prospective_ledger_ignores_missing_quotes_for_complete_or_expired_picks(self) -> None:
        ledger = {
            "entries": [
                {
                    "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
                    "picks": [
                        {
                            "contract_symbol": "DONE260515C00100000",
                            "expiry": "2026-05-15",
                            "outcomes": {
                                "fixed_exit_marks": {
                                    "one_hour": {"mark": 1.0},
                                    "end_of_day": {"mark": 1.0},
                                    "next_day_close": {"mark": 1.0},
                                    "friday_close": {"mark": 1.0},
                                }
                            },
                        },
                        {
                            "contract_symbol": "OLD260515C00100000",
                            "expiry": "2026-05-15",
                            "outcomes": {"fixed_exit_marks": {"one_hour": None}},
                        },
                    ],
                }
            ]
        }

        _, stats = mark_prospective_ledger(
            ledger,
            {},
            now_utc=datetime(2026, 5, 18, 16, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(stats["quotes_missing"], 0)

    def test_fetch_tradier_quotes_batches_large_contract_lists(self) -> None:
        symbols = [f"AAA260515C{i:08d}" for i in range(TRADIER_QUOTE_BATCH_SIZE + 2)]
        requested_batches: list[list[str]] = []

        class FakeResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request: object, timeout: int = 20) -> FakeResponse:
            self.assertEqual(timeout, 20)
            query = parse_qs(urlparse(request.full_url).query)
            batch = query["symbols"][0].split(",")
            requested_batches.append(batch)
            return FakeResponse(
                {
                    "quotes": {
                        "quote": [
                            {"symbol": symbol, "bid": 1.0, "ask": 1.2}
                            for symbol in batch
                        ]
                    }
                }
            )

        with patch("engine.orographic.prospective.urlopen", fake_urlopen):
            quotes = fetch_tradier_quotes(
                symbols,
                env={"TRADIER_ACCESS_TOKEN": "token", "TRADIER_BASE_URL": "https://api.example.test/v1"},
            )

        self.assertEqual(len(requested_batches), 2)
        self.assertEqual(len(requested_batches[0]), TRADIER_QUOTE_BATCH_SIZE)
        self.assertEqual(len(requested_batches[1]), 2)
        self.assertEqual(sorted(quotes), sorted(symbols))

    def test_mark_prospective_ledger_file_fetches_only_due_active_contracts(self) -> None:
        ledger = {
            "entries": [
                {
                    "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
                    "picks": [
                        {
                            "contract_symbol": "ACTIVE260522C00100000",
                            "expiry": "2026-05-22",
                            "emission_quote": {"mid": 1.0},
                            "outcomes": {
                                "fixed_exit_marks": {
                                    "one_hour": None,
                                    "end_of_day": {"mark": 1.0},
                                    "next_day_close": {"mark": 1.0},
                                    "friday_close": {"mark": 1.0},
                                }
                            },
                        },
                        {
                            "contract_symbol": "DONE260522C00100000",
                            "expiry": "2026-05-22",
                            "outcomes": {
                                "fixed_exit_marks": {
                                    "one_hour": {"mark": 1.0},
                                    "end_of_day": {"mark": 1.0},
                                    "next_day_close": {"mark": 1.0},
                                    "friday_close": {"mark": 1.0},
                                }
                            },
                        },
                        {
                            "contract_symbol": "OLD260515C00100000",
                            "expiry": "2026-05-15",
                            "outcomes": {"fixed_exit_marks": {"one_hour": None}},
                        },
                    ],
                },
                {
                    "run_generated_at_utc": "2026-05-18T18:00:00+00:00",
                    "picks": [
                        {
                            "contract_symbol": "NOTDUE260522C00100000",
                            "expiry": "2026-05-22",
                            "outcomes": {"fixed_exit_marks": {"one_hour": None}},
                        }
                    ],
                },
            ]
        }
        requested_symbols: list[str] = []

        def fake_fetch(symbols: list[str]) -> dict[str, dict[str, object]]:
            requested_symbols.extend(symbols)
            return {"ACTIVE260522C00100000": {"symbol": "ACTIVE260522C00100000", "bid": 1.4, "ask": 1.6}}

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ledger.json"
            path.write_text(json.dumps(ledger), encoding="utf-8")
            with patch("engine.orographic.prospective.fetch_tradier_quotes", fake_fetch):
                _, stats = mark_prospective_ledger_file(
                    path,
                    now_utc=datetime(2026, 5, 18, 16, 5, tzinfo=timezone.utc),
                )

        self.assertEqual(requested_symbols, ["ACTIVE260522C00100000"])
        self.assertEqual(stats["marks_written"], 1)
        self.assertEqual(stats["quotes_missing"], 0)


if __name__ == "__main__":
    unittest.main()
