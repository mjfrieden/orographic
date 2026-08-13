from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.pipeline import (
    append_moonshot_prospective_ledger,
    append_research_run_ledger,
    append_board_recommendation_history,
    append_prospective_pick_ledger,
    build_live_shadow_attribution_artifact,
    build_promotion_readiness,
    PipelineConfig,
    append_side_aware_shadow_ledger,
    load_universe,
    run_scan,
    write_forge_rejection_waterfall_artifacts,
    write_live_shadow_attribution_artifacts,
    write_snapshot,
)
from engine.orographic.positions import append_position_history, fetch_position_snapshot
from engine.orographic.payoff_challenger_evidence import write_payoff_challenger_evidence
from engine.orographic.counterfactual_veto_evidence import write_counterfactual_veto_evidence
from engine.orographic.promotion_comparison import write_promotion_comparison

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Orographic weekly options scan.")
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated universe override. If omitted, the universe file or default list is used.",
    )
    parser.add_argument(
        "--universe-file",
        default="engine/sample_universe.txt",
        help="Optional newline-separated universe file.",
    )
    parser.add_argument(
        "--output",
        default="web/data/latest_run.json",
        help="Snapshot JSON output path.",
    )
    parser.add_argument(
        "--positions-log-output",
        default="",
        help="Optional private JSON path for per-run position snapshots. Do not point this at a public, git-tracked file.",
    )
    parser.add_argument(
        "--positions-log-max-entries",
        type=int,
        default=500,
        help="Maximum number of per-run position snapshots to keep when --positions-log-output is enabled.",
    )
    parser.add_argument(
        "--research-ledger-output",
        default="",
        help="Optional path for the canonical per-run research ledger. Defaults beside the snapshot diagnostics.",
    )
    parser.add_argument(
        "--research-ledger-max-entries",
        type=int,
        default=500,
        help="Maximum number of scan entries to retain in the canonical research ledger.",
    )
    parser.add_argument(
        "--no-research-ledger",
        action="store_true",
        help="Disable writing the canonical per-run research ledger.",
    )
    parser.add_argument(
        "--prospective-ledger-output",
        default="",
        help="Optional path for the prospective pick ledger. Defaults beside the snapshot diagnostics.",
    )
    parser.add_argument(
        "--prospective-ledger-max-entries",
        type=int,
        default=500,
        help="Maximum number of scan entries to retain in the prospective pick ledger.",
    )
    parser.add_argument(
        "--no-prospective-ledger",
        action="store_true",
        help="Disable writing the prospective pick ledger.",
    )
    parser.add_argument(
        "--moonshot-ledger-output",
        default="",
        help="Optional path for the dedicated moonshot prospective ledger. Defaults beside the snapshot diagnostics.",
    )
    parser.add_argument(
        "--moonshot-ledger-max-entries",
        type=int,
        default=500,
        help="Maximum number of scan entries to retain in the moonshot prospective ledger.",
    )
    parser.add_argument(
        "--no-moonshot-ledger",
        action="store_true",
        help="Disable writing the dedicated moonshot prospective ledger.",
    )
    parser.add_argument(
        "--shadow-ledger-output",
        default="",
        help="Optional path for the side-aware Scout shadow disagreement ledger. Defaults beside the snapshot diagnostics.",
    )
    parser.add_argument(
        "--shadow-ledger-max-entries",
        type=int,
        default=500,
        help="Maximum number of scan entries to retain in the side-aware Scout shadow ledger.",
    )
    parser.add_argument(
        "--no-shadow-ledger",
        action="store_true",
        help="Disable writing the side-aware Scout shadow disagreement ledger.",
    )
    parser.add_argument(
        "--board-history-output",
        default="",
        help="Optional path for a rolling live/shadow board recommendation history. Defaults beside the snapshot diagnostics.",
    )
    parser.add_argument(
        "--board-history-max-entries",
        type=int,
        default=500,
        help="Maximum number of scan entries to retain in the board recommendation history.",
    )
    parser.add_argument(
        "--no-board-history",
        action="store_true",
        help="Disable writing the rolling board recommendation history.",
    )
    parser.add_argument("--live-size", type=int, default=1)
    parser.add_argument(
        "--model-stack",
        choices=("unified_rnd", "current_gated"),
        default="unified_rnd",
        help="Unified R&D runs all integrated models in one lane; current_gated preserves the old promotion-gated baseline.",
    )
    parser.add_argument(
        "--forge-intake",
        type=int,
        default=12,
        help="Number of Scout signals to send into Forge. Defaults to 12 to reduce live-board starvation.",
    )
    parser.add_argument(
        "--minimum-days-to-expiry",
        type=int,
        default=7,
        help="Minimum option DTE for live scan contract selection. Defaults to the recovered 7-14 DTE policy.",
    )
    parser.add_argument(
        "--maximum-days-to-expiry",
        type=int,
        default=14,
        help="Maximum option DTE for live scan contract selection. Defaults to the recovered 7-14 DTE policy.",
    )
    parser.add_argument(
        "--minimum-live-score",
        type=float,
        default=0.86,
        help="Minimum Forge score required for live-board call candidates.",
    )
    parser.add_argument(
        "--minimum-put-live-score",
        type=float,
        default=0.84,
        help="Minimum Forge score required for live-board put candidates.",
    )
    parser.add_argument(
        "--max-live-extrinsic-ratio",
        type=float,
        default=0.90,
        help="Maximum extrinsic ratio allowed on live-board candidates.",
    )
    parser.add_argument(
        "--moonshot-size",
        type=int,
        default=1,
        help="Number of Nimrod-inspired tail-upside satellite picks to emit.",
    )
    parser.add_argument(
        "--moonshot-threshold",
        type=float,
        default=0.68,
        help="Minimum tail-upside score for the dedicated moonshot lane.",
    )
    parser.add_argument(
        "--moonshot-max-cost-basis",
        type=float,
        default=225.0,
        help="Maximum premium cost basis for moonshot satellite eligibility.",
    )
    parser.add_argument(
        "--enforce-pre-council-friction-gate",
        action="store_true",
        help="Research-only: drop contracts that fail the pre-Council friction gate before Council selection.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.symbols.strip():
        universe = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    else:
        universe = load_universe(args.universe_file)

    payload = run_scan(
        PipelineConfig(
            universe=universe,
            live_size=max(int(args.live_size), 1),
            shadow_size=0,
            forge_intake=max(int(args.forge_intake), 1),
            counterfactual_observation_size=0,
            preserve_shadow_veto_live_policy=False,
            model_stack=str(args.model_stack),
            minimum_days_to_expiry=max(int(args.minimum_days_to_expiry), 0),
            maximum_days_to_expiry=max(int(args.maximum_days_to_expiry), 0),
            minimum_live_score=max(min(float(args.minimum_live_score), 1.0), 0.0),
            minimum_put_live_score=max(min(float(args.minimum_put_live_score), 1.0), 0.0),
            max_live_extrinsic_ratio=max(min(float(args.max_live_extrinsic_ratio), 1.0), 0.0),
            moonshot_size=max(int(args.moonshot_size), 0),
            moonshot_threshold=max(min(float(args.moonshot_threshold), 1.0), 0.0),
            moonshot_max_cost_basis=max(float(args.moonshot_max_cost_basis), 0.0),
            enforce_pre_council_friction_gate=bool(args.enforce_pre_council_friction_gate),
        )
    )
    diagnostic_sources: dict[str, str] = {}
    if not args.no_research_ledger:
        research_ledger_path = (
            Path(args.research_ledger_output)
            if args.research_ledger_output.strip()
            else Path(args.output).parent / "diagnostics" / "research_run_ledger.json"
        )
        written = append_research_run_ledger(
            research_ledger_path,
            payload,
            max_entries=max(int(args.research_ledger_max_entries), 1),
        )
        diagnostic_sources["research_ledger"] = str(written)
        log.info("Updated research run ledger at %s.", written)
    if not args.no_prospective_ledger:
        prospective_ledger_path = (
            Path(args.prospective_ledger_output)
            if args.prospective_ledger_output.strip()
            else Path(args.output).parent / "diagnostics" / "prospective_pick_ledger.json"
        )
        written = append_prospective_pick_ledger(
            prospective_ledger_path,
            payload,
            max_entries=max(int(args.prospective_ledger_max_entries), 1),
        )
        diagnostic_sources["prospective_ledger"] = str(written)
        log.info("Updated prospective pick ledger at %s.", written)
    if not args.no_moonshot_ledger:
        moonshot_ledger_path = (
            Path(args.moonshot_ledger_output)
            if args.moonshot_ledger_output.strip()
            else Path(args.output).parent / "diagnostics" / "moonshot_prospective_ledger.json"
        )
        written = append_moonshot_prospective_ledger(
            moonshot_ledger_path,
            payload,
            max_entries=max(int(args.moonshot_ledger_max_entries), 1),
        )
        diagnostic_sources["moonshot_ledger"] = str(written)
        log.info("Updated moonshot prospective ledger at %s.", written)
    if not args.no_board_history:
        board_history_path = (
            Path(args.board_history_output)
            if args.board_history_output.strip()
            else Path(args.output).parent / "diagnostics" / "board_recommendation_history.json"
        )
        written = append_board_recommendation_history(
            board_history_path,
            payload,
            max_entries=max(int(args.board_history_max_entries), 1),
        )
        diagnostic_sources["board_history"] = str(written)
        log.info("Updated board recommendation history at %s.", written)
    if not args.no_shadow_ledger:
        ledger_path = (
            Path(args.shadow_ledger_output)
            if args.shadow_ledger_output.strip()
            else Path(args.output).parent / "diagnostics" / "side_aware_scout_shadow_ledger.json"
        )
        written = append_side_aware_shadow_ledger(
            ledger_path,
            payload,
            max_entries=max(int(args.shadow_ledger_max_entries), 1),
        )
        diagnostic_sources["shadow_ledger"] = str(written)
        log.info("Updated side-aware Scout shadow ledger at %s.", written)

    if diagnostic_sources:
        prospective_source = diagnostic_sources.get("prospective_ledger")
        shadow_source = diagnostic_sources.get("shadow_ledger")
        if prospective_source:
            challenger_evidence_path = Path(args.output).parent / "diagnostics" / "payoff_challenger_evidence_latest.json"
            challenger_evidence = write_payoff_challenger_evidence(
                Path(prospective_source),
                challenger_evidence_path,
            )
            diagnostic_sources["payoff_challenger_evidence"] = str(challenger_evidence_path)
            log.info(
                "Updated payoff challenger prospective evidence at %s (%s).",
                challenger_evidence_path,
                challenger_evidence["decision"],
            )
            veto_evidence_path = Path(args.output).parent / "diagnostics" / "counterfactual_veto_evidence_latest.json"
            veto_evidence = write_counterfactual_veto_evidence(
                Path(prospective_source),
                veto_evidence_path,
            )
            diagnostic_sources["counterfactual_veto_evidence"] = str(veto_evidence_path)
            log.info(
                "Updated advisory Scout veto evidence at %s (%s).",
                veto_evidence_path,
                veto_evidence["decision"],
            )
        if prospective_source and shadow_source:
            comparison_path = Path(args.output).parent / "diagnostics" / "promotion_shadow_active_comparison_latest.json"
            comparison = write_promotion_comparison(Path(prospective_source), Path(shadow_source), comparison_path)
            diagnostic_sources["promotion_comparison"] = str(comparison_path)
            log.info("Updated promotion shadow/active comparison at %s (%s).", comparison_path, comparison["decision"])
        payload["diagnostic_sources"] = diagnostic_sources
        payload["promotion_readiness"] = build_promotion_readiness(payload)
        payload["attribution"] = build_live_shadow_attribution_artifact(payload)

    write_snapshot(args.output, payload)
    diagnostic_paths = write_forge_rejection_waterfall_artifacts(args.output, payload)
    log.info(
        "Wrote Forge rejection waterfall artifacts to %s and %s.",
        diagnostic_paths[0],
        diagnostic_paths[1],
    )
    attribution_paths = write_live_shadow_attribution_artifacts(args.output, payload)
    log.info(
        "Wrote live/shadow attribution artifacts to %s and %s.",
        attribution_paths[0],
        attribution_paths[1],
    )

    if args.positions_log_output.strip():
        try:
            snapshot = fetch_position_snapshot(
                run_generated_at_utc=payload.get("generated_at_utc"),
            )
            if snapshot.get("configured"):
                append_position_history(
                    args.positions_log_output,
                    snapshot,
                    max_entries=max(int(args.positions_log_max_entries), 1),
                )
                log.info(
                    "Captured %d standing positions to %s.",
                    snapshot.get("positions_count", 0),
                    args.positions_log_output,
                )
            else:
                log.info(
                    "Skipped position history capture for %s: %s",
                    args.positions_log_output,
                    snapshot.get("status", "unknown"),
                )
        except Exception as exc:
            log.warning(
                "Position history capture failed for %s: %s",
                args.positions_log_output,
                exc,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
