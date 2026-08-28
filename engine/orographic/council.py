"""
engine/orographic/council.py

Portfolio selection layer for the Orographic pipeline.

Replaces the naive rank-ordering loop with a Markowitz minimum-variance
optimizer (via scipy) that explicitly accounts for the correlation between
the underlying equity returns of the candidate set.

This prevents the system from treating NVDA + AMD + MSFT as three independent
trades when they share a near-1.0 correlation. Positions are sized using
a simple fractional Kelly criterion derived from the current policy score.

Falls back to the original rank-ordering behaviour if:
  - scipy is not installed
  - fewer than 2 candidates are available
  - the correlation matrix is singular or degenerate
"""
from __future__ import annotations

import logging
import warnings
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from engine.backtest.risk_controls import sector_for_symbol

from .market_shock import MarketShockRegime, NEUTRAL_MARKET_SHOCK
from .schemas import ContractCandidate, CouncilResult, MarketRegime

log = logging.getLogger(__name__)

ABSTAIN_REASON_LABELS = {
    "no_forge_candidates": "No Forge candidates reached Council.",
    "below_live_score": "All candidates fell below the live-score gate.",
    "extrinsic_limit": "All candidates failed the extrinsic ceiling.",
    "model_no_trade": "Model no-trade discipline blocked every live candidate.",
    "fill_quality": "All candidates failed the fill-quality gate.",
    "execution_policy": "All candidates failed executable-liquidity or repeat-entry controls.",
    "sentinel_no_trade_pressure": "Sentinel event pressure blocked every live candidate.",
    "symbol_probation": "All candidates are live-blocked by symbol probation.",
    "score_and_extrinsic_limit": "Candidates failed both score and extrinsic gates.",
    "mixed_core_filters": "Candidates were blocked by a mix of score and extrinsic gates.",
    "side_balance": "Eligible candidates were rejected by side-balance controls.",
    "selection_threshold": "Eligible candidates survived core filters but did not reach the live board.",
    "market_shock_abstain": "Market-shock overlay blocked live options selection.",
}

LIVE_PROBATION_SYMBOLS = frozenset({"NFLX", "TLT"})
LIVE_PROBATION_REASON = (
    "symbol_probation: insufficient/negative realized option-outcome evidence for live promotion"
)
MAX_LIVE_NO_TRADE_PROB = 0.58
MIN_LIVE_FILL_QUALITY = 0.45
MAX_LIVE_SENTINEL_NO_TRADE_PRESSURE = 0.42


# ── Markowitz helpers ─────────────────────────────────────────────────────────

def _fetch_corr_matrix(symbols: list[str], lookback_days: int = 60) -> np.ndarray | None:
    """
    Build a correlation matrix of the underlying symbols using the past
    `lookback_days` of daily returns via yfinance. Returns None on failure.
    """
    try:
        import yfinance as yf
        unique = list(dict.fromkeys(symbols))   # preserve order, deduplicate
        if len(unique) < 2:
            return None

        dfs = []
        for sym in unique:
            df = yf.Ticker(sym).history(period="3mo", interval="1d", auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            close = pd.to_numeric(df.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
            if len(close) < 20:
                continue
            rets = close.pct_change().dropna().tail(lookback_days)
            dfs.append(rets.rename(sym))

        if len(dfs) < 2:
            return None

        combined = pd.concat(dfs, axis=1).dropna()
        if len(combined) < 10:
            return None

        corr = combined.corr().values
        return corr
    except Exception as exc:
        log.debug("Correlation matrix fetch failed: %s", exc)
        return None


def _markowitz_weights(
    expected_returns: np.ndarray,
    corr: np.ndarray,
    vols: np.ndarray,
    target_n: int,
) -> np.ndarray | None:
    """
    Minimum-variance allocation using scipy convex optimization.
    Returns normalized weights of length `len(expected_returns)`, or None.
    """
    try:
        from scipy.optimize import minimize

        n = len(expected_returns)
        if n < 2:
            return None

        # Covariance ≈ corr * outer(vols, vols)
        cov = corr * np.outer(vols, vols)
        # Regularise for numerical stability
        cov += np.eye(n) * 1e-6

        def portfolio_variance(w: np.ndarray) -> float:
            return float(w @ cov @ w)

        constraints = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
        ]
        bounds = [(0.01, 1.0)] * n   # long-only, minimum 1% per leg

        x0 = np.ones(n) / n
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = minimize(
                portfolio_variance,
                x0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"ftol": 1e-9, "maxiter": 500},
            )

        if not result.success:
            log.debug("Markowitz optimizer did not converge: %s", result.message)
            return None

        weights = result.x
        # Sort descending → take top target_n → renormalize
        ranked_idx = np.argsort(-weights)
        top_idx = ranked_idx[:target_n]
        top_weights = weights[top_idx]
        top_weights /= top_weights.sum()
        return top_idx, top_weights

    except ImportError:
        log.warning("scipy not installed — falling back to rank-order selection.")
        return None
    except Exception as exc:
        log.debug("Markowitz optimizer exception: %s", exc)
        return None


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(max(float(value), low), high)


