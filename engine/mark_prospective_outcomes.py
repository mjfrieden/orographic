from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from urllib.error import URLError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.prospective import mark_prospective_ledger_file


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mark due outcomes in the prospective options pick ledger.")
    parser.add_argument(
        "--ledger",
        default="web/data/diagnostics/prospective_pick_ledger.json",
        help="Prospective pick ledger path.",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=500,
        help="Maximum unique contract symbols to quote in one run.",
    )
    parser.add_argument(
        "--ignore-missing",
        action="store_true",
        help="Exit successfully when the ledger file does not exist yet.",
    )
    parser.add_argument(
        "--allow-quote-fetch-failure",
        action="store_true",
        help="Log a Tradier quote fetch failure and preserve the ledger instead of failing the process.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.ignore_missing and not Path(args.ledger).exists():
        log.info("Prospective ledger %s does not exist yet; skipping.", args.ledger)
        return 0
    try:
        path, stats = mark_prospective_ledger_file(args.ledger, max_symbols=max(int(args.max_symbols), 1))
    except (TimeoutError, URLError) as exc:
        if not args.allow_quote_fetch_failure:
            raise
        log.warning("Unable to fetch Tradier quotes; preserving %s unchanged: %s", args.ledger, exc)
        return 0
    log.info(
        "Updated %s: entries=%d picks=%d marks_written=%d valid_windows=%d retryable_missing=%d "
        "stale_quotes=%d missed_windows=%d legacy_skipped=%d complete=%d partial=%d pending=%d.",
        path,
        stats["entries_seen"],
        stats["picks_seen"],
        stats["marks_written"],
        stats["capture_windows_valid"],
        stats["capture_windows_quote_missing"],
        stats["capture_windows_stale_quote"],
        stats["capture_windows_missed"],
        stats["legacy_capture_policy_picks_skipped"],
        stats["picks_completed"],
        stats["picks_partial"],
        stats["picks_pending"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
