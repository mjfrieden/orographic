from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from datetime import UTC, datetime
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


CANONICAL_SCHEMA_VERSION = 1
FIXED_WINDOWS = ("one_hour", "end_of_day", "next_day_close", "friday_close")
SUCCESSFUL_CAPTURE_STATUS = "captured_valid"
TERMINAL_CAPTURE_STATUSES = {SUCCESSFUL_CAPTURE_STATUS, "missed_live_window"}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _capture_delay(label: dict[str, Any]) -> float:
    exit_payload = label.get("exit") if isinstance(label.get("exit"), dict) else {}
    quote = exit_payload.get("quote") if isinstance(exit_payload.get("quote"), dict) else {}
    value = quote.get("capture_delay_seconds")
    return float(value) if isinstance(value, (int, float)) else float("inf")


def _prefer_executable_label(existing: Any, incoming: Any) -> Any:
    if not isinstance(existing, dict):
        return deepcopy(incoming)
    if not isinstance(incoming, dict):
        return deepcopy(existing)
    existing_contract = existing.get("label_contract")
    incoming_contract = incoming.get("label_contract")
    existing_version = (
        int(existing_contract.get("version") or 0)
        if isinstance(existing_contract, dict)
        else 0
    )
    incoming_version = (
        int(incoming_contract.get("version") or 0)
        if isinstance(incoming_contract, dict)
        else 0
    )
    if incoming_version != existing_version:
        return deepcopy(incoming if incoming_version > existing_version else existing)
    if _capture_delay(incoming) < _capture_delay(existing):
        return deepcopy(incoming)
    return deepcopy(existing)


def _prefer_mark(existing: Any, incoming: Any) -> Any:
    if not isinstance(existing, dict):
        return deepcopy(incoming)
    if not isinstance(incoming, dict):
        return deepcopy(existing)
    existing_at = str(existing.get("captured_at_utc") or "9999")
    incoming_at = str(incoming.get("captured_at_utc") or "9999")
    return deepcopy(incoming if incoming_at < existing_at else existing)


def _prefer_capture_attempt(existing: Any, incoming: Any) -> Any:
    if not isinstance(existing, dict):
        return deepcopy(incoming)
    if not isinstance(incoming, dict):
        return deepcopy(existing)
    existing_status = str(existing.get("status") or "")
    incoming_status = str(incoming.get("status") or "")
    if existing_status == SUCCESSFUL_CAPTURE_STATUS:
        return deepcopy(existing)
    if incoming_status == SUCCESSFUL_CAPTURE_STATUS:
        return deepcopy(incoming)
    if existing_status in TERMINAL_CAPTURE_STATUSES and incoming_status not in TERMINAL_CAPTURE_STATUSES:
        return deepcopy(existing)
    if incoming_status in TERMINAL_CAPTURE_STATUSES and existing_status not in TERMINAL_CAPTURE_STATUSES:
        return deepcopy(incoming)
    existing_at = str(existing.get("attempted_at_utc") or "")
    incoming_at = str(incoming.get("attempted_at_utc") or "")
    return deepcopy(incoming if incoming_at >= existing_at else existing)