def _kelly_weight(score: float, win_rate_est: float = 0.54, b: float = 1.2) -> float:
    """
    Fractional Kelly sizing (half-Kelly): f = (p*b - q) / b * 0.5
    Uses the normalized policy score to nudge the win-rate estimate.
    """
    normalized = _clip((score + 1.0) / 2.0) if score < 0.0 else _clip(score)
    p = min(max(win_rate_est + (normalized - 0.5) * 0.16, 0.30), 0.75)
    q = 1.0 - p
    kelly_full = (p * b - q) / b
    return max(round(kelly_full * 0.5, 4), 0.05)   # half-Kelly, min 5%


def _base_candidate_score(candidate: ContractCandidate) -> float:
    for field in (
        "learned_rank_score",
        "final_candidate_score",
        "utility_after_friction_score",
        "payoff_model_score",
        "forge_score",
    ):
        value = getattr(candidate, field, None)
        if value is not None:
            return _clip(float(value))
    return _clip(float(candidate.forge_score))


def _normalized_edge_score(candidate: ContractCandidate) -> float:
    edge = getattr(candidate, "expected_edge_after_friction_pct", None)
    if edge is None:
        edge = getattr(candidate, "expected_return_pct", None)
        edge = float(edge) * 0.25 if edge is not None else 0.0
    return _clip(0.5 + float(edge) / 0.40)


def _candidate_fill_quality(candidate: ContractCandidate) -> float:
    for field in ("prob_fill_quality_ok", "fill_quality_score", "liquidity_score"):
        value = getattr(candidate, field, None)
        if value is not None:
            return _clip(float(value))
    return 0.5


def _candidate_path_quality(candidate: ContractCandidate) -> float:
    value = getattr(candidate, "path_holding_quality_score", None)
    if value is not None:
        return _clip(float(value))
    return 0.5


def _candidate_sentinel_alignment(candidate: ContractCandidate) -> float:
    confidence = _clip(float(getattr(candidate, "sentinel_confidence", 0.0) or 0.0))
    side_relevance = getattr(
        candidate,
        "sentinel_call_relevance" if candidate.option_type == "call" else "sentinel_put_relevance",
        None,
    )
    if side_relevance is None:
        return 0.5
    return _clip(float(side_relevance)) * max(confidence, 0.35)


def _sentinel_no_trade_pressure(candidate: ContractCandidate) -> float:
    relevance = _clip(float(getattr(candidate, "sentinel_no_trade_relevance", 0.0) or 0.0))
    confidence = _clip(float(getattr(candidate, "sentinel_confidence", 0.0) or 0.0))
    return _clip(relevance * max(confidence, 0.35))


def _candidate_no_trade_pressure(candidate: ContractCandidate) -> float:
    return max(
        _clip(float(getattr(candidate, "prob_no_trade", 0.0) or 0.0)),
        _clip(float(getattr(candidate, "scout_no_trade_prob", 0.0) or 0.0)),
        _sentinel_no_trade_pressure(candidate),
    )


def _candidate_live_score(candidate: ContractCandidate) -> float:
    """Council consumes the single Forge production score without a second ensemble."""
    return _base_candidate_score(candidate)


def _avg_pairwise_corr(corr: np.ndarray | None) -> float | None:
    if corr is None or corr.ndim != 2 or corr.shape[0] < 2:
        return None
    upper = corr[np.triu_indices(corr.shape[0], k=1)]
    if len(upper) == 0:
        return None
    return round(float(np.nanmean(upper)), 4)


