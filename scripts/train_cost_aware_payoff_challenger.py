from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.train_payoff_model import DEFAULT_OPTIONS_DATA_DIR, default_input_paths, train


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train the observation-only cost-aware multi-task payoff challenger."
    )
    parser.add_argument("--input", action="append", type=Path, default=None)
    parser.add_argument(
        "--output-model",
        type=Path,
        default=Path("engine/orographic/models/payoff_cost_aware_challenger.pkl"),
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=Path("output/payoff_cost_aware_challenger_report.json"),
    )
    parser.add_argument(
        "--output-model-card",
        type=Path,
        default=Path("engine/orographic/models/payoff_cost_aware_challenger_card.json"),
    )
    parser.add_argument("--options-data-dir", type=Path, default=DEFAULT_OPTIONS_DATA_DIR)
    parser.add_argument("--min-side-examples", type=int, default=75)
    args = parser.parse_args()
    input_paths = args.input or default_input_paths()
    missing = [path for path in input_paths if not path.exists()]
    if missing:
        raise SystemExit("Missing inputs: " + ", ".join(str(path) for path in missing))
    report = train(
        input_paths,
        output_model=args.output_model,
        output_report=args.output_report,
        output_model_card=args.output_model_card,
        options_data_dir=args.options_data_dir,
        min_side_examples=max(int(args.min_side_examples), 1),
        artifact_mode="observation_only_never_used_for_routing",
    )
    print(json.dumps({
        "status": report["promotion_gates"]["status"],
        "model": str(args.output_model),
        "execution_effect": "none_observation_only",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
