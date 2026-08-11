from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from engine.orographic.research_readiness import build_research_readiness


NOW = datetime(2026, 7, 21, 20, 0, tzinfo=UTC)


def _pick(*, complete: bool = True, executable: bool = True) -> dict:
    friday = (
        {"mark": 1.1, "bid": 1.05 if executable else None, "pnl_pct_from_emission": 0.1}
        if complete
        else None
    )
    marks = {
        "one_hour": {"mark": 1.0},
        "end_of_day": {"mark": 1.0},
        "next_day_close": {"mark": 1.0},
        "friday_close": friday,
    }
    return {
        "emission_quote": {"mid": 1.0, "ask": 1.02 if executable else None},
        "outcomes": {
            "status": "complete" if complete else "partial",
            "fixed_exit_marks": marks,
        },
    }


def _ledger(*, picks: int = 10, complete: int = 10, executable: int = 10) -> dict:
    rows = [
        _pick(complete=index < complete, executable=index < executable)
        for index in range(picks)
    ]
    marked = picks
    return {
        "updated_at_utc": (NOW - timedelta(minutes=10)).isoformat(),
        "outcome_summary": {
            "picks": picks,
            "with_any_mark": marked,
            "with_all_fixed_marks": complete,
            "complete": complete,
            "partial": picks - complete,
            "pending": 0,
        },
        "last_mark_summary": {"quotes_missing": picks - complete},
        "entries": [{"picks": rows}],
    }


def _snapshot(*, active: bool = True) -> dict:
    return {
        "generated_at_utc": (NOW - timedelta(minutes=15)).isoformat(),
        "model_modes": {"payoff_ranker": "active" if active else "shadow"},
        "model_artifacts": {
            "payoff_model": {"required": True, "present": True},
            "path_model": {"required": False, "present": False},
        },
    }


def _audit(*, feed_status: str = "healthy") -> dict:
    return {
        "status": "passed",
        "summary": {
            "event_feed_status": feed_status,
            "event_feed_http_429_responses": 0,
            "event_feed_new_rows": 10,
        },
        "checks": [{"name": "combined_dataset_consistency", "passed": True}],
    }


def _events(coverage: float = 1.0) -> dict:
    return {"summary": {"complete_outcome_event_coverage_pct": coverage}}


def _promotion(*, eligible: bool = True) -> dict:
    return {
        "decision": "eligible" if eligible else "not_ready",
        "as_of_utc": NOW.isoformat(),
        "windows": [
            {
                "window": "3_month",
                "coverage_complete": eligible,
                "checks": {
                    "sharpe_non_worse": eligible,
                    "drawdown_non_worse": eligible,
                },
            }
        ],
    }


class ResearchReadinessTests(unittest.TestCase):
    def test_green_report_exposes_stable_ui_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = {}
            for name in (
                "snapshot",
                "prospective_ledger",
                "moonshot_ledger",
                "research_audit",
                "event_coverage",
                "promotion_comparison",
            ):
                path = root / f"{name}.json"
                path.write_text("{}", encoding="utf-8")
                paths[name] = path
            report = build_research_readiness(
                snapshot=_snapshot(),
                prospective_ledger=_ledger(),
                moonshot_ledger=_ledger(),
                research_audit=_audit(),
                event_coverage=_events(),
                promotion_comparison=_promotion(),
                source_paths=paths,
                now_utc=NOW,
            )

        self.assertEqual(report["status"], "green")
        self.assertTrue(report["research_claims_allowed"])
        self.assertTrue(report["promotion_allowed"])
        self.assertEqual(len(report["gates"]), 6)
        self.assertEqual(report["ui_contract"]["status_values"], ["green", "amber", "red"])

    def test_observed_mark_coverage_is_not_cohort_completion(self) -> None:
        report = build_research_readiness(
            snapshot=_snapshot(active=False),
            prospective_ledger=_ledger(picks=10, complete=6, executable=6),
            moonshot_ledger=_ledger(picks=10, complete=6, executable=6),
            research_audit=_audit(),
            event_coverage=_events(),
            promotion_comparison=_promotion(eligible=False),
            now_utc=NOW,
        )
        label_gate = next(
            gate for gate in report["gates"] if gate["code"] == "label_cohort_completion"
        )

        self.assertEqual(label_gate["status"], "red")
        self.assertEqual(label_gate["metrics"]["observed_mark_coverage_pct"], 1.0)
        self.assertEqual(label_gate["metrics"]["total_cohort_completion_pct"], 0.6)
        self.assertFalse(report["research_claims_allowed"])

    def test_active_models_and_unready_promotion_are_blocking(self) -> None:
        report = build_research_readiness(
            snapshot=_snapshot(active=True),
            prospective_ledger=_ledger(),
            moonshot_ledger=_ledger(),
            research_audit=_audit(),
            event_coverage=_events(),
            promotion_comparison=_promotion(eligible=False),
            now_utc=NOW,
        )
        promotion_gate = next(
            gate for gate in report["gates"] if gate["code"] == "promotion_eligibility"
        )

        self.assertEqual(promotion_gate["status"], "red")
        self.assertTrue(promotion_gate["blocking"])
        self.assertFalse(report["promotion_allowed"])

    def test_rate_limited_feed_is_amber_when_coverage_is_sufficient(self) -> None:
        audit = _audit(feed_status="rate_limited")
        audit["summary"]["event_feed_http_429_responses"] = 3
        report = build_research_readiness(
            snapshot=_snapshot(),
            prospective_ledger=_ledger(),
            moonshot_ledger=_ledger(),
            research_audit=audit,
            event_coverage=_events(coverage=0.8),
            promotion_comparison=_promotion(),
            now_utc=NOW,
        )
        event_gate = next(
            gate for gate in report["gates"] if gate["code"] == "event_feed_health"
        )

        self.assertEqual(event_gate["status"], "amber")
        self.assertEqual(report["status"], "amber")

    def test_missing_required_artifact_and_model_fail_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["model_artifacts"]["payoff_model"]["present"] = False
        report = build_research_readiness(
            snapshot=snapshot,
            prospective_ledger=_ledger(),
            moonshot_ledger=_ledger(),
            research_audit=_audit(),
            event_coverage=_events(),
            promotion_comparison=_promotion(),
            source_paths={"missing": Path("definitely-not-present.json")},
            now_utc=NOW,
        )
        integrity = next(
            gate for gate in report["gates"] if gate["code"] == "artifact_integrity"
        )

        self.assertEqual(integrity["status"], "red")
        self.assertIn("payoff_model", integrity["metrics"]["missing_required_models"])
        self.assertIn("missing", integrity["metrics"]["missing_sources"])


if __name__ == "__main__":
    unittest.main()
