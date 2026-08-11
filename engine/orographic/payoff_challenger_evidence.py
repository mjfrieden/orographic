from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
from statistics import mean
from typing import Any


MIN_RESOLVED = 100
MIN_RESOLVED_RUNS = 30
MIN_DISAGREEMENTS = 30
MIN_SIDE_ROWS = 30
MIN_REGIME_ROWS = 25
MIN_QUALIFIED_REGIMES = 2
MIN_REPLAY_RUNS = 30
BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 20260809
ELIGIBLE_LANES = {"live", "shadow", "council_holdout"}


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _auc(probabilities: list[float], outcomes: list[int]) -> float | None:
    positives = [probability for probability, outcome in zip(probabilities, outcomes) if outcome == 1]
    negatives = [probability for probability, outcome in zip(probabilities, outcomes) if outcome == 0]
    if not positives or not negatives:
        return None
    wins = sum(
        1.0 if positive > negative else 0.5 if positive == negative else 0.0
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def _probability_metrics(rows: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    probabilities = [float(row[probability_key]) for row in rows]
    outcomes = [int(row["profitable"]) for row in rows]
    if not rows:
        return {"observations": 0, "positive_rate": None, "auc": None, "brier": None, "log_loss": None}
    clipped = [min(max(value, 1e-6), 1 - 1e-6) for value in probabilities]
    return {
        "observations": len(rows),
        "positive_rate": round(mean(outcomes), 4),
        "auc": round(value, 4) if (value := _auc(probabilities, outcomes)) is not None else None,
        "brier": round(mean((probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes)), 4),
        "log_loss": round(
            -mean(outcome * math.log(probability) + (1 - outcome) * math.log(1 - probability)
                  for probability, outcome in zip(clipped, outcomes)),
            4,
        ),
    }


def _baseline_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"positive_rate": None, "brier": None}
    positive_rate = mean(int(row["profitable"]) for row in rows)
    return {
        "positive_rate": round(positive_rate, 4),
        "brier": round(mean((positive_rate - int(row["profitable"])) ** 2 for row in rows), 4),
    }


def _strict_friday_label(pick: dict[str, Any]) -> dict[str, Any] | None:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    labels = outcomes.get("executable_labels") if isinstance(outcomes.get("executable_labels"), dict) else {}
    label = labels.get("friday_close") if isinstance(labels.get("friday_close"), dict) else None
    contract = label.get("label_contract") if isinstance(label, dict) and isinstance(label.get("label_contract"), dict) else {}
    if not label or int(contract.get("version") or 0) < 2:
        return None
    if _number(label.get("net_executable_return")) is None:
        return None
    return label


def _candidate_rows(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict):
            continue
        run_id = str(entry.get("run_generated_at_utc") or "")
        for pick in entry.get("picks", []):
            if not isinstance(pick, dict):
                continue
            if pick.get("lane") not in ELIGIBLE_LANES:
                continue
            scores = pick.get("scores") if isinstance(pick.get("scores"), dict) else {}
            active_probability = _number(scores.get("prob_positive_option_pnl"))
            shadow_probability = _number(scores.get("payoff_shadow_prob_positive"))
            artifact_sha256 = str(scores.get("payoff_shadow_artifact_sha256") or "").strip()
            contract_symbol = str(pick.get("contract_symbol") or "")
            key = (run_id, contract_symbol)
            if (
                active_probability is None or shadow_probability is None or not artifact_sha256
                or not run_id or not contract_symbol or key in seen
            ):
                continue
            seen.add(key)
            label = _strict_friday_label(pick)
            net_return = _number(label.get("net_executable_return")) if label else None
            net_pnl = _number(label.get("net_executable_pnl_usd")) if label else None
            context = pick.get("context") if isinstance(pick.get("context"), dict) else {}
            regime = context.get("regime") if isinstance(context.get("regime"), dict) else {}
            outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
            attempts = outcomes.get("capture_attempts") if isinstance(outcomes.get("capture_attempts"), dict) else {}
            friday_attempt = attempts.get("friday_close") if isinstance(attempts.get("friday_close"), dict) else {}
            rows.append({
                "run_id": run_id,
                "recommendation_id": pick.get("recommendation_id"),
                "contract_symbol": contract_symbol,
                "lane": pick.get("lane"),
                "side": str(pick.get("option_type") or "unknown").lower(),
                "regime": str(regime.get("mode") or "unknown").lower(),
                "active_probability": active_probability,
                "shadow_probability": shadow_probability,
                "artifact_sha256": artifact_sha256,
                "stored_disagreement": bool(scores.get("payoff_shadow_disagreement")),
                "decision_disagreement": (active_probability >= 0.5) != (shadow_probability >= 0.5),
                "net_return": net_return,
                "net_pnl": net_pnl,
                "profitable": int(bool(label.get("is_net_profitable"))) if label else None,
                "resolved": label is not None,
                "friday_capture_status": str(friday_attempt.get("status") or "unknown"),
            })
    return rows


def _bootstrap_lift(differences: list[float]) -> dict[str, Any]:
    if not differences:
        return {
            "observations": 0,
            "mean_lift": None,
            "confidence_interval_95": {"lower": None, "upper": None},
            "probability_positive": None,
            "method": "paired-run nonparametric bootstrap",
            "resamples": BOOTSTRAP_RESAMPLES,
        }
    rng = random.Random(BOOTSTRAP_SEED)
    size = len(differences)
    samples = sorted(mean(rng.choice(differences) for _ in range(size)) for _ in range(BOOTSTRAP_RESAMPLES))
    return {
        "observations": size,
        "mean_lift": round(mean(differences), 6),
        "confidence_interval_95": {
            "lower": round(samples[int(0.025 * (BOOTSTRAP_RESAMPLES - 1))], 6),
            "upper": round(samples[int(0.975 * (BOOTSTRAP_RESAMPLES - 1))], 6),
        },
        "probability_positive": round(sum(value > 0 for value in samples) / BOOTSTRAP_RESAMPLES, 4),
        "method": "paired-run nonparametric bootstrap",
        "resamples": BOOTSTRAP_RESAMPLES,
    }


def _rank_replay(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["run_id"]].append(row)
    complete_runs: list[list[dict[str, Any]]] = []
    incomplete_runs = 0
    single_candidate_runs = 0
    for candidates in grouped.values():
        if len(candidates) < 2:
            single_candidate_runs += 1
        elif not all(candidate["resolved"] for candidate in candidates):
            incomplete_runs += 1
        else:
            complete_runs.append(candidates)

    active_returns: list[float] = []
    shadow_returns: list[float] = []
    selection_disagreements = 0
    for candidates in complete_runs:
        active = max(candidates, key=lambda row: (row["active_probability"], row["contract_symbol"]))
        shadow = max(candidates, key=lambda row: (row["shadow_probability"], row["contract_symbol"]))
        active_returns.append(float(active["net_return"]))
        shadow_returns.append(float(shadow["net_return"]))
        selection_disagreements += active["contract_symbol"] != shadow["contract_symbol"]
    inference = _bootstrap_lift([shadow - active for active, shadow in zip(active_returns, shadow_returns)])
    return {
        "eligible_complete_runs": len(complete_runs),
        "incomplete_runs_excluded": incomplete_runs,
        "single_candidate_runs_excluded": single_candidate_runs,
        "selection_disagreements": selection_disagreements,
        "active_top1_avg_net_return": round(mean(active_returns), 4) if active_returns else None,
        "shadow_top1_avg_net_return": round(mean(shadow_returns), 4) if shadow_returns else None,
        "paired_inference": inference,
        "selection_policy": "top one active-payoff versus challenger probability per run from the identical fully resolved, post-friction candidate set",
    }


def _segment_report(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    return {
        segment: {
            "rows": len(segment_rows),
            "active": _probability_metrics(segment_rows, "active_probability"),
            "challenger": _probability_metrics(segment_rows, "shadow_probability"),
            "baseline": _baseline_metrics(segment_rows),
        }
        for segment, segment_rows in sorted(grouped.items())
    }


def build_payoff_challenger_evidence(ledger: dict[str, Any]) -> dict[str, Any]:
    all_candidates = _candidate_rows(ledger)
    active_artifact_sha256 = all_candidates[-1]["artifact_sha256"] if all_candidates else None
    candidates = [
        row for row in all_candidates
        if row["artifact_sha256"] == active_artifact_sha256
    ]
    resolved = [row for row in candidates if row["resolved"]]
    active = _probability_metrics(resolved, "active_probability")
    challenger = _probability_metrics(resolved, "shadow_probability")
    baseline = _baseline_metrics(resolved)
    by_side = _segment_report(resolved, "side")
    by_regime = _segment_report(resolved, "regime")
    replay = _rank_replay(candidates)
    disagreements = [row for row in resolved if row["decision_disagreement"]]
    stored_disagreements = [row for row in resolved if row["stored_disagreement"]]
    friday_valid = sum(row["friday_capture_status"] == "captured_valid" for row in candidates)
    friday_missed = sum(row["friday_capture_status"] == "missed_live_window" for row in candidates)
    friday_retryable = sum(row["friday_capture_status"] == "quote_missing_retryable" for row in candidates)
    friday_capture_denominator = friday_valid + friday_missed
    friday_capture_integrity = (
        friday_valid / friday_capture_denominator if friday_capture_denominator else None
    )

    resolved_runs = len({row["run_id"] for row in resolved})
    side_coverage = all((by_side.get(side) or {}).get("rows", 0) >= MIN_SIDE_ROWS for side in ("call", "put"))
    qualified_regimes = sum(
        int(report["rows"] >= MIN_REGIME_ROWS)
        for report in by_regime.values()
    )
    sample_gates = {
        "resolved_recommendations": len(resolved) >= MIN_RESOLVED,
        "resolved_runs": resolved_runs >= MIN_RESOLVED_RUNS,
        "decision_disagreements": len(disagreements) >= MIN_DISAGREEMENTS,
        "side_coverage": side_coverage,
        "regime_coverage": qualified_regimes >= MIN_QUALIFIED_REGIMES,
        "complete_rank_replay_runs": replay["eligible_complete_runs"] >= MIN_REPLAY_RUNS,
        "friday_capture_integrity": (
            friday_capture_integrity is not None
            and friday_capture_integrity >= 0.95
            and friday_retryable == 0
        ),
    }
    challenger_auc = challenger.get("auc")
    active_auc = active.get("auc")
    challenger_brier = challenger.get("brier")
    active_brier = active.get("brier")
    baseline_brier = baseline.get("brier")
    lower = replay["paired_inference"]["confidence_interval_95"]["lower"]
    performance_gates = {
        "discrimination_non_worse": (
            challenger_auc is not None and active_auc is not None
            and challenger_auc >= 0.53 and challenger_auc >= active_auc
        ),
        "calibration_skill": (
            challenger_brier is not None and active_brier is not None and baseline_brier is not None
            and challenger_brier <= active_brier and challenger_brier < baseline_brier
        ),
        "rank_replay_positive": (
            replay["shadow_top1_avg_net_return"] is not None
            and replay["shadow_top1_avg_net_return"] > 0
            and lower is not None and lower > 0
            and replay["paired_inference"]["probability_positive"] is not None
            and replay["paired_inference"]["probability_positive"] >= 0.95
        ),
        "side_stability": all(
            report["challenger"]["auc"] is not None
            and report["challenger"]["auc"] >= 0.50
            and report["challenger"]["brier"] <= report["baseline"]["brier"]
            for side, report in by_side.items() if side in {"call", "put"}
        ) and side_coverage,
        "regime_stability": sum(
            report["rows"] >= MIN_REGIME_ROWS
            and report["challenger"]["auc"] is not None
            and report["challenger"]["auc"] >= 0.53
            and report["challenger"]["brier"] <= report["baseline"]["brier"]
            for report in by_regime.values()
        ) >= MIN_QUALIFIED_REGIMES,
    }
    sample_ready = all(sample_gates.values())
    decision = "eligible_for_live_shadow" if sample_ready and all(performance_gates.values()) else (
        "hold" if sample_ready else "collecting_evidence"
    )
    return {
        "artifact": "payoff_challenger_prospective_evidence",
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "decision": decision,
        "execution_effect": "none_observation_only",
        "policy": {
            "label": "strict Friday-close executable outcome v2 or newer",
            "candidate_pairing": "active and challenger score the same post-friction recommendation; friction vetoes remain ineligible",
            "missing_outcomes": "rank replay excludes a run unless every scored candidate is resolved",
            "minimums": {
                "resolved_recommendations": MIN_RESOLVED,
                "resolved_runs": MIN_RESOLVED_RUNS,
                "decision_disagreements": MIN_DISAGREEMENTS,
                "rows_per_side": MIN_SIDE_ROWS,
                "rows_per_regime": MIN_REGIME_ROWS,
                "qualified_regimes": MIN_QUALIFIED_REGIMES,
                "rank_replay_runs": MIN_REPLAY_RUNS,
            },
        },
        "coverage": {
            "scored_recommendations": len(candidates),
            "older_model_scored_recommendations_excluded": len(all_candidates) - len(candidates),
            "resolved_recommendations": len(resolved),
            "resolved_runs": resolved_runs,
            "decision_disagreements": len(disagreements),
            "stored_broad_disagreements": len(stored_disagreements),
            "unresolved_scored_recommendations": len(candidates) - len(resolved),
            "friday_capture_valid": friday_valid,
            "friday_capture_missed": friday_missed,
            "friday_capture_quote_missing_retryable": friday_retryable,
            "friday_capture_integrity_pct": round(friday_capture_integrity, 4) if friday_capture_integrity is not None else None,
        },
        "overall": {"active": active, "challenger": challenger, "baseline": baseline},
        "disagreement_cohort": {
            "rows": len(disagreements),
            "active_accuracy": round(mean(int((row["active_probability"] >= 0.5) == bool(row["profitable"])) for row in disagreements), 4) if disagreements else None,
            "challenger_accuracy": round(mean(int((row["shadow_probability"] >= 0.5) == bool(row["profitable"])) for row in disagreements), 4) if disagreements else None,
            "avg_net_executable_return": round(mean(float(row["net_return"]) for row in disagreements), 4) if disagreements else None,
        },
        "rank_replay": replay,
        "by_side": by_side,
        "by_regime": by_regime,
        "gates": {"sample": sample_gates, "performance": performance_gates},
        "source": {
            "artifact": ledger.get("artifact"),
            "schema_version": ledger.get("schema_version"),
            "updated_at_utc": ledger.get("updated_at_utc"),
        },
        "model_cohort": {
            "artifact_sha256": active_artifact_sha256,
            "policy": "analyze only the latest observed challenger artifact; never pool model versions",
        },
        "replay_command": "python scripts/build_payoff_challenger_evidence.py",
    }


def write_payoff_challenger_evidence(ledger_path: Path, output_path: Path) -> dict[str, Any]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    artifact = build_payoff_challenger_evidence(ledger)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return artifact