def _annotate_risk(
    candidates: list[ContractCandidate],
    *,
    max_sector_share: float,
) -> None:
    sector_counts = Counter(sector_for_symbol(row.symbol) for row in candidates)
    total = max(len(candidates), 1)
    for candidate in candidates:
        sector = sector_for_symbol(candidate.symbol)
        candidate.sector = sector
        live_score = _candidate_live_score(candidate)
        fill_quality = _candidate_fill_quality(candidate)
        no_trade_pressure = _candidate_no_trade_pressure(candidate)
        sentinel_pressure = _sentinel_no_trade_pressure(candidate)
        candidate.suggested_allocation_pct = round(
            min(max(_kelly_weight(live_score) * candidate.allocation_weight, 0.01), 0.25),
            4,
        )
        candidate.risk_adjusted_score = round(
            live_score
            * (1.0 - min(candidate.extrinsic_ratio, 1.0) * 0.04)
            * (0.90 + 0.10 * fill_quality),
            4,
        )
        flags = list(candidate.council_risk_flags)
        if sector_counts[sector] / total > max_sector_share:
            flags.append("sector_cluster")
        if candidate.extrinsic_ratio > 0.94:
            flags.append("high_extrinsic")
        if candidate.spread_pct > 0.16:
            flags.append("wide_spread")
        if no_trade_pressure > MAX_LIVE_NO_TRADE_PROB:
            flags.append("model_no_trade")
        if fill_quality < MIN_LIVE_FILL_QUALITY:
            flags.append("fill_quality")
        if sentinel_pressure > MAX_LIVE_SENTINEL_NO_TRADE_PRESSURE:
            flags.append("sentinel_no_trade_pressure")
        candidate.council_risk_flags = sorted(set(flags))


def _abstain_reason_label(reason: str) -> str:
    return ABSTAIN_REASON_LABELS.get(reason, "Council abstained after applying the live-board filters.")


def _audit_candidate(row: ContractCandidate) -> dict[str, Any]:
    return {
        "symbol": row.symbol,
        "contract_symbol": row.contract_symbol,
        "option_type": row.option_type,
        "expiry": row.expiry,
        "strike": row.strike,
        "forge_score": row.forge_score,
        "effective_candidate_score": round(_effective_candidate_score(row), 4),
        "learned_rank_score": row.learned_rank_score,
        "final_candidate_score": row.final_candidate_score,
        "risk_adjusted_score": row.risk_adjusted_score,
        "utility_after_friction_score": row.utility_after_friction_score,
        "expected_edge_after_friction_pct": row.expected_edge_after_friction_pct,
        "friction_buffer_pct": row.friction_buffer_pct,
        "prob_no_trade": row.prob_no_trade,
        "scout_no_trade_prob": row.scout_no_trade_prob,
        "prob_fill_quality_ok": row.prob_fill_quality_ok,
        "execution_policy_passed": row.execution_policy_passed,
        "execution_policy_reasons": row.execution_policy_reasons,
        "conservative_exit_bid": row.conservative_exit_bid,
        "round_trip_spread_drag_pct": row.round_trip_spread_drag_pct,
        "reentry_blocked": row.reentry_blocked,
        "prior_entry_ask": row.prior_entry_ask,
        "sentinel_confidence": row.sentinel_confidence,
        "sentinel_no_trade_relevance": row.sentinel_no_trade_relevance,
        "sentinel_no_trade_pressure": round(_sentinel_no_trade_pressure(row), 4),
        "extrinsic_ratio": row.extrinsic_ratio,
        "contract_cost": row.contract_cost,
        "spread_pct": row.spread_pct,
        "delta": row.delta,
        "iv_rank": row.iv_rank,
        "council_risk_flags": row.council_risk_flags,
    }


def _normalize_market_shock(value: MarketShockRegime | dict[str, Any] | None) -> MarketShockRegime:
    if isinstance(value, MarketShockRegime):
        return value
    if not isinstance(value, dict):
        return NEUTRAL_MARKET_SHOCK
    try:
        return MarketShockRegime(
            label=str(value.get("label") or NEUTRAL_MARKET_SHOCK.label),
            severity=float(value.get("severity") or 0.0),
            stance=str(value.get("stance") or "allow"),
            global_abstain=bool(value.get("global_abstain", False)),
            live_score_buffer=float(value.get("live_score_buffer") or 0.0),
            put_score_buffer=float(value.get("put_score_buffer") or 0.0),
            max_extrinsic_ratio=(
                float(value["max_extrinsic_ratio"])
                if value.get("max_extrinsic_ratio") is not None
                else None
            ),
            preferred_sides=list(value.get("preferred_sides") or []),
            blocked_sides=list(value.get("blocked_sides") or []),
            drivers=list(value.get("drivers") or []),
            strategy_notes=list(value.get("strategy_notes") or []),
            source=str(value.get("source") or "cross_asset_event_overlay"),
        )
    except (TypeError, ValueError):
        return NEUTRAL_MARKET_SHOCK


def _effective_candidate_score(candidate: ContractCandidate) -> float:
    adjusted = getattr(candidate, "risk_adjusted_score", None)
    if adjusted is not None:
        return float(adjusted)
    return _candidate_live_score(candidate)


def _board_utility(board: list[ContractCandidate]) -> float:
    return round(sum(_effective_candidate_score(row) for row in board), 6)


