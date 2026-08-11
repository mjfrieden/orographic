from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.research_readiness import build_research_readiness


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build fail-closed research-readiness health for Orographic."
    )
    parser.add_argument("--snapshot", type=Path, default=Path("web/data/latest_run.json"))
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
        "--research-audit",
        type=Path,
        default=Path("output/research_datasets/research_data_capture_audit.json"),
    )
    parser.add_argument(
        "--event-coverage",
        type=Path,
        default=Path("output/research_datasets/event_outcome_coverage.json"),
    )
    parser.add_argument(
        "--promotion-comparison",
        type=Path,
        default=Path("web/data/diagnostics/promotion_shadow_active_comparison_latest.json"),
    )
    parser.add_argument(
        "--operational-health",
        type=Path,
        default=Path("web/data/diagnostics/scan_health_summary_latest.json"),
        help="Optional scan-health artifact used only as an event-feed metadata fallback.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/data/diagnostics/research_readiness_health_latest.json"),
    )
    parser.add_argument("--max-snapshot-age-minutes", type=int, default=240)
    parser.add_argument("--max-evidence-age-minutes", type=int, default=1440)
    parser.add_argument("--amber-completion-pct", type=float, default=0.95)
    parser.add_argument("--red-completion-pct", type=float, default=0.80)
    parser.add_argument("--min-executable-quote-coverage-pct", type=float, default=0.95)
    parser.add_argument("--min-complete-event-coverage-pct", type=float, default=0.50)
    parser.add_argument("--warn-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sources = {
        "snapshot": args.snapshot,
        "prospective_ledger": args.prospective_ledger,
        "moonshot_ledger": args.moonshot_ledger,
        "research_audit": args.research_audit,
        "event_coverage": args.event_coverage,
        "promotion_comparison": args.promotion_comparison,
        "operational_health": args.operational_health,
    }
    report = build_research_readiness(
        snapshot=_load(args.snapshot),
        prospective_ledger=_load(args.prospective_ledger),
        moonshot_ledger=_load(args.moonshot_ledger),
        research_audit=_load(args.research_audit),
        event_coverage=_load(args.event_coverage),
        promotion_comparison=_load(args.promotion_comparison),
        operational_health=_load(args.operational_health),
        source_paths=sources,
        max_snapshot_age_minutes=max(args.max_snapshot_age_minutes, 1),
        max_evidence_age_minutes=max(args.max_evidence_age_minutes, 1),
        amber_completion_pct=min(max(args.amber_completion_pct, 0.0), 1.0),
        red_completion_pct=min(max(args.red_completion_pct, 0.0), 1.0),
        min_executable_quote_coverage_pct=min(
            max(args.min_executable_quote_coverage_pct, 0.0), 1.0
        ),
        min_complete_event_coverage_pct=min(
            max(args.min_complete_event_coverage_pct, 0.0), 1.0
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "headline", "summary")}, indent=2))
    return 0 if args.warn_only or report["status"] != "red" else 1


if __name__ == "__main__":
    raise SystemExit(main())
