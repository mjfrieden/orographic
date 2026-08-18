from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import unittest

from engine.orographic.payoff_stack_audit import build_payoff_stack_audit
from engine.orographic.schemas import ContractCandidate
from engine.train_payoff_model import TradeExample


def _example(index: int, decision_date: date) -> TradeExample:
    side = "call" if index % 2 == 0 else "put"
    positive = int(index % 3 != 0)
    realized = 0.25 if positive else -0.20
    score = 0.35 + 0.5 * positive
    candidate = ContractCandidate(
        symbol=f"S{index % 8}",
        contract_symbol=f"S{index % 8}-{decision_date}-{side}-{index}",
        option_type=side,
        expiry=(decision_date + timedelta(days=10)).isoformat(),
        strike=100.0,
        bid=1.0,
        ask=1.1,
        last=1.05,
        premium=1.1,
        contract_cost=110.0,
        spread_pct=0.09,
        open_interest=500,
        volume=100,
        implied_volatility=0.35,
        delta=0.35 if side == "call" else -0.35,
        moneyness=0.01,
        projected_move_pct=0.04,
        breakeven_move_pct=0.03,
        expected_return_pct=realized,
        extrinsic_ratio=0.75,
        scout_score=0.3 if side == "call" else -0.3,
        forge_score=score,
        payoff_model_score=score,
        pre_payoff_forge_score=score,
        path_holding_quality_score=0.55,
        realized_vol_20d=0.25,
        atr_pct_14d=0.03,
    )
    return TradeExample(
        candidate=candidate,
        entry_date=decision_date,
        exit_date=decision_date + timedelta(days=4),
        entry_spot=100.0,
        exit_spot=102.0 if side == "call" else 98.0,
        regime_bucket="risk_on" if index % 4 < 2 else "risk_off",
        pnl_pct=realized,
        prob_positive_option_pnl=positive,
        prob_no_trade=1 - positive,
        prob_fill_quality_ok=1,
        expected_option_return_pct=realized,
        prob_exceeds_breakeven=positive,
        max_favorable_excursion_before_expiry=max(realized, 0.0),
        adverse_excursion_risk=min(realized, 0.0),
    )


class PayoffStackAuditTests(unittest.TestCase):
    def test_empty_evidence_fails_closed(self) -> None:
        report = build_payoff_stack_audit([])
        self.assertEqual(report["status"], "hold_insufficient_evidence")
        self.assertFalse(report["active_model_change_allowed"])

    def test_frozen_folds_purge_unavailable_labels_and_hash_evidence(self) -> None:
        start = date(2026, 1, 2)
        examples = [
            _example(day * 8 + candidate, start + timedelta(days=day))
            for day in range(36)
            for candidate in range(8)
        ]
        report = build_payoff_stack_audit(
            examples,
            fixed_artifact_path=Path("/does/not/exist.pkl"),
            minimum_train_rows=40,
            minimum_validation_rows=16,
            minimum_train_rows_per_side=10,
            minimum_ready_folds=2,
            minimum_validation_dates=5,
            minimum_history_days=30,
            required_window_days=(10, 20, 30),
            bootstrap_iterations=100,
            now_utc=datetime(2026, 3, 1, tzinfo=UTC),
        )

        self.assertFalse(report["active_model_change_allowed"])
        self.assertIn("fold_frozen_retrained", report["variants"])
        self.assertIn("fold_frozen_inverted", report["variants"])
        self.assertTrue(report["sample_gates"]["full_3_6_12_month_windows"]["passed"])
        self.assertNotIn("current_fixed_artifact", report["variants"])
        self.assertGreaterEqual(
            sum(fold["ready"] for fold in report["folds"]),
            2,
        )
        for fold in report["folds"]:
            if not fold["ready"]:
                continue
            self.assertLess(
                fold["training_labels_available_through"],
                fold["validation_start"],
            )
            self.assertEqual(len(fold["training_evidence_sha256"]), 64)
            self.assertEqual(len(fold["validation_evidence_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
