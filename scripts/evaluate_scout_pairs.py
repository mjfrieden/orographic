"""Explicit offline entry point; never called by scheduled scans implicitly."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.orographic.scout_pair_evaluation import evaluate_pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--inputs-sha256", required=True)
    parser.add_argument("--pair-readiness", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--evaluator-revision", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = evaluate_pairs(args.inputs, args.inputs_sha256,
        json.loads(args.pair_readiness.read_bytes()), args.output_dir,
        evaluator_revision=args.evaluator_revision, run_id=args.run_id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
