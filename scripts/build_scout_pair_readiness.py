from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.backtest.results import canonicalize_option_outcome_dataset
from engine.orographic.scout_pair_readiness import build_scout_pair_readiness
from scripts.build_research_datasets import canonical_option_outcome_rows


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build fail-closed matched call/put Scout evidence readiness."
    )
    parser.add_argument(
        "--option-outcomes",
        type=Path,
        default=None,
        help="Canonical option_outcome_dataset. If omitted, build strict rows from the prospective ledger.",
    )
    parser.add_argument(
        "--prospective-ledger",
        type=Path,
        default=Path("web/data/diagnostics/prospective_pick_ledger.json"),
    )
    parser.add_argument(
        "--historical-archive-manifest",
        type=Path,
        default=Path("engine/data/optionsdx/coverage_manifest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/data/diagnostics/scout_pair_readiness_latest.json"),
    )
    parser.add_argument("--warn-only", action="store_true")
    return parser.parse_args()


def _outcome_payload(args: argparse.Namespace) -> dict:
    if args.option_outcomes is not None:
        return _load(args.option_outcomes)
    rows = canonicalize_option_outcome_dataset(
        canonical_option_outcome_rows(
            args.prospective_ledger,
            source_artifact="prospective_pick_ledger",
            exit_window="friday_close",
            require_executable_label=True,
        )
    )
    return {
        "artifact": "option_outcome_dataset",
        "label_policy": "strict_executable_quote_or_fill_v2",
        "generated_at": None,
        "rows": rows,
    }


def main() -> int:
    args = parse_args()
    report = build_scout_pair_readiness(
        _outcome_payload(args),
        historical_archive_manifest=_load(args.historical_archive_manifest),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "coverage": report["coverage"],
        "next_action": report["next_action"],
    }, indent=2))
    ready = report["status"] == "ready_for_fold_frozen_evaluation"
    return 0 if args.warn_only or ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
