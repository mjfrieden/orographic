from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class SnapshotSmokeError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SnapshotSmokeError(f"{path} must contain a JSON object")
    return payload


def validate_snapshot_artifacts(
    snapshot_path: Path,
    shadow_ledger_path: Path,
    prospective_ledger_path: Path,
    diagnostics_dir: Path,
) -> dict[str, Any]:
    payload = _load(snapshot_path)
    if payload.get("error"):
        raise SnapshotSmokeError(f"snapshot contains pipeline error: {payload.get('error')}")

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    regime = payload.get("regime") if isinstance(payload.get("regime"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    council_summary = council.get("summary") if isinstance(council.get("summary"), dict) else {}
    scout_rows = payload.get("scout_signals") if isinstance(payload.get("scout_signals"), list) else []
    forge_rows = payload.get("forge_candidates") if isinstance(payload.get("forge_candidates"), list) else []
    live_rows = council.get("live_board") if isinstance(council.get("live_board"), list) else []
    shadow_rows = council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []

    scout_count = int(summary.get("scout_signal_count") or 0)
    forge_count = int(summary.get("forge_candidate_count") or 0)
    if scout_count == 0 and (forge_count != 0 or council.get("abstain") is not True):
        raise SnapshotSmokeError(
            "zero-signal snapshot must fail closed with zero Forge candidates and an explicit Council abstention"
        )
    if scout_count != len(scout_rows):
        raise SnapshotSmokeError("snapshot scout signal count does not match stored rows")
    if forge_count != len(forge_rows):
        raise SnapshotSmokeError("snapshot forge candidate count does not match stored rows")
    if int(council_summary.get("candidate_count") or 0) != len(forge_rows):
        raise SnapshotSmokeError("council candidate count does not match forge candidates")
    if int(council_summary.get("live_count") or 0) != len(live_rows):
        raise SnapshotSmokeError("council live count does not match live board")
    if int(council_summary.get("shadow_count") or 0) != len(shadow_rows):
        raise SnapshotSmokeError("council shadow count does not match shadow board")

    shadow_ledger = _load(shadow_ledger_path)
    if shadow_ledger.get("artifact") != "side_aware_scout_shadow_ledger":
        raise SnapshotSmokeError("shadow ledger artifact was not written")
    prospective = _load(prospective_ledger_path)
    if prospective.get("artifact") != "prospective_pick_ledger":
        raise SnapshotSmokeError("prospective pick ledger artifact was not written")
    attribution = payload.get("attribution") if isinstance(payload.get("attribution"), dict) else {}
    if attribution.get("artifact") != "live_shadow_attribution":
        raise SnapshotSmokeError("snapshot attribution artifact missing")
    if not (diagnostics_dir / "live_shadow_attribution_latest.json").exists():
        raise SnapshotSmokeError("live/shadow attribution artifact was not written")
    if not (diagnostics_dir / "board_recommendation_history.json").exists():
        raise SnapshotSmokeError("board recommendation history was not written")

    artifacts = payload.get("model_artifacts") if isinstance(payload.get("model_artifacts"), dict) else {}
    missing = [
        name
        for name, row in artifacts.items()
        if isinstance(row, dict)
        and bool(row.get("required", True))
        and (not row.get("present") or not row.get("sha256"))
    ]
    if missing:
        raise SnapshotSmokeError(f"snapshot missing required model artifact status: {missing}")
    return {
        "scout_signal_count": scout_count,
        "forge_candidate_count": forge_count,
        "abstain": council.get("abstain"),
        "regime": regime.get("mode"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an Orographic smoke-test snapshot and its ledgers.")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--shadow-ledger", type=Path, required=True)
    parser.add_argument("--prospective-ledger", type=Path, required=True)
    parser.add_argument("--diagnostics-dir", type=Path, required=True)
    args = parser.parse_args()
    result = validate_snapshot_artifacts(
        args.snapshot,
        args.shadow_ledger,
        args.prospective_ledger,
        args.diagnostics_dir,
    )
    print(f"Snapshot smoke test passed: {json.dumps(result, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
