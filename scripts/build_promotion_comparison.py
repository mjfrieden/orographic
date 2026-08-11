from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.promotion_comparison import write_promotion_comparison


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay Orographic's canonical 3/6/12-month shadow-versus-active promotion comparison.")
    parser.add_argument("--prospective-ledger", type=Path, default=Path("web/data/diagnostics/prospective_pick_ledger.json"))
    parser.add_argument("--shadow-ledger", type=Path, default=Path("web/data/diagnostics/side_aware_scout_shadow_ledger.json"))
    parser.add_argument("--output", type=Path, default=Path("web/data/diagnostics/promotion_shadow_active_comparison_latest.json"))
    args = parser.parse_args()
    artifact = write_promotion_comparison(args.prospective_ledger, args.shadow_ledger, args.output)
    print(f"Wrote {args.output} ({artifact['decision']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
