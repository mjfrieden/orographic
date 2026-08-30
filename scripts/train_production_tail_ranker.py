"""Train the single active Orographic tail-utility production ranker.

The model treats option outcomes asymmetrically:

* severe loss: return <= -50%
* manageable loss: -50% < return < 0%
* small win: 0% <= return < 50%
* big win: return >= 50%

It ranks expected after-friction bucket utility and exposes an explicit
abstention gate for big-win probability, severe-loss probability, and
expected utility.  It does not create a shadow or independent runtime lane.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
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
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.utils.class_weight import compute_sample_weight

from engine.orographic.payoff_model import feature_row
from engine.orographic.council import select_board
from engine.orographic.execution_policy import LiveExecutionPolicy, apply_live_execution_policy
from engine.orographic.production_ranker import score_production_candidates
from engine.orographic.schemas import MarketRegime
from engine.orographic.validation import purged_date_splits
from engine.train_payoff_model import _candidate_from_trade


PROFILE_ID = "production_v2"
MODE = "production_tail_utility"
FEATURE_COLS = [
    "option_type_is_call",
    "moneyness",
    "abs_delta",
    "implied_volatility",
    "iv_rank",
    "realized_vol_20d",
    "atr_pct_14d",
    "vrp_gap",
    "projected_move_pct",
    "breakeven_move_pct",
    "extrinsic_ratio",
    "dte",
    "spread_pct",
    "log_open_interest",
    "log_volume",
    "premium_pct_of_spot",
    "expected_return_pct",
    "heuristic_edge_after_friction_pct",
    "liquidity_score",
    "fill_quality_score",
]
TAIL_GATE = {
    "minimum_expected_utility": 0.5582922181314538,
    "minimum_big_win_probability": 0.3916891329717107,
    "maximum_severe_loss_probability": 0.65,
}
DEFAULT_DEVELOPMENT = Path("output/option_outcomes_live_recommendations.json")
DEFAULT_MODEL = Path("engine/orographic/models/production_payoff_ranker.pkl")
DEFAULT_CARD = Path("engine/orographic/models/production_payoff_ranker_card.json")
FORWARD_SOURCE_ID = "orographic-live-research-data/output/research_datasets/strict_option_outcomes.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_rows(path: Path, *, scored_only: bool, deduplicate: bool) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("artifact") != "option_outcome_dataset":
        raise ValueError(f"{path} is not an option_outcome_dataset")
    frame = pd.DataFrame(payload.get("rows") or [])
    if scored_only:
        frame = frame[frame["final_candidate_score"].notna()].copy()
    if deduplicate:
        frame = frame.drop_duplicates(
            ["symbol", "option_type", "strike", "expiry", "entry_date", "exit_date"]
        )
    return frame.reset_index(drop=True)


def _feature_matrix(frame: pd.DataFrame) -> np.ndarray:
    rows: list[dict[str, float]] = []
    for trade in frame.to_dict("records"):
        candidate = _candidate_from_trade(trade)
        regime = MarketRegime(
            mode=str(trade.get("regime_mode") or "neutral"),
            bias=float(trade.get("regime_bias") or 0.0),
            source_symbol=str(trade.get("regime_source_symbol") or "SPY"),
        )
        rows.append(
            feature_row(
                candidate,
                regime,
                as_of=date.fromisoformat(str(trade["entry_date"])),
            )
        )
    return pd.DataFrame(rows)[FEATURE_COLS].fillna(0.0).to_numpy(dtype=float)


def _outcome_classes(returns: np.ndarray) -> np.ndarray:
    return np.select(
        [returns >= 0.50, returns >= 0.0, returns > -0.50],
        [3, 2, 1],
        default=0,
    ).astype(int)


def _model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        learning_rate=0.04,
        max_iter=180,
        max_depth=3,
        min_samples_leaf=15,
        l2_regularization=1.0,
        random_state=42,
    )


def _fit(X: np.ndarray, y: np.ndarray) -> HistGradientBoostingClassifier:
    return _model().fit(X, y, sample_weight=compute_sample_weight("balanced", y))


def _aligned_probabilities(model: HistGradientBoostingClassifier, X: np.ndarray) -> np.ndarray:
    raw = np.asarray(model.predict_proba(X), dtype=float)
    aligned = np.zeros((len(X), 4), dtype=float)
    for column, outcome_class in enumerate(model.classes_):
        aligned[:, int(outcome_class)] = raw[:, column]
    return aligned


def _liquidity_mask(frame: pd.DataFrame) -> np.ndarray:
    age = (
        pd.to_numeric(frame["last_trade_age_seconds"], errors="coerce").fillna(0.0)
        if "last_trade_age_seconds" in frame
        else pd.Series(np.zeros(len(frame)))
    )
    return (
        (pd.to_numeric(frame["entry_spread_pct"], errors="coerce") <= 0.12)
        & (pd.to_numeric(frame["entry_open_interest"], errors="coerce") >= 150)
        & (pd.to_numeric(frame["entry_volume"], errors="coerce") >= 25)
        & (age.to_numpy(dtype=float) <= 1_800.0)
    ).to_numpy(dtype=bool)


def _tail_gate(probabilities: np.ndarray, utilities: np.ndarray) -> np.ndarray:
    return (
        (utilities >= TAIL_GATE["minimum_expected_utility"])
        & (probabilities[:, 3] >= TAIL_GATE["minimum_big_win_probability"])
        & (probabilities[:, 0] <= TAIL_GATE["maximum_severe_loss_probability"])
    )


def _select_one_per_group(
    frame: pd.DataFrame,
    returns: np.ndarray,
    probabilities: np.ndarray,
    utilities: np.ndarray,
    groups: np.ndarray,
) -> tuple[list[int], dict[str, Any]]:
    eligible = _tail_gate(probabilities, utilities) & _liquidity_mask(frame)
    selected: list[int] = []
    for group in sorted(set(groups.tolist())):
        group_indices = np.where((groups == group) & eligible)[0]
        if len(group_indices):
            selected.append(int(group_indices[np.argmax(utilities[group_indices])]))
    realized = returns[selected] if selected else np.array([], dtype=float)
    return selected, {
        "available_groups": int(len(set(groups.tolist()))),
        "eligible_candidates": int(eligible.sum()),
        "selected_trades": int(len(selected)),
        "trade_rate": round(len(selected) / max(len(set(groups.tolist())), 1), 4),
        "average_return_after_friction_pct": round(float(realized.mean()), 4) if len(realized) else None,
        "median_return_after_friction_pct": round(float(np.median(realized)), 4) if len(realized) else None,
        "total_equal_weight_return": round(float(realized.sum()), 4) if len(realized) else None,
        "big_win_rate": round(float(np.mean(realized >= 0.50)), 4) if len(realized) else None,
        "positive_return_rate": round(float(np.mean(realized > 0.0)), 4) if len(realized) else None,
        "severe_loss_rate": round(float(np.mean(realized <= -0.50)), 4) if len(realized) else None,
        "selected_contracts": [str(frame.iloc[index]["contract_symbol"]) for index in selected],
    }


def _safe_auc(labels: np.ndarray, probabilities: np.ndarray) -> float | None:
    if len(labels) < 2 or len(set(labels.tolist())) < 2:
        return None
    return round(float(roc_auc_score(labels, probabilities)), 4)


def _integrated_forward_replay(frame: pd.DataFrame, model_path: Path) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in frame.to_dict("records"):
        groups[str(row["run_generated_at_utc"])].append(row)
    selected_rows: list[dict[str, Any]] = []
    abstain_reasons: dict[str, int] = {}
    for run_at, rows in sorted(groups.items()):
        candidates = []
        source_by_candidate: dict[int, dict[str, Any]] = {}
        for row in rows:
            candidate = _candidate_from_trade(row)
            candidate.bid = float(row["entry_bid"])
            candidate.ask = float(row["entry_ask"])
            candidate.premium = candidate.ask
            candidate.contract_cost = candidate.ask * 100.0
            candidate.spread_pct = float(row["entry_spread_pct"])
            candidate.open_interest = int(row["entry_open_interest"])
            candidate.volume = int(row["entry_volume"])
            candidate.last_trade_age_seconds = row.get("last_trade_age_seconds")
            candidates.append(candidate)
            source_by_candidate[id(candidate)] = row
        regime = MarketRegime(
            mode=str(rows[0].get("regime_mode") or "neutral"),
            bias=float(rows[0].get("regime_bias") or 0.0),
            source_symbol=str(rows[0].get("regime_source_symbol") or "SPY"),
        )
        score_production_candidates(
            candidates,
            regime,
            as_of=date.fromisoformat(str(rows[0]["entry_date"])),
            model_path=model_path,
        )
        apply_live_execution_policy(
            candidates,
            as_of_utc=datetime.fromisoformat(run_at.replace("Z", "+00:00")),
            policy=LiveExecutionPolicy(min_open_interest=150),
        )
        board = select_board(
            candidates,
            regime,
            live_size=1,
            shadow_size=0,
            minimum_live_score=0.86,
            minimum_put_live_score=0.84,
            max_live_extrinsic_ratio=1.0,
            fetch_live_corr=False,
        )
        if not board.live_board:
            reason = str((board.summary.get("abstain_audit") or {}).get("primary_reason") or "unknown")
            abstain_reasons[reason] = abstain_reasons.get(reason, 0) + 1
            continue
        candidate = board.live_board[0]
        source = source_by_candidate[id(candidate)]
        selected_rows.append(
            {
                "run_generated_at_utc": run_at,
                "contract_symbol": candidate.contract_symbol,
                "return_after_friction_pct": round(float(source["hold_period_return_after_friction_pct"]), 4),
                "prob_big_win": candidate.prob_big_win,
                "prob_severe_loss": candidate.prob_severe_loss,
                "expected_tail_utility": candidate.expected_tail_utility,
                "production_score": candidate.forge_score,
            }
        )
    realized = np.array([row["return_after_friction_pct"] for row in selected_rows], dtype=float)
    return {
        "available_scans": len(groups),
        "selected_trades": len(selected_rows),
        "trade_rate": round(len(selected_rows) / max(len(groups), 1), 4),
        "average_return_after_friction_pct": round(float(realized.mean()), 4) if len(realized) else None,
        "median_return_after_friction_pct": round(float(np.median(realized)), 4) if len(realized) else None,
        "total_equal_weight_return": round(float(realized.sum()), 4) if len(realized) else None,
        "big_win_rate": round(float(np.mean(realized >= 0.50)), 4) if len(realized) else None,
        "positive_return_rate": round(float(np.mean(realized > 0.0)), 4) if len(realized) else None,
        "severe_loss_rate": round(float(np.mean(realized <= -0.50)), 4) if len(realized) else None,
        "abstain_reasons": dict(sorted(abstain_reasons.items())),
        "selected_rows": selected_rows,
    }


def _segment_auc(
    frame: pd.DataFrame,
    labels: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for side in ("call", "put"):
        index = np.where(frame["option_type"].to_numpy(dtype=object) == side)[0]
        result[side] = {
            "rows": int(len(index)),
            "big_win_rate": round(float(labels[index].mean()), 4) if len(index) else None,
            "auc": _safe_auc(labels[index], probabilities[index]),
        }
    return result


def train(
    development_path: Path,
    forward_path: Path,
    model_path: Path,
    card_path: Path,
) -> dict[str, Any]:
    development = _load_rows(development_path, scored_only=False, deduplicate=True)
    X = _feature_matrix(development)
    returns = development["hold_period_return_after_friction_pct"].to_numpy(dtype=float)
    labels = _outcome_classes(returns)
    bucket_values = np.array(
        [float(np.mean(np.clip(returns[labels == outcome], -1.0, 3.0))) for outcome in range(4)],
        dtype=float,
    )

    feature_dates = np.array([date.fromisoformat(value) for value in development["entry_date"]], dtype=object)
    label_dates = np.array([date.fromisoformat(value) for value in development["exit_date"]], dtype=object)
    oof = np.full((len(development), 4), np.nan, dtype=float)
    folds: list[dict[str, Any]] = []
    for fold, (train_index, validation_index) in enumerate(
        purged_date_splits(feature_dates, label_dates, n_splits=5),
        start=1,
    ):
        fold_model = _fit(X[train_index], labels[train_index])
        oof[validation_index] = _aligned_probabilities(fold_model, X[validation_index])
        folds.append(
            {
                "fold": fold,
                "training_rows": int(len(train_index)),
                "validation_rows": int(len(validation_index)),
                "training_label_end": str(max(label_dates[train_index])),
                "validation_start": str(min(feature_dates[validation_index])),
            }
        )
    valid = np.isfinite(oof).all(axis=1)
    oof_utilities = oof[valid] @ bucket_values
    _, development_policy = _select_one_per_group(
        development.iloc[np.where(valid)[0]].reset_index(drop=True),
        returns[valid],
        oof[valid],
        oof_utilities,
        development["entry_date"].to_numpy(dtype=object)[valid],
    )

    model = _fit(X, labels)
    forward = _load_rows(forward_path, scored_only=True, deduplicate=False)
    forward_X = _feature_matrix(forward)
    forward_returns = forward["hold_period_return_after_friction_pct"].to_numpy(dtype=float)
    forward_probabilities = _aligned_probabilities(model, forward_X)
    forward_utilities = forward_probabilities @ bucket_values
    selected_indices, forward_policy = _select_one_per_group(
        forward,
        forward_returns,
        forward_probabilities,
        forward_utilities,
        forward["run_generated_at_utc"].to_numpy(dtype=object),
    )
    forward_policy["selected_rows"] = [
        {
            "run_generated_at_utc": str(forward.iloc[index]["run_generated_at_utc"]),
            "contract_symbol": str(forward.iloc[index]["contract_symbol"]),
            "return_after_friction_pct": round(float(forward_returns[index]), 4),
            "prob_big_win": round(float(forward_probabilities[index, 3]), 4),
            "prob_severe_loss": round(float(forward_probabilities[index, 0]), 4),
            "expected_tail_utility": round(float(forward_utilities[index]), 4),
        }
        for index in selected_indices
    ]

    artifact = {
        "artifact": "production_payoff_ranker",
        "version": 2,
        "mode": MODE,
        "profile_id": PROFILE_ID,
        "objective": "large_after_friction_return_with_explicit_abstention",
        "feature_cols": FEATURE_COLS,
        "base_model": model,
        "outcome_classes": {
            0: "severe_loss_return_le_-0.50",
            1: "manageable_loss_-0.50_to_0",
            2: "small_win_0_to_0.50",
            3: "big_win_return_ge_0.50",
        },
        "bucket_values": bucket_values.tolist(),
        "tail_gate": TAIL_GATE,
        "training_rows": int(len(development)),
        "training_start": str(min(feature_dates)),
        "training_end": str(max(feature_dates)),
        "authority": {
            "forge_ranking": True,
            "active_research_routing": True,
            "probability_sizing": False,
            "council_route": "single_production_lane",
        },
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, model_path)
    model_hash = _sha256(model_path)
    integrated_forward_policy = _integrated_forward_replay(forward, model_path)

    big_win = (forward_returns >= 0.50).astype(int)
    severe_loss = (forward_returns <= -0.50).astype(int)
    card = {
        "artifact": "production_payoff_ranker_card",
        "version": 2,
        "profile_id": PROFILE_ID,
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "objective": artifact["objective"],
        "status": "active_research",
        "authority": artifact["authority"],
        "model_sha256": model_hash,
        "feature_policy": {
            "included": FEATURE_COLS,
            "excluded": ["sentinel_event_scores", "path_model_scores", "shadow_challenger_scores"],
        },
        "tail_contract": {
            "big_win_return_threshold": 0.50,
            "severe_loss_return_threshold": -0.50,
            "bucket_values": [round(float(value), 6) for value in bucket_values],
            "tail_gate": TAIL_GATE,
            "selection": "one highest-utility eligible contract per scan",
        },
        "source_validation": {
            "development_policy": development_policy,
            "forward_policy": forward_policy,
            "integrated_forward_policy": integrated_forward_policy,
            "forward_big_win_auc": _safe_auc(big_win, forward_probabilities[:, 3]),
            "forward_severe_loss_auc": _safe_auc(severe_loss, forward_probabilities[:, 0]),
            "forward_big_win_brier": round(float(brier_score_loss(big_win, forward_probabilities[:, 3])), 4),
            "call_auc": _segment_auc(forward, big_win, forward_probabilities[:, 3])["call"]["auc"],
            "put_auc": _segment_auc(forward, big_win, forward_probabilities[:, 3])["put"]["auc"],
            "by_side": _segment_auc(forward, big_win, forward_probabilities[:, 3]),
            "split_policy": "purged_date_grouped_development_folds_plus_later_frozen_forward_window",
            "folds": folds,
        },
        "production_policy": {
            "live_execution_policy_required": True,
            "council_live_board_only": True,
            "live_size": 1,
            "shadow_size": 0,
            "probability_sizing_enabled": False,
            "research_risk_posture": "active_with_liquidity_gates_and_prospective_kill_switch",
            "kill_switch": "disable active routing if 10 resolved live picks have negative cumulative after-friction return",
        },
        "sources": {
            "development": str(development_path),
            "forward": FORWARD_SOURCE_ID,
        },
    }
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return card


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", type=Path, default=DEFAULT_DEVELOPMENT)
    parser.add_argument("--forward", type=Path, required=True)
    parser.add_argument("--output-model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output-card", type=Path, default=DEFAULT_CARD)
    args = parser.parse_args()
    card = train(args.development, args.forward, args.output_model, args.output_card)
    print(
        json.dumps(
            {
                "output_model": str(args.output_model),
                "output_card": str(args.output_card),
                "status": card["status"],
                "source_validation": card["source_validation"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
