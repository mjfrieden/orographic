from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.payoff_stack_audit import build_payoff_stack_audit
from engine.train_payoff_model import load_examples


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the research-only fold-frozen payoff-stack audit."
    )
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        default=None,
        help="Strict executable option outcome dataset; repeat for multiple inputs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/data/diagnostics/payoff_stack_fold_frozen_audit_latest.json"),
    )
    parser.add_argument("--bootstrap-iterations", type=int, default=4000)
    args = parser.parse_args()
    inputs = args.input or [Path("output/option_outcomes_live_recommendations.json")]
    missing = [path for path in inputs if not path.exists()]
    if missing:
        raise SystemExit("Missing strict outcome inputs: " + ", ".join(map(str, missing)))
    non_strict: list[Path] = []
    for path in inputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("artifact") != "option_outcome_dataset"
            or not str(payload.get("label_policy") or "").startswith(
                "strict_executable_quote_or_fill_v"
            )
        ):
            non_strict.append(path)
    if non_strict:
        raise SystemExit(
            "Payoff fold audit requires strict executable datasets: "
            + ", ".join(map(str, non_strict))
        )
    examples, metadata = load_examples(inputs, options_data_dir=None)
    report = build_payoff_stack_audit(
        examples,
        source={
            "input_files": [str(path) for path in inputs],
            "input_artifacts": metadata.get("input_artifact_by_file", {}),
            "deduplicated_examples": metadata.get("deduplicated_examples"),
        },
        bootstrap_iterations=max(int(args.bootstrap_iterations), 100),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "coverage": report["coverage"],
        "next_action": report["next_action"],
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
