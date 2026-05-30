from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.path_outcomes import apply_archived_quote_path_labels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add archived option-quote path labels to Orographic prospective ledgers.")
    parser.add_argument("--archive-dir", type=Path, default=Path("engine/data/live_options_archive"))
    parser.add_argument(
        "--ledger",
        action="append",
        type=Path,
        default=[],
        help="Ledger path to update in place. Can be passed multiple times.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ledgers = args.ledger or [
        Path("web/data/diagnostics/prospective_pick_ledger.json"),
        Path("web/data/diagnostics/moonshot_prospective_ledger.json"),
    ]
    summaries: dict[str, dict[str, int]] = {}
    for ledger_path in ledgers:
        if not ledger_path.exists():
            summaries[str(ledger_path)] = {
                "entries_seen": 0,
                "picks_seen": 0,
                "labels_observed": 0,
                "labels_missing": 0,
                "labels_missing_entry_mark": 0,
            }
            continue
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        updated, stats = apply_archived_quote_path_labels(ledger, archive_dir=args.archive_dir)
        ledger_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        summaries[str(ledger_path)] = stats
    print(json.dumps(summaries, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