def _board_passes_diversification(
    board: list[ContractCandidate],
    *,
    max_same_side_share: float,
    max_same_sector_share: float,
) -> bool:
    if not board:
        return True
    side_counts = Counter(row.option_type for row in board)
    if max(side_counts.values()) / len(board) > max_same_side_share:
        return False
    sector_counts = Counter(sector_for_symbol(row.symbol) for row in board)
    if max(sector_counts.values()) / len(board) > max_same_sector_share:
        return False
    return True


def _apply_turnover_penalty(
    live_board: list[ContractCandidate],
    unique_eligible: list[ContractCandidate],
    *,
    live_size: int,
    prior_live_board_symbols: list[str],
    max_same_side_share: float,
    max_same_sector_share: float,
    turnover_switch_penalty: float,
) -> tuple[list[ContractCandidate], dict[str, Any]]:
    normalized_prior = [str(symbol).upper() for symbol in prior_live_board_symbols if str(symbol).strip()]
    if not live_board or not normalized_prior:
        return live_board, {
            "applied": False,
            "reason": "no_prior_live_board",
            "prior_live_symbols": normalized_prior,
            "retained_symbols": [],
            "new_symbols": [row.symbol for row in live_board],
            "replacements": 0,
            "required_uplift": 0.0,
            "actual_uplift": 0.0,
        }

    eligible_by_symbol = {row.symbol: row for row in unique_eligible}
    preferred_board: list[ContractCandidate] = []
    preferred_symbols: set[str] = set()
    for symbol in normalized_prior:
        candidate = eligible_by_symbol.get(symbol)
        if candidate is None or symbol in preferred_symbols or len(preferred_board) >= live_size:
            continue
        preferred_board.append(candidate)
        preferred_symbols.add(symbol)
    if not preferred_board:
        return live_board, {
            "applied": False,
            "reason": "prior_symbols_no_longer_eligible",
            "prior_live_symbols": normalized_prior,
            "retained_symbols": [],
            "new_symbols": [row.symbol for row in live_board],
            "replacements": 0,
            "required_uplift": 0.0,
            "actual_uplift": 0.0,
        }

    fill_pool = list(live_board) + [
        row for row in sorted(unique_eligible, key=_effective_candidate_score, reverse=True)
        if row.symbol not in {candidate.symbol for candidate in live_board}
    ]
    for candidate in fill_pool:
        if len(preferred_board) >= live_size:
            break
        if candidate.symbol in preferred_symbols:
            continue
        preferred_board.append(candidate)
        preferred_symbols.add(candidate.symbol)

    if {row.symbol for row in preferred_board} == {row.symbol for row in live_board}:
        return live_board, {
            "applied": False,
            "reason": "board_already_matches_prior_bias",
            "prior_live_symbols": normalized_prior,
            "retained_symbols": [row.symbol for row in live_board if row.symbol in normalized_prior],
            "new_symbols": [row.symbol for row in live_board if row.symbol not in normalized_prior],
            "replacements": len([row for row in live_board if row.symbol not in normalized_prior]),
            "required_uplift": 0.0,
            "actual_uplift": 0.0,
        }

    if not _board_passes_diversification(
        preferred_board,
        max_same_side_share=max_same_side_share,
        max_same_sector_share=max_same_sector_share,
    ):
        return live_board, {
            "applied": False,
            "reason": "preferred_board_failed_diversification",
            "prior_live_symbols": normalized_prior,
            "retained_symbols": [row.symbol for row in preferred_board if row.symbol in normalized_prior],
            "new_symbols": [row.symbol for row in preferred_board if row.symbol not in normalized_prior],
            "replacements": len([row for row in live_board if row.symbol not in normalized_prior]),
            "required_uplift": 0.0,
            "actual_uplift": 0.0,
        }

    current_symbols = [row.symbol for row in live_board]
    replacements = len([symbol for symbol in current_symbols if symbol not in normalized_prior])
    current_utility = _board_utility(live_board)
    preferred_utility = _board_utility(preferred_board)
    actual_uplift = round(current_utility - preferred_utility, 6)
    required_uplift = round(replacements * turnover_switch_penalty, 6)
    if actual_uplift < required_uplift:
        return preferred_board, {
            "applied": True,
            "reason": "retained_prior_board",
            "prior_live_symbols": normalized_prior,
            "retained_symbols": [row.symbol for row in preferred_board if row.symbol in normalized_prior],
            "new_symbols": [row.symbol for row in preferred_board if row.symbol not in normalized_prior],
            "replacements": replacements,
            "required_uplift": required_uplift,
            "actual_uplift": actual_uplift,
        }
    return live_board, {
        "applied": False,
        "reason": "new_board_cleared_turnover_threshold",
        "prior_live_symbols": normalized_prior,
        "retained_symbols": [row.symbol for row in live_board if row.symbol in normalized_prior],
        "new_symbols": [row.symbol for row in live_board if row.symbol not in normalized_prior],
        "replacements": replacements,
        "required_uplift": required_uplift,
        "actual_uplift": actual_uplift,
    }


