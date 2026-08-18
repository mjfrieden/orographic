from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.counterfactual_veto_evidence import write_counterfactual_veto_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Build advisory Scout veto-value and threshold-frontier evidence.")
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("web/data/diagnostics/prospective_pick_ledger.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/data/diagnostics/counterfactual_veto_evidence_latest.json"),
    )
    args = parser.parse_args()
    artifact = write_counterfactual_veto_evidence(args.ledger, args.output)
    print(f"Wrote {args.output} ({artifact['decision']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
