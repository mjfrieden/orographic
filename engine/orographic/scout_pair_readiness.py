"""Fail-closed readiness checks for matched call/put Scout evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
import hashlib
import json
import math
from typing import Any

import numpy as np

from .validation import purged_date_splits


MIN_COMPLETE_PAIRS = 150
MIN_EDGE_PAIRS_PER_SIDE = 50
MIN_DECISION_DATES = 30
MIN_REGIME_PAIRS = 25
MIN_QUALIFIED_REGIMES = 2
MIN_FOLD_TRAIN_PAIRS = 80
MIN_FOLD_VALIDATION_PAIRS = 20
MIN_FOLD_TRAIN_EDGE_PER_SIDE = 20
MIN_READY_FOLDS = 3
MIN_ARCHIVE_SYMBOLS = 20
MIN_ARCHIVE_QUOTE_DATES = 60


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _date_text(value: object) -> str:
    raw = str(value or "").strip()
    return raw[:10] if len(raw) >= 10 else ""


def _gate(passed: bool, **details: Any) -> dict[str, Any]:
    return {"passed": bool(passed), **details}


def _record_hash(records: list[dict[str, Any]]) -> str:
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _archive_coverage(manifest: dict[str, Any]) -> dict[str, Any]:
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    symbols = int(summary.get("symbol_count") or 0)
    quote_dates = int(summary.get("quote_date_count") or 0)
    rows = int(summary.get("row_count") or 0)
    adequate = symbols >= MIN_ARCHIVE_SYMBOLS and quote_dates >= MIN_ARCHIVE_QUOTE_DATES
    return {
        "symbols": symbols,
        "quote_dates": quote_dates,
        "rows": rows,
        "adequate_for_historical_pair_backfill": adequate,
        "requirements": {
            "minimum_symbols": MIN_ARCHIVE_SYMBOLS,
            "minimum_quote_dates": MIN_ARCHIVE_QUOTE_DATES,
        },
    }


def _pair_records(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    explicit_rows = 0
    strict_rows = 0
    for row in rows:
        pair_id = str(row.get("paired_observation_id") or "").strip()
        if not pair_id:
            continue
        explicit_rows += 1
        label_version = _number(row.get("executable_label_contract_version"))
        if label_version is None or label_version < 2:
            continue
        side = str(row.get("option_type") or "").strip().lower()
        pnl_pct = _number(row.get("pnl_pct"))
        if side not in {"call", "put"} or pnl_pct is None:
            continue
        strict_rows += 1
        grouped[pair_id].append(row)

    records: list[dict[str, Any]] = []
    rejected = Counter()
    for pair_id, pair_rows in sorted(grouped.items()):
        by_side: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in pair_rows:
            by_side[str(row.get("option_type") or "").lower()].append(row)
        if len(by_side.get("call", [])) != 1 or len(by_side.get("put", [])) != 1:
            rejected["not_exactly_one_contract_per_side"] += 1
            continue
        call = by_side["call"][0]
        put = by_side["put"][0]
        symbols = {str(call.get("symbol") or "").upper(), str(put.get("symbol") or "").upper()}
        entry_dates = {_date_text(call.get("entry_date")), _date_text(put.get("entry_date"))}
        expiries = {_date_text(call.get("expiry")), _date_text(put.get("expiry"))}
        if len(symbols) != 1 or "" in symbols:
            rejected["symbol_mismatch"] += 1
            continue
        if len(entry_dates) != 1 or "" in entry_dates:
            rejected["decision_date_mismatch"] += 1
            continue
        if len(expiries) != 1 or "" in expiries:
            rejected["expiry_mismatch"] += 1
            continue
        call_return = float(call["pnl_pct"])
        put_return = float(put["pnl_pct"])
        if call_return > 0 and call_return > put_return:
            label = "call_edge"
        elif put_return > 0 and put_return > call_return:
            label = "put_edge"
        else:
            label = "no_trade"
        label_date = max(
            _date_text(call.get("executable_label_available_at_utc") or call.get("exit_date")),
            _date_text(put.get("executable_label_available_at_utc") or put.get("exit_date")),
        )
        if not label_date:
            rejected["missing_label_availability_date"] += 1
            continue
        regimes = [
            str(call.get("regime_mode") or "unknown").lower(),
            str(put.get("regime_mode") or "unknown").lower(),
        ]
        regime = regimes[0] if regimes[0] == regimes[1] else "mixed"
        records.append({
            "pair_id": pair_id,
            "symbol": next(iter(symbols)),
            "decision_date": next(iter(entry_dates)),
            "label_available_date": label_date,
            "expiry": next(iter(expiries)),
            "label": label,
            "call_return": round(call_return, 6),
            "put_return": round(put_return, 6),
            "return_spread_call_minus_put": round(call_return - put_return, 6),
            "regime": regime,
        })
    return records, {
        "input_rows": len(rows),
        "explicit_pair_rows": explicit_rows,
        "strict_executable_pair_rows": strict_rows,
        "pair_ids_with_strict_rows": len(grouped),
        "rejected_pair_ids": int(sum(rejected.values())),
        **{f"rejected_{name}": int(count) for name, count in sorted(rejected.items())},
    }


def _fold_plan(records: list[dict[str, Any]]) -> dict[str, Any]:
    if len({row["decision_date"] for row in records}) < 3:
        return {
            "method": "expanding walk-forward grouped by decision date; labels purged by availability date",
            "folds": [],
            "ready_folds": 0,
            "required_ready_folds": MIN_READY_FOLDS,
        }
    feature_dates = np.array([row["decision_date"] for row in records], dtype=object)
    label_dates = np.array([row["label_available_date"] for row in records], dtype=object)
    splits = list(purged_date_splits(feature_dates, label_dates, n_splits=5))
    folds: list[dict[str, Any]] = []
    for fold_number, (train_idx, validation_idx) in enumerate(splits, start=1):
        train_labels = Counter(records[index]["label"] for index in train_idx)
        validation_labels = Counter(records[index]["label"] for index in validation_idx)
        train_edges = {
            "call_edge": int(train_labels.get("call_edge", 0)),
            "put_edge": int(train_labels.get("put_edge", 0)),
        }
        ready = (
            len(train_idx) >= MIN_FOLD_TRAIN_PAIRS
            and len(validation_idx) >= MIN_FOLD_VALIDATION_PAIRS
            and min(train_edges.values()) >= MIN_FOLD_TRAIN_EDGE_PER_SIDE
        )
        folds.append({
            "fold": fold_number,
            "ready": ready,
            "train_pairs": int(len(train_idx)),
            "validation_pairs": int(len(validation_idx)),
            "train_label_counts": dict(sorted(train_labels.items())),
            "validation_label_counts": dict(sorted(validation_labels.items())),
            "training_labels_available_through": max(
                records[index]["label_available_date"] for index in train_idx
            ),
            "validation_start": min(records[index]["decision_date"] for index in validation_idx),
            "training_evidence_sha256": _record_hash(
                [records[index] for index in train_idx]
            ),
            "validation_evidence_sha256": _record_hash(
                [records[index] for index in validation_idx]
            ),
            "artifact_policy": "retrain and freeze Scout artifacts on this fold's training rows before scoring validation dates",
        })
    return {
        "method": "expanding walk-forward grouped by decision date; labels purged by availability date",
        "artifact_policy": "one immutable model/scaler/calibrator bundle per fold; never reuse future-fold artifacts",
        "folds": folds,
        "ready_folds": sum(bool(row["ready"]) for row in folds),
        "required_ready_folds": MIN_READY_FOLDS,
        "requirements_per_ready_fold": {
            "minimum_train_pairs": MIN_FOLD_TRAIN_PAIRS,
            "minimum_validation_pairs": MIN_FOLD_VALIDATION_PAIRS,
            "minimum_train_edges_per_side": MIN_FOLD_TRAIN_EDGE_PER_SIDE,
        },
    }


def build_scout_pair_readiness(
    option_outcome_payload: dict[str, Any],
    *,
    historical_archive_manifest: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    rows = option_outcome_payload.get("rows")
    outcome_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    records, quality = _pair_records(outcome_rows)
    label_counts = Counter(row["label"] for row in records)
    regime_counts = Counter(row["regime"] for row in records)
    decision_dates = sorted({row["decision_date"] for row in records})
    fold_plan = _fold_plan(records)
    archive = _archive_coverage(historical_archive_manifest or {})
    gates = {
        "strict_executable_labels": _gate(
            quality["explicit_pair_rows"] == quality["strict_executable_pair_rows"],
            explicit_rows=quality["explicit_pair_rows"],
            strict_v2_rows=quality["strict_executable_pair_rows"],
        ),
        "minimum_complete_pairs": _gate(
            len(records) >= MIN_COMPLETE_PAIRS,
            actual=len(records),
            required=MIN_COMPLETE_PAIRS,
        ),
        "minimum_call_put_edges": _gate(
            min(int(label_counts.get("call_edge", 0)), int(label_counts.get("put_edge", 0)))
            >= MIN_EDGE_PAIRS_PER_SIDE,
            actual={
                "call_edge": int(label_counts.get("call_edge", 0)),
                "put_edge": int(label_counts.get("put_edge", 0)),
            },
            required_each=MIN_EDGE_PAIRS_PER_SIDE,
        ),
        "independent_decision_dates": _gate(
            len(decision_dates) >= MIN_DECISION_DATES,
            actual=len(decision_dates),
            required=MIN_DECISION_DATES,
        ),
        "regime_coverage": _gate(
            sum(
                count >= MIN_REGIME_PAIRS
                for regime, count in regime_counts.items()
                if regime not in {"unknown", "mixed", ""}
            )
            >= MIN_QUALIFIED_REGIMES,
            actual=dict(sorted(regime_counts.items())),
            required_regimes=MIN_QUALIFIED_REGIMES,
            required_pairs_per_regime=MIN_REGIME_PAIRS,
        ),
        "fold_frozen_plan": _gate(
            int(fold_plan["ready_folds"]) >= MIN_READY_FOLDS,
            actual_ready_folds=int(fold_plan["ready_folds"]),
            required_ready_folds=MIN_READY_FOLDS,
        ),
    }
    ready = all(bool(gate["passed"]) for gate in gates.values())
    generated = (now_utc or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "artifact": "scout_pair_readiness",
        "schema_version": 1,
        "generated_at_utc": generated,
        "status": "ready_for_fold_frozen_evaluation" if ready else "hold_collecting_pairs",
        "execution_effect": "none_research_only",
        "active_model_change_allowed": False,
        "source": {
            "artifact": option_outcome_payload.get("artifact"),
            "generated_at": option_outcome_payload.get("generated_at"),
            "label_policy": option_outcome_payload.get("label_policy"),
        },
        "coverage": {
            **quality,
            "complete_explicit_pairs": len(records),
            "decision_dates": len(decision_dates),
            "first_decision_date": decision_dates[0] if decision_dates else None,
            "last_decision_date": decision_dates[-1] if decision_dates else None,
            "label_counts": dict(sorted(label_counts.items())),
            "regime_counts": dict(sorted(regime_counts.items())),
            "evidence_sha256": _record_hash(records),
        },
        "historical_archive": archive,
        "promotion_gates": gates,
        "fold_frozen_evaluation_plan": fold_plan,
        "next_action": (
            "Run the pre-registered fold-frozen Scout evaluation; do not write active artifacts."
            if ready
            else "Continue prospective matched-pair outcome capture; historical archive coverage is insufficient for a credible backfill."
        ),
    }
