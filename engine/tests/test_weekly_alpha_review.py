from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from engine.orographic.trajectory_exit_overlay import (
    ARTIFACT as TRAJECTORY_EXIT_OVERLAY,
    evaluate_pick_overlay,
    evaluate_trajectory_exit_overlay,
)
from engine.orographic.weekly_alpha_review import (
    HOLD_OUT_CHALLENGER,
    PRODUCTION_LANE,
    build_weekly_alpha_review,
)
from scripts.audit_research_data_capture import build_audit_report
from scripts.build_scan_health_summary import build_scan_health_summary

import pandas as pd


def _mark(window: str, pnl: float) -> dict:
    return {window: {"pnl_pct_from_emission": pnl}}


class TrajectoryExitOverlayTests(unittest.TestCase):
    def test_target_fill_requires_recorded_bid_not_midpoint(self) -> None:
        pick = {
            "lane": "live",
            "symbol": "XLE",
            "contract_symbol": "XLE1",
            "emission_quote": {"ask": 1.0},
            "outcomes": {
                "trajectory_marks": [
                    {"captured_at_utc": "2026-09-08T16:00:00+00:00", "bid": 1.10, "ask": 1.40},
                    {"captured_at_utc": "2026-09-08T17:00:00+00:00", "bid": 1.25, "ask": 1.30},
                ],
                "fixed_exit_marks": _mark("friday_close", -0.40),
            },
        }
        scored = evaluate_pick_overlay(pick)
        assert scored is not None
        self.assertEqual(scored["overlay_return"], 0.25)
        self.assertEqual(scored["overlay_reason"], "target_25_bid")
        self.assertAlmostEqual(scored["return_lift"], 0.65)

    def test_overlay_summary_stays_observation_only(self) -> None:
        ledger = {
            "entries": [{
                "run_generated_at_utc": "2026-09-08T14:00:00+00:00",
                "picks": [{
                    "lane": "live",
                    "symbol": "WFC",
                    "emission_quote": {"ask": 2.0},
                    "outcomes": {
                        "trajectory_marks": [
                            {"captured_at_utc": "2026-09-08T16:00:00+00:00", "pnl_pct_from_emission": -0.55},
                        ],
                        "fixed_exit_marks": _mark("friday_close", -0.20),
                    },
                }],
            }]
        }
        report = evaluate_trajectory_exit_overlay(ledger, as_of_utc=datetime(2026, 9, 9, tzinfo=UTC))
        self.assertEqual(report["artifact"], TRAJECTORY_EXIT_OVERLAY)
        self.assertEqual(report["authority"], "observation_only_never_used_for_routing")
        self.assertFalse(report["promotion_ready"])
        self.assertEqual(report["coverage"]["stop_hits"], 1)
        self.assertEqual(report["overall"]["mean_return_lift"], -0.3)

    def test_compact_dashboard_first_hit_is_enough_to_score_overlay(self) -> None:
        pick = {
            "lane": "live",
            "emission_quote": {"ask": 1.0},
            "outcomes": {
                "trajectory_overlay": {
                    "mark_count": 12,
                    "first_hit": {
                        "captured_at_utc": "2026-09-08T17:00:00+00:00",
                        "bid": 1.25,
                        "pnl_pct_from_emission": 0.25,
                        "event": "target_25_bid",
                    },
                },
                "fixed_exit_marks": {"friday_close": {"pnl_pct_from_emission": -0.20}},
            },
        }
        scored = evaluate_pick_overlay(pick)
        assert scored is not None
        self.assertEqual(scored["overlay_reason"], "target_25_bid")
        self.assertAlmostEqual(scored["return_lift"], 0.45)


