from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo


ELIGIBLE_LANES = {"live", "shadow", "council_holdout", "counterfactual_observation"}
NO_TRADE_THRESHOLDS = (0.50, 0.60, 0.70, 0.80, 0.90)
MARGIN_THRESHOLDS = (0.00, 0.10, 0.20, 0.30)
CURRENT_NO_TRADE_THRESHOLD = 0.70
CURRENT_MARGIN_THRESHOLD = 0.20
MIN_RESOLVED_VETOES = 100
MIN_TRADING_DAYS = 30
MIN_SIDE_ROWS = 20
MIN_REGIME_ROWS = 20
MIN_QUALIFIED_REGIMES = 2
MIN_WALK_FORWARD_FOLDS = 3
MIN_TRAIN_VETOES = 10
MIN_TRAIN_DAYS = 5
BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 20260811
CENTRAL = ZoneInfo("America/Chicago")


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _strict_friday_label(pick: dict[str, Any]) -> dict[str, Any] | None:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    labels = outcomes.get("executable_labels") if isinstance(outcomes.get("executable_labels"), dict) else {}
    label = labels.get("friday_close") if isinstance(labels.get("friday_close"), dict) else None
    contract = label.get("label_contract") if isinstance(label, dict) and isinstance(label.get("label_contract"), dict) else {}
    if not label or int(contract.get("version") or 0) < 2:
        return None
    return label if _number(label.get("net_executable_return")) is not None else None


def _side_model_hash(pick: dict[str, Any]) -> str:
    context = pick.get("context") if isinstance(pick.get("context"), dict) else {}
    artifacts = context.get("model_artifacts") if isinstance(context.get("model_artifacts"), dict) else {}
    side = artifacts.get("scout_side_model") if isinstance(artifacts.get("scout_side_model"), dict) else {}
    return str(side.get("sha256") or "unversioned").strip()


def _candidate_rows(ledger: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    raw: list[dict[str, Any]] = []
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict):
            continue
        run_raw = entry.get("run_generated_at_utc")
        run_at = _timestamp(run_raw)
        if run_at is None:
            continue
        trading_day = run_at.astimezone(CENTRAL).date().isoformat()
        for pick in entry.get("picks", []):
            if not isinstance(pick, dict) or pick.get("lane") not in ELIGIBLE_LANES:
                continue
            risk = pick.get("risk_features") if isinstance(pick.get("risk_features"), dict) else {}
            call = _number(risk.get("scout_call_edge_prob"))
            put = _number(risk.get("scout_put_edge_prob"))
            no_trade = _number(risk.get("scout_no_trade_prob"))
            symbol = str(pick.get("symbol") or "").strip().upper()
            contract_symbol = str(pick.get("contract_symbol") or "").strip().upper()
            if call is None or put is None or no_trade is None or not symbol or not contract_symbol:
                continue
            label = _strict_friday_label(pick)
            outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
            attempts = outcomes.get("capture_attempts") if isinstance(outcomes.get("capture_attempts"), dict) else {}
            friday = attempts.get("friday_close") if isinstance(attempts.get("friday_close"), dict) else {}
            context = pick.get("context") if isinstance(pick.get("context"), dict) else {}
            regime = context.get("regime") if isinstance(context.get("regime"), dict) else {}
            side = str(pick.get("option_type") or "unknown").lower()
            direction_probability = call if side == "call" else put if side == "put" else max(call, put)
            raw.append({
                "run_at": run_at,
                "run_id": str(run_raw),
                "trading_day": trading_day,
                "symbol": symbol,
                "contract_symbol": contract_symbol,
                "lane": str(pick.get("lane")),
                "side": side,
                "regime": str(regime.get("mode") or "unknown").lower(),
                "call_probability": call,
                "put_probability": put,
                "no_trade_probability": no_trade,
                "no_trade_is_preferred": no_trade >= max(call, put),
                "no_trade_margin_vs_direction": no_trade - direction_probability,
                "side_model_sha256": _side_model_hash(pick),
                "resolved": label is not None,
                "net_return": _number(label.get("net_executable_return")) if label else None,
                "net_pnl": _number(label.get("net_executable_pnl_usd")) if label else None,
                "capture_status": str(friday.get("status") or "unknown"),
            })

    # Repeated intraday scans are not independent observations. The first
    # recommendation for a symbol-contract-day is pre-registered as canonical.
    canonical: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in sorted(raw, key=lambda item: (item["run_at"], item["contract_symbol"])):
        key = (row["trading_day"], row["symbol"], row["contract_symbol"])
        canonical.setdefault(key, row)
    return list(canonical.values()), len(raw) - len(canonical)


