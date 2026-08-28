from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.pipeline import (
    append_research_run_ledger,
    append_board_recommendation_history,
    append_prospective_pick_ledger,
    PipelineConfig,
    load_universe,
    run_scan,
    write_forge_rejection_waterfall_artifacts,
    write_snapshot,
)
from engine.orographic.positions import append_position_history, fetch_position_snapshot

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
        choices=("production_v2",),
        default="production_v2",
        help="Single production Scout, volatility/contract ranker, and Council path.",
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
        "--enforce-pre-council-friction-gate",
        action="store_true",
        help="Research-only: drop contracts that fail the pre-Council friction gate before Council selection.",
    )
    parser.add_argument(
        "--live-execution-policy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply executable-liquidity and same-contract cooldown vetoes to the live Council board.",
    )
    parser.add_argument("--same-contract-cooldown-hours", type=float, default=72.0)
    parser.add_argument("--max-entry-spread-pct", type=float, default=0.12)
    parser.add_argument("--min-execution-open-interest", type=int, default=200)
    parser.add_argument("--min-execution-volume", type=int, default=25)
    parser.add_argument("--min-execution-edge-after-friction-pct", type=float, default=0.05)
    parser.add_argument(
        "--no-paired-side-capture",
        action="store_true",
        help="Disable research-only matched call/put outcome capture for this scan.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.symbols.strip():
        universe = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    else:
        universe = load_universe(args.universe_file)
    board_history_path = (
        Path(args.board_history_output)
        if args.board_history_output.strip()
        else Path(args.output).parent / "diagnostics" / "board_recommendation_history.json"
    )

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
            moonshot_size=0,
            moonshot_threshold=1.0,
            moonshot_max_cost_basis=0.0,
            enforce_pre_council_friction_gate=bool(args.enforce_pre_council_friction_gate),
            live_execution_policy_enabled=bool(args.live_execution_policy),
            same_contract_cooldown_hours=max(float(args.same_contract_cooldown_hours), 0.0),
            max_entry_spread_pct=max(min(float(args.max_entry_spread_pct), 1.0), 0.0),
            min_execution_open_interest=max(int(args.min_execution_open_interest), 0),
            min_execution_volume=max(int(args.min_execution_volume), 0),
            min_execution_edge_after_friction_pct=float(args.min_execution_edge_after_friction_pct),
            paired_side_capture_enabled=not bool(args.no_paired_side_capture),
            board_history_path=board_history_path,
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
    if diagnostic_sources:
        payload["diagnostic_sources"] = diagnostic_sources

    write_snapshot(args.output, payload)
    diagnostic_paths = write_forge_rejection_waterfall_artifacts(args.output, payload)
    log.info(
        "Wrote Forge rejection waterfall artifacts to %s and %s.",
        diagnostic_paths[0],
        diagnostic_paths[1],
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
