from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.path_hazard import load_records, save_artifact, train_and_evaluate


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the observation-only competing-risk exit challenger.")
    parser.add_argument("--input", action="append", type=Path, default=None)
    parser.add_argument("--output-model", type=Path, default=Path("engine/orographic/models/path_hazard_challenger.pkl"))
    parser.add_argument("--output-card", type=Path, default=Path("engine/orographic/models/path_hazard_challenger_card.json"))
    parser.add_argument("--output-diagnostic", type=Path, default=Path("web/data/diagnostics/path_hazard_challenger_latest.json"))
    args = parser.parse_args()
    inputs = args.input or [Path("output/option_outcomes_live_recommendations.json")]
    records, quality = load_records(inputs)
    artifact, evaluation = train_and_evaluate(records)
    gates = {
        "minimum_exact_paths": {"passed": quality["records_with_valid_pre_exit_marks"] >= 150, "actual": quality["records_with_valid_pre_exit_marks"], "required_min": 150},
        "minimum_target_events": {"passed": quality["event_counts"]["target"] >= 30, "actual": quality["event_counts"]["target"], "required_min": 30},
        "minimum_stop_events": {"passed": quality["event_counts"]["stop"] >= 30, "actual": quality["event_counts"]["stop"], "required_min": 30},
        "no_post_exit_leakage": {"passed": quality["invalid_post_exit_marks"] == 0, "excluded_marks": quality["invalid_post_exit_marks"]},
        "positive_paired_lift": {"passed": (evaluation.get("paired_clustered_lift") or {}).get("lower_95") is not None and float(evaluation["paired_clustered_lift"]["lower_95"]) > 0.0, "actual": evaluation.get("paired_clustered_lift")},
    }
    card = {
        "artifact": "path_competing_risk_challenger",
        "version": 1,
        "status": "pending_prospective_validation" if artifact is not None and all(gate["passed"] for gate in gates.values()) else "hold",
        "mode": "observation_only_never_used_for_orders",
        "execution_effect": "none_exit_advice_only",
        "data_quality": quality,
        "evaluation": evaluation,
        "promotion_gates": gates,
        "input_files": [str(path) for path in inputs],
    }
    if artifact is not None:
        save_artifact(artifact, args.output_model)
        card["model_path"] = str(args.output_model)
        card["model_sha256"] = hashlib.sha256(args.output_model.read_bytes()).hexdigest()
    args.output_card.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(card, indent=2, sort_keys=True) + "\n"
    args.output_card.write_text(rendered, encoding="utf-8")
    args.output_diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.output_diagnostic.write_text(rendered, encoding="utf-8")
    print(json.dumps({"status": card["status"], "data_quality": quality, "execution_effect": card["execution_effect"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