def _descriptive(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row["resolved"]]
    returns = [float(row["net_return"]) for row in resolved]
    pnls = [float(row["net_pnl"]) for row in resolved if row["net_pnl"] is not None]
    return {
        "observations": len(rows),
        "resolved": len(resolved),
        "trading_days": len({row["trading_day"] for row in resolved}),
        "mean_net_executable_return": round(mean(returns), 6) if returns else None,
        "median_net_executable_return": round(median(returns), 6) if returns else None,
        "positive_rate": round(mean(value > 0 for value in returns), 4) if returns else None,
        "mean_net_executable_pnl_usd": round(mean(pnls), 4) if pnls else None,
    }


def _clustered_veto_benefit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row["resolved"]:
            by_day[row["trading_day"]].append(-float(row["net_return"]))
    daily = [mean(values) for _, values in sorted(by_day.items())]
    if not daily:
        return {
            "trading_days": 0,
            "mean_avoided_net_return": None,
            "confidence_interval_95": {"lower": None, "upper": None},
            "probability_positive": None,
            "method": "market-day clustered nonparametric bootstrap",
            "resamples": BOOTSTRAP_RESAMPLES,
        }
    rng = random.Random(BOOTSTRAP_SEED)
    size = len(daily)
    samples = sorted(mean(rng.choice(daily) for _ in range(size)) for _ in range(BOOTSTRAP_RESAMPLES))
    return {
        "trading_days": size,
        "mean_avoided_net_return": round(mean(daily), 6),
        "confidence_interval_95": {
            "lower": round(samples[int(0.025 * (BOOTSTRAP_RESAMPLES - 1))], 6),
            "upper": round(samples[int(0.975 * (BOOTSTRAP_RESAMPLES - 1))], 6),
        },
        "probability_positive": round(sum(value > 0 for value in samples) / BOOTSTRAP_RESAMPLES, 4),
        "method": "market-day clustered nonparametric bootstrap",
        "resamples": BOOTSTRAP_RESAMPLES,
        "estimand": "equal-weight daily mean return avoided by abstaining; positive values support the veto",
    }


def _is_vetoed(row: dict[str, Any], no_trade_threshold: float, margin_threshold: float) -> bool:
    return (
        row["no_trade_is_preferred"]
        and row["no_trade_probability"] >= no_trade_threshold
        and row["no_trade_margin_vs_direction"] >= margin_threshold
    )


def _frontier_point(rows: list[dict[str, Any]], no_trade_threshold: float, margin_threshold: float) -> dict[str, Any]:
    vetoed = [row for row in rows if _is_vetoed(row, no_trade_threshold, margin_threshold)]
    retained = [row for row in rows if row not in vetoed]
    return {
        "no_trade_threshold": no_trade_threshold,
        "margin_threshold": margin_threshold,
        "vetoed": _descriptive(vetoed),
        "retained": _descriptive(retained),
        "veto_benefit": _clustered_veto_benefit(vetoed),
    }


