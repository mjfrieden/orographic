from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from engine.orographic.scout_pair_readiness import build_scout_pair_readiness


def _pair_rows(
    pair_number: int,
    *,
    label: str,
    decision_date: datetime,
    regime: str,
) -> list[dict[str, object]]:
    call_return = 0.20 if label == "call_edge" else -0.10
    put_return = 0.20 if label == "put_edge" else -0.10
    if label == "no_trade":
        call_return = put_return = -0.05
    shared = {
        "paired_observation_id": f"PAIR-{pair_number}",
        "symbol": f"SYM{pair_number % 20:02d}",
        "entry_date": decision_date.date().isoformat(),
        "exit_date": (decision_date + timedelta(days=4)).date().isoformat(),
        "expiry": (decision_date + timedelta(days=10)).date().isoformat(),
        "executable_label_available_at_utc": (
            decision_date + timedelta(days=4)
        ).isoformat().replace("+00:00", "Z"),
        "executable_label_contract_version": 2,
        "regime_mode": regime,
    }
    return [
        {**shared, "option_type": "call", "pnl_pct": call_return},
        {**shared, "option_type": "put", "pnl_pct": put_return},
    ]


class ScoutPairReadinessTests(unittest.TestCase):
    def test_empty_evidence_fails_closed_and_reports_archive_gap(self) -> None:
        report = build_scout_pair_readiness(
            {"artifact": "option_outcome_dataset", "rows": []},
            historical_archive_manifest={
                "summary": {"symbol_count": 1, "quote_date_count": 3, "row_count": 299617}
            },
            now_utc=datetime(2026, 8, 13, tzinfo=UTC),
        )

        self.assertEqual(report["status"], "hold_collecting_pairs")
        self.assertFalse(report["active_model_change_allowed"])
        self.assertEqual(report["coverage"]["complete_explicit_pairs"], 0)
        self.assertFalse(
            report["historical_archive"]["adequate_for_historical_pair_backfill"]
        )
        self.assertFalse(report["promotion_gates"]["minimum_complete_pairs"]["passed"])

    def test_ready_evidence_builds_purged_fold_frozen_plan(self) -> None:
        rows: list[dict[str, object]] = []
        start = datetime(2026, 1, 2, tzinfo=UTC)
        for index in range(240):
            label = "call_edge" if index % 3 == 0 else "put_edge" if index % 3 == 1 else "no_trade"
            regime = "risk_on" if index % 2 == 0 else "risk_off"
            rows.extend(
                _pair_rows(
                    index,
                    label=label,
                    decision_date=start + timedelta(days=index),
                    regime=regime,
                )
            )

        report = build_scout_pair_readiness(
            {"artifact": "option_outcome_dataset", "rows": rows},
            now_utc=datetime(2026, 12, 31, tzinfo=UTC),
        )

        self.assertEqual(report["status"], "ready_for_fold_frozen_evaluation")
        self.assertEqual(report["coverage"]["complete_explicit_pairs"], 240)
        self.assertEqual(len(report["coverage"]["evidence_sha256"]), 64)
        self.assertGreaterEqual(
            report["fold_frozen_evaluation_plan"]["ready_folds"], 3
        )
        for fold in report["fold_frozen_evaluation_plan"]["folds"]:
            self.assertLess(
                fold["training_labels_available_through"], fold["validation_start"]
            )
            self.assertEqual(len(fold["training_evidence_sha256"]), 64)
            self.assertEqual(len(fold["validation_evidence_sha256"]), 64)

    def test_mismatched_or_non_strict_pairs_do_not_count(self) -> None:
        rows = _pair_rows(
            1,
            label="call_edge",
            decision_date=datetime(2026, 5, 1, tzinfo=UTC),
            regime="neutral",
        )
        rows[0]["executable_label_contract_version"] = 1
        rows[1]["symbol"] = "OTHER"

        report = build_scout_pair_readiness(
            {"artifact": "option_outcome_dataset", "rows": rows}
        )

        self.assertEqual(report["coverage"]["complete_explicit_pairs"], 0)
        self.assertEqual(report["coverage"]["strict_executable_pair_rows"], 1)
        self.assertFalse(report["promotion_gates"]["strict_executable_labels"]["passed"])

    def test_unknown_regime_cannot_satisfy_breadth_gate(self) -> None:
        rows: list[dict[str, object]] = []
        start = datetime(2026, 1, 2, tzinfo=UTC)
        for index in range(240):
            rows.extend(
                _pair_rows(
                    index,
                    label="call_edge" if index % 2 == 0 else "put_edge",
                    decision_date=start + timedelta(days=index),
                    regime="unknown" if index % 2 == 0 else "risk_on",
                )
            )

        report = build_scout_pair_readiness(
            {"artifact": "option_outcome_dataset", "rows": rows}
        )

        self.assertFalse(report["promotion_gates"]["regime_coverage"]["passed"])
        self.assertEqual(report["status"], "hold_collecting_pairs")


if __name__ == "__main__":
    unittest.main()
