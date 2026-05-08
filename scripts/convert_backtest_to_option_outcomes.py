from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.backtest.results import option_outcome_dataset_payload_from_results_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a legacy backtest_results JSON or normalize an option_outcome_dataset artifact without rerunning replay."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to a backtest results JSON or existing option_outcome_dataset artifact.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for the canonical option_outcome_dataset artifact. Defaults beside the input.",
    )
    return parser.parse_args()


def default_output_path(input_path: Path) -> Path:
    if "backtest_results" in input_path.name:
        return input_path.with_name(input_path.name.replace("backtest_results", "option_outcomes", 1))
    if "option_outcomes" in input_path.name:
        return input_path
    return input_path.with_name(f"{input_path.stem}_option_outcomes.json")


def main() -> None:
    args = parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    converted = option_outcome_dataset_payload_from_results_payload(payload)
    output_path = args.output or default_output_path(args.input)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(converted, indent=2) + "\n", encoding="utf-8")
    print(f"Converted {args.input} -> {output_path}")


if __name__ == "__main__":
    main()
