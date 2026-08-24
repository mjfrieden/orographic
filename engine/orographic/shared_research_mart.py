from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import shutil
from typing import Any, Iterable
import uuid

import pandas as pd

from .evidence_store import validate_canonical_bundle


MART_SCHEMA_VERSION = "cirrus_orographic_research_mart_v1"


@dataclass(frozen=True)
class TableContract:
    primary_key: tuple[str, ...]
    columns: tuple[str, ...]


TABLE_CONTRACTS: dict[str, TableContract] = {
    "model_runs": TableContract(
        primary_key=("run_key",),
        columns=(
            "run_key", "source_system", "cohort", "source_run_id",
            "decision_at_utc", "available_at_utc", "model_version",
            "regime_mode", "regime_bias", "source_bundle_id",
            "source_payload_json",
        ),
    ),
    "recommendations": TableContract(
        primary_key=("recommendation_key",),
        columns=(
            "recommendation_key", "run_key", "source_system", "cohort",
            "source_recommendation_id", "lane", "model_version",
            "decision_at_utc", "available_at_utc", "underlying_symbol",
            "contract_symbol", "option_type", "expiry_date", "strike",
            "entry_bid", "entry_ask", "entry_mid", "score", "status",
            "source_bundle_id", "source_payload_json",
        ),
    ),
    "execution_outcomes": TableContract(
        primary_key=("outcome_key",),
        columns=(
            "outcome_key", "recommendation_key", "source_system", "cohort",
            "exit_policy", "entry_at_utc", "exit_at_utc",
            "label_available_at_utc", "entry_price", "exit_price",
            "executable_return", "observation_count", "exit_reason",
            "label_contract_id", "label_contract_version", "is_executable",
            "is_excluded", "source_bundle_id",
        ),
    ),
    "option_quotes": TableContract(
        primary_key=("quote_key",),
        columns=(
            "quote_key", "source_system", "cohort", "recommendation_key",
            "contract_symbol", "underlying_symbol", "observed_at_utc",
            "available_at_utc", "quote_date", "quote_source", "bid", "ask",
            "last_price", "mid", "executable_exit", "open_interest", "volume",
            "implied_volatility", "delta", "gamma", "theta_per_day", "vega",
            "source_bundle_id",
        ),
    ),
    "feature_snapshots": TableContract(
        primary_key=("feature_key",),
        columns=(
            "feature_key", "recommendation_key", "source_system",
            "feature_schema_version", "available_at_utc", "features_sha256",
            "features_json", "source_metadata_json", "source_bundle_id",
        ),
    ),
    "path_exclusions": TableContract(
        primary_key=("exclusion_key",),
        columns=(
            "exclusion_key", "recommendation_key", "source_system",
            "reason_code", "details", "excluded_at_utc", "source_bundle_id",
        ),
    ),
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _json(value: Any) -> str:
    return json.dumps(_clean(value), sort_keys=True, separators=(",", ":"), default=str)


def _text(value: Any) -> str | None:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _frame_records(frame: pd.DataFrame) -> Iterable[dict[str, Any]]:
    for row in frame.to_dict(orient="records"):
        yield {str(key): _clean(value) for key, value in row.items()}


def _run_key(system: str, cohort: str, source_run_id: Any) -> str:
    return f"{system}|{cohort}|{source_run_id}"


def _recommendation_key(system: str, cohort: str, source_id: Any) -> str:
    return f"{system}|{cohort}|{source_id}"


def _model_version(payload: dict[str, Any]) -> str:
    identity = {
        "model_modes": payload.get("model_modes"),
        "model_artifacts": _nested(payload, "context", "model_artifacts"),
    }
    return hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()[:20]


def _read_manifest(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Manifest is not an object: {path}")
    return loaded


def validate_cirrus_export(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"Cirrus export manifest is missing: {manifest_path}")
    manifest = _read_manifest(manifest_path)
    failures: list[str] = []
    for name, artifact in dict(manifest.get("artifacts") or {}).items():
        path = root / f"{name}.parquet"
        if not path.exists():
            failures.append(f"missing:{name}")
            continue
        if artifact.get("sha256") != _sha256(path):
            failures.append(f"hash:{name}")
        rows = len(pd.read_parquet(path))
        if int(artifact.get("rows", -1)) != rows:
            failures.append(f"rows:{name}")
    if failures:
        raise ValueError("Cirrus export validation failed: " + ", ".join(failures))
    return manifest


def _orographic_rows(
    canonical_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    bundle_id = str(manifest.get("bundle_id") or "unknown")
    result = {name: [] for name in TABLE_CONTRACTS}
    known_runs: set[str] = set()
    known_recommendations: set[str] = set()

    for cohort, filename in (
        ("primary", "prospective_pick_ledger.json"),
        ("moonshot", "moonshot_prospective_ledger.json"),
    ):
        payload = _read_manifest(canonical_dir / filename)
        for entry in payload.get("entries", []):
            if not isinstance(entry, dict):
                continue
            source_run_id = _text(entry.get("run_generated_at_utc"))
            if not source_run_id:
                continue
            run_key = _run_key("orographic", cohort, source_run_id)
            version = _model_version(entry)
            regime = entry.get("regime") if isinstance(entry.get("regime"), dict) else {}
            if run_key not in known_runs:
                result["model_runs"].append({
                    "run_key": run_key,
                    "source_system": "orographic",
                    "cohort": cohort,
                    "source_run_id": source_run_id,
                    "decision_at_utc": source_run_id,
                    "available_at_utc": source_run_id,
                    "model_version": version,
                    "regime_mode": _text(regime.get("mode")),
                    "regime_bias": _number(regime.get("bias")),
                    "source_bundle_id": bundle_id,
                    "source_payload_json": _json({key: value for key, value in entry.items() if key != "picks"}),
                })
                known_runs.add(run_key)
            for pick in entry.get("picks", []):
                if not isinstance(pick, dict):
                    continue
                source_id = _text(pick.get("recommendation_id")) or "|".join(
                    filter(None, (source_run_id, _text(pick.get("contract_symbol")), _text(pick.get("lane"))))
                )
                rec_key = _recommendation_key("orographic", cohort, source_id)
                quote = pick.get("emission_quote") if isinstance(pick.get("emission_quote"), dict) else {}
                scores = pick.get("scores") if isinstance(pick.get("scores"), dict) else {}
                result["recommendations"].append({
                    "recommendation_key": rec_key,
                    "run_key": run_key,
                    "source_system": "orographic",
                    "cohort": cohort,
                    "source_recommendation_id": source_id,
                    "lane": _text(pick.get("lane")),
                    "model_version": _model_version({**entry, "context": pick.get("context")}),
                    "decision_at_utc": source_run_id,
                    "available_at_utc": _text(quote.get("captured_at_utc")) or source_run_id,
                    "underlying_symbol": _text(pick.get("symbol") or pick.get("underlying")),
                    "contract_symbol": _text(pick.get("contract_symbol")),
                    "option_type": _text(pick.get("option_type")),
                    "expiry_date": _text(pick.get("expiry")),
                    "strike": _number(pick.get("strike")),
                    "entry_bid": _number(quote.get("bid")),
                    "entry_ask": _number(quote.get("ask")),
                    "entry_mid": _number(quote.get("mid")),
                    "score": _number(scores.get("final_candidate_score") or scores.get("forge_score")),
                    "status": _text(_nested(pick, "outcomes", "status")),
                    "source_bundle_id": bundle_id,
                    "source_payload_json": _json(pick),
                })
                known_recommendations.add(rec_key)

    outcome_path = canonical_dir / "recommendation_outcomes.parquet"
    outcomes = pd.read_parquet(outcome_path) if outcome_path.exists() else pd.DataFrame()
    for row in _frame_records(outcomes):
        source_artifact = (_text(row.get("source_artifact")) or "").lower()
        cohort = "moonshot" if "moonshot" in source_artifact else "primary"
        run_id = _text(row.get("run_generated_at_utc")) or _text(row.get("decision_at_utc"))
        source_id = _text(row.get("recommendation_id"))
        if not run_id or not source_id:
            continue
        run_key = _run_key("orographic", cohort, run_id)
        rec_key = _recommendation_key("orographic", cohort, source_id)
        if run_key not in known_runs:
            result["model_runs"].append({
                "run_key": run_key, "source_system": "orographic", "cohort": cohort,
                "source_run_id": run_id, "decision_at_utc": run_id,
                "available_at_utc": run_id, "model_version": "outcome_only",
                "regime_mode": _text(row.get("regime_mode")),
                "regime_bias": _number(row.get("regime_bias")),
                "source_bundle_id": bundle_id, "source_payload_json": "{}",
            })
            known_runs.add(run_key)
        if rec_key not in known_recommendations:
            result["recommendations"].append({
                "recommendation_key": rec_key, "run_key": run_key,
                "source_system": "orographic", "cohort": cohort,
                "source_recommendation_id": source_id, "lane": _text(row.get("lane")),
                "model_version": _text(row.get("payoff_model_artifact_sha256")) or "outcome_only",
                "decision_at_utc": run_id,
                "available_at_utc": _text(row.get("entry_quote_observed_at_utc")) or run_id,
                "underlying_symbol": _text(row.get("symbol")),
                "contract_symbol": _text(row.get("contract_symbol")),
                "option_type": _text(row.get("option_type")),
                "expiry_date": _text(row.get("expiry")), "strike": _number(row.get("strike")),
                "entry_bid": _number(row.get("entry_bid")), "entry_ask": _number(row.get("entry_ask")),
                "entry_mid": _number(row.get("entry_price")),
                "score": _number(row.get("final_candidate_score")), "status": "resolved",
                "source_bundle_id": bundle_id, "source_payload_json": "{}",
            })
            known_recommendations.add(rec_key)
        exit_policy = _text(row.get("fixed_exit_window")) or "unknown"
        outcome_key = f"{rec_key}|{exit_policy}"
        entry_price = _number(row.get("entry_price"))
        exit_price = _number(row.get("exit_price"))
        result["execution_outcomes"].append({
            "outcome_key": outcome_key, "recommendation_key": rec_key,
            "source_system": "orographic", "cohort": cohort, "exit_policy": exit_policy,
            "entry_at_utc": _text(row.get("entry_quote_observed_at_utc")) or _text(row.get("entry_date")),
            "exit_at_utc": _text(row.get("exit_quote_observed_at_utc")) or _text(row.get("exit_date")),
            "label_available_at_utc": _text(row.get("executable_label_available_at_utc")),
            "entry_price": entry_price, "exit_price": exit_price,
            "executable_return": _number(row.get("pnl_pct")),
            "observation_count": _integer(_nested(row, "archived_quote_path", "observation_count")),
            "exit_reason": exit_policy,
            "label_contract_id": _text(row.get("executable_label_contract_id")),
            "label_contract_version": _integer(row.get("executable_label_contract_version")),
            "is_executable": bool(entry_price is not None and exit_price is not None and _text(row.get("executable_label_contract_id"))),
            "is_excluded": False, "source_bundle_id": bundle_id,
        })

    quote_path = canonical_dir / "live_option_quotes.parquet"
    quotes = pd.read_parquet(quote_path) if quote_path.exists() else pd.DataFrame()
    for row in _frame_records(quotes):
        observed = _text(row.get("chain_snapshot_at_utc") or row.get("captured_at_utc"))
        contract = _text(row.get("contract_symbol"))
        source = _text(row.get("canonical_source_file") or row.get("source")) or "orographic_chain"
        quote_key = "orographic_market|" + "|".join(filter(None, (contract, observed, source)))
        result["option_quotes"].append({
            "quote_key": quote_key, "source_system": "orographic", "cohort": "shared_market",
            "recommendation_key": None, "contract_symbol": contract,
            "underlying_symbol": _text(row.get("underlying_symbol") or row.get("symbol")),
            "observed_at_utc": observed, "available_at_utc": observed,
            "quote_date": _text(row.get("quote_date")), "quote_source": source,
            "bid": _number(row.get("bid")), "ask": _number(row.get("ask")),
            "last_price": _number(row.get("last") or row.get("last_price")),
            "mid": _number(row.get("mid")), "executable_exit": _number(row.get("bid")),
            "open_interest": _number(row.get("open_interest")), "volume": _number(row.get("volume")),
            "implied_volatility": _number(row.get("implied_volatility")),
            "delta": _number(row.get("delta")), "gamma": _number(row.get("gamma")),
            "theta_per_day": _number(row.get("theta") or row.get("theta_per_day")),
            "vega": _number(row.get("vega")), "source_bundle_id": bundle_id,
        })
    return result


def _cirrus_rows(export_dir: Path, manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    bundle_id = str(manifest.get("bundle_id") or "unknown")
    result = {name: [] for name in TABLE_CONTRACTS}
    frames = {
        name: pd.read_parquet(export_dir / f"{name}.parquet")
        for name in (
            "scan_runs", "tracked_picks", "candidate_feature_snapshots",
            "option_quote_snapshots", "option_path_outcomes", "path_exclusions",
        )
    }
    picks = {int(row["id"]): row for row in _frame_records(frames["tracked_picks"])}
    exclusions = {
        int(row["tracked_pick_id"]): row for row in _frame_records(frames["path_exclusions"])
    }
    live_marks = set(
        int(row["tracked_pick_id"])
        for row in _frame_records(frames["option_quote_snapshots"])
        if _text(row.get("source")) == "live_chain_mark"
    )
    for row in _frame_records(frames["scan_runs"]):
        source_id = _text(row.get("id"))
        if not source_id:
            continue
        result["model_runs"].append({
            "run_key": _run_key("cirrus", "prospective", source_id),
            "source_system": "cirrus", "cohort": "prospective", "source_run_id": source_id,
            "decision_at_utc": _text(row.get("generated_at")),
            "available_at_utc": _text(row.get("created_ts")) or _text(row.get("generated_at")),
            "model_version": _text(row.get("playbook")) or "cirrus_scan",
            "regime_mode": _text(row.get("world_mode")),
            "regime_bias": _number(row.get("world_risk_score")),
            "source_bundle_id": bundle_id, "source_payload_json": _json(row),
        })
    for pick_id, row in picks.items():
        cohort = "prospective"
        rec_key = _recommendation_key("cirrus", cohort, pick_id)
        result["recommendations"].append({
            "recommendation_key": rec_key,
            "run_key": _run_key("cirrus", cohort, row.get("scan_run_id")),
            "source_system": "cirrus", "cohort": cohort,
            "source_recommendation_id": str(pick_id), "lane": _text(row.get("lane")),
            "model_version": _text(row.get("lane")) or "cirrus_scan",
            "decision_at_utc": _text(row.get("scan_generated_at")),
            "available_at_utc": _text(row.get("created_ts")) or _text(row.get("scan_generated_at")),
            "underlying_symbol": _text(row.get("ticker")),
            "contract_symbol": _text(row.get("contract_symbol")),
            "option_type": "put" if _text(row.get("direction")) == "bearish" else "call",
            "expiry_date": _text(row.get("expiry")), "strike": _number(row.get("strike")),
            "entry_bid": _number(row.get("bid")), "entry_ask": _number(row.get("ask")),
            "entry_mid": _number(row.get("mid")), "score": _number(row.get("score")),
            "status": _text(row.get("status")), "source_bundle_id": bundle_id,
            "source_payload_json": _json(row),
        })
    for row in _frame_records(frames["option_path_outcomes"]):
        pick_id = int(row["tracked_pick_id"])
        rec_key = _recommendation_key("cirrus", "prospective", pick_id)
        reason = _text(row.get("strategy_exit_reason"))
        excluded = pick_id in exclusions
        executable = (
            not excluded and pick_id in live_marks
            and (_integer(row.get("observation_count")) or 0) >= 2
            and reason not in {None, "latest_mark"}
        )
        pick = picks.get(pick_id, {})
        result["execution_outcomes"].append({
            "outcome_key": f"{rec_key}|cirrus_path_25_25_v1",
            "recommendation_key": rec_key, "source_system": "cirrus",
            "cohort": "prospective", "exit_policy": "cirrus_path_25_25_v1",
            "entry_at_utc": _text(pick.get("scan_generated_at")),
            "exit_at_utc": _text(row.get("strategy_exit_date")),
            "label_available_at_utc": _text(row.get("updated_ts")),
            "entry_price": _number(pick.get("ask") or pick.get("mid") or pick.get("bid")),
            "exit_price": _number(row.get("strategy_exit_price")),
            "executable_return": _number(row.get("strategy_return")),
            "observation_count": _integer(row.get("observation_count")),
            "exit_reason": reason, "label_contract_id": "cirrus.option_path.25_25",
            "label_contract_version": 1, "is_executable": executable,
            "is_excluded": excluded, "source_bundle_id": bundle_id,
        })
    for row in _frame_records(frames["option_quote_snapshots"]):
        pick_id = int(row["tracked_pick_id"])
        pick = picks.get(pick_id, {})
        observed = _text(row.get("observed_ts"))
        source = _text(row.get("source")) or "unknown"
        rec_key = _recommendation_key("cirrus", "prospective", pick_id)
        result["option_quotes"].append({
            "quote_key": f"cirrus_market|{pick_id}|{observed}|{source}",
            "source_system": "cirrus", "cohort": "prospective",
            "recommendation_key": rec_key, "contract_symbol": _text(pick.get("contract_symbol")),
            "underlying_symbol": _text(pick.get("ticker")), "observed_at_utc": observed,
            "available_at_utc": observed, "quote_date": _text(row.get("observed_date")),
            "quote_source": source, "bid": _number(row.get("bid")),
            "ask": _number(row.get("ask")), "last_price": _number(row.get("last_price")),
            "mid": _number(row.get("mid")), "executable_exit": _number(row.get("executable_exit")),
            "open_interest": _number(row.get("open_interest")), "volume": _number(row.get("volume")),
            "implied_volatility": _number(row.get("implied_volatility")),
            "delta": _number(row.get("delta")), "gamma": _number(row.get("gamma")),
            "theta_per_day": _number(row.get("theta_per_day")), "vega": _number(row.get("vega")),
            "source_bundle_id": bundle_id,
        })
    for row in _frame_records(frames["candidate_feature_snapshots"]):
        pick_id = int(row["tracked_pick_id"])
        rec_key = _recommendation_key("cirrus", "prospective", pick_id)
        schema = _text(row.get("feature_schema_version")) or "unknown"
        result["feature_snapshots"].append({
            "feature_key": f"{rec_key}|{schema}", "recommendation_key": rec_key,
            "source_system": "cirrus", "feature_schema_version": schema,
            "available_at_utc": _text(row.get("available_at")),
            "features_sha256": _text(row.get("features_sha256")),
            "features_json": _text(row.get("features_json")) or "{}",
            "source_metadata_json": _text(row.get("source_metadata_json")) or "{}",
            "source_bundle_id": bundle_id,
        })
    for pick_id, row in exclusions.items():
        rec_key = _recommendation_key("cirrus", "prospective", pick_id)
        result["path_exclusions"].append({
            "exclusion_key": f"{rec_key}|{_text(row.get('reason_code')) or 'unknown'}",
            "recommendation_key": rec_key, "source_system": "cirrus",
            "reason_code": _text(row.get("reason_code")), "details": _text(row.get("details")),
            "excluded_at_utc": _text(row.get("excluded_ts")), "source_bundle_id": bundle_id,
        })
    return result


def _validate_tables(frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    failures: list[str] = []
    checks: dict[str, Any] = {}
    for name, contract in TABLE_CONTRACTS.items():
        frame = frames[name]
        missing = sorted(set(contract.columns) - set(frame.columns))
        duplicate_count = int(frame.duplicated(list(contract.primary_key)).sum()) if not frame.empty else 0
        null_key_count = int(frame[list(contract.primary_key)].isna().any(axis=1).sum()) if not frame.empty else 0
        checks[f"{name}_schema"] = not missing
        checks[f"{name}_unique"] = duplicate_count == 0
        checks[f"{name}_keys_present"] = null_key_count == 0
        if missing:
            failures.append(f"{name}:missing_columns={','.join(missing)}")
        if duplicate_count:
            failures.append(f"{name}:duplicate_keys={duplicate_count}")
        if null_key_count:
            failures.append(f"{name}:null_keys={null_key_count}")

    run_keys = set(frames["model_runs"]["run_key"])
    rec_keys = set(frames["recommendations"]["recommendation_key"])
    orphan_recommendations = int((~frames["recommendations"]["run_key"].isin(run_keys)).sum())
    orphan_children = {
        name: int((~frames[name]["recommendation_key"].isin(rec_keys)).sum())
        for name in ("execution_outcomes", "feature_snapshots", "path_exclusions")
    }
    quote_children = frames["option_quotes"]["recommendation_key"].dropna()
    orphan_children["option_quotes"] = int((~quote_children.isin(rec_keys)).sum())
    checks["recommendations_have_runs"] = orphan_recommendations == 0
    checks["children_have_recommendations"] = all(value == 0 for value in orphan_children.values())
    if orphan_recommendations:
        failures.append(f"recommendations:orphan_runs={orphan_recommendations}")
    for name, count in orphan_children.items():
        if count:
            failures.append(f"{name}:orphan_recommendations={count}")

    decisions = frames["recommendations"][["recommendation_key", "decision_at_utc"]].copy()
    decisions["decision_at_utc"] = pd.to_datetime(
        decisions["decision_at_utc"], errors="coerce", utc=True
    )
    recommendation_available = pd.to_datetime(
        frames["recommendations"]["available_at_utc"], errors="coerce", utc=True
    )
    recommendation_decisions = pd.to_datetime(
        frames["recommendations"]["decision_at_utc"], errors="coerce", utc=True
    )
    recommendations_before_decision = int(
        (
            recommendation_available.notna()
            & recommendation_decisions.notna()
            & (recommendation_available < recommendation_decisions)
        ).sum()
    )
    features = frames["feature_snapshots"].merge(
        decisions, on="recommendation_key", how="left", validate="many_to_one"
    )
    feature_available = pd.to_datetime(features["available_at_utc"], errors="coerce", utc=True)
    features_after_decision = int(
        (
            feature_available.notna()
            & features["decision_at_utc"].notna()
            & (feature_available > features["decision_at_utc"])
        ).sum()
    )
    outcomes = frames["execution_outcomes"].merge(
        decisions, on="recommendation_key", how="left", validate="many_to_one"
    )
    label_available = pd.to_datetime(
        outcomes["label_available_at_utc"], errors="coerce", utc=True
    )
    outcomes_before_decision = int(
        (
            label_available.notna()
            & outcomes["decision_at_utc"].notna()
            & (label_available < outcomes["decision_at_utc"])
        ).sum()
    )
    quote_observed = pd.to_datetime(frames["option_quotes"]["observed_at_utc"], errors="coerce", utc=True)
    quote_available = pd.to_datetime(frames["option_quotes"]["available_at_utc"], errors="coerce", utc=True)
    quotes_available_before_observed = int(
        (quote_observed.notna() & quote_available.notna() & (quote_available < quote_observed)).sum()
    )
    checks["recommendations_not_available_before_decision"] = recommendations_before_decision == 0
    checks["features_point_in_time_safe"] = features_after_decision == 0
    checks["outcomes_not_available_before_decision"] = outcomes_before_decision == 0
    checks["quotes_not_available_before_observation"] = quotes_available_before_observed == 0
    for label, count in (
        ("recommendations:available_before_decision", recommendations_before_decision),
        ("feature_snapshots:available_after_decision", features_after_decision),
        ("execution_outcomes:label_before_decision", outcomes_before_decision),
        ("option_quotes:available_before_observation", quotes_available_before_observed),
    ):
        if count:
            failures.append(f"{label}={count}")

    return {"status": "passed" if not failures else "failed", "checks": checks, "failures": failures}


def build_shared_research_mart(
    *,
    orographic_canonical_dir: str | Path,
    output_dir: str | Path,
    cirrus_export_dir: str | Path | None = None,
) -> dict[str, Any]:
    orographic_dir = Path(orographic_canonical_dir)
    orographic_manifest = validate_canonical_bundle(orographic_dir)
    source_rows = [_orographic_rows(orographic_dir, orographic_manifest)]
    sources: list[dict[str, Any]] = [{
        "source_system": "orographic",
        "bundle_id": orographic_manifest.get("bundle_id"),
        "manifest_file": "evidence_manifest.json",
        "manifest_sha256": _sha256(orographic_dir / "evidence_manifest.json"),
    }]
    if cirrus_export_dir is not None:
        cirrus_dir = Path(cirrus_export_dir)
        cirrus_manifest = validate_cirrus_export(cirrus_dir)
        source_rows.append(_cirrus_rows(cirrus_dir, cirrus_manifest))
        sources.append({
            "source_system": "cirrus",
            "bundle_id": cirrus_manifest.get("bundle_id"),
            "manifest_file": "manifest.json",
            "manifest_sha256": _sha256(cirrus_dir / "manifest.json"),
        })

    frames: dict[str, pd.DataFrame] = {}
    for name, contract in TABLE_CONTRACTS.items():
        rows = [row for source in source_rows for row in source[name]]
        frame = pd.DataFrame(rows, columns=contract.columns)
        if not frame.empty:
            frame = frame.sort_values(list(contract.primary_key), kind="mergesort").reset_index(drop=True)
        frames[name] = frame
    validation = _validate_tables(frames)
    if validation["status"] != "passed":
        raise ValueError("Shared research mart validation failed: " + ", ".join(validation["failures"]))

    output = Path(output_dir)
    staging = output.parent / f".{output.name}.{uuid.uuid4().hex}.tmp"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        artifacts: dict[str, Any] = {}
        for name, frame in frames.items():
            path = staging / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            artifacts[name] = {
                "path": path.name,
                "rows": int(len(frame)),
                "sha256": _sha256(path),
                "primary_key": list(TABLE_CONTRACTS[name].primary_key),
                "columns": list(TABLE_CONTRACTS[name].columns),
            }
        identity = {
            "schema_version": MART_SCHEMA_VERSION,
            "sources": sources,
            "artifacts": artifacts,
            "validation": validation,
        }
        mart_id = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
        manifest = {
            "artifact": "cirrus_orographic_shared_research_mart",
            "mart_id": mart_id,
            "generated_at_utc": _now_iso(),
            **identity,
        }
        (staging / "mart_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        backup = output.parent / f".{output.name}.{uuid.uuid4().hex}.bak"
        if output.exists():
            output.rename(backup)
        try:
            staging.rename(output)
        except Exception:
            if backup.exists() and not output.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def validate_shared_research_mart(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    manifest = _read_manifest(root / "mart_manifest.json")
    failures: list[str] = []
    if manifest.get("artifact") != "cirrus_orographic_shared_research_mart":
        failures.append("artifact")
    if manifest.get("schema_version") != MART_SCHEMA_VERSION:
        failures.append("schema_version")
    if _nested(manifest, "validation", "status") != "passed":
        failures.append("stored_validation")
    identity = {
        "schema_version": manifest.get("schema_version"),
        "sources": manifest.get("sources"),
        "artifacts": manifest.get("artifacts"),
        "validation": manifest.get("validation"),
    }
    expected_mart_id = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
    if manifest.get("mart_id") != expected_mart_id:
        failures.append("mart_id")
    frames: dict[str, pd.DataFrame] = {}
    for name, contract in TABLE_CONTRACTS.items():
        artifact = dict(manifest.get("artifacts") or {}).get(name, {})
        path = root / str(artifact.get("path") or f"{name}.parquet")
        if not path.exists():
            failures.append(f"missing:{name}")
            continue
        if artifact.get("sha256") != _sha256(path):
            failures.append(f"hash:{name}")
            continue
        frame = pd.read_parquet(path)
        frames[name] = frame
        if int(artifact.get("rows", -1)) != len(frame):
            failures.append(f"rows:{name}")
        if list(artifact.get("primary_key") or []) != list(contract.primary_key):
            failures.append(f"primary_key:{name}")
    if len(frames) == len(TABLE_CONTRACTS):
        validation = _validate_tables(frames)
        failures.extend(validation["failures"])
    if failures:
        raise ValueError("Shared research mart validation failed: " + ", ".join(failures))
    return manifest
