"""Observation-only competing-risk research for option exits.

The model treats +25% target, -50% stop, and expiry as competing outcomes.
Only quote marks captured after entry and no later than the recorded exit are
eligible.  The module never modifies positions or submits broker orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

from engine.orographic.path_model import FEATURE_COLS, feature_matrix
from engine.orographic.schemas import MarketRegime
from engine.orographic.validation import purged_date_splits
from engine.train_payoff_model import _candidate_from_trade, _safe_float

TARGET_RETURN = 0.25
STOP_RETURN = -0.50
HAZARD_FEATURE_COLS = [*FEATURE_COLS, "elapsed_fraction", "remaining_fraction"]


@dataclass
class PathRecord:
    entry_date: date
    label_date: date
    base_features: np.ndarray
    event: str
    duration_days: int
    terminal_return: float
    mechanical_return: float
    exact_path: bool
    valid_marks: int
    invalid_post_exit_marks: int
    side: str
    regime: str


def _timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _regime(trade: dict[str, Any]) -> MarketRegime:
    mode = str(trade.get("regime_mode") or "neutral")
    if mode not in {"risk_on", "risk_off", "neutral"}:
        mode = "neutral"
    return MarketRegime(mode, 0.25 if mode == "risk_on" else -0.25 if mode == "risk_off" else 0.0, "SPY")


def record_from_trade(trade: dict[str, Any]) -> PathRecord | None:
    try:
        entry_date = date.fromisoformat(str(trade["entry_date"]))
        label_date = date.fromisoformat(str(trade.get("exit_date") or trade.get("expiry")))
        candidate = _candidate_from_trade(trade)
    except (KeyError, TypeError, ValueError):
        return None
    entry_bound = datetime.combine(entry_date, time.min, tzinfo=timezone.utc)
    exit_bound = datetime.combine(label_date, time.max, tzinfo=timezone.utc)
    archived = trade.get("archived_quote_path") if isinstance(trade.get("archived_quote_path"), dict) else {}
    marks = archived.get("marks") if isinstance(archived.get("marks"), list) else []
    trajectory = trade.get("trajectory_marks") if isinstance(trade.get("trajectory_marks"), list) else []
    marks = [*trajectory, *marks]
    valid: list[tuple[datetime, float]] = []
    post_exit = 0
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        captured = _timestamp(mark.get("captured_at_utc"))
        value = mark.get("pnl_pct_from_emission")
        if captured is None or value is None:
            continue
        if captured > exit_bound:
            post_exit += 1
            continue
        if captured >= entry_bound:
            valid.append((captured, _safe_float(value)))
    valid.sort(key=lambda item: item[0])
    event = "expiry"
    event_time = exit_bound
    for captured, value in valid:
        if value >= TARGET_RETURN:
            event, event_time = "target", captured
            break
        if value <= STOP_RETURN:
            event, event_time = "stop", captured
            break
    terminal_return = _safe_float(
        trade.get("hold_period_return_after_friction_pct"),
        _safe_float(trade.get("pnl_pct")),
    )
    mechanical_return = TARGET_RETURN if event == "target" else STOP_RETURN if event == "stop" else terminal_return
    duration_days = max(1, int(math.ceil((event_time - entry_bound).total_seconds() / 86400.0)))
    features = feature_matrix([candidate], _regime(trade), as_of=entry_date, feature_cols=FEATURE_COLS)[0]
    return PathRecord(
        entry_date=entry_date,
        label_date=label_date,
        base_features=features,
        event=event,
        duration_days=duration_days,
        terminal_return=terminal_return,
        mechanical_return=mechanical_return,
        exact_path=bool(valid),
        valid_marks=len(valid),
        invalid_post_exit_marks=post_exit,
        side=str(trade.get("option_type") or "unknown"),
        regime=_regime(trade).mode,
    )


def load_records(paths: list[Path]) -> tuple[list[PathRecord], dict[str, Any]]:
    import json

    records: list[PathRecord] = []
    source_rows = 0
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows") if payload.get("artifact") == "option_outcome_dataset" else payload.get("all_trades", [])
        for trade in rows if isinstance(rows, list) else []:
            if not isinstance(trade, dict):
                continue
            source_rows += 1
            record = record_from_trade(trade)
            if record is not None:
                records.append(record)
    return records, {
        "source_rows": source_rows,
        "usable_terminal_records": len(records),
        "records_with_valid_pre_exit_marks": sum(record.exact_path for record in records),
        "valid_pre_exit_marks": sum(record.valid_marks for record in records),
        "invalid_post_exit_marks": sum(record.invalid_post_exit_marks for record in records),
        "event_counts": {name: sum(record.event == name for record in records) for name in ("target", "stop", "expiry")},
        "side_counts": {name: sum(record.side == name for record in records) for name in ("call", "put")},
        "regime_counts": {name: sum(record.regime == name for record in records) for name in ("risk_on", "risk_off", "neutral")},
    }


def _person_period(records: list[PathRecord]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows: list[np.ndarray] = []
    target: list[int] = []
    stop: list[int] = []
    for record in records:
        horizon = max(record.duration_days, 1)
        for day_idx in range(1, horizon + 1):
            elapsed = day_idx / horizon
            rows.append(np.concatenate([record.base_features, [elapsed, 1.0 - elapsed]]))
            terminal = day_idx == horizon
            target.append(int(terminal and record.event == "target"))
            stop.append(int(terminal and record.event == "stop"))
    return np.asarray(rows, dtype=float), np.asarray(target, dtype=int), np.asarray(stop, dtype=int)


def _fit_binary(X: np.ndarray, y: np.ndarray) -> Any:
    estimator: Any
    if len(set(y.tolist())) < 2:
        estimator = DummyClassifier(strategy="constant", constant=int(y[0]) if len(y) else 0)
    else:
        estimator = LogisticRegression(C=0.5, class_weight="balanced", max_iter=1000, random_state=42)
    model = Pipeline([("scale", RobustScaler()), ("model", estimator)])
    model.fit(X, y)
    return model


def _positive_probability(model: Any, X: np.ndarray) -> np.ndarray:
    probs = np.asarray(model.predict_proba(X), dtype=float)
    classes = [int(value) for value in model.named_steps["model"].classes_]
    if 1 not in classes:
        return np.zeros(len(X))
    if probs.shape[1] == 1:
        return np.ones(len(X))
    return probs[:, classes.index(1)]


def cumulative_incidence(bundle: dict[str, Any], base_features: np.ndarray, horizon_days: int) -> tuple[float, float, float]:
    horizon = max(int(horizon_days), 1)
    rows = np.asarray([
        np.concatenate([base_features, [day / horizon, 1.0 - day / horizon]])
        for day in range(1, horizon + 1)
    ])
    target_hazard = np.clip(_positive_probability(bundle["target_hazard_model"], rows), 0.0, 1.0)
    stop_hazard = np.clip(_positive_probability(bundle["stop_hazard_model"], rows), 0.0, 1.0)
    survival = 1.0
    target_ci = 0.0
    stop_ci = 0.0
    for target, stop in zip(target_hazard, stop_hazard):
        total = max(float(target + stop), 1.0)
        target, stop = float(target / total), float(stop / total)
        target_ci += survival * target
        stop_ci += survival * stop
        survival *= max(1.0 - target - stop, 0.0)
    return target_ci, stop_ci, survival


def _clustered_lift_interval(rows: list[tuple[date, float]], *, iterations: int = 1000) -> dict[str, Any]:
    if not rows:
        return {"mean": None, "lower_95": None, "upper_95": None}
    by_date: dict[date, list[float]] = {}
    for day, lift in rows:
        by_date.setdefault(day, []).append(lift)
    days = sorted(by_date)
    rng = np.random.default_rng(42)
    samples = []
    for _ in range(iterations):
        chosen = rng.choice(days, size=len(days), replace=True)
        samples.append(float(np.mean([value for day in chosen for value in by_date[day]])))
    values = np.asarray([lift for _, lift in rows])
    return {
        "mean": round(float(values.mean()), 4),
        "lower_95": round(float(np.quantile(samples, 0.025)), 4),
        "upper_95": round(float(np.quantile(samples, 0.975)), 4),
        "independent_dates": len(days),
    }


def train_and_evaluate(records: list[PathRecord]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    exact = [record for record in records if record.exact_path]
    report: dict[str, Any] = {
        "split_policy": "date_grouped_purged_by_recorded_exit_date",
        "fixed_policy": "hold identical entries to recorded terminal exit",
        "mechanical_policy": "+25% target / -50% stop / otherwise recorded terminal exit",
        "shadow_policy": "use mechanical policy only when OOF target incidence >= 0.55 and exceeds stop incidence by >= 0.15",
    }
    if len(exact) < 50 or len({record.event for record in exact}) < 2:
        report.update({"status": "insufficient_exact_paths", "exact_path_records": len(exact)})
        return None, report
    dates = [record.entry_date for record in exact]
    label_dates = [record.label_date for record in exact]
    splits = list(purged_date_splits(dates, label_dates, n_splits=min(5, max(2, len(exact) // 100))))
    all_target: list[int] = []
    all_stop: list[int] = []
    all_target_prob: list[float] = []
    all_stop_prob: list[float] = []
    lifts: list[tuple[date, float]] = []
    for train_idx, val_idx in splits:
        train_rows = [exact[i] for i in train_idx]
        X_train, y_target, y_stop = _person_period(train_rows)
        bundle = {"target_hazard_model": _fit_binary(X_train, y_target), "stop_hazard_model": _fit_binary(X_train, y_stop)}
        for idx in val_idx:
            record = exact[idx]
            target_prob, stop_prob, _ = cumulative_incidence(bundle, record.base_features, record.duration_days)
            use_mechanical = target_prob >= 0.55 and target_prob - stop_prob >= 0.15
            policy_return = record.mechanical_return if use_mechanical else record.terminal_return
            lifts.append((record.entry_date, policy_return - record.terminal_return))
            all_target.append(int(record.event == "target")); all_stop.append(int(record.event == "stop"))
            all_target_prob.append(target_prob); all_stop_prob.append(stop_prob)
    lift_interval = _clustered_lift_interval(lifts)
    report.update({
        "status": "evaluated",
        "exact_path_records": len(exact),
        "oof_records": len(all_target),
        "target_brier": round(float(brier_score_loss(all_target, all_target_prob)), 4),
        "stop_brier": round(float(brier_score_loss(all_stop, all_stop_prob)), 4),
        "target_auc": round(float(roc_auc_score(all_target, all_target_prob)), 4) if len(set(all_target)) > 1 else None,
        "stop_auc": round(float(roc_auc_score(all_stop, all_stop_prob)), 4) if len(set(all_stop)) > 1 else None,
        "paired_clustered_lift": lift_interval,
    })
    X_all, y_target_all, y_stop_all = _person_period(exact)
    artifact = {
        "artifact": "path_competing_risk_challenger",
        "version": 1,
        "mode": "observation_only_never_used_for_orders",
        "feature_cols": HAZARD_FEATURE_COLS,
        "target_hazard_model": _fit_binary(X_all, y_target_all),
        "stop_hazard_model": _fit_binary(X_all, y_stop_all),
        "thresholds": {"target_return": TARGET_RETURN, "stop_return": STOP_RETURN},
        "execution_effect": "none_exit_advice_only",
    }
    return artifact, report


def save_artifact(artifact: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path)
