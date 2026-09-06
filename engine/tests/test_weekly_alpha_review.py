from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from engine.orographic.weekly_alpha_review import HOLD_OUT_CHALLENGER, build_weekly_alpha_review
from scripts.audit_research_data_capture import build_audit_report
from scripts.build_scan_health_summary import build_scan_health_summary

import pandas as pd


class WeeklyAlphaReviewTests(unittest.TestCase):
    def test_opens_holdout_challenger_and_keeps_single_production_lane(self) -> None:
        as_of = datetime(2026, 9, 2, 16, 0, tzinfo=UTC)
        snapshot = {
            "generated_at_utc": "2026-09-02T14:24:00+00:00",
            "scan_settings": {"model_stack": "production_v2"},
            "regime": {"mode": "neutral"},
            "council": {
                "live_board": [{
                    "symbol": "WFC", "contract_symbol": "WFC260911C00088000",
                    "option_type": "call", "ask": 2.14, "spread_pct": 0.0676,
                    "prob_big_win": 0.59, "expected_tail_utility": 0.66,
                }]
            },
        }
        board = {"entries": [
            {"run_generated_at_utc": "2026-09-01T14:13:49+00:00", "abstain": False,
             "live_board": [{"symbol": "BAC", "contract_symbol": "BAC1", "option_type": "call", "ask": 1.0}]},
            {"run_generated_at_utc": "2026-09-01T17:14:42+00:00", "abstain": True, "live_board": []},
        ]}
        dashboard = {"entries": [{
            "run_generated_at_utc": "2026-09-01T14:13:49+00:00",
            "picks": [
                {
                    "lane": "live", "symbol": "BAC", "contract_symbol": "BAC1",
                    "emission_quote": {"spread_pct": 0.03},
                    "outcomes": {"fixed_exit_marks": {"end_of_day": {"pnl_pct_from_emission": -0.17}}},
                },
                {
                    "lane": "council_holdout", "symbol": "XLE", "contract_symbol": "XLE1",
                    "emission_quote": {"spread_pct": 0.06},
                    "outcomes": {"fixed_exit_marks": {"end_of_day": {"pnl_pct_from_emission": 0.62}}},
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
        self.assertEqual(review["challenger_to_open"]["experiment_id"], HOLD_OUT_CHALLENGER)
        self.assertEqual(review["challenger_to_open"]["mean_return_lift"], 0.79)
        actions = {row["lane"]: row["action"] for row in review["lane_decisions"]}
        self.assertEqual(actions["production_v2_council_live_board"], "keep")
        self.assertEqual(actions["moonshot"], "remain_retired")
        self.assertEqual(actions["path_hazard_challenger"], "replace")
        self.assertEqual(actions[HOLD_OUT_CHALLENGER], "open_observation_only")
        self.assertFalse(review["kill_switch"]["rebuild_production_change_allowed"])


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
        now = datetime(2026, 9, 2, 14, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot = root / "latest_run.json"
            snapshot.write_text(json.dumps({
                "generated_at_utc": "2026-09-02T14:24:00+00:00",
                "regime": {"mode": "neutral"},
                "summary": {"scout_signal_count": 8, "forge_candidate_count": 3},
                "council": {"abstain": False, "summary": {"live_count": 1, "shadow_count": 0}},
            }))
            ledger = root / "ledger.json"
            ledger.write_text(json.dumps({
                "updated_at_utc": "2026-09-02T14:26:00+00:00",
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
