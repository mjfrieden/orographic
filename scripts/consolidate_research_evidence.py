#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.evidence_store import (  # noqa: E402
    build_canonical_evidence_bundle,
    validate_canonical_bundle,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge restored and current Orographic evidence into one canonical bundle."
    )
    parser.add_argument(
        "--source-root",
        action="append",
        type=Path,
        default=[],
        help="Restored or current evidence root; may be supplied more than once.",
    )
    parser.add_argument(
        "--prospective-ledger",
        type=Path,
        default=Path("web/data/diagnostics/prospective_pick_ledger.json"),
    )
    parser.add_argument(
        "--moonshot-ledger",
        type=Path,
        default=Path("web/data/diagnostics/moonshot_prospective_ledger.json"),
    )
    parser.add_argument(
        "--payoff-evidence",
        type=Path,
        default=Path("web/data/diagnostics/payoff_challenger_evidence_latest.json"),
    )
    parser.add_argument(
        "--strict-outcomes",
        action="append",
        type=Path,
        default=[],
        help="Strict executable option-outcome JSON/JSON.GZ artifact; may be repeated.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/canonical_evidence"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    roots = list(args.source_root)
    roots.extend(
        path
        for path in (
            Path("output/restored_canonical_evidence"),
            Path("output/restored_legacy_evidence"),
            Path("output/research_datasets"),
            Path("engine/data/live_options_archive"),
        )
        if path.exists()
    )
    manifest = build_canonical_evidence_bundle(
        source_roots=roots,
        current_prospective_ledger=args.prospective_ledger,
        current_moonshot_ledger=args.moonshot_ledger,
        payoff_evidence=args.payoff_evidence,
        strict_outcome_artifacts=[
            *args.strict_outcomes,
            Path("data/evidence_seed/strict_option_outcomes.json.gz"),
            Path("output/research_datasets/strict_option_outcomes.json"),
        ],
        output_dir=args.output_dir,
    )
    validate_canonical_bundle(args.output_dir)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
