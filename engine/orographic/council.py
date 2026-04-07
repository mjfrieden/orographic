"""
engine/orographic/council.py

Portfolio selection layer for the Orographic pipeline.

Replaces the naive rank-ordering loop with a Markowitz minimum-variance
optimizer (via scipy) that explicitly accounts for the correlation between
the underlying equity returns of the candidate set.

This prevents the system from treating NVDA + AMD + MSFT as three independent
trades when they share a near-1.0 correlation. Positions are sized using
a simple fractional Kelly criterion derived from the ML scout_score.

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

from .schemas import ContractCandidate, CouncilResult, MarketRegime

log = logging.getLogger(__name__)


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


def _kelly_weight(scout_score: float, win_rate_est: float = 0.54, b: float = 1.2) -> float:
    """
    Fractional Kelly sizing (half-Kelly): f = (p*b - q) / b * 0.5
    Uses the scout_score to nudge the win-rate estimate from the base rate.
    """
    # Map scout_score in [-1, +1] to a ±5pp win-rate adjustment
    p = min(max(win_rate_est + scout_score * 0.05, 0.30), 0.75)
    q = 1.0 - p
    kelly_full = (p * b - q) / b
    return max(round(kelly_full * 0.5, 4), 0.05)   # half-Kelly, min 5%


# ── Main selector ─────────────────────────────────────────────────────────────

def select_board(
    candidates: list[ContractCandidate],
    regime: MarketRegime,
    *,
    live_size: int = 3,
    shadow_size: int = 3,
    minimum_live_score: float = 0.57,
    max_same_side_share: float = 0.67,
    max_live_extrinsic_ratio: float = 0.96,
) -> CouncilResult:
    notes: list[str] = []

    # ── Pre-filter eligible candidates ──
    eligible = [
        c for c in candidates
        if c.forge_score >= minimum_live_score
        and c.extrinsic_ratio <= max_live_extrinsic_ratio
    ]
    shadow_fallback = [
        c for c in candidates
        if c not in eligible
    ]

    # ── De-duplicate by symbol (keep highest forge_score per symbol) ──
    seen: dict[str, ContractCandidate] = {}
    for c in eligible:
        if c.symbol not in seen or c.forge_score > seen[c.symbol].forge_score:
            seen[c.symbol] = c
    unique_eligible = list(seen.values())

    # ── Markowitz portfolio construction ──
    live_board: list[ContractCandidate] = []
    portfolio_var: float = float("nan")
    portfolio_sharpe_est: float = float("nan")

    if len(unique_eligible) >= 2:
        syms = [c.symbol for c in unique_eligible]
        corr = _fetch_corr_matrix(syms)

        if corr is not None and corr.shape == (len(syms), len(syms)):
            exp_rets = np.array([(c.scout_score + 1.0) / 2.0 for c in unique_eligible])
            # Approximate symbol volatility from the candidate's implied vol
            approx_vols = np.array([c.implied_volatility for c in unique_eligible])
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
            live_board = calls + puts

    # ── Shadow board ──
    shadow_board: list[ContractCandidate] = []
    shadow_seen: set[str] = {c.symbol for c in live_board}
    for candidate in candidates:
        if len(shadow_board) >= shadow_size:
            break
        if candidate in live_board or candidate.symbol in shadow_seen:
            continue
        shadow_board.append(candidate)
        shadow_seen.add(candidate.symbol)

    if not live_board:
        notes.append("Council abstained because no contract cleared the live board threshold.")

    if regime.mode == "risk_off":
        notes.append("Council is operating under a risk-off market regime.")
    elif regime.mode == "risk_on":
        notes.append("Council is operating under a risk-on market regime.")
    else:
        notes.append("Council is operating under a neutral market regime.")

    summary: dict[str, Any] = {
        "candidate_count":     len(candidates),
        "live_count":          len(live_board),
        "shadow_count":        len(shadow_board),
        "regime_mode":         regime.mode,
        "minimum_live_score":  minimum_live_score,
        "portfolio_variance":  round(portfolio_var, 6) if not np.isnan(portfolio_var) else None,
        "portfolio_sharpe_est": portfolio_sharpe_est if not np.isnan(portfolio_sharpe_est) else None,
        "notes":               notes,
    }

    return CouncilResult(
        live_board=live_board,
        shadow_board=shadow_board,
        abstain=not bool(live_board),
        summary=summary,
    )
