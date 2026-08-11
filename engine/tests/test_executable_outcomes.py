from __future__ import annotations

import unittest

from engine.orographic.executable_outcomes import (
    CONTRACT_ID,
    ExecutableOutcomeRequest,
    FillObservation,
    OutcomeContractError,
    QuoteObservation,
    build_executable_option_outcome,
)


def _request(**changes) -> ExecutableOutcomeRequest:
    values = {
        "recommendation_id": "rec-123",
        "symbol": "SPY",
        "contract_symbol": "SPY260807C00650000",
        "decision_at_utc": "2026-08-03T15:00:00Z",
        "horizon": "friday_close",
        "horizon_target_at_utc": "2026-08-07T20:00:00Z",
        "label_available_at_utc": "2026-08-07T20:00:10Z",
        "entry_quote": QuoteObservation(1.00, 1.10, "2026-08-03T14:59:55Z", "OPRA"),
        "exit_quote": QuoteObservation(1.40, 1.50, "2026-08-07T20:00:05Z", "OPRA"),
        "contracts": 2,
        "entry_fees_usd": 2.50,
        "exit_fees_usd": 2.50,
    }
    values.update(changes)
    return ExecutableOutcomeRequest(**values)


class ExecutableOutcomeTests(unittest.TestCase):
    def test_quote_only_label_uses_entry_ask_and_exit_bid(self) -> None:
        outcome = build_executable_option_outcome(_request())

        self.assertEqual(outcome["label_contract"]["id"], CONTRACT_ID)
        self.assertEqual(outcome["entry"]["execution_price_source"], "entry_ask_proxy")
        self.assertEqual(outcome["exit"]["execution_price_source"], "exit_bid_proxy")
        self.assertAlmostEqual(outcome["gross_executable_pnl_usd"], 60.0)
        self.assertAlmostEqual(outcome["net_executable_pnl_usd"], 55.0)
        self.assertAlmostEqual(outcome["cost_basis_usd"], 222.5)
        self.assertAlmostEqual(outcome["net_executable_return"], 55.0 / 222.5)
        self.assertTrue(outcome["is_net_profitable"])
        self.assertEqual(outcome["entry"]["quote"]["age_at_decision_seconds"], 5.0)
        self.assertEqual(outcome["exit"]["quote"]["capture_delay_seconds"], 5.0)
        self.assertEqual(outcome["exit"]["quote"]["age_at_label_availability_seconds"], 5.0)

    def test_actual_fills_override_quotes_without_double_counting_slippage(self) -> None:
        outcome = build_executable_option_outcome(_request(
            contracts=1,
            entry_fees_usd=1.0,
            exit_fees_usd=1.0,
            entry_fill=FillObservation(1.08, "2026-08-03T15:00:02Z", "buy-1"),
            exit_fill=FillObservation(1.42, "2026-08-07T20:00:07Z", "sell-1"),
        ))

        self.assertEqual(outcome["entry"]["execution_price_source"], "actual_fill")
        self.assertEqual(outcome["exit"]["execution_price_source"], "actual_fill")
        self.assertEqual(outcome["entry"]["execution_id"], "buy-1")
        self.assertAlmostEqual(outcome["gross_executable_pnl_usd"], 34.0)
        self.assertAlmostEqual(outcome["net_executable_pnl_usd"], 32.0)
        self.assertAlmostEqual(outcome["entry"]["signed_adverse_slippage_usd"], -2.0)
        self.assertAlmostEqual(outcome["exit"]["signed_adverse_slippage_usd"], -2.0)
        self.assertAlmostEqual(outcome["total_signed_adverse_slippage_usd"], -4.0)
        self.assertEqual(outcome["entry"]["quote_age_at_execution_seconds"], 7.0)
        self.assertEqual(outcome["exit"]["quote_age_at_execution_seconds"], 2.0)

    def test_rejects_future_entry_quote_and_premature_exit_evidence(self) -> None:
        with self.assertRaisesRegex(OutcomeContractError, "after the decision"):
            build_executable_option_outcome(_request(
                entry_quote=QuoteObservation(1.0, 1.1, "2026-08-03T15:00:01Z"),
            ))
        with self.assertRaisesRegex(OutcomeContractError, "before the horizon"):
            build_executable_option_outcome(_request(
                exit_quote=QuoteObservation(1.4, 1.5, "2026-08-07T19:59:59Z"),
            ))

    def test_label_availability_must_follow_all_exit_evidence(self) -> None:
        with self.assertRaisesRegex(OutcomeContractError, "before the exit quote"):
            build_executable_option_outcome(_request(label_available_at_utc="2026-08-07T20:00:04Z"))
        with self.assertRaisesRegex(OutcomeContractError, "before the exit fill"):
            build_executable_option_outcome(_request(
                label_available_at_utc="2026-08-07T20:00:06Z",
                exit_fill=FillObservation(1.4, "2026-08-07T20:00:07Z"),
            ))

    def test_fill_slippage_cannot_use_a_future_reference_quote(self) -> None:
        with self.assertRaisesRegex(OutcomeContractError, "reference quote"):
            build_executable_option_outcome(_request(
                exit_fill=FillObservation(1.4, "2026-08-07T20:00:03Z"),
            ))

    def test_rejects_invalid_market_and_naive_timestamps(self) -> None:
        with self.assertRaisesRegex(OutcomeContractError, "bid cannot exceed"):
            build_executable_option_outcome(_request(
                entry_quote=QuoteObservation(1.2, 1.1, "2026-08-03T14:59:55Z"),
            ))
        with self.assertRaisesRegex(OutcomeContractError, "include a timezone"):
            build_executable_option_outcome(_request(decision_at_utc="2026-08-03T15:00:00"))

    def test_optional_freshness_limits_fail_closed(self) -> None:
        with self.assertRaisesRegex(OutcomeContractError, "entry quote exceeds"):
            build_executable_option_outcome(_request(max_entry_quote_age_seconds=4))
        with self.assertRaisesRegex(OutcomeContractError, "exit quote exceeds"):
            build_executable_option_outcome(_request(max_exit_capture_delay_seconds=4))


if __name__ == "__main__":
    unittest.main()
