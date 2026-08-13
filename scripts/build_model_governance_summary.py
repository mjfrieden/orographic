from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.model_governance import build_model_governance_summary


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the model-governance UI artifact.")
    parser.add_argument("--scan-health", type=Path, default=Path("web/data/diagnostics/scan_health_summary_latest.json"))
    parser.add_argument("--capture-health", type=Path, default=Path("web/data/diagnostics/outcome_capture_health_latest.json"))
    parser.add_argument("--scout-card", type=Path, default=Path("engine/orographic/models/scout_hierarchical_challenger_card.json"))
    parser.add_argument("--scout-pair-readiness", type=Path, default=Path("web/data/diagnostics/scout_pair_readiness_latest.json"))
    parser.add_argument("--payoff-card", type=Path, default=Path("engine/orographic/models/payoff_cost_aware_challenger_card.json"))
    parser.add_argument("--payoff-evidence", type=Path, default=Path("web/data/diagnostics/payoff_challenger_evidence_latest.json"))
    parser.add_argument("--veto-evidence", type=Path, default=Path("web/data/diagnostics/counterfactual_veto_evidence_latest.json"))
    parser.add_argument("--path-evidence", type=Path, default=Path("web/data/diagnostics/path_hazard_challenger_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("web/data/diagnostics/model_governance_summary_latest.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_model_governance_summary(
        scan_health=_load(args.scan_health),
        capture_health=_load(args.capture_health),
        scout_card=_load(args.scout_card),
        scout_pair_readiness=_load(args.scout_pair_readiness),
        payoff_card=_load(args.payoff_card),
        payoff_evidence=_load(args.payoff_evidence),
        veto_evidence=_load(args.veto_evidence),
        path_evidence=_load(args.path_evidence),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output), "summary": report["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
