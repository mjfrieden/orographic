"""Single production contract ranker.

The production score has one learned input: a volatility/contract model that
is used only for within-scan ordering.  Probability calibration is retained as
telemetry because the available card does not establish sizing-grade
calibration.  Executability and after-friction utility are explicit score
components and remain binding gates downstream.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any
import warnings

import numpy as np

from .payoff_model import (
    _expected_edge_after_friction_pct,
    _friction_buffer_pct,
    _predict_classifier,
    _rank_percentile,
    _safe_float,
    _utility_after_friction_score,
    feature_matrix,
    fill_quality_score,
    liquidity_score,
)
from .schemas import ContractCandidate, MarketRegime


MODEL_PATH = Path(__file__).parent / "models" / "production_payoff_ranker.pkl"
PRODUCTION_PROFILE = "production_v2"


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_artifact(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Production payoff ranker is missing: {path}")
    import joblib

    artifact = joblib.load(path)
    if not isinstance(artifact, dict):
        raise ValueError("Production payoff ranker must be a dictionary artifact")
    if artifact.get("mode") not in {"production_rank_only", "production_tail_utility"}:
        raise ValueError("Production payoff ranker lacks active production authority")
    if not artifact.get("feature_cols") or artifact.get("base_model") is None:
        raise ValueError("Production payoff ranker is incomplete")
    return artifact


def score_production_candidates(
    candidates: list[ContractCandidate],
    regime: MarketRegime | None = None,
    *,
    as_of: date | None = None,
    model_path: Path = MODEL_PATH,
) -> list[ContractCandidate]:
    """Rank executable contracts with one learned model and transparent costs."""
    if not candidates:
        return candidates

    artifact = _load_artifact(model_path)
    feature_cols = list(artifact["feature_cols"])
    X = feature_matrix(candidates, regime, as_of=as_of, feature_cols=feature_cols)
    if artifact.get("mode") == "production_tail_utility":
        probability_matrix = np.asarray(artifact["base_model"].predict_proba(X), dtype=float)
        aligned = np.zeros((len(candidates), 4), dtype=float)
        for column, outcome_class in enumerate(artifact["base_model"].classes_):
            aligned[:, int(outcome_class)] = probability_matrix[:, column]
        bucket_values = np.asarray(artifact.get("bucket_values") or [-0.80, -0.24, 0.21, 1.33], dtype=float)
        if bucket_values.shape != (4,):
            raise ValueError("Production tail ranker must declare four bucket utility values")
        expected_utilities = aligned @ bucket_values
        utility_ranks = _rank_percentile(expected_utilities)
        artifact_hash = _sha256_file(model_path)
        thresholds = artifact.get("tail_gate") or {}
        minimum_utility = float(thresholds.get("minimum_expected_utility", 0.0))
        minimum_big_win_probability = float(thresholds.get("minimum_big_win_probability", 0.35))
        maximum_severe_loss_probability = float(thresholds.get("maximum_severe_loss_probability", 0.65))

        for index, candidate in enumerate(candidates):
            learned_rank = _clip(float(utility_ranks[index]))
            liquidity = _clip(liquidity_score(candidate))
            fill_quality = _clip(fill_quality_score(candidate))
            spread_quality = 1.0 - _clip(_safe_float(candidate.spread_pct) / 0.12)
            execution_quality = _clip(
                0.45 * liquidity + 0.35 * fill_quality + 0.20 * spread_quality
            )
            expected_utility = float(expected_utilities[index])
            utility_score = _clip(0.5 + expected_utility / 1.50)
            big_win_probability = float(aligned[index, 3])
            severe_loss_probability = float(aligned[index, 0])
            tail_reasons: list[str] = []
            if expected_utility < minimum_utility:
                tail_reasons.append("expected_utility")
            if big_win_probability < minimum_big_win_probability:
                tail_reasons.append("big_win_probability")
            if severe_loss_probability > maximum_severe_loss_probability:
                tail_reasons.append("severe_loss_probability")
            final_score = _clip(
                0.70 * learned_rank + 0.20 * execution_quality + 0.10 * utility_score
            )

            candidate.pre_payoff_forge_score = round(_safe_float(candidate.forge_score), 4)
            candidate.prob_positive_option_pnl = round(float(aligned[index, 2] + aligned[index, 3]), 4)
            candidate.prob_big_win = round(big_win_probability, 4)
            candidate.prob_severe_loss = round(severe_loss_probability, 4)
            candidate.expected_tail_utility = round(expected_utility, 4)
            candidate.tail_utility_rank = round(learned_rank, 4)
            candidate.tail_gate_passed = not tail_reasons
            candidate.tail_gate_reasons = tail_reasons
            candidate.payoff_edge_score = round(learned_rank, 4)
            candidate.payoff_model_score = round(learned_rank, 4)
            candidate.liquidity_score = round(liquidity, 4)
            candidate.fill_quality_score = round(execution_quality, 4)
            candidate.prob_fill_quality_ok = round(execution_quality, 4)
            candidate.friction_buffer_pct = _friction_buffer_pct(candidate)
            candidate.expected_edge_after_friction_pct = round(expected_utility, 4)
            candidate.utility_after_friction_score = round(utility_score, 4)
            candidate.prob_no_trade = round(1.0 - final_score, 4)
            candidate.no_trade_score = round(final_score, 4)
            candidate.expected_option_return_pct_model = round(expected_utility, 4)
            candidate.expected_option_return_pct_rank = round(learned_rank, 4)
            candidate.prob_exceeds_breakeven = candidate.prob_positive_option_pnl
            candidate.breakeven_edge_score = round(learned_rank, 4)
            candidate.path_holding_quality_score = None
            candidate.path_early_profit_take_prob = None
            candidate.path_expected_mfe_pct = None
            candidate.path_decay_risk = None
            candidate.path_model_mode = "retired"
            candidate.path_model_artifact_sha256 = None
            candidate.final_candidate_score = round(final_score, 4)
            candidate.learned_rank_score = round(final_score, 4)
            candidate.forge_score = round(final_score, 4)
            candidate.ranker_mode = "production_tail_utility_v1"
            candidate.ranker_artifact_sha256 = artifact_hash
            candidate.notes.append(
                "Active tail rank: big-win payoff utility with severe-loss abstention and execution quality"
            )

        candidates.sort(key=lambda candidate: candidate.forge_score, reverse=True)
        return candidates

    raw_probabilities = _predict_classifier(artifact["base_model"], X, 0.5)
    intercept = artifact.get("calibrator")
    if intercept is None:
        probabilities = np.clip(raw_probabilities, 1e-6, 1 - 1e-6)
    else:
        clipped = np.clip(raw_probabilities, 1e-6, 1 - 1e-6)
        logits = np.log(clipped / (1.0 - clipped)) + float(intercept)
        probabilities = np.clip(1.0 / (1.0 + np.exp(-logits)), 1e-6, 1 - 1e-6)
    ranks = _rank_percentile(probabilities)
    artifact_hash = _sha256_file(model_path)

    for index, candidate in enumerate(candidates):
        learned_rank = _clip(float(ranks[index]))
        liquidity = _clip(liquidity_score(candidate))
        fill_quality = _clip(fill_quality_score(candidate))
        spread_quality = 1.0 - _clip(_safe_float(candidate.spread_pct) / 0.12)
        execution_quality = _clip(
            0.45 * liquidity + 0.35 * fill_quality + 0.20 * spread_quality
        )
        edge_after_friction = _expected_edge_after_friction_pct(
            candidate,
            _safe_float(candidate.expected_return_pct),
        )
        utility = _utility_after_friction_score(edge_after_friction)
        final_score = _clip(
            0.70 * learned_rank + 0.20 * execution_quality + 0.10 * utility
        )

        candidate.pre_payoff_forge_score = round(_safe_float(candidate.forge_score), 4)
        candidate.prob_positive_option_pnl = round(float(probabilities[index]), 4)
        candidate.payoff_edge_score = round(learned_rank, 4)
        candidate.payoff_model_score = round(learned_rank, 4)
        candidate.liquidity_score = round(liquidity, 4)
        candidate.fill_quality_score = round(execution_quality, 4)
        candidate.prob_fill_quality_ok = round(execution_quality, 4)
        candidate.friction_buffer_pct = _friction_buffer_pct(candidate)
        candidate.expected_edge_after_friction_pct = edge_after_friction
        candidate.utility_after_friction_score = round(utility, 4)
        candidate.prob_no_trade = round(1.0 - final_score, 4)
        candidate.no_trade_score = round(final_score, 4)
        candidate.expected_option_return_pct_model = None
        candidate.expected_option_return_pct_rank = None
        candidate.prob_exceeds_breakeven = None
        candidate.breakeven_edge_score = None
        candidate.path_holding_quality_score = None
        candidate.path_early_profit_take_prob = None
        candidate.path_expected_mfe_pct = None
        candidate.path_decay_risk = None
        candidate.path_model_mode = "retired"
        candidate.path_model_artifact_sha256 = None
        candidate.final_candidate_score = round(final_score, 4)
        candidate.learned_rank_score = round(final_score, 4)
        candidate.forge_score = round(final_score, 4)
        candidate.ranker_mode = PRODUCTION_PROFILE
        candidate.ranker_artifact_sha256 = artifact_hash
        candidate.notes.append(
            "Production v2 rank: volatility/contract rank 70%, execution quality 20%, after-cost utility 10%"
        )

    candidates.sort(key=lambda candidate: candidate.forge_score, reverse=True)
    return candidates