def _walk_forward_threshold_selection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Select thresholds on expanding history and score only unseen dates."""
    resolved = [row for row in rows if row["resolved"]]
    days = sorted({row["trading_day"] for row in resolved})
    if len(days) < 10:
        return {
            "eligible_folds": 0,
            "folds": [],
            "out_of_fold_vetoes": _descriptive([]),
            "out_of_fold_veto_benefit": _clustered_veto_benefit([]),
            "method": "five-block expanding walk-forward; one trading-day embargo; threshold selected on training dates only",
        }
    block_size = max(math.ceil(len(days) / 5), 1)
    folds: list[dict[str, Any]] = []
    out_of_fold: list[dict[str, Any]] = []
    for test_start in range(block_size, len(days), block_size):
        test_days = days[test_start:min(test_start + block_size, len(days))]
        embargo_day = days[test_start - 1]
        train_days = set(days[:max(test_start - 1, 0)])
        train_rows = [row for row in resolved if row["trading_day"] in train_days]
        test_rows = [row for row in resolved if row["trading_day"] in set(test_days)]
        choices: list[tuple[float, int, float, float]] = []
        for no_trade in NO_TRADE_THRESHOLDS:
            for margin in MARGIN_THRESHOLDS:
                vetoed = [row for row in train_rows if _is_vetoed(row, no_trade, margin)]
                veto_days = len({row["trading_day"] for row in vetoed})
                if len(vetoed) < MIN_TRAIN_VETOES or veto_days < MIN_TRAIN_DAYS:
                    continue
                # Selection objective is computed only from prior market days.
                daily: dict[str, list[float]] = defaultdict(list)
                for row in vetoed:
                    daily[row["trading_day"]].append(-float(row["net_return"]))
                score = mean(mean(values) for values in daily.values())
                choices.append((score, len(vetoed), no_trade, margin))
        if not choices:
            folds.append({
                "test_start": test_days[0],
                "test_end": test_days[-1],
                "embargo_day": embargo_day,
                "status": "insufficient_training_vetoes",
                "training_rows": len(train_rows),
                "test_rows": len(test_rows),
            })
            continue
        _, train_vetoes, selected_no_trade, selected_margin = max(
            choices,
            key=lambda item: (item[0], item[1], -abs(item[2] - CURRENT_NO_TRADE_THRESHOLD), -abs(item[3] - CURRENT_MARGIN_THRESHOLD)),
        )
        test_vetoes = [
            row for row in test_rows
            if _is_vetoed(row, selected_no_trade, selected_margin)
        ]
        out_of_fold.extend(test_vetoes)
        folds.append({
            "test_start": test_days[0],
            "test_end": test_days[-1],
            "embargo_day": embargo_day,
            "status": "evaluated",
            "training_rows": len(train_rows),
            "training_vetoes": train_vetoes,
            "test_rows": len(test_rows),
            "test_vetoes": len(test_vetoes),
            "selected_rule": {
                "no_trade_threshold": selected_no_trade,
                "margin_threshold": selected_margin,
            },
            "test_veto_benefit": _clustered_veto_benefit(test_vetoes),
        })
    return {
        "eligible_folds": sum(fold["status"] == "evaluated" for fold in folds),
        "folds": folds,
        "out_of_fold_vetoes": _descriptive(out_of_fold),
        "out_of_fold_veto_benefit": _clustered_veto_benefit(out_of_fold),
        "method": "five-block expanding walk-forward; one trading-day embargo; threshold selected on training dates only",
        "selection_objective": "maximize training-period equal-weight daily avoided net executable return subject to minimum coverage",
        "minimum_training_coverage": {"vetoes": MIN_TRAIN_VETOES, "trading_days": MIN_TRAIN_DAYS},
    }


def _segment(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    return {
        name: {"outcomes": _descriptive(group), "veto_benefit": _clustered_veto_benefit(group)}
        for name, group in sorted(grouped.items())
    }


def _progress(current: int, required: int) -> dict[str, int | float]:
    return {
        "current": current,
        "required": required,
        "remaining": max(required - current, 0),
        "progress_pct": round(min(current / required, 1.0), 4),
    }


def build_counterfactual_veto_evidence(ledger: dict[str, Any]) -> dict[str, Any]:
    independent, duplicate_count = _candidate_rows(ledger)
    latest_hash = independent[-1]["side_model_sha256"] if independent else None
    cohort = [row for row in independent if row["side_model_sha256"] == latest_hash]
    older_model_rows = len(independent) - len(cohort)
    resolved = [row for row in cohort if row["resolved"]]
    current_vetoes = [
        row for row in cohort
        if _is_vetoed(row, CURRENT_NO_TRADE_THRESHOLD, CURRENT_MARGIN_THRESHOLD)
    ]
    resolved_vetoes = [row for row in current_vetoes if row["resolved"]]
    current_rule = _frontier_point(cohort, CURRENT_NO_TRADE_THRESHOLD, CURRENT_MARGIN_THRESHOLD)
    walk_forward = _walk_forward_threshold_selection(cohort)
    by_side = _segment(resolved_vetoes, "side")
    by_regime = _segment(resolved_vetoes, "regime")

    valid = sum(row["capture_status"] == "captured_valid" for row in cohort)
    missed = sum(row["capture_status"] == "missed_live_window" for row in cohort)
    retryable = sum(row["capture_status"] == "quote_missing_retryable" for row in cohort)
    denominator = valid + missed
    integrity = valid / denominator if denominator else None
    days = len({row["trading_day"] for row in resolved_vetoes})
    call_rows = int((by_side.get("call") or {}).get("outcomes", {}).get("resolved", 0))
    put_rows = int((by_side.get("put") or {}).get("outcomes", {}).get("resolved", 0))
    qualified_regimes = sum(
        int(report.get("outcomes", {}).get("resolved", 0) >= MIN_REGIME_ROWS)
        for report in by_regime.values()
    )
    inference = current_rule["veto_benefit"]
    lower = inference["confidence_interval_95"]["lower"]
    probability_positive = inference["probability_positive"]
    walk_forward_inference = walk_forward["out_of_fold_veto_benefit"]
    walk_forward_lower = walk_forward_inference["confidence_interval_95"]["lower"]
    walk_forward_probability = walk_forward_inference["probability_positive"]
    sample_gates = {
        "resolved_current_rule_vetoes": len(resolved_vetoes) >= MIN_RESOLVED_VETOES,
        "independent_trading_days": days >= MIN_TRADING_DAYS,
        "call_coverage": call_rows >= MIN_SIDE_ROWS,
        "put_coverage": put_rows >= MIN_SIDE_ROWS,
        "regime_coverage": qualified_regimes >= MIN_QUALIFIED_REGIMES,
        "friday_capture_integrity": integrity is not None and integrity >= 0.95 and retryable == 0,
        "walk_forward_folds": walk_forward["eligible_folds"] >= MIN_WALK_FORWARD_FOLDS,
    }
    performance_gates = {
        "current_rule_avoids_losses": lower is not None and lower > 0,
        "bootstrap_probability_positive": probability_positive is not None and probability_positive >= 0.95,
        "walk_forward_out_of_fold_positive": (
            walk_forward_lower is not None and walk_forward_lower > 0
            and walk_forward_probability is not None and walk_forward_probability >= 0.95
        ),
    }
    sample_ready = all(sample_gates.values())
    decision = "eligible_for_policy_review" if sample_ready and all(performance_gates.values()) else (
        "hold" if sample_ready else "collecting_evidence"
    )
    if not cohort:
        next_action = "Collect Forge-ranked recommendations with Scout side probabilities."
    elif len(resolved_vetoes) < MIN_RESOLVED_VETOES:
        next_action = "Capture more strict Friday-close outcomes for independent current-rule veto observations."
    elif days < MIN_TRADING_DAYS:
        next_action = "Continue observation across more independent market days."
    elif not sample_gates["call_coverage"] or not sample_gates["put_coverage"]:
        next_action = "Continue until both call and put veto cohorts meet coverage minimums."
    elif not sample_gates["regime_coverage"]:
        next_action = "Continue collection across at least two sufficiently sampled regimes."
    elif not sample_gates["friday_capture_integrity"]:
        next_action = "Restore at least 95% Friday-close capture integrity with no retryable gaps."
    elif not sample_gates["walk_forward_folds"]:
        next_action = "Accumulate enough dated outcomes for at least three embargoed walk-forward folds."
    else:
        next_action = "Review the threshold frontier; any policy change still requires an explicit controlled experiment."

    frontier = [
        _frontier_point(cohort, no_trade, margin)
        for no_trade in NO_TRADE_THRESHOLDS
        for margin in MARGIN_THRESHOLDS
    ]
    return {
        "artifact": "counterfactual_scout_veto_evidence",
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "decision": decision,
        "execution_effect": "none_advisory_only",
        "policy": {
            "cohort": "Forge-ranked live, shadow, Council-holdout, and counterfactual-observation candidates",
            "label": "strict Friday-close executable outcome v2 or newer",
            "independence_unit": "first recommendation per Central trading date, symbol, and option contract",
            "inference_unit": "market-day cluster",
            "current_rule": {
                "no_trade_probability_gte": CURRENT_NO_TRADE_THRESHOLD,
                "no_trade_margin_over_active_direction_gte": CURRENT_MARGIN_THRESHOLD,
                "required_preferred_class": "no_trade",
            },
            "scope_limit": "Conditional on reaching Forge; this artifact does not estimate outcomes for symbols never ranked by Forge.",
            "authority": "May recommend a policy review only; cannot alter thresholds, Council eligibility, or Tradier routing.",
        },
        "coverage": {
            "raw_scored_recommendations": len(independent) + duplicate_count,
            "independent_recommendations": len(cohort),
            "repeated_scan_rows_excluded": duplicate_count,
            "older_side_model_rows_excluded": older_model_rows,
            "resolved_recommendations": len(resolved),
            "current_rule_vetoes": len(current_vetoes),
            "resolved_current_rule_vetoes": len(resolved_vetoes),
            "independent_veto_trading_days": days,
            "friday_capture_valid": valid,
            "friday_capture_missed": missed,
            "friday_capture_quote_missing_retryable": retryable,
            "friday_capture_integrity_pct": round(integrity, 4) if integrity is not None else None,
        },
        "current_rule": current_rule,
        "threshold_frontier": frontier,
        "walk_forward_threshold_selection": walk_forward,
        "current_rule_by_side": by_side,
        "current_rule_by_regime": by_regime,
        "gates": {"sample": sample_gates, "performance": performance_gates},
        "readiness": {
            "sample_ready": sample_ready,
            "blocking_sample_gates": [name for name, passed in sample_gates.items() if not passed],
            "progress": {
                "resolved_current_rule_vetoes": _progress(len(resolved_vetoes), MIN_RESOLVED_VETOES),
                "independent_trading_days": _progress(days, MIN_TRADING_DAYS),
                "call_rows": _progress(call_rows, MIN_SIDE_ROWS),
                "put_rows": _progress(put_rows, MIN_SIDE_ROWS),
                "qualified_regimes": _progress(qualified_regimes, MIN_QUALIFIED_REGIMES),
                "walk_forward_folds": _progress(walk_forward["eligible_folds"], MIN_WALK_FORWARD_FOLDS),
            },
            "next_action": next_action,
        },
        "model_cohort": {
            "scout_side_model_sha256": latest_hash,
            "policy": "analyze only the latest observed side-model artifact; never pool model versions",
        },
        "source": {
            "artifact": ledger.get("artifact"),
            "schema_version": ledger.get("schema_version"),
            "updated_at_utc": ledger.get("updated_at_utc"),
        },
        "replay_command": "python scripts/build_counterfactual_veto_evidence.py",
    }


def write_counterfactual_veto_evidence(ledger_path: Path, output_path: Path) -> dict[str, Any]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    artifact = build_counterfactual_veto_evidence(ledger)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return artifact
