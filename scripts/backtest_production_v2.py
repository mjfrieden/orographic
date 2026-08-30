"""Evaluate the frozen production-v2 ranker on later executable outcomes.

This is an evidence-mart backtest, not a synthetic option-chain replay.  It
uses the exact executable entry/exit labels in a strict outcome dataset and
keeps the production artifact frozen.  The report deliberately separates:

* model discrimination on deduplicated contracts;
* within-scan ranking against the previously recorded candidate score; and
* the live Council/execution-policy replay.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from engine.orographic.council import select_board
from engine.orographic.execution_policy import LiveExecutionPolicy, apply_live_execution_policy
from engine.orographic.production_ranker import score_production_candidates
from engine.orographic.schemas import ContractCandidate, MarketRegime
from engine.train_payoff_model import _candidate_from_trade, _training_feature_matrix, load_examples


DEFAULT_ARTIFACT = Path("engine/orographic/models/production_payoff_ranker.pkl")
DEFAULT_TRAINING_SOURCE = Path("output/option_outcomes_live_recommendations.json")


def _number(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_auc(y: np.ndarray, score: np.ndarray) -> float | None:
    if len(y) < 2 or len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, score))


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(float(value), digits) if value is not None and np.isfinite(value) else None


def _bootstrap_auc_by_date(
    y: np.ndarray,
    score: np.ndarray,
    dates: np.ndarray,
    *,
    iterations: int = 5_000,
    seed: int = 20260830,
) -> list[float | None]:
    unique_dates = np.array(sorted(set(dates.tolist())), dtype=object)
    rng = np.random.default_rng(seed)
    estimates: list[float] = []
    date_indices = {day: np.where(dates == day)[0] for day in unique_dates}
    for _ in range(iterations):
        sampled = rng.choice(unique_dates, size=len(unique_dates), replace=True)
        idx = np.concatenate([date_indices[day] for day in sampled])
        auc = _safe_auc(y[idx], score[idx])
        if auc is not None:
            estimates.append(auc)
    if not estimates:
        return [None, None]
    return [_round(float(np.quantile(estimates, 0.025))), _round(float(np.quantile(estimates, 0.975)))]


def _probabilities(artifact: dict[str, Any], X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if artifact.get("mode") == "production_tail_utility":
        raw = np.asarray(artifact["base_model"].predict_proba(X), dtype=float)
        aligned = np.zeros((len(X), 4), dtype=float)
        for column, outcome_class in enumerate(artifact["base_model"].classes_):
            aligned[:, int(outcome_class)] = raw[:, column]
        return aligned[:, 3], aligned[:, 3]
    raw = np.asarray(artifact["base_model"].predict_proba(X)[:, 1], dtype=float)
    calibrator = artifact.get("calibrator")
    if calibrator is None:
        return raw, np.clip(raw, 1e-6, 1 - 1e-6)
    clipped = np.clip(raw, 1e-6, 1 - 1e-6)
    logits = np.log(clipped / (1.0 - clipped)) + float(calibrator)
    return raw, np.clip(1.0 / (1.0 + np.exp(-logits)), 1e-6, 1 - 1e-6)


def _segment_metrics(y: np.ndarray, score: np.ndarray, segments: np.ndarray) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for segment in sorted(set(segments.tolist())):
        idx = np.where(segments == segment)[0]
        segment_y = y[idx]
        segment_score = score[idx]
        baseline = np.full(len(idx), float(segment_y.mean()))
        result[str(segment)] = {
            "rows": int(len(idx)),
            "positive_rate": _round(float(segment_y.mean())),
            "auc": _round(_safe_auc(segment_y, segment_score)),
            "brier": _round(float(brier_score_loss(segment_y, segment_score))),
            "baseline_brier": _round(float(brier_score_loss(segment_y, baseline))),
        }
    return result


def _candidate_with_exact_quote(row: dict[str, Any]) -> ContractCandidate:
    candidate = _candidate_from_trade(row)
    bid = _number(row.get("entry_bid"))
    ask = _number(row.get("entry_ask"))
    if bid is not None:
        candidate.bid = bid
    if ask is not None:
        candidate.ask = ask
        candidate.premium = ask
        candidate.last = ask
        candidate.contract_cost = ask * 100.0
        candidate.spread_cost = ask
    candidate.spread_pct = _number(row.get("entry_spread_pct")) or candidate.spread_pct
    candidate.open_interest = int(_number(row.get("entry_open_interest")) or 0)
    candidate.volume = int(_number(row.get("entry_volume")) or 0)
    return candidate


def _grouped_auc(
    rows: list[dict[str, Any]], score_field: str, *, target_field: str = "positive_pnl_after_friction"
) -> dict[str, Any]:
    comparable_pairs = 0
    concordance_sum = 0.0
    eligible_runs = 0
    for run in sorted({str(row["run_generated_at_utc"]) for row in rows}):
        group = [row for row in rows if str(row["run_generated_at_utc"]) == run and row.get(score_field) is not None]
        positives = [row for row in group if bool(row[target_field])]
        negatives = [row for row in group if not bool(row[target_field])]
        pairs = len(positives) * len(negatives)
        if not pairs:
            continue
        eligible_runs += 1
        concordance = 0.0
        for positive in positives:
            for negative in negatives:
                p_score = float(positive[score_field])
                n_score = float(negative[score_field])
                concordance += 1.0 if p_score > n_score else 0.5 if p_score == n_score else 0.0
        comparable_pairs += pairs
        concordance_sum += concordance
    return {
        "auc": _round(concordance_sum / comparable_pairs if comparable_pairs else None),
        "eligible_runs": eligible_runs,
        "comparable_positive_negative_pairs": comparable_pairs,
    }


def _bootstrap_selection_by_date(
    records: list[dict[str, Any]],
    prefix: str,
    *,
    iterations: int = 5_000,
    seed: int = 20260830,
) -> dict[str, list[float | None]]:
    chosen = [record for record in records if record.get(f"{prefix}_row") is not None]
    dates = sorted({record["decision_date"] for record in chosen})
    if len(dates) < 2:
        return {"win_rate_95_ci": [None, None], "avg_return_95_ci": [None, None]}
    by_date = {day: [record for record in chosen if record["decision_date"] == day] for day in dates}
    rng = np.random.default_rng(seed)
    win_rates: list[float] = []
    avg_returns: list[float] = []
    for _ in range(iterations):
        sampled = rng.choice(dates, size=len(dates), replace=True)
        rows = [record[f"{prefix}_row"] for day in sampled for record in by_date[day]]
        win_rates.append(float(np.mean([bool(row["positive_pnl_after_friction"]) for row in rows])))
        avg_returns.append(float(np.mean([float(row["hold_period_return_after_friction_pct"]) for row in rows])))
    return {
        "win_rate_95_ci": [_round(float(np.quantile(win_rates, 0.025))), _round(float(np.quantile(win_rates, 0.975)))],
        "avg_return_95_ci": [_round(float(np.quantile(avg_returns, 0.025))), _round(float(np.quantile(avg_returns, 0.975)))],
    }


def _selection_summary(records: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    chosen = [record for record in records if record.get(f"{prefix}_row") is not None]
    if not chosen:
        return {"runs": 0, "win_rate": None, "avg_return_after_friction_pct": None, "total_pnl_usd": None}
    rows = [record[f"{prefix}_row"] for record in chosen]
    result = {
        "runs": len(rows),
        "win_rate": _round(float(np.mean([bool(row["positive_pnl_after_friction"]) for row in rows]))),
        "avg_return_after_friction_pct": _round(float(np.mean([float(row["hold_period_return_after_friction_pct"]) for row in rows]))),
        "median_return_after_friction_pct": _round(float(np.median([float(row["hold_period_return_after_friction_pct"]) for row in rows]))),
        "total_pnl_usd": _round(float(np.sum([float(row["pnl"]) for row in rows])), 2),
        "contracts": [str(row["contract_symbol"]) for row in rows],
    }
    result.update(_bootstrap_selection_by_date(records, prefix))
    return result


def _training_lineage(training_source: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"source_path": str(training_source), "verified_exact_retrain": False}
    if not training_source.exists():
        result["reason"] = "training source unavailable"
        return result
    if artifact.get("mode") == "production_tail_utility":
        payload = json.loads(training_source.read_text(encoding="utf-8"))
        rows = payload.get("rows") or []
        keys = ("symbol", "option_type", "strike", "expiry", "entry_date", "exit_date")
        deduplicated = {tuple(row.get(key) for key in keys) for row in rows}
        training_rows = int(artifact.get("training_rows", 0) or 0)
        training_end = str(artifact.get("training_end") or "")
        source_end = max((str(row.get("entry_date")) for row in rows), default="")
        result.update({
            "verified_exact_retrain": len(deduplicated) == training_rows and source_end == training_end,
            "source_sha256": _sha256(training_source),
            "source_generated_at": payload.get("generated_at"),
            "source_backtest_start": min((str(row.get("entry_date")) for row in rows), default=None),
            "source_backtest_end": source_end or None,
            "source_rows": len(rows),
            "deduplicated_examples": len(deduplicated),
            "training_rows": training_rows,
            "model_training_end": training_end,
        })
        return result
    examples, metadata = load_examples([training_source], options_data_dir=None)
    from scripts.train_payoff_shadow_challenger import _last_inner_split, _fit_sigmoid_calibrator
    from engine.train_payoff_model import _balanced_sample_weight, _fit_classifier, _positive_proba

    X = _training_feature_matrix(examples, feature_cols=list(artifact["feature_cols"]))
    y = np.array([example.prob_positive_option_pnl for example in examples], dtype=int)
    sides = np.array([example.candidate.option_type for example in examples], dtype=object)
    feature_dates = np.array([example.entry_date for example in examples], dtype=object)
    label_dates = np.array([example.exit_date or example.entry_date for example in examples], dtype=object)
    train_idx, calibration_idx = _last_inner_split(feature_dates, label_dates)
    model = _fit_classifier(X[train_idx], y[train_idx], _balanced_sample_weight(sides[train_idx], y[train_idx]), family="linear")
    calibrator = _fit_sigmoid_calibrator(_positive_proba(model, X[calibration_idx]), y[calibration_idx])
    original = artifact["base_model"]
    exact = bool(
        np.allclose(model.named_steps["model"].coef_, original.named_steps["model"].coef_)
        and np.allclose(model.named_steps["model"].intercept_, original.named_steps["model"].intercept_)
        and np.allclose(model.named_steps["scaler"].center_, original.named_steps["scaler"].center_)
        and np.allclose(model.named_steps["scaler"].scale_, original.named_steps["scaler"].scale_)
        and np.isclose(float(calibrator), float(artifact["calibrator"]))
    )
    payload = json.loads(training_source.read_text(encoding="utf-8"))
    result.update({
        "verified_exact_retrain": exact,
        "source_sha256": _sha256(training_source),
        "source_generated_at": payload.get("generated_at"),
        "source_backtest_start": payload.get("backtest_start"),
        "source_backtest_end": payload.get("backtest_end"),
        "source_rows": len(payload.get("rows") or []),
        "deduplicated_examples": len(examples),
        "training_rows": int(len(train_idx)),
        "calibration_rows": int(len(calibration_idx)),
        "metadata": metadata,
    })
    return result


def run_backtest(dataset: Path, artifact_path: Path, training_source: Path) -> dict[str, Any]:
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    if payload.get("artifact") != "option_outcome_dataset":
        raise ValueError("dataset must be an option_outcome_dataset")
    if not str(payload.get("label_policy") or "").startswith("strict_executable_quote_or_fill_v"):
        raise ValueError("dataset must use a strict executable label policy")
    raw_rows = [deepcopy(row) for row in payload.get("rows") or []]
    if not raw_rows:
        raise ValueError("dataset has no rows")

    artifact = joblib.load(artifact_path)
    if artifact.get("mode") not in {"production_rank_only", "production_tail_utility"}:
        raise ValueError("artifact is not an active production ranker")
    lineage = _training_lineage(training_source, artifact)

    if artifact.get("mode") == "production_tail_utility":
        from scripts.train_production_tail_ranker import _feature_matrix, _load_rows

        model_frame = _load_rows(dataset, scored_only=False, deduplicate=True)
        X = _feature_matrix(model_frame)
        y = (
            model_frame["hold_period_return_after_friction_pct"].to_numpy(dtype=float) >= 0.50
        ).astype(int)
        sides = model_frame["option_type"].to_numpy(dtype=object)
        entry_dates = model_frame["entry_date"].to_numpy(dtype=object)
        _, source_metadata = load_examples([dataset], options_data_dir=None)
        model_row_count = len(model_frame)
    else:
        examples, source_metadata = load_examples([dataset], options_data_dir=None)
        X = _training_feature_matrix(examples, feature_cols=list(artifact["feature_cols"]))
        y = np.array([example.prob_positive_option_pnl for example in examples], dtype=int)
        sides = np.array([example.candidate.option_type for example in examples], dtype=object)
        entry_dates = np.array([example.entry_date.isoformat() for example in examples], dtype=object)
        model_row_count = len(examples)
    raw_probs, probs = _probabilities(artifact, X)
    baseline = np.full(len(y), float(y.mean()))
    model_metrics = {
        "rows": model_row_count,
        "positive_rate": _round(float(y.mean())),
        "raw_auc": _round(_safe_auc(y, raw_probs)),
        "calibrated_auc": _round(_safe_auc(y, probs)),
        "calibrated_auc_cluster_bootstrap_95_ci": _bootstrap_auc_by_date(y, probs, entry_dates),
        "average_precision": _round(float(average_precision_score(y, probs))),
        "brier": _round(float(brier_score_loss(y, probs))),
        "baseline_brier": _round(float(brier_score_loss(y, baseline))),
        "brier_skill_vs_constant": _round(1.0 - float(brier_score_loss(y, probs)) / float(brier_score_loss(y, baseline))),
        "mean_probability": _round(float(probs.mean())),
        "probability_range": [_round(float(probs.min())), _round(float(probs.max()))],
        "by_side": _segment_metrics(y, probs, sides),
    }
    top_count = max(int(np.ceil(len(y) * 0.10)), 1)
    order = np.argsort(probs)
    model_metrics["top_decile"] = {
        "rows": top_count,
        "positive_rate": _round(float(y[order[-top_count:]].mean())),
        "lift_vs_base_rate": _round(float(y[order[-top_count:]].mean() / y.mean())),
    }
    model_metrics["bottom_decile"] = {
        "rows": top_count,
        "positive_rate": _round(float(y[order[:top_count]].mean())),
        "lift_vs_base_rate": _round(float(y[order[:top_count]].mean() / y.mean())),
    }

    indexed_rows: dict[int, dict[str, Any]] = {index: row for index, row in enumerate(raw_rows)}
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in indexed_rows.items():
        row["big_win_after_friction"] = float(row["hold_period_return_after_friction_pct"]) >= 0.50
        groups[str(row["run_generated_at_utc"])].append((index, row))

    replay_records: list[dict[str, Any]] = []
    prior_exposures: list[dict[str, Any]] = []
    for run_at in sorted(groups, key=_utc):
        indexed_group = groups[run_at]
        candidates: list[ContractCandidate] = []
        candidate_index: dict[int, int] = {}
        for index, row in indexed_group:
            candidate = _candidate_with_exact_quote(row)
            candidates.append(candidate)
            candidate_index[id(candidate)] = index
        first = indexed_group[0][1]
        regime = MarketRegime(
            mode=str(first.get("regime_mode") or "neutral"),
            bias=float(_number(first.get("regime_bias")) or 0.0),
            source_symbol=str(first.get("regime_source_symbol") or "SPY"),
        )
        as_of = date.fromisoformat(str(first["entry_date"]))
        score_production_candidates(candidates, regime, as_of=as_of, model_path=artifact_path)
        for candidate in candidates:
            row = indexed_rows[candidate_index[id(candidate)]]
            row["production_v2_score"] = float(candidate.forge_score)
            row["production_v2_probability"] = float(
                candidate.prob_big_win
                if artifact.get("mode") == "production_tail_utility"
                else candidate.prob_positive_option_pnl
                or 0.0
            )

        apply_live_execution_policy(
            candidates,
            prior_exposures=prior_exposures,
            as_of_utc=_utc(run_at),
            policy=LiveExecutionPolicy(min_open_interest=150),
        )
        council = select_board(
            candidates,
            regime,
            live_size=1,
            shadow_size=0,
            minimum_live_score=0.86,
            minimum_put_live_score=0.84,
            max_live_extrinsic_ratio=1.0,
            corr_matrix=None,
            fetch_live_corr=False,
        )
        score_top = indexed_rows[candidate_index[id(candidates[0])]] if candidates else None
        legacy_candidates = [
            row for _, row in indexed_group if _number(row.get("final_candidate_score")) is not None
        ]
        legacy_top = max(legacy_candidates, key=lambda row: float(row["final_candidate_score"])) if legacy_candidates else None
        live_row = indexed_rows[candidate_index[id(council.live_board[0])]] if council.live_board else None
        if council.live_board:
            chosen = council.live_board[0]
            prior_exposures.append({
                "contract_symbol": chosen.contract_symbol.upper(),
                "symbol": chosen.symbol.upper(),
                "emitted_at_utc": _utc(run_at),
                "ask": chosen.ask,
                "expected_edge_after_friction_pct": chosen.expected_edge_after_friction_pct,
            })
        replay_records.append({
            "run_generated_at_utc": run_at,
            "decision_date": str(first["entry_date"]),
            "candidate_count": len(candidates),
            "legacy_candidate_count": len(legacy_candidates),
            "production_top_row": score_top,
            "legacy_top_row": legacy_top,
            "policy_live_row": live_row,
            "abstained": council.abstain,
            "abstain_reason": (council.summary.get("abstain_audit") or {}).get("primary_reason"),
        })

    scored_rows = list(indexed_rows.values())
    shared_rows = [row for row in scored_rows if _number(row.get("final_candidate_score")) is not None]
    random_expected = {
        "runs": len(groups),
        "expected_win_rate": _round(float(np.mean([
            np.mean([bool(row["positive_pnl_after_friction"]) for _, row in group]) for group in groups.values()
        ]))),
        "expected_avg_return_after_friction_pct": _round(float(np.mean([
            np.mean([float(row["hold_period_return_after_friction_pct"]) for _, row in group]) for group in groups.values()
        ]))),
    }
    within_scan = {
        "production_v2_all_rows": _grouped_auc(scored_rows, "production_v2_score", target_field="big_win_after_friction"),
        "production_v2_shared_legacy_cohort": _grouped_auc(shared_rows, "production_v2_score", target_field="big_win_after_friction"),
        "legacy_recorded_score_shared_cohort": _grouped_auc(shared_rows, "final_candidate_score", target_field="big_win_after_friction"),
        "production_top_score": _selection_summary(replay_records, "production_top"),
        "legacy_top_score": _selection_summary(replay_records, "legacy_top"),
        "policy_live_board": _selection_summary(replay_records, "policy_live"),
        "random_candidate_expectation": random_expected,
        "policy_abstention_runs": int(sum(record["abstained"] for record in replay_records)),
        "policy_abstention_reasons": dict(sorted({
            reason: sum(record.get("abstain_reason") == reason for record in replay_records)
            for reason in {record.get("abstain_reason") for record in replay_records if record.get("abstain_reason")}
        }.items())),
    }

    training_end = lineage.get("source_backtest_end")
    holdout_start = min(str(row["entry_date"]) for row in raw_rows)
    result = {
        "artifact": "production_v2_holdout_backtest",
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "hold_and_rebuild" if (model_metrics["calibrated_auc"] or 0.0) < 0.53 else "continue_with_guardrails",
        "dataset": {
            "path": str(dataset),
            "sha256": _sha256(dataset),
            "label_policy": payload.get("label_policy"),
            "generated_at": payload.get("generated_at"),
            "raw_rows": len(raw_rows),
            "deduplicated_model_rows": model_row_count,
            "decision_runs": len(groups),
            "decision_dates": len({str(row["entry_date"]) for row in raw_rows}),
            "backtest_start": payload.get("backtest_start"),
            "backtest_end": payload.get("backtest_end"),
            "exact_quote_path_coverage_ratio": source_metadata.get("exact_quote_path_coverage_ratio"),
        },
        "artifact_under_test": {
            "path": str(artifact_path),
            "sha256": _sha256(artifact_path),
            "profile_id": artifact.get("profile_id"),
            "mode": artifact.get("mode"),
            "feature_cols": artifact.get("feature_cols"),
            "probability_sizing_authorized": bool((artifact.get("authority") or {}).get("probability_sizing")),
        },
        "training_lineage": lineage,
        "holdout_integrity": {
            "verified": bool(lineage.get("verified_exact_retrain") and training_end and holdout_start > training_end),
            "training_source_end": training_end,
            "holdout_start": holdout_start,
            "holdout_end": max(str(row["exit_date"]) for row in raw_rows),
        },
        "model_discrimination": model_metrics,
        "within_scan_replay": within_scan,
        "run_details": [{
            "run_generated_at_utc": record["run_generated_at_utc"],
            "decision_date": record["decision_date"],
            "candidate_count": record["candidate_count"],
            "production_top_contract": (record["production_top_row"] or {}).get("contract_symbol"),
            "production_top_return": (record["production_top_row"] or {}).get("hold_period_return_after_friction_pct"),
            "legacy_top_contract": (record["legacy_top_row"] or {}).get("contract_symbol"),
            "legacy_top_return": (record["legacy_top_row"] or {}).get("hold_period_return_after_friction_pct"),
            "policy_live_contract": (record["policy_live_row"] or {}).get("contract_symbol"),
            "policy_live_return": (record["policy_live_row"] or {}).get("hold_period_return_after_friction_pct"),
            "abstained": record["abstained"],
            "abstain_reason": record["abstain_reason"],
        } for record in replay_records],
        "limitations": [
            "Only 8 decision dates and 17 scans are available; scan-level performance estimates are unstable.",
            "The mart contains prior recommendations and paired counterfactuals, not every contract visible to Forge in each historical scan.",
            "Repeated scans and overlapping holding windows are dependent; model AUC confidence intervals are clustered by entry date.",
            "The policy replay uses captured quote/liquidity fields but cannot reconstruct unavailable bid-size or full-chain alternatives.",
        ],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--training-source", type=Path, default=DEFAULT_TRAINING_SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_backtest(args.dataset, args.artifact, args.training_source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "decision": result["decision"],
        "holdout_integrity": result["holdout_integrity"],
        "model_discrimination": result["model_discrimination"],
        "within_scan_replay": result["within_scan_replay"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
