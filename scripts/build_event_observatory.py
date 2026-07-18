from __future__ import annotations

import argparse
from pathlib import Path

from engine.orographic.event_observatory import (
    DEFAULT_EVENT_OBSERVATORY_PATH,
    SOURCE_KINDS,
    build_observatory,
    write_observatory,
    write_quality_report,
)


def _input_spec(value: str) -> tuple[str, str, Path]:
    parts = value.split("=", 2)
    if len(parts) != 3 or parts[1] not in SOURCE_KINDS:
        raise argparse.ArgumentTypeError(
            "Input must use SOURCE=KIND=PATH where KIND is one of " + ", ".join(sorted(SOURCE_KINDS))
        )
    return parts[0], parts[1], Path(parts[2])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Orographic's immutable, point-in-time Event Observatory."
    )
    parser.add_argument(
        "--input",
        action="append",
        type=_input_spec,
        required=True,
        help="Source input as SOURCE=KIND=PATH; repeat for multiple inputs.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_EVENT_OBSERVATORY_PATH)
    parser.add_argument(
        "--quality-output",
        type=Path,
        default=None,
        help="Quality JSON path. Defaults to <output>.quality.json.",
    )
    parser.add_argument(
        "--observed-at",
        default=None,
        help="UTC collection timestamp for sources without first_seen_at. Defaults to now.",
    )
    parser.add_argument("--replace", action="store_true", help="Build without merging the existing output.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame, report = build_observatory(
        args.input,
        existing_path=None if args.replace else args.output,
        observed_at=args.observed_at,
    )
    quality_path = args.quality_output or args.output.with_suffix(args.output.suffix + ".quality.json")
    write_observatory(frame, args.output)
    write_quality_report(report, quality_path)
    print(f"Saved Event Observatory -> {args.output}")
    print(f"Saved quality report -> {quality_path}")
    print(f"Rows: {report.rows}; symbols: {report.symbols}; sources: {report.sources}")
    print(
        f"Duplicates removed: {report.duplicate_rows_removed}; "
        f"invalid rows removed: {report.invalid_rows_removed}; "
        f"p95 delay minutes: {report.p95_delay_minutes}"
    )


if __name__ == "__main__":
    main()
