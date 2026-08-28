from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "engine" / "orographic" / "models" / "artifact_manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def validate_manifest(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = _load_json(manifest_path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return ["manifest has no artifacts map"]

    for name, spec in artifacts.items():
        if not isinstance(spec, dict):
            errors.append(f"{name}: artifact spec is not an object")
            continue
        path_value = spec.get("path")
        expected_hash = spec.get("sha256")
        required = bool(spec.get("required", True))
        if not isinstance(path_value, str) or not path_value:
            errors.append(f"{name}: missing path")
            continue
        path = _artifact_path(path_value)
        if not path.exists():
            if required:
                errors.append(f"{name}: missing required artifact at {path_value}")
            continue
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            errors.append(f"{name}: missing or invalid sha256 in manifest")
            continue
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            errors.append(
                f"{name}: sha256 mismatch for {path_value}; expected {expected_hash}, got {actual_hash}"
            )

    return errors


def validate_model_cards(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = _load_json(manifest_path)
    artifacts = manifest.get("artifacts", {})
    expected_hashes = {
        name: spec.get("sha256")
        for name, spec in artifacts.items()
        if isinstance(spec, dict)
    }

    scout_card_path = _artifact_path(artifacts.get("scout_model_card", {}).get("path", ""))
    production_ranker_card_path = _artifact_path(
        artifacts.get("production_payoff_ranker_card", {}).get("path", "")
    )

    if scout_card_path.exists():
        scout = _load_json(scout_card_path)
        scout_artifacts = scout.get("artifacts", {})
        checks = {
            "model_sha256": expected_hashes.get("scout_model"),
            "scaler_sha256": expected_hashes.get("scout_scaler"),
            "side_model_sha256": expected_hashes.get("scout_side_model"),
        }
        for key, expected in checks.items():
            if scout_artifacts.get(key) != expected:
                errors.append(f"scout_model_card: {key} does not match manifest")
        side = scout.get("side_aware_output", {})
        if side.get("mode") != "trained_option_payoff_three_class":
            errors.append("scout_model_card: side-aware output is not option-payoff trained")
        if not side.get("decision_contract"):
            errors.append("scout_model_card: side-aware output is missing decision_contract")
        activation = scout.get("activation_policy", {})
        if activation.get("default") != "active":
            errors.append("scout_model_card: activation policy default is not active")
        if not activation.get("active_env"):
            errors.append("scout_model_card: activation policy is missing active_env")
        primary_target = scout.get("primary_target", {})
        primary_mode = primary_target.get("mode")
        if not primary_mode:
            target_text = str(scout.get("target") or "").lower()
            if "strict-real" in target_text and "option" in target_text:
                primary_mode = "strict_real_option_direction"
            elif target_text:
                primary_mode = "underlying_forward_return"
        metrics = side.get("training_metrics", {})
        source_counts = (metrics.get("source_metadata") or {}).get("class_counts") or {}
        if int(source_counts.get("put_edge", 0) or 0) < 1:
            errors.append("scout_model_card: side-aware source has no put_edge examples")
        if primary_mode == "strict_real_option_direction":
            balance_report = primary_target.get("balance_report") or {}
            minority_share = balance_report.get("minority_share")
            if minority_share is None or float(minority_share) < 0.19:
                errors.append("scout_model_card: strict-real option-direction target has insufficient minority-side coverage")
            segment_sides = (scout.get("observability", {}).get("segments", {}).get("by_side") or {}).keys()
            segment_sides = {str(side) for side in segment_sides}
            if not {"call", "put"}.issubset(segment_sides):
                errors.append("scout_model_card: strict-real option-direction target collapsed to a single predicted side")
            actual_sides = (scout.get("observability", {}).get("segments", {}).get("by_actual_side") or {}).keys()
            actual_sides = {str(side) for side in actual_sides}
            if not {"call_edge", "put_edge"}.issubset(actual_sides):
                errors.append("scout_model_card: strict-real option-direction target lacks actual-side observability coverage")
    else:
        errors.append("scout_model_card: file missing")

    if production_ranker_card_path.exists():
        ranker_card = _load_json(production_ranker_card_path)
        if ranker_card.get("profile_id") != "production_v2":
            errors.append("production_payoff_ranker_card: wrong production profile")
        authority = ranker_card.get("authority", {})
        if authority.get("forge_ranking") is not True:
            errors.append("production_payoff_ranker_card: Forge ranking authority missing")
        if authority.get("probability_sizing") is not False:
            errors.append("production_payoff_ranker_card: probability sizing must remain disabled")
        validation = ranker_card.get("source_validation", {})
        if validation.get("call_auc") is None or validation.get("put_auc") is None:
            errors.append("production_payoff_ranker_card: side validation missing")
    else:
        errors.append("production_payoff_ranker_card: file missing")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Orographic model artifact manifest and model cards.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    manifest_path = args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest
    errors = []
    errors.extend(validate_manifest(manifest_path))
    errors.extend(validate_model_cards(manifest_path))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Validated model artifacts from {manifest_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
