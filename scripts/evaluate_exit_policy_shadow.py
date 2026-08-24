from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.exit_policy import build_exit_policy_shadow_artifact


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate executable shadow exit policies.")
    parser.add_argument("--ledger", default="web/data/diagnostics/prospective_pick_ledger.json")
    parser.add_argument("--output", default="web/data/diagnostics/exit_policy_shadow_latest.json")
    parser.add_argument("--rows-output", default="output/research_datasets/exit_policy_shadow_rows.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    artifact = build_exit_policy_shadow_artifact(ledger)
    rows = artifact.pop("rows")
    rows_output = Path(args.rows_output)
    rows_output.parent.mkdir(parents=True, exist_ok=True)
    rows_output.write_text(json.dumps({
        "artifact": "orographic_exit_policy_shadow_rows",
        "schema_version": artifact["schema_version"],
        "generated_at_utc": artifact["generated_at_utc"],
        "row_contract": artifact["row_contract"],
        "rows": rows,
    }, indent=2), encoding="utf-8")
    artifact["rows_artifact"] = str(rows_output)
    artifact["row_count"] = len(rows)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
