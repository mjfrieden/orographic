from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from engine.orographic.trajectory_exit_overlay import (
    ARTIFACT as TRAJECTORY_EXIT_OVERLAY,
    EARLY_HARVEST_ARTIFACT,
    evaluate_early_harvest_overlay,
    evaluate_pick_overlay,
    evaluate_trajectory_exit_overlay,
)
from engine.orographic.weekly_alpha_review import (
    FRICTION_VETO_VALUE,
    HOLD_OUT_CHALLENGER,
    PAIRED_OPPOSITE_CHALLENGER,
    PRODUCTION_LANE,
    RESEARCH_SIDE_SPLIT,
    TIGHT_SPREAD_CHALLENGER,
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

    def test_compact_mark_count_is_visible_even_without_a_first_hit(self) -> None:
        ledger = {
            "entries": [{
                "run_generated_at_utc": "2026-09-08T14:00:00+00:00",
                "picks": [{
                    "lane": "live",
                    "emission_quote": {"ask": 1.0},
                    "outcomes": {
                        "trajectory_overlay": {"mark_count": 9, "first_hit": None},
                        "fixed_exit_marks": _mark("next_day_close", -0.40),
                    },
                }],
            }]
        }
        report = evaluate_trajectory_exit_overlay(ledger, as_of_utc=datetime(2026, 9, 9, tzinfo=UTC))
        self.assertEqual(report["coverage"]["trajectory_marks_seen"], 9)
        self.assertEqual(report["coverage"]["hold_fallbacks"], 1)

    def test_early_harvest_credits_tighter_fill_from_compact_target_hit(self) -> None:
        pick = {
            "lane": "live",
            "emission_quote": {"ask": 1.0},
            "outcomes": {
                "trajectory_overlay": {
                    "mark_count": 4,
                    "first_hit": {
                        "captured_at_utc": "2026-09-09T18:00:00+00:00",
                        "pnl_pct_from_emission": 0.25,
                        "event": "target_25_bid",
                    },
                },
                "fixed_exit_marks": {"next_day_close": {"pnl_pct_from_emission": -0.40}},
            },
        }
        scored = evaluate_pick_overlay(pick, target_return=0.10, stop_return=-0.40)
        assert scored is not None
        self.assertEqual(scored["overlay_reason"], "target_10_bid")
        self.assertAlmostEqual(scored["overlay_return"], 0.10)
        self.assertAlmostEqual(scored["return_lift"], 0.50)

    def test_compact_crossings_score_a_ten_percent_fill_without_a_25_hit(self) -> None:
        pick = {
            "lane": "live",
            "emission_quote": {"ask": 1.0},
            "outcomes": {
                "trajectory_overlay": {
                    "mark_count": 6,
                    "first_hit": None,
                    "min_bid_pnl": -0.08,
                    "max_bid_pnl": 0.12,
                    "crossings": {
                        "target_10_bid": {
                            "captured_at_utc": "2026-09-09T18:00:00+00:00",
                            "pnl_pct_from_emission": 0.12,
                            "event": "target_10_bid",
                        },
                    },
                },
                "fixed_exit_marks": {"next_day_close": {"pnl_pct_from_emission": -0.40}},
            },
        }
        scored = evaluate_pick_overlay(pick, target_return=0.10, stop_return=-0.40)
        assert scored is not None
        self.assertEqual(scored["overlay_reason"], "target_10_bid")
        self.assertAlmostEqual(scored["overlay_return"], 0.10)

    def test_early_harvest_overlay_stays_observation_only(self) -> None:
        ledger = {
            "entries": [{
                "run_generated_at_utc": "2026-09-09T17:11:18+00:00",
                "picks": [{
                    "lane": "live",
                    "emission_quote": {"ask": 2.17},
                    "outcomes": {
                        "trajectory_marks": [
                            {"captured_at_utc": "2026-09-09T18:11:00+00:00", "pnl_pct_from_emission": 0.1174},
                        ],
                        "fixed_exit_marks": _mark("next_day_close", -0.4085),
                    },
                }],
            }]
        }
        report = evaluate_early_harvest_overlay(ledger, as_of_utc=datetime(2026, 9, 11, tzinfo=UTC))
        self.assertEqual(report["artifact"], EARLY_HARVEST_ARTIFACT)
        self.assertEqual(report["authority"], "observation_only_never_used_for_routing")
        self.assertEqual(report["coverage"]["target_hits"], 1)
        self.assertFalse(report["promotion_ready"])


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
        self.assertEqual(actions[TRAJECTORY_EXIT_OVERLAY], "hold_do_not_promote")
        self.assertEqual(actions[HOLD_OUT_CHALLENGER], "keep_observation_only")
        self.assertFalse(review["kill_switch"]["rebuild_production_change_allowed"])
        self.assertEqual(review["platform"]["feature_snapshot_schema"], "orographic_pick_features_v1")
        self.assertEqual(review["research_evidence_source"], "dashboard_summary")

    def test_opens_paired_opposite_and_keeps_working_friction_veto(self) -> None:
        as_of = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
        dashboard = {"entries": [{
            "run_generated_at_utc": "2026-09-09T17:11:18+00:00",
            "picks": [
                {
                    "lane": "live", "symbol": "SBUX", "contract_symbol": "SBUX1",
                    "option_type": "call",
                    "emission_quote": {"ask": 2.17, "spread_pct": 0.0376},
                    "outcomes": {"fixed_exit_marks": _mark("next_day_close", -0.4085)},
                },
                {
                    "lane": "council_holdout", "symbol": "WFC", "contract_symbol": "WFC1",
                    "option_type": "call",
                    "emission_quote": {"ask": 1.5, "spread_pct": 0.0665},
                    "outcomes": {"fixed_exit_marks": _mark("next_day_close", -0.1586)},
                },
                {
                    "lane": "council_holdout", "symbol": "WFC", "contract_symbol": "WFC2",
                    "option_type": "call",
                    "emission_quote": {"ask": 1.1, "spread_pct": 0.139},
                    "outcomes": {"fixed_exit_marks": _mark("next_day_close", -0.2353)},
                },
                {
                    "lane": "friction_veto", "symbol": "IWM", "contract_symbol": "IWM1",
                    "option_type": "call",
                    "emission_quote": {"ask": 1.0, "spread_pct": 0.0052},
                    "outcomes": {"fixed_exit_marks": _mark("next_day_close", -0.4109)},
                },
                {
                    "lane": "paired_side_observation", "symbol": "SBUX", "contract_symbol": "SBUXP",
                    "option_type": "put",
                    "emission_quote": {"ask": 2.0, "spread_pct": 0.0478},
                    "outcomes": {"fixed_exit_marks": _mark("next_day_close", 0.3823)},
                },
            ],
        }]}
        review = build_weekly_alpha_review(
            as_of_utc=as_of,
            snapshot={
                "generated_at_utc": "2026-09-10T20:10:46+00:00",
                "scan_settings": {"model_stack": "production_v2"},
                "regime": {"mode": "extreme_vol"},
                "council": {"live_board": [], "abstain": True},
            },
            board_history={"entries": [
                {"run_generated_at_utc": "2026-09-09T17:11:18+00:00", "abstain": False,
                 "live_board": [{"symbol": "SBUX", "contract_symbol": "SBUX1", "option_type": "call"}]},
            ]},
            dashboard=dashboard,
            scan_health={"status": "failed", "failed_checks": [{"name": "research_audit_passed"}]},
            rebuild_readiness={"status": "hold_collecting_executable_evidence", "production_model_change_allowed": False},
            mart_shadow={
                "mart_id": "abc",
                "generated_at_utc": "2026-08-24T03:36:22+00:00",
                "cross_system_comparison": {"paired_executable_outcomes": 0, "paired_market_dates": 3},
                "execution_quality": {"executable_win_rate": 0.34, "avg_executable_return": -0.02},
            },
            mart_sync={"status": "missing_orographic_canonical"},
        )

        self.assertEqual(review["schema_version"], 4)
        self.assertEqual(review["challenger_to_open"]["experiment_id"], PAIRED_OPPOSITE_CHALLENGER)
        self.assertAlmostEqual(review["paired_opposite_challenger"]["mean_return_lift"], 0.7908)
        self.assertAlmostEqual(review["tight_spread_challenger"]["mean_return_lift"], 0.2499)
        self.assertEqual(review["tight_spread_challenger"]["rows"][0]["holdout_contract"], "WFC1")
        self.assertTrue(review["friction_veto_value"]["gate_working"])
        self.assertEqual(review["friction_veto_value"]["action"], "keep_as_gate")
        self.assertEqual(review["production"]["week_live_marks"]["resolved"], 1)
        self.assertAlmostEqual(review["production"]["week_live_marks"]["mean_return"], -0.4085)
        self.assertAlmostEqual(review["research_side_split"]["put_minus_call_return"], 0.7908)
        self.assertTrue(review["research_side_split"]["live_emitted_losing_side"])
        self.assertFalse(review["research_side_split"]["promotion_ready"])
        actions = {row["lane"]: row["action"] for row in review["lane_decisions"]}
        self.assertEqual(actions[PRODUCTION_LANE], "keep")
        self.assertEqual(actions[FRICTION_VETO_VALUE], "keep_as_gate")
        self.assertEqual(actions[TRAJECTORY_EXIT_OVERLAY], "replace")
        self.assertEqual(actions[EARLY_HARVEST_ARTIFACT], "hold_do_not_promote")
        self.assertEqual(actions[PAIRED_OPPOSITE_CHALLENGER], "keep_observation_only")
        self.assertEqual(actions[TIGHT_SPREAD_CHALLENGER], "keep_observation_only")
        self.assertEqual(actions[RESEARCH_SIDE_SPLIT], "keep_observation_only")
        self.assertFalse(review["kill_switch"]["rebuild_production_change_allowed"])

    def test_infers_opposite_side_from_occ_contract_when_option_type_is_absent(self) -> None:
        as_of = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
        dashboard = {"entries": [{
            "run_generated_at_utc": "2026-09-09T17:11:18+00:00",
            "picks": [
                {
                    "lane": "live", "symbol": "SBUX", "contract_symbol": "SBUX260918C00100000",
                    "emission_quote": {"ask": 2.17, "spread_pct": 0.0376},
                    "outcomes": {"fixed_exit_marks": _mark("next_day_close", -0.4085)},
                },
                {
                    "lane": "paired_side_observation", "symbol": "SBUX",
                    "contract_symbol": "SBUX260918P00100000",
                    "emission_quote": {"ask": 2.0, "spread_pct": 0.0478},
                    "outcomes": {"fixed_exit_marks": _mark("next_day_close", 0.3823)},
                },
            ],
        }]}
        review = build_weekly_alpha_review(
            as_of_utc=as_of,
            snapshot={"scan_settings": {"model_stack": "production_v2"}, "regime": {"mode": "extreme_vol"}, "council": {"live_board": [], "abstain": True}},
            board_history={"entries": []},
            dashboard=dashboard,
            scan_health={"status": "failed", "failed_checks": []},
            rebuild_readiness={"production_model_change_allowed": False},
            mart_shadow={"generated_at_utc": "2026-08-24T03:36:22+00:00", "cross_system_comparison": {"paired_executable_outcomes": 0}},
        )
        self.assertEqual(review["challenger_to_open"]["experiment_id"], PAIRED_OPPOSITE_CHALLENGER)
        self.assertEqual(review["paired_opposite_challenger"]["rows"][0]["opposite_side"], "put")
        self.assertAlmostEqual(review["paired_opposite_challenger"]["mean_return_lift"], 0.7908)


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

    def test_scan_health_uses_diagnostic_audit_fallback(self) -> None:
        now = datetime(2026, 9, 10, 20, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot = root / "latest_run.json"
            snapshot.write_text(json.dumps({
                "generated_at_utc": "2026-09-10T20:10:46+00:00",
                "regime": {"mode": "extreme_vol"},
                "summary": {"scout_signal_count": 0, "forge_candidate_count": 0},
                "council": {"abstain": True, "summary": {"live_count": 0, "shadow_count": 0}},
            }))
            ledger = root / "ledger.json"
            ledger.write_text(json.dumps({
                "updated_at_utc": "2026-09-10T20:13:00+00:00",
                "outcome_summary": {
                    "picks": 1, "pending": 0, "partial": 0, "complete": 1,
                    "with_any_mark": 1, "with_all_fixed_marks": 1,
                    "missing_outcome_quotes": 0, "capture_policy_v2_picks": 1,
                    "capture_windows_valid": 4, "capture_windows_quote_missing": 0,
                    "capture_windows_missed": 0,
                },
                "last_mark_summary": {"marks_written": 1, "quotes_missing": 0},
            }))
            fallback = root / "audit_latest.json"
            fallback.write_text(json.dumps({"status": "passed", "summary": {}}))
            archive = root / "archive.json"
            archive.write_text(json.dumps({"summary": {"rows_archived": 9, "symbols_archived": 3}}))
            rows = root / "rows.json"
            rows.write_text(json.dumps([{}]))
            report = build_scan_health_summary(
                snapshot=snapshot,
                prospective_ledger=ledger,
                moonshot_ledger=ledger,
                research_audit=root / "missing_audit.json",
                archive_manifest=archive,
                recommendation_dataset=rows,
                moonshot_dataset=root / "missing_moonshot.json",
                combined_dataset=rows,
                now_utc=now,
                r2_status="success",
                dashboard_push_status="success",
                dashboard_deploy_status="success",
                research_audit_fallbacks=[fallback],
            )

        checks = {check["name"]: check for check in report["checks"]}
        self.assertTrue(checks["research_audit_passed"]["passed"])
        self.assertEqual(report["research"]["audit_status"], "passed")


class SharedMartCanonicalFallbackTests(unittest.TestCase):
    def test_prefers_restored_canonical_when_consolidated_bundle_is_missing(self) -> None:
        from scripts.sync_shared_research_mart import _candidate_canonical_dirs, _valid_canonical_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing = root / "canonical_evidence"
            restored = root / "restored_canonical_evidence"
            restored.mkdir()
            (restored / "evidence_manifest.json").write_text("{}", encoding="utf-8")
            dirs = _candidate_canonical_dirs(missing)
            found = None
            for candidate in dirs:
                # Resolve against the temp root rather than process cwd.
                mapped = restored if candidate.name == "restored_canonical_evidence" else missing
                found = _valid_canonical_dir(mapped)
                if found is not None:
                    break
            self.assertEqual(found, restored)

    def test_missing_canonical_status_lists_checked_dirs(self) -> None:
        from scripts.sync_shared_research_mart import _candidate_canonical_dirs

        dirs = [str(path) for path in _candidate_canonical_dirs(Path("output/canonical_evidence"))]
        self.assertIn("output/canonical_evidence", dirs)
        self.assertIn("output/restored_canonical_evidence", dirs)


if __name__ == "__main__":
    unittest.main()
