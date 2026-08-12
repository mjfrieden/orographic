from __future__ import annotations

from datetime import datetime, timezone
import json
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
import unittest
from unittest import mock

from engine.orographic.prospective import (
    _outcome_summary,
    backfill_executable_labels_from_fixed_marks,
    due_fixed_exit_windows,
    fetch_tradier_quotes,
    mark_prospective_ledger,
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
                                    "capture_policy_version": 2,
                                },
                                "fixed_exit_marks": {
                                    "one_hour": None,
                                    "end_of_day": None,
                                    "next_day_close": None,
                                    "friday_close": None,
                                },
                                "capture_attempts": {
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
            now_utc=datetime(2026, 5, 11, 20, 10, tzinfo=timezone.utc),
        )

        pick = updated["entries"][0]["picks"][0]
        self.assertEqual(stats["marks_written"], 1)
        self.assertEqual(pick["outcomes"]["status"], "partial")
        self.assertIsNone(pick["outcomes"]["fixed_exit_marks"]["one_hour"])
        self.assertEqual(pick["outcomes"]["fixed_exit_marks"]["end_of_day"]["mark_source"], "mid")
        self.assertIsNone(pick["outcomes"]["fixed_exit_marks"]["next_day_close"])
        self.assertEqual(pick["outcomes"]["path_rules"]["max_favorable_excursion_pct"], 0.5)
        self.assertEqual(
            pick["outcomes"]["path_rules"]["first_hit"],
            {"window": "end_of_day", "rule": "take_profit_40_pct_fixed_mark_proxy"},
        )
        self.assertTrue(pick["outcomes"]["quote_verification"]["outcome_quotes_captured"])
        self.assertEqual(updated["outcome_summary"]["partial"], 1)
        self.assertEqual(stats["picks_partial"], 1)
        self.assertEqual(stats["picks_pending"], 0)
        self.assertEqual(stats["capture_windows_missed"], 1)
        self.assertEqual(pick["outcomes"]["capture_attempts"]["one_hour"]["status"], "missed_live_window")

    def test_mark_prospective_ledger_captures_fresh_intraday_trajectory_marks(self) -> None:
        ledger = {
            "entries": [{
                "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
                "picks": [{
                    "contract_symbol": "AAA260515C00100000",
                    "emission_quote": {"mid": 1.0, "bid": 0.95, "ask": 1.05},
                    "outcomes": {
                        "quote_verification": {"capture_policy_version": 2},
                        "fixed_exit_marks": {},
                    },
                }],
            }],
        }
        quote = {
            "bid": 1.10,
            "ask": 1.20,
            "bid_observed_at_utc": "2026-05-11T15:14:55+00:00",
        }
        first, stats = mark_prospective_ledger(
            ledger,
            {"AAA260515C00100000": quote},
            now_utc=datetime(2026, 5, 11, 15, 15, tzinfo=timezone.utc),
        )
        repeated, repeated_stats = mark_prospective_ledger(
            first,
            {"AAA260515C00100000": quote},
            now_utc=datetime(2026, 5, 11, 15, 15, 30, tzinfo=timezone.utc),
        )

        marks = repeated["entries"][0]["picks"][0]["outcomes"]["trajectory_marks"]
        self.assertEqual(stats["trajectory_marks_written"], 1)
        self.assertEqual(repeated_stats["trajectory_marks_written"], 0)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["pnl_pct_from_emission"], 0.15)

    def test_trajectory_capture_stops_after_friday_close(self) -> None:
        ledger = {"entries": [{
            "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
            "picks": [{
                "contract_symbol": "AAA260515C00100000",
                "emission_quote": {"mid": 1.0},
                "outcomes": {"quote_verification": {"capture_policy_version": 2}, "fixed_exit_marks": {}},
            }],
        }]}

        updated, stats = mark_prospective_ledger(
            ledger,
            {"AAA260515C00100000": {"bid": 2.0, "ask": 2.1}},
            now_utc=datetime(2026, 5, 15, 21, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(stats["trajectory_active_picks"], 0)
        self.assertNotIn("trajectory_marks", updated["entries"][0]["picks"][0]["outcomes"])

    def test_mark_prospective_ledger_counts_missing_due_quotes(self) -> None:
        ledger = {
            "entries": [
                {
                    "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
                    "picks": [{"contract_symbol": "MISS", "outcomes": {
                        "quote_verification": {"capture_policy_version": 2},
                        "fixed_exit_marks": {"one_hour": None},
                    }}],
                }
            ]
        }

        _, stats = mark_prospective_ledger(
            ledger,
            {},
            now_utc=datetime(2026, 5, 11, 16, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(stats["quotes_missing"], 1)
        self.assertEqual(stats["capture_windows_quote_missing"], 1)

    def test_mark_prospective_ledger_rejects_stale_broker_quote(self) -> None:
        ledger = {"entries": [{
            "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
            "picks": [{
                "contract_symbol": "STALE",
                "emission_quote": {"bid": 0.95, "ask": 1.05, "mid": 1.0},
                "outcomes": {
                    "quote_verification": {"capture_policy_version": 2},
                    "fixed_exit_marks": {"one_hour": None},
                    "capture_attempts": {"one_hour": None},
                },
            }],
        }]}

        updated, stats = mark_prospective_ledger(
            ledger,
            {"STALE": {
                "bid": 1.45,
                "ask": 1.55,
                "bid_observed_at_utc": "2026-05-11T15:00:00+00:00",
            }},
            now_utc=datetime(2026, 5, 11, 16, 5, tzinfo=timezone.utc),
        )

        outcomes = updated["entries"][0]["picks"][0]["outcomes"]
        self.assertIsNone(outcomes["fixed_exit_marks"]["one_hour"])
        self.assertEqual(outcomes["capture_attempts"]["one_hour"]["status"], "stale_quote_retryable")
        self.assertEqual(stats["capture_windows_stale_quote"], 1)
        self.assertFalse(outcomes["quote_verification"]["capture_integrity_passed"])

    def test_mark_prospective_ledger_adds_executable_label_when_evidence_is_complete(self) -> None:
        ledger = {
            "entries": [{
                "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
                "picks": [{
                    "recommendation_id": "rec-1",
                    "symbol": "AAA",
                    "contract_symbol": "AAA260515C00100000",
                    "emission_quote": {
                        "bid": 0.95,
                        "ask": 1.05,
                        "mid": 1.0,
                        "captured_at_utc": "2026-05-11T14:59:55+00:00",
                        "entry_data_source": "real_chain",
                    },
                    "outcomes": {
                        "quote_verification": {"capture_policy_version": 2},
                        "fixed_exit_marks": {"one_hour": None},
                    },
                }],
            }],
        }
        quotes = {"AAA260515C00100000": {"bid": 1.45, "ask": 1.55, "last": 1.5}}

        updated, stats = mark_prospective_ledger(
            ledger,
            quotes,
            now_utc=datetime(2026, 5, 11, 16, 5, tzinfo=timezone.utc),
        )

        label = updated["entries"][0]["picks"][0]["outcomes"]["executable_labels"]["one_hour"]
        self.assertEqual(stats["executable_labels_written"], 1)
        self.assertEqual(stats["executable_labels_skipped"], 0)
        self.assertEqual(label["label_contract"]["version"], 2)
        self.assertAlmostEqual(label["midpoint_counterfactual_pnl_usd"], 50.0)
        self.assertAlmostEqual(label["total_execution_friction_usd"], 10.0)
        self.assertEqual(label["entry"]["execution_price"], 1.05)
        self.assertEqual(label["exit"]["execution_price"], 1.45)
        self.assertAlmostEqual(label["net_executable_pnl_usd"], 40.0)
        self.assertEqual(label["entry"]["quote"]["age_at_decision_seconds"], 5.0)
        self.assertEqual(label["exit"]["quote"]["capture_delay_seconds"], 300.0)

    def test_mark_prospective_ledger_preserves_legacy_mark_when_v1_evidence_is_incomplete(self) -> None:
        ledger = {"entries": [{
            "run_generated_at_utc": "2026-05-11T15:00:00+00:00",
            "picks": [{
                "contract_symbol": "AAA260515C00100000",
                "emission_quote": {"bid": 0.95, "ask": 1.05, "mid": 1.0},
                "outcomes": {
                    "quote_verification": {"capture_policy_version": 2},
                    "fixed_exit_marks": {"one_hour": None},
                },
            }],
        }]}

        updated, stats = mark_prospective_ledger(
            ledger,
            {"AAA260515C00100000": {"bid": 1.45, "ask": 1.55}},
            now_utc=datetime(2026, 5, 11, 16, 5, tzinfo=timezone.utc),
        )

        outcomes = updated["entries"][0]["picks"][0]["outcomes"]
        self.assertIsNotNone(outcomes["fixed_exit_marks"]["one_hour"])
        self.assertNotIn("one_hour", outcomes["executable_labels"])
        self.assertEqual(stats["executable_labels_skipped"], 1)

    def test_mark_prospective_ledger_backfills_v1_from_stored_historical_quote(self) -> None:
        ledger = {"entries": [{
            "run_generated_at_utc": "2026-05-11T15:00:00Z",
            "picks": [{
                "recommendation_id": "rec-1",
                "symbol": "AAA",
                "contract_symbol": "AAA260515C00100000",
                "emission_quote": {
                    "bid": 0.95,
                    "ask": 1.05,
                    "captured_at_utc": "2026-05-11T14:59:55Z",
                },
                "outcomes": {"fixed_exit_marks": {"one_hour": {
                    "bid": 1.25,
                    "ask": 1.35,
                    "captured_at_utc": "2026-05-11T16:00:05Z",
                    "mark": 1.3,
                }}},
            }],
        }]}

        updated, stats = mark_prospective_ledger(
            ledger,
            {"AAA260515C00100000": {"bid": 9.0, "ask": 9.1}},
            now_utc=datetime(2026, 5, 11, 16, 5, tzinfo=timezone.utc),
        )

        label = updated["entries"][0]["picks"][0]["outcomes"]["executable_labels"]["one_hour"]
        self.assertEqual(stats["marks_written"], 0)
        self.assertEqual(stats["executable_labels_written"], 1)
        self.assertEqual(label["exit"]["execution_price"], 1.25)

    def test_offline_backfill_uses_stored_quote_without_current_market_data(self) -> None:
        ledger = {"entries": [{
            "run_generated_at_utc": "2026-05-11T15:00:00Z",
            "picks": [{
                "recommendation_id": "rec-1",
                "symbol": "AAA",
                "contract_symbol": "AAA260515C00100000",
                "emission_quote": {
                    "bid": 0.95,
                    "ask": 1.05,
                    "captured_at_utc": "2026-05-11T14:59:55Z",
                },
                "outcomes": {"fixed_exit_marks": {"one_hour": {
                    "bid": 1.25,
                    "ask": 1.35,
                    "captured_at_utc": "2026-05-11T16:00:05Z",
                    "mark": 1.3,
                }}},
            }],
        }]}

        updated, stats = backfill_executable_labels_from_fixed_marks(ledger)

        label = updated["entries"][0]["picks"][0]["outcomes"]["executable_labels"]["one_hour"]
        self.assertEqual(stats, {"marks_seen": 1, "labels_written": 1, "labels_skipped": 0})
        self.assertEqual(label["entry"]["execution_price"], 1.05)
        self.assertEqual(label["exit"]["execution_price"], 1.25)
        self.assertNotIn("executable_labels", ledger["entries"][0]["picks"][0]["outcomes"])

    def test_outcome_summary_attributes_resolved_shadow_disagreement_pnl(self) -> None:
        summary = _outcome_summary([{"picks": [{
            "scores": {
                "payoff_shadow_prob_positive": 0.7,
                "payoff_shadow_disagreement": True,
            },
            "outcomes": {
                "status": "complete",
                "fixed_exit_marks": {},
                "executable_labels": {"friday_close": {"net_executable_return": -0.25}},
            },
        }]}])

        self.assertEqual(summary["payoff_shadow_scored"], 1)
        self.assertEqual(summary["payoff_shadow_disagreements"], 1)
        self.assertEqual(summary["payoff_shadow_resolved_friday"], 1)
        self.assertEqual(summary["payoff_shadow_disagreement_avg_net_return"], -0.25)

    def test_fetch_tradier_quotes_batches_requests(self) -> None:
        requested_batches: list[list[str]] = []

        class _Response:
            def __init__(self, payload: dict[str, object]) -> None:
                self._payload = payload

            def read(self) -> bytes:
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        def fake_urlopen(request, timeout: int = 20):
            symbols = parse_qs(urlparse(request.full_url).query)["symbols"][0].split(",")
            requested_batches.append(symbols)
            payload = {
                "quotes": {
                    "quote": [
                        {"symbol": symbol, "bid": 1.0, "ask": 1.2, "last": 1.1, "close": 1.05}
                        for symbol in symbols
                    ]
                }
            }
            return _Response(payload)

        env = {"TRADIER_ACCESS_TOKEN": "token", "TRADIER_BASE_URL": "https://api.tradier.com/v1"}
        symbols = [
            "AAA260515C00100000",
            "BBB260515C00100000",
            "CCC260515C00100000",
            "DDD260515C00100000",
            "EEE260515C00100000",
        ]

        with mock.patch("engine.orographic.prospective.urlopen", side_effect=fake_urlopen):
            quotes = fetch_tradier_quotes(symbols, env=env, batch_size=2)

        self.assertEqual(requested_batches, [symbols[:2], symbols[2:4], symbols[4:]])
        self.assertEqual(sorted(quotes.keys()), sorted(symbols))

    def test_fetch_tradier_quotes_retries_transient_timeout(self) -> None:
        class _Response:
            def read(self) -> bytes:
                return json.dumps({"quotes": {"quote": {"symbol": "AAA", "bid": 1.0, "ask": 1.2}}}).encode("utf-8")

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        env = {"TRADIER_ACCESS_TOKEN": "token", "TRADIER_BASE_URL": "https://api.tradier.com/v1"}
        with (
            mock.patch(
                "engine.orographic.prospective.urlopen",
                side_effect=[URLError("timed out"), _Response()],
            ) as urlopen_mock,
            mock.patch("engine.orographic.prospective.time_module.sleep") as sleep_mock,
        ):
            quotes = fetch_tradier_quotes(["AAA"], env=env)

        self.assertEqual(quotes["AAA"]["symbol"], "AAA")
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
