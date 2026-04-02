from __future__ import annotations

import argparse

from orographic.pipeline import PipelineConfig, load_universe, run_scan, write_snapshot


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