# ── Main selector ─────────────────────────────────────────────────────────────

def select_board(
    candidates: list[ContractCandidate],
    regime: MarketRegime,
    *,
    live_size: int = 3,
    shadow_size: int = 3,
    minimum_live_score: float = 0.57,
    minimum_put_live_score: float | None = None,
    max_same_side_share: float = 0.67,
    max_same_sector_share: float = 0.67,
    max_live_extrinsic_ratio: float = 0.96,
    max_live_no_trade_prob: float = MAX_LIVE_NO_TRADE_PROB,
    min_live_fill_quality: float = MIN_LIVE_FILL_QUALITY,
    max_live_sentinel_no_trade_pressure: float = MAX_LIVE_SENTINEL_NO_TRADE_PRESSURE,
    corr_matrix: np.ndarray | None = None,
    fetch_live_corr: bool = True,
    prior_live_board_symbols: list[str] | None = None,
    turnover_switch_penalty: float = 0.03,
    market_shock: MarketShockRegime | dict[str, Any] | None = None,
) -> CouncilResult:
    notes: list[str] = []
    shock = _normalize_market_shock(market_shock)
    effective_minimum_live_score = min(max(minimum_live_score + shock.live_score_buffer, 0.0), 1.0)
    effective_minimum_put_live_score = (
        min(max(minimum_put_live_score + shock.put_score_buffer, 0.0), 1.0)
        if minimum_put_live_score is not None
        else None
    )
    effective_max_live_extrinsic_ratio = (
        min(max_live_extrinsic_ratio, shock.max_extrinsic_ratio)
        if shock.max_extrinsic_ratio is not None
        else max_live_extrinsic_ratio
    )
    if shock.label != NEUTRAL_MARKET_SHOCK.label:
        notes.append(
            "Market-shock overlay active: "
            f"{shock.label} severity={shock.severity:.2f} stance={shock.stance}."
        )
        notes.extend(shock.strategy_notes[:2])
    _annotate_risk(candidates, max_sector_share=max_same_sector_share)

    # ── Pre-filter eligible candidates ──
    eligible: list[ContractCandidate] = []
    score_only_blocked: list[ContractCandidate] = []
    extrinsic_only_blocked: list[ContractCandidate] = []
    score_and_extrinsic_blocked: list[ContractCandidate] = []
    no_trade_blocked: list[ContractCandidate] = []
    fill_quality_blocked: list[ContractCandidate] = []
    execution_policy_blocked: list[ContractCandidate] = []
    sentinel_pressure_blocked: list[ContractCandidate] = []
    mixed_policy_blocked: list[ContractCandidate] = []
    probation_blocked: list[ContractCandidate] = []

    for candidate in candidates:
        symbol_on_probation = candidate.symbol.upper() in LIVE_PROBATION_SYMBOLS
        if symbol_on_probation:
            flags = set(candidate.council_risk_flags)
            flags.add("symbol_probation")
            candidate.council_risk_flags = sorted(flags)
            if LIVE_PROBATION_REASON not in candidate.notes:
                candidate.notes.append(LIVE_PROBATION_REASON)

        required_score = (
            effective_minimum_put_live_score
            if candidate.option_type == "put" and effective_minimum_put_live_score is not None
            else effective_minimum_live_score
        )
        live_score = _effective_candidate_score(candidate)
        fill_quality = _candidate_fill_quality(candidate)
        no_trade_pressure = _candidate_no_trade_pressure(candidate)
        sentinel_pressure = _sentinel_no_trade_pressure(candidate)
        score_ok = live_score >= required_score
        extrinsic_ok = candidate.extrinsic_ratio <= effective_max_live_extrinsic_ratio
        no_trade_ok = True
        fill_quality_ok = fill_quality >= min_live_fill_quality
        sentinel_ok = True
        execution_policy_ok = candidate.execution_policy_passed is not False
        if shock.label != NEUTRAL_MARKET_SHOCK.label:
            flags = set(candidate.council_risk_flags)
            flags.add(f"market_shock:{shock.label}")
            if candidate.option_type in shock.blocked_sides:
                flags.add("market_shock_side_block")
            candidate.council_risk_flags = sorted(flags)
        if score_ok and extrinsic_ok and no_trade_ok and fill_quality_ok and sentinel_ok and execution_policy_ok and not symbol_on_probation:
            eligible.append(candidate)
            continue

        if symbol_on_probation:
            probation_blocked.append(candidate)
            continue

        failures: list[str] = []
        if not score_ok:
            failures.append("score")
        if not extrinsic_ok:
            failures.append("extrinsic")
        if not no_trade_ok:
            failures.append("no_trade")
            no_trade_blocked.append(candidate)
        if not fill_quality_ok:
            failures.append("fill_quality")
            fill_quality_blocked.append(candidate)
        if not sentinel_ok:
            failures.append("sentinel")
            sentinel_pressure_blocked.append(candidate)
        if not execution_policy_ok:
            failures.append("execution_policy")
            execution_policy_blocked.append(candidate)

        if failures == ["score"]:
            score_only_blocked.append(candidate)
        elif failures == ["extrinsic"]:
            extrinsic_only_blocked.append(candidate)
        elif failures == ["score", "extrinsic"]:
            score_and_extrinsic_blocked.append(candidate)
        else:
            mixed_policy_blocked.append(candidate)

    shadow_fallback = [
        c for c in candidates
        if c not in eligible
    ]

    # ── De-duplicate by symbol (keep highest Council policy score per symbol) ──
    seen: dict[str, ContractCandidate] = {}
    for c in eligible:
        if c.symbol not in seen or _effective_candidate_score(c) > _effective_candidate_score(seen[c.symbol]):
            seen[c.symbol] = c
    unique_eligible = sorted(seen.values(), key=_effective_candidate_score, reverse=True)
    side_balance_rejections = 0
    side_balance_demotions = 0

    # ── Markowitz portfolio construction ──
    live_board: list[ContractCandidate] = []
    portfolio_var: float = float("nan")
    portfolio_sharpe_est: float = float("nan")
    corr_used: np.ndarray | None = None

    if len(unique_eligible) >= 2:
        syms = [c.symbol for c in unique_eligible]
        corr = corr_matrix
        if corr is None and fetch_live_corr:
            corr = _fetch_corr_matrix(syms)

        if corr is not None and corr.shape == (len(syms), len(syms)):
            corr_used = corr
            exp_rets = np.array([_effective_candidate_score(c) for c in unique_eligible])
            # Approximate symbol volatility from the candidate's implied vol
            approx_vols = np.array([max(float(c.implied_volatility), 0.10) for c in unique_eligible])
            result = _markowitz_weights(exp_rets, corr, approx_vols, live_size)

            if result is not None:
                top_idx, top_weights = result
                live_board = [unique_eligible[i] for i in top_idx]

                # Estimate portfolio variance and a directional Sharpe
                cov = corr[np.ix_(top_idx, top_idx)] * np.outer(
                    approx_vols[top_idx], approx_vols[top_idx]
                )
                portfolio_var = float(top_weights @ cov @ top_weights)
                portfolio_sigma = portfolio_var ** 0.5
                portfolio_mu = float(top_weights @ exp_rets[top_idx])
                from .market_data import fetch_risk_free_rate
                rf_weekly = (1 + fetch_risk_free_rate()) ** (1 / 52) - 1
                portfolio_sharpe_est = round(
                    (portfolio_mu - rf_weekly) / portfolio_sigma, 4
                ) if portfolio_sigma > 0 else 0.0

                notes.append(
                    f"Markowitz optimizer selected {len(live_board)} contracts "
                    f"(portfolio σ={portfolio_sigma:.3%}, est. Sharpe={portfolio_sharpe_est:.2f})"
                )
            else:
                notes.append("Markowitz optimizer fell back to rank-order (convergence failure).")
        else:
            notes.append("Correlation matrix unavailable — using rank-order fallback.")

    # ── Fallback: original rank-order with side-balance guard ──
    if not live_board:
        for candidate in unique_eligible:
            if len(live_board) >= live_size:
                break
            projected = live_board + [candidate]
            side_counts = Counter(row.option_type for row in projected)
            same_side_share = max(side_counts.values()) / len(projected)
            if len(projected) > 1 and same_side_share > max_same_side_share:
                side_balance_rejections += 1
                continue
            live_board.append(candidate)

    # ── Side-balance guard on Markowitz output ──
    if live_board:
        side_counts = Counter(c.option_type for c in live_board)
        if max(side_counts.values()) / len(live_board) > max_same_side_share:
            notes.append(
                "Side-balance guard demoted an over-concentrated position to shadow."
            )
            # Drop the excess until balanced
            calls = [c for c in live_board if c.option_type == "call"]
            puts  = [c for c in live_board if c.option_type == "put"]
            while calls and puts and max(len(calls), len(puts)) / (len(calls) + len(puts)) > max_same_side_share:
                if len(calls) > len(puts):
                    shadow_fallback.insert(0, calls.pop())
                else:
                    shadow_fallback.insert(0, puts.pop())
                side_balance_demotions += 1
            live_board = calls + puts

    turnover_diag = {
        "applied": False,
        "reason": "not_evaluated",
        "prior_live_symbols": [str(symbol).upper() for symbol in (prior_live_board_symbols or []) if str(symbol).strip()],
        "retained_symbols": [],
        "new_symbols": [row.symbol for row in live_board],
        "replacements": 0,
        "required_uplift": 0.0,
        "actual_uplift": 0.0,
    }
    if prior_live_board_symbols:
        live_board, turnover_diag = _apply_turnover_penalty(
            live_board,
            unique_eligible,
            live_size=live_size,
            prior_live_board_symbols=prior_live_board_symbols,
            max_same_side_share=max_same_side_share,
            max_same_sector_share=max_same_sector_share,
            turnover_switch_penalty=turnover_switch_penalty,
        )
        if turnover_diag["applied"]:
            notes.append(
                "Turnover penalty kept the prior live board because replacement uplift "
                f"({turnover_diag['actual_uplift']:.3f}) stayed below the switch threshold "
                f"({turnover_diag['required_uplift']:.3f})."
            )
        elif turnover_diag["reason"] == "new_board_cleared_turnover_threshold":
            notes.append(
                "Board change cleared the turnover threshold with "
                f"{turnover_diag['actual_uplift']:.3f} utility uplift against a "
                f"{turnover_diag['required_uplift']:.3f} switch hurdle."
            )

    # ── Shadow board ──
    shadow_board: list[ContractCandidate] = []
    shadow_seen: set[str] = {c.symbol for c in live_board}
    shadow_pool = sorted(
        candidates,
        key=lambda row: (
            _effective_candidate_score(row),
            row.forge_score,
        ),
        reverse=True,
    )
    for candidate in shadow_pool:
        if len(shadow_board) >= shadow_size:
            break
        if candidate in live_board or candidate.symbol in shadow_seen:
            continue
        shadow_board.append(candidate)
        shadow_seen.add(candidate.symbol)

    if not live_board:
        notes.append("Council abstained because no contract cleared the live board threshold.")

    market_shock_abstained = bool(shock.global_abstain and candidates)
    if market_shock_abstained and live_board:
        shadow_board = sorted(
            list({row.contract_symbol: row for row in [*live_board, *shadow_board]}.values()),
            key=lambda row: (
                _effective_candidate_score(row),
                row.forge_score,
            ),
            reverse=True,
        )[:shadow_size]
        live_board = []
        notes.append("Market-shock overlay forced a global live-board abstain.")
    elif market_shock_abstained:
        notes.append("Market-shock overlay confirmed the live-board abstain.")

    if market_shock_abstained:
        primary_reason = "market_shock_abstain"
    elif not candidates:
        primary_reason = "no_forge_candidates"
    elif not eligible:
        if len(probation_blocked) == len(candidates):
            primary_reason = "symbol_probation"
        elif len(no_trade_blocked) == len(candidates):
            primary_reason = "model_no_trade"
        elif len(execution_policy_blocked) == len(candidates):
            primary_reason = "execution_policy"
        elif len(fill_quality_blocked) == len(candidates):
            primary_reason = "fill_quality"
        elif len(sentinel_pressure_blocked) == len(candidates):
            primary_reason = "sentinel_no_trade_pressure"
        elif len(extrinsic_only_blocked) + len(score_and_extrinsic_blocked) + len(probation_blocked) == len(candidates):
            primary_reason = (
                "score_and_extrinsic_limit"
                if len(score_and_extrinsic_blocked) == len(candidates)
                else "extrinsic_limit" if not probation_blocked else "mixed_core_filters"
            )
        elif len(score_only_blocked) + len(score_and_extrinsic_blocked) + len(probation_blocked) == len(candidates):
            primary_reason = (
                "score_and_extrinsic_limit"
                if len(score_and_extrinsic_blocked) == len(candidates)
                else "below_live_score" if not probation_blocked else "mixed_core_filters"
            )
        else:
            primary_reason = "mixed_core_filters"
    elif not live_board and (side_balance_rejections > 0 or side_balance_demotions > 0):
        primary_reason = "side_balance"
    elif not live_board:
        primary_reason = "selection_threshold"
    else:
        primary_reason = "live_board_available"

    if regime.mode == "risk_off":
        notes.append("Council is operating under a risk-off market regime.")
    elif regime.mode == "risk_on":
        notes.append("Council is operating under a risk-on market regime.")
    elif regime.mode == "extreme_vol":
        notes.append("Council is operating under an extreme-volatility market regime.")
    else:
        notes.append("Council is operating under a neutral market regime.")

    summary: dict[str, Any] = {
        "candidate_count":     len(candidates),
        "live_count":          len(live_board),
        "shadow_count":        len(shadow_board),
        "regime_mode":         regime.mode,
        "minimum_live_score":  effective_minimum_live_score,
        "portfolio_variance":  round(portfolio_var, 6) if not np.isnan(portfolio_var) else None,
        "portfolio_sharpe_est": portfolio_sharpe_est if not np.isnan(portfolio_sharpe_est) else None,
        "live_side_counts": dict(Counter(row.option_type for row in live_board)),
        "live_sector_counts": dict(Counter(sector_for_symbol(row.symbol) for row in live_board)),
        "candidate_sector_counts": dict(Counter(sector_for_symbol(row.symbol) for row in candidates)),
        "avg_pairwise_correlation": _avg_pairwise_corr(corr_used),
        "no_trade_discipline": {
            "minimum_live_score": minimum_live_score,
            "minimum_put_live_score": minimum_put_live_score,
            "effective_minimum_live_score": effective_minimum_live_score,
            "effective_minimum_put_live_score": effective_minimum_put_live_score,
            "max_live_extrinsic_ratio": max_live_extrinsic_ratio,
            "effective_max_live_extrinsic_ratio": effective_max_live_extrinsic_ratio,
            "max_live_no_trade_prob": max_live_no_trade_prob,
            "min_live_fill_quality": min_live_fill_quality,
            "max_live_sentinel_no_trade_pressure": max_live_sentinel_no_trade_pressure,
            "max_same_side_share": max_same_side_share,
            "max_same_sector_share": max_same_sector_share,
            "live_probation_symbols": sorted(LIVE_PROBATION_SYMBOLS),
            "turnover_switch_penalty": turnover_switch_penalty,
        },
        "market_shock": shock.to_dict(),
        "turnover": turnover_diag,
        "abstain_audit": {
            "primary_reason": primary_reason,
            "primary_reason_label": _abstain_reason_label(primary_reason),
            "requested_live_size": live_size,
            "candidate_count": len(candidates),
            "core_filter_pass_count": len(eligible),
            "unique_eligible_count": len(unique_eligible),
            "score_only_fail_count": len(score_only_blocked),
            "extrinsic_only_fail_count": len(extrinsic_only_blocked),
            "score_and_extrinsic_fail_count": len(score_and_extrinsic_blocked),
            "model_no_trade_fail_count": len(no_trade_blocked),
            "fill_quality_fail_count": len(fill_quality_blocked),
            "execution_policy_fail_count": len(execution_policy_blocked),
            "sentinel_no_trade_pressure_fail_count": len(sentinel_pressure_blocked),
            "mixed_policy_fail_count": len(mixed_policy_blocked),
            "symbol_probation_fail_count": len(probation_blocked),
            "side_balance_rejections": side_balance_rejections,
            "side_balance_demotions": side_balance_demotions,
            "blocked_symbols": {
                "score_only": [row.symbol for row in score_only_blocked[:3]],
                "extrinsic_only": [row.symbol for row in extrinsic_only_blocked[:3]],
                "score_and_extrinsic": [row.symbol for row in score_and_extrinsic_blocked[:3]],
                "model_no_trade": [row.symbol for row in no_trade_blocked[:3]],
                "fill_quality": [row.symbol for row in fill_quality_blocked[:3]],
                "execution_policy": [row.symbol for row in execution_policy_blocked[:3]],
                "sentinel_no_trade_pressure": [row.symbol for row in sentinel_pressure_blocked[:3]],
                "mixed_policy": [row.symbol for row in mixed_policy_blocked[:3]],
                "symbol_probation": [row.symbol for row in probation_blocked[:3]],
            },
            "best_rejected_candidates": {
                "score_only": [_audit_candidate(row) for row in score_only_blocked[:5]],
                "extrinsic_only": [_audit_candidate(row) for row in extrinsic_only_blocked[:5]],
                "score_and_extrinsic": [_audit_candidate(row) for row in score_and_extrinsic_blocked[:5]],
                "model_no_trade": [_audit_candidate(row) for row in no_trade_blocked[:5]],
                "fill_quality": [_audit_candidate(row) for row in fill_quality_blocked[:5]],
                "execution_policy": [_audit_candidate(row) for row in execution_policy_blocked[:5]],
                "sentinel_no_trade_pressure": [_audit_candidate(row) for row in sentinel_pressure_blocked[:5]],
                "mixed_policy": [_audit_candidate(row) for row in mixed_policy_blocked[:5]],
                "symbol_probation": [_audit_candidate(row) for row in probation_blocked[:5]],
            },
        },
        "notes":               notes,
    }

    return CouncilResult(
        live_board=live_board,
        shadow_board=shadow_board,
        abstain=not bool(live_board),
        summary=summary,
    )
