#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.weekly_alpha_review import build_weekly_alpha_review


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the weekly Orographic alpha review.")
    parser.add_argument("--snapshot", type=Path, default=Path("web/data/latest_run.json"))
    parser.add_argument(
        "--board-history",
        type=Path,
        default=Path("web/data/diagnostics/board_recommendation_history.json"),
    )
    parser.add_argument(
        "--dashboard",
        type=Path,
        default=Path("web/data/diagnostics/prospective_dashboard_summary_latest.json"),
    )
    parser.add_argument(
        "--scan-health",
        type=Path,
        default=Path("web/data/diagnostics/scan_health_summary_latest.json"),
    )
    parser.add_argument(
        "--rebuild-readiness",
        type=Path,
        default=Path("web/data/diagnostics/orographic_rebuild_readiness_latest.json"),
    )
    parser.add_argument(
        "--mart-shadow",
        type=Path,
        default=Path("web/data/diagnostics/shared_mart_shadow_evidence_latest.json"),
    )
    parser.add_argument(
        "--mart-sync",
        type=Path,
        default=Path("web/data/diagnostics/shared_mart_sync_latest.json"),
    )
    parser.add_argument(
        "--payoff-challenger",
        type=Path,
        default=Path("web/data/diagnostics/payoff_challenger_evidence_latest.json"),
    )
    parser.add_argument(
        "--path-hazard",
        type=Path,
        default=Path("web/data/diagnostics/path_hazard_challenger_latest.json"),
    )
    parser.add_argument(
        "--promotion",
        type=Path,
        default=Path("web/data/diagnostics/promotion_shadow_active_comparison_latest.json"),
    )
    parser.add_argument(
        "--exit-shadow",
        type=Path,
        default=Path("web/data/diagnostics/exit_policy_shadow_latest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/data/diagnostics/weekly_alpha_review_latest.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot = _load(args.snapshot)
    as_of = datetime.now(UTC)
    generated = snapshot.get("generated_at_utc")
    if isinstance(generated, str):
        try:
            as_of = datetime.fromisoformat(generated.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=UTC)
    artifact = build_weekly_alpha_review(
        as_of_utc=as_of,
        snapshot=snapshot,
        board_history=_load(args.board_history),
        dashboard=_load(args.dashboard),
        scan_health=_load(args.scan_health),
        rebuild_readiness=_load(args.rebuild_readiness),
        mart_shadow=_load(args.mart_shadow),
        mart_sync=_load(args.mart_sync),
        payoff_challenger=_load(args.payoff_challenger),
        path_hazard=_load(args.path_hazard),
        promotion=_load(args.promotion),
        exit_shadow=_load(args.exit_shadow),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "written",
        "alpha_verdict": artifact["alpha_verdict"],
        "live_emissions": len(artifact["production"]["week"]["live_emissions"]),
        "challenger": artifact["challenger_to_open"]["experiment_id"],
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