class WeeklyAlphaReviewTests(unittest.TestCase):
    def test_selects_holdout_by_decision_score_not_realized_pnl(self) -> None:
        as_of = datetime(2026, 9, 9, 16, 0, tzinfo=UTC)
        snapshot = {
            "generated_at_utc": "2026-09-09T14:10:23+00:00",
            "scan_settings": {"model_stack": "production_v2"},
            "regime": {"mode": "neutral"},
            "council": {"live_board": [], "abstain": True},
        }
        board = {"entries": [
            {"run_generated_at_utc": "2026-09-08T14:13:49+00:00", "abstain": False,
             "live_board": [{"symbol": "BAC", "contract_symbol": "BAC1", "option_type": "call", "ask": 1.0}]},
            {"run_generated_at_utc": "2026-09-08T17:14:42+00:00", "abstain": True, "live_board": []},
        ]}
        dashboard = {"entries": [{
            "run_generated_at_utc": "2026-09-08T14:13:49+00:00",
            "picks": [
                {
                    "lane": "live", "symbol": "BAC", "contract_symbol": "BAC1",
                    "scores": {"final_candidate_score": 0.91},
                    "outcomes": {"fixed_exit_marks": _mark("end_of_day", -0.17)},
                },
                {
                    "lane": "council_holdout", "symbol": "CHERRY", "contract_symbol": "CHERRY1",
                    "scores": {"final_candidate_score": 0.40},
                    "outcomes": {"fixed_exit_marks": _mark("end_of_day", 0.80)},
                },
                {
                    "lane": "council_holdout", "symbol": "XLE", "contract_symbol": "XLE1",
                    "scores": {"final_candidate_score": 0.88},
                    "outcomes": {
                        "fixed_exit_marks": _mark("end_of_day", 0.10),
                        "trajectory_marks": [
                            {"captured_at_utc": "2026-09-08T16:00:00+00:00", "bid": 1.3, "ask": 1.35},
                        ],
                    },
                    "emission_quote": {"ask": 1.0},
                },
            ],
        }]}
        review = build_weekly_alpha_review(
            as_of_utc=as_of,
            snapshot=snapshot,
            board_history=board,
            dashboard=dashboard,
            scan_health={"status": "failed", "failed_checks": [{"name": "research_audit_passed"}]},
            rebuild_readiness={"status": "hold_collecting_executable_evidence", "production_model_change_allowed": False},
            mart_shadow={
                "mart_id": "abc",
                "generated_at_utc": "2026-08-24T03:36:22+00:00",
                "cross_system_comparison": {"paired_executable_outcomes": 0, "paired_market_dates": 3},
                "execution_quality": {"executable_win_rate": 0.34, "avg_executable_return": -0.02},
            },
            mart_sync={"status": "cirrus_export_unavailable"},
            payoff_challenger={"rank_replay": {"active_top1_avg_net_return": -0.33, "shadow_top1_avg_net_return": -0.35}},
            path_hazard={"status": "hold"},
            promotion={"decision": "not_ready"},
            exit_shadow={"summary": {"live_by_policy": {"standing_limit_25": {"coverage_pct": 0.13}}}},
        )

        self.assertEqual(review["alpha_verdict"], "insufficient_paired_evidence")
        self.assertTrue(review["cirrus"]["mart_stale"])
        self.assertEqual(review["challenger_to_open"]["experiment_id"], HOLD_OUT_CHALLENGER)
        self.assertEqual(review["challenger_to_open"]["rows"][0]["holdout_top1_symbol"], "XLE")
        self.assertEqual(review["challenger_to_open"]["mean_return_lift"], 0.27)
        actions = {row["lane"]: row["action"] for row in review["lane_decisions"]}
        self.assertEqual(actions[PRODUCTION_LANE], "keep")
        self.assertEqual(actions["moonshot"], "remain_retired")
        self.assertEqual(actions["path_hazard_challenger"], "replace")
        self.assertEqual(actions[TRAJECTORY_EXIT_OVERLAY], "open_observation_only")
        self.assertEqual(actions[HOLD_OUT_CHALLENGER], "keep_observation_only")
        self.assertFalse(review["kill_switch"]["rebuild_production_change_allowed"])
        self.assertEqual(review["platform"]["feature_snapshot_schema"], "orographic_pick_features_v1")
        self.assertEqual(review["research_evidence_source"], "dashboard_summary")