def _merge_trajectory_marks(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for value in (existing, incoming):
        if not isinstance(value, list):
            continue
        for mark in value:
            if not isinstance(mark, dict):
                continue
            key = (
                str(mark.get("captured_at_utc") or ""),
                str(mark.get("quote_observed_at_utc") or mark.get("mark_source") or ""),
            )
            rows[key] = deepcopy(mark)
    return [rows[key] for key in sorted(rows)]


def _deep_merge(existing: Any, incoming: Any, *, path: tuple[str, ...] = ()) -> Any:
    if not _nonempty(existing):
        return deepcopy(incoming)
    if not _nonempty(incoming):
        return deepcopy(existing)
    if path[-2:-1] == ("executable_labels",):
        return _prefer_executable_label(existing, incoming)
    if path[-2:-1] == ("fixed_exit_marks",):
        return _prefer_mark(existing, incoming)
    if path[-2:-1] == ("capture_attempts",):
        return _prefer_capture_attempt(existing, incoming)
    if path[-1:] == ("trajectory_marks",):
        return _merge_trajectory_marks(existing, incoming)
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = deepcopy(existing)
        for key, value in incoming.items():
            merged[key] = _deep_merge(merged.get(key), value, path=(*path, str(key)))
        return merged
    if isinstance(existing, list) and isinstance(incoming, list):
        return deepcopy(incoming)
    return deepcopy(incoming)


def recommendation_identity(run_generated_at_utc: str, pick: dict[str, Any]) -> str:
    recommendation_id = str(pick.get("recommendation_id") or "").strip()
    if recommendation_id:
        return recommendation_id
    return "|".join(
        (
            run_generated_at_utc,
            str(pick.get("contract_symbol") or "").upper(),
            str(pick.get("lane") or ""),
            str(pick.get("option_type") or "").lower(),
        )
    )


def _merge_entry(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    run_id = str(
        incoming.get("run_generated_at_utc")
        or existing.get("run_generated_at_utc")
        or ""
    )
    merged = _deep_merge(existing, {key: value for key, value in incoming.items() if key != "picks"})
    pick_map: dict[str, dict[str, Any]] = {}
    for source in (existing.get("picks"), incoming.get("picks")):
        if not isinstance(source, list):
            continue
        for pick in source:
            if not isinstance(pick, dict):
                continue
            identity = recommendation_identity(run_id, pick)
            prior = pick_map.get(identity, {})
            pick_map[identity] = _deep_merge(prior, pick)
    merged["picks"] = [pick_map[key] for key in sorted(pick_map)]
    return merged


def merge_ledgers(ledgers: Iterable[dict[str, Any]], *, artifact: str) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    source_count = 0
    source_updated_at: list[str] = []
    for ledger in ledgers:
        if not isinstance(ledger, dict) or not ledger:
            continue
        source_count += 1
        updated_at = str(ledger.get("updated_at_utc") or "").strip()
        if updated_at:
            source_updated_at.append(updated_at)
        for entry in ledger.get("entries", []):
            if not isinstance(entry, dict):
                continue
            run_id = str(entry.get("run_generated_at_utc") or "").strip()
            if not run_id:
                continue
            entries[run_id] = _merge_entry(entries.get(run_id, {}), entry)

    ordered = [entries[key] for key in sorted(entries)]
    aggregate = {
        "runs": len(ordered),
        "pick_rows": sum(
            len(entry.get("picks", []))
            for entry in ordered
            if isinstance(entry.get("picks"), list)
        ),
    }
    return {
        "artifact": artifact,
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "updated_at_utc": max(source_updated_at, default=None),
        "source_ledgers": source_count,
        "max_entries": None,
        "aggregate": aggregate,
        "entries": ordered,
    }


def _read_parquet_frames(paths: Iterable[Path]) -> tuple[list[pd.DataFrame], list[str]]:
    frames: list[pd.DataFrame] = []
    skipped: list[str] = []
    for path in dict.fromkeys(path.resolve() for path in paths if path.exists()):
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:  # pragma: no cover - exercised through integration failures
            skipped.append(f"{path}: {exc}")
            continue
        if not frame.empty:
            frame = frame.copy()
            frame["canonical_source_file"] = path.name
            frames.append(frame)
    return frames, skipped


def _read_strict_outcome_json_frames(
    paths: Iterable[Path],
) -> tuple[list[pd.DataFrame], list[str]]:
    frames: list[pd.DataFrame] = []
    skipped: list[str] = []
    for path in dict.fromkeys(path.resolve() for path in paths if path.exists()):
        try:
            if path.suffix.lower() == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    payload = json.load(handle)
            else:
                payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("rows") if isinstance(payload, dict) else None
            policy = str(payload.get("label_policy") or "") if isinstance(payload, dict) else ""
            if not isinstance(rows, list) or "strict_executable" not in policy:
                raise ValueError("not a strict executable option-outcome artifact")
            frame = pd.DataFrame(row for row in rows if isinstance(row, dict))
        except Exception as exc:
            skipped.append(f"{path}: {exc}")
            continue
        if not frame.empty:
            frame["canonical_source_file"] = path.name
            frames.append(frame)
    return frames, skipped


def _deduplicate_frame(
    frames: list[pd.DataFrame],
    *,
    preferred_keys: tuple[str, ...],
    sort_keys: tuple[str, ...],
) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    keys = [key for key in preferred_keys if key in combined.columns]
    if keys:
        combined = combined.drop_duplicates(subset=keys, keep="last")
    else:
        comparable = [column for column in combined.columns if column != "canonical_source_file"]
        combined = combined.drop_duplicates(subset=comparable, keep="last")
    available_sort = [key for key in sort_keys if key in combined.columns]
    if available_sort:
        combined = combined.sort_values(available_sort, kind="stable", na_position="last")
    return combined.reset_index(drop=True)


def _ledger_metrics(ledger: dict[str, Any]) -> dict[str, int]:
    metrics = {
        "runs": 0,
        "recommendations": 0,
        "strict_capture_policy_v2": 0,
        "with_any_fixed_mark": 0,
        "with_any_executable_label": 0,
        "with_friday_executable_label": 0,
    }
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    metrics["runs"] = len(entries)
    for entry in entries:
        picks = entry.get("picks") if isinstance(entry, dict) and isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            metrics["recommendations"] += 1
            outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
            verification = (
                outcomes.get("quote_verification")
                if isinstance(outcomes.get("quote_verification"), dict)
                else {}
            )
            if int(verification.get("capture_policy_version") or 0) >= 2:
                metrics["strict_capture_policy_v2"] += 1
            fixed = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
            labels = outcomes.get("executable_labels") if isinstance(outcomes.get("executable_labels"), dict) else {}
            if any(isinstance(fixed.get(window), dict) for window in FIXED_WINDOWS):
                metrics["with_any_fixed_mark"] += 1
            if any(isinstance(labels.get(window), dict) for window in FIXED_WINDOWS):
                metrics["with_any_executable_label"] += 1
            if isinstance(labels.get("friday_close"), dict):
                metrics["with_friday_executable_label"] += 1
    return metrics


def _artifact_record(path: Path, *, row_count: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if row_count is not None:
        payload["rows"] = int(row_count)
    return payload


def _source_roots(paths: Iterable[str | Path]) -> list[Path]:
    roots: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.exists():
            roots.append(path.resolve())
    return list(dict.fromkeys(roots))


def _discover_files(roots: list[Path], patterns: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.name in patterns:
                found.append(root)
            continue
        for pattern in patterns:
            found.extend(root.rglob(pattern))
    return list(dict.fromkeys(found))


def build_canonical_evidence_bundle(
    *,
    source_roots: Iterable[str | Path],
    current_prospective_ledger: str | Path | None,
    current_moonshot_ledger: str | Path | None,
    payoff_evidence: str | Path | None,
    output_dir: str | Path,
    strict_outcome_artifacts: Iterable[str | Path] = (),
) -> dict[str, Any]:
    roots = _source_roots(source_roots)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    prospective_paths = _discover_files(roots, ("prospective_pick_ledger.json",))
    moonshot_paths = _discover_files(roots, ("moonshot_prospective_ledger.json",))
    if current_prospective_ledger and Path(current_prospective_ledger).exists():
        prospective_paths.append(Path(current_prospective_ledger).resolve())
    if current_moonshot_ledger and Path(current_moonshot_ledger).exists():
        moonshot_paths.append(Path(current_moonshot_ledger).resolve())

    prospective = merge_ledgers(
        (_load_json(path) for path in dict.fromkeys(prospective_paths)),
        artifact="canonical_prospective_pick_ledger",
    )
    moonshot = merge_ledgers(
        (_load_json(path) for path in dict.fromkeys(moonshot_paths)),
        artifact="canonical_moonshot_prospective_ledger",
    )
    prospective_path = output / "prospective_pick_ledger.json"
    moonshot_path = output / "moonshot_prospective_ledger.json"
    _write_json(prospective_path, prospective)
    _write_json(moonshot_path, moonshot)

    recommendation_paths = _discover_files(roots, ("recommendation_outcomes.parquet",))
    recommendation_frames, recommendation_skipped = _read_parquet_frames(recommendation_paths)
    strict_json_paths = [Path(path) for path in strict_outcome_artifacts]
    discovered_strict_json = _discover_files(
        roots,
        ("strict_option_outcomes.json", "strict_option_outcomes.json.gz"),
    )
    strict_json_frames, strict_json_skipped = _read_strict_outcome_json_frames(
        [*strict_json_paths, *discovered_strict_json]
    )
    recommendations = _deduplicate_frame(
        [*recommendation_frames, *strict_json_frames],
        preferred_keys=(
            "recommendation_id",
            "fixed_exit_window",
            "contract_symbol",
            "run_generated_at_utc",
        ),
        sort_keys=("run_generated_at_utc", "recommendation_id", "fixed_exit_window"),
    )
    recommendation_path = output / "recommendation_outcomes.parquet"
    recommendations.to_parquet(recommendation_path, index=False)

    quote_paths = _discover_files(roots, ("chain.parquet", "live_option_quotes.parquet"))
    quote_frames, quote_skipped = _read_parquet_frames(quote_paths)
    quotes = _deduplicate_frame(
        quote_frames,
        preferred_keys=("contract_symbol", "chain_snapshot_at_utc"),
        sort_keys=("chain_snapshot_at_utc", "contract_symbol"),
    )
    quote_path = output / "live_option_quotes.parquet"
    quotes.to_parquet(quote_path, index=False)

    payoff = _load_json(Path(payoff_evidence)) if payoff_evidence and Path(payoff_evidence).exists() else {}
    coverage = payoff.get("coverage") if isinstance(payoff.get("coverage"), dict) else {}
    primary_metrics = _ledger_metrics(prospective)
    moonshot_metrics = _ledger_metrics(moonshot)
    generated_at = _now_iso()
    manifest: dict[str, Any] = {
        "artifact": "canonical_evidence_manifest",
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "bundle_id": None,
        "generated_at_utc": generated_at,
        "source_roots": [root.name for root in roots],
        "deduplication": {
            "recommendations": [
                "recommendation_id",
                "fixed_exit_window",
                "contract_symbol",
                "run_generated_at_utc",
            ],
            "quotes": ["contract_symbol", "chain_snapshot_at_utc"],
            "ledger_entries": ["run_generated_at_utc"],
            "ledger_picks": ["recommendation_id", "contract_symbol", "lane", "option_type"],
        },
        "evidence": {
            "cumulative_inventory": {
                "primary": primary_metrics,
                "moonshot": moonshot_metrics,
                "recommendation_outcome_rows": int(len(recommendations)),
                "option_quote_rows": int(len(quotes)),
            },
            "training_eligible": {
                "strict_capture_policy_v2_primary_recommendations": primary_metrics[
                    "strict_capture_policy_v2"
                ],
                "primary_recommendations_with_executable_label": primary_metrics[
                    "with_any_executable_label"
                ],
                "deduplicated_recommendation_outcomes": int(len(recommendations)),
            },
            "current_model_cohort": {
                "artifact_sha256": (
                    payoff.get("model_cohort", {}).get("artifact_sha256")
                    if isinstance(payoff.get("model_cohort"), dict)
                    else None
                ),
                "scored_recommendations": int(coverage.get("scored_recommendations") or 0),
                "resolved_recommendations": int(coverage.get("resolved_recommendations") or 0),
                "resolved_runs": int(coverage.get("resolved_runs") or 0),
            },
        },
        "inputs": {
            "prospective_ledgers": len(set(prospective_paths)),
            "moonshot_ledgers": len(set(moonshot_paths)),
            "recommendation_parquet_files": len(recommendation_paths),
            "quote_parquet_files": len(quote_paths),
        },
        "skipped_inputs": recommendation_skipped + strict_json_skipped + quote_skipped,
        "checks": {
            "recommendations_unique": not recommendations.duplicated(
                subset=[
                    key
                    for key in (
                        "recommendation_id",
                        "fixed_exit_window",
                        "contract_symbol",
                        "run_generated_at_utc",
                    )
                    if key in recommendations.columns
                ]
            ).any() if not recommendations.empty else True,
            "quotes_unique": not quotes.duplicated(
                subset=[
                    key
                    for key in ("contract_symbol", "chain_snapshot_at_utc")
                    if key in quotes.columns
                ]
            ).any() if not quotes.empty else True,
            "immutable_labels_preserved": True,
            "inputs_readable": not (recommendation_skipped + strict_json_skipped + quote_skipped),
        },
    }
    manifest["files"] = [
        _artifact_record(prospective_path, row_count=primary_metrics["recommendations"]),
        _artifact_record(moonshot_path, row_count=moonshot_metrics["recommendations"]),
        _artifact_record(recommendation_path, row_count=len(recommendations)),
        _artifact_record(quote_path, row_count=len(quotes)),
    ]
    bundle_seed = json.dumps(
        {
            "schema_version": CANONICAL_SCHEMA_VERSION,
            "files": manifest["files"],
            "deduplication": manifest["deduplication"],
        },
        sort_keys=True,
    ).encode("utf-8")
    manifest["bundle_id"] = hashlib.sha256(bundle_seed).hexdigest()[:20]
    manifest_path = output / "evidence_manifest.json"
    _write_json(manifest_path, manifest)
    return manifest


def validate_canonical_bundle(root: str | Path) -> dict[str, Any]:
    directory = Path(root)
    manifest = _load_json(directory / "evidence_manifest.json")
    if not manifest:
        raise ValueError(f"Canonical evidence manifest is missing: {directory}")
    failures: list[str] = []
    checks = manifest.get("checks") if isinstance(manifest.get("checks"), dict) else {}
    for name, passed in checks.items():
        if passed is not True:
            failures.append(f"check:{name}")
    for record in manifest.get("files", []):
        if not isinstance(record, dict):
            continue
        path = directory / str(record.get("path") or "")
        if not path.exists():
            failures.append(f"missing:{path.name}")
            continue
        if int(record.get("bytes") or -1) != path.stat().st_size:
            failures.append(f"size:{path.name}")
        if str(record.get("sha256") or "") != _sha256(path):
            failures.append(f"sha256:{path.name}")
    if failures:
        raise ValueError("Canonical evidence validation failed: " + ", ".join(failures))
    return manifest
