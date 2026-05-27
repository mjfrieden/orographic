from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.live_options_archive import archive_live_option_chains
from engine.orographic.pipeline import load_universe


def _symbols_from_snapshot(path: Path) -> list[str]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    settings = payload.get("scan_settings") if isinstance(payload.get("scan_settings"), dict) else {}
    _ = settings
    symbols: list[str] = []
    for key in ("scout_signals", "forge_candidates"):
        rows = payload.get(key) if isinstance(payload.get(key), list) else []
        for row in rows:
            if isinstance(row, dict) and str(row.get("symbol") or "").strip():
                symbols.append(str(row["symbol"]).strip().upper())
    return list(dict.fromkeys(symbols))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive live option chains for future Orographic model training.")
    parser.add_argument("--output-dir", type=Path, default=Path("engine/data/live_options_archive"))
    parser.add_argument("--universe-file", type=Path, default=Path("engine/sample_universe.txt"))
    parser.add_argument("--snapshot", type=Path, default=Path("web/data/latest_run.json"))
    parser.add_argument("--symbols", default="", help="Comma-separated symbol override.")
    parser.add_argument(
        "--snapshot-symbols-only",
        action="store_true",
        help="Archive only symbols observed in the latest snapshot instead of the full universe.",
    )
    parser.add_argument("--min-dte", type=int, default=1)
    parser.add_argument("--max-dte", type=int, default=45)
    parser.add_argument("--max-expiries-per-symbol", type=int, default=6)
    parser.add_argument("--quote-date", default="", help="Optional YYYY-MM-DD quote date override for tests/backfills.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.symbols.strip():
        symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    elif args.snapshot_symbols_only:
        symbols = _symbols_from_snapshot(args.snapshot)
    else:
        symbols = load_universe(args.universe_file)
    if not symbols:
        raise SystemExit("No symbols available for option-chain archiving.")
    quote_date = date.fromisoformat(args.quote_date) if args.quote_date.strip() else None
    result = archive_live_option_chains(
        symbols,
        output_dir=args.output_dir,
        min_dte=max(int(args.min_dte), 0),
        max_dte=max(int(args.max_dte), 0),
        max_expiries_per_symbol=max(int(args.max_expiries_per_symbol), 1),
        today=quote_date,
    )
    print(json.dumps(result.manifest["summary"], indent=2))
    print(f"Saved live option-chain archive manifest to {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