class RetiredMoonshotDatasetTests(unittest.TestCase):
    def test_audit_passes_when_moonshot_dataset_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = root / "coverage_manifest.json"
            manifest.write_text(json.dumps({"summary": {"rows_archived": 4, "symbols_archived": 1}}))
            prospective = root / "prospective.json"
            prospective.write_text(json.dumps({"entries": [{"picks": [{"contract_symbol": "AAA1"}]}]}))
            moonshot_ledger = root / "moonshot.json"
            moonshot_ledger.write_text(json.dumps({"entries": [{"picks": [{"contract_symbol": "OLD1"}]}]}))
            recommendation = root / "option_recommendation_outcomes.parquet"
            combined = root / "all_recommendation_outcomes.parquet"
            pd.DataFrame([{"contract_symbol": "AAA1"}]).to_parquet(recommendation, index=False)
            pd.DataFrame([{"contract_symbol": "AAA1"}]).to_parquet(combined, index=False)

            report = build_audit_report(
                live_archive_manifest=manifest,
                prospective_ledger=prospective,
                moonshot_ledger=moonshot_ledger,
                recommendation_dataset=recommendation,
                moonshot_dataset=root / "missing_moonshot.parquet",
                combined_dataset=combined,
            )

        self.assertEqual(report["status"], "passed")
        checks = {check["name"]: check for check in report["checks"]}
        self.assertTrue(checks["moonshot_dataset_matches_ledger"]["passed"])
        self.assertEqual(checks["combined_dataset_consistency"]["expected"], 1)

    def test_scan_health_treats_missing_moonshot_dataset_as_zero_rows(self) -> None:
        now = datetime(2026, 9, 9, 14, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot = root / "latest_run.json"
            snapshot.write_text(json.dumps({
                "generated_at_utc": "2026-09-09T14:10:23+00:00",
                "regime": {"mode": "neutral"},
                "summary": {"scout_signal_count": 8, "forge_candidate_count": 3},
                "council": {"abstain": False, "summary": {"live_count": 1, "shadow_count": 0}},
            }))
            ledger = root / "ledger.json"
            ledger.write_text(json.dumps({
                "updated_at_utc": "2026-09-09T14:11:00+00:00",
                "outcome_summary": {
                    "picks": 1, "pending": 0, "partial": 0, "complete": 1,
                    "with_any_mark": 1, "with_all_fixed_marks": 1,
                    "missing_outcome_quotes": 0, "capture_policy_v2_picks": 1,
                    "capture_windows_valid": 4, "capture_windows_quote_missing": 0,
                    "capture_windows_missed": 0,
                },
                "last_mark_summary": {"marks_written": 1, "quotes_missing": 0},
            }))
            audit = root / "audit.json"
            audit.write_text(json.dumps({"status": "passed", "summary": {}}))
            archive = root / "archive.json"
            archive.write_text(json.dumps({"summary": {"rows_archived": 9, "symbols_archived": 3}}))
            recommendations = root / "recommendations.json"
            recommendations.write_text(json.dumps([{}]))
            combined = root / "combined.json"
            combined.write_text(json.dumps([{}]))
            report = build_scan_health_summary(
                snapshot=snapshot,
                prospective_ledger=ledger,
                moonshot_ledger=ledger,
                research_audit=audit,
                archive_manifest=archive,
                recommendation_dataset=recommendations,
                moonshot_dataset=root / "missing_moonshot.json",
                combined_dataset=combined,
                now_utc=now,
                r2_status="success",
                dashboard_push_status="success",
                dashboard_deploy_status="success",
            )

        checks = {check["name"]: check for check in report["checks"]}
        self.assertTrue(checks["combined_dataset_consistency"]["passed"])
        self.assertTrue(checks["combined_dataset_consistency"]["moonshot_dataset_missing"])


if __name__ == "__main__":
    unittest.main()
