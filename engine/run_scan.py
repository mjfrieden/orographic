from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.pipeline import (
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
    parser.add_argument("--live-size", type=int, default=3)
    parser.add_argument("--shadow-size", type=int, default=3)
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
            shadow_size=max(int(args.shadow_size), 1),
        )
    )
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
