"""
engine/backtest/results.py

Aggregates TradeLeg records from the backtest into:
  - Per-trade table
  - Weekly equity curve
  - Summary statistics (win rate, Sharpe, max drawdown, avg winner/loser)
  - JSON output for dashboard consumption
"""
from __future__ import annotations

from collections import Counter
import json
import math
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from .pricer import BUDGET_PER_TRADE, HARD_COST_CEILING_USD, TradeLeg

# Default output location — sits alongside latest_run.json
DEFAULT_OUTPUT = Path(__file__).parents[2] / "web" / "data" / "backtest_results.json"
DEFAULT_OPTION_OUTCOME_OUTPUT = Path("output/option_outcomes_latest.json")


# ── Statistics helpers ──────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _sharpe(weekly_returns: list[float], rf_annual: float = 0.04) -> float:
    """Annualised Sharpe ratio from weekly return series."""
    if len(weekly_returns) < 2:
        return 0.0
    rf_weekly = (1 + rf_annual) ** (1 / 52) - 1
    excess = [r - rf_weekly for r in weekly_returns]
    mu = _mean(excess)
    sigma = _std(excess)
    if sigma == 0:
        return 0.0
    return round((mu / sigma) * math.sqrt(52), 4)


def _max_drawdown(equity_curve: list[float]) -> float:
    """Maximum peak-to-trough drawdown from an equity curve."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        dd = (value - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
    return round(max_dd, 4)


def apply_coverage_policy(
    results: dict[str, Any],
    *,
    strict_options_data: bool = False,
    min_real_coverage_pct: float = 0.0,
) -> dict[str, Any]:
    coverage = results.get("options_data_coverage", {})
    entry_real_trade_pct = float(coverage.get("entry_real_trade_pct", 0.0))
    exit_real_trade_pct = float(coverage.get("exit_real_trade_pct", 0.0))
    coverage_failed = (
        min_real_coverage_pct > 0
        and (entry_real_trade_pct < min_real_coverage_pct or exit_real_trade_pct < min_real_coverage_pct)
    )
    results["coverage_policy"] = {
        "strict_options_data": strict_options_data,
        "min_real_coverage_pct": min_real_coverage_pct,
        "coverage_failed": coverage_failed,
    }
    return results


def default_option_outcome_output_path(results_output_path: Path = DEFAULT_OUTPUT) -> Path:
    target = Path(results_output_path)
    if target == DEFAULT_OUTPUT:
        return DEFAULT_OPTION_OUTCOME_OUTPUT

    if target.suffix == ".json":
        if "backtest_results" in target.name:
            return target.with_name(target.name.replace("backtest_results", "option_outcomes", 1))
        return target.with_name(f"{target.stem}_option_outcomes.json")

    if "backtest_results" in target.name:
        return target.with_name(target.name.replace("backtest_results", "option_outcomes", 1))
    return target.with_name(f"{target.name}_option_outcomes.json")


def _build_side_breakdown(trades: list[TradeLeg]) -> list[dict[str, Any]]:
    side_stats: dict[str, dict[str, float | int]] = {}
    for trade in trades:
        side = str(trade.option_type).lower()
        if side not in {"call", "put"}:
            continue
        if side not in side_stats:
            side_stats[side] = {
                "trades": 0,
                "wins": 0,
                "expired_worthless": 0,
                "total_pnl": 0.0,
                "sum_pnl_pct": 0.0,
                "total_cost_basis": 0.0,
            }
        bucket = side_stats[side]
        bucket["trades"] += 1
        bucket["total_pnl"] += trade.pnl
        bucket["sum_pnl_pct"] += trade.pnl_pct
        bucket["total_cost_basis"] += trade.cost_basis
        if trade.pnl > 0:
            bucket["wins"] += 1
        if trade.expired_worthless:
            bucket["expired_worthless"] += 1

    breakdown: list[dict[str, Any]] = []
    for side in ["call", "put"]:
        bucket = side_stats.get(side)
        if not bucket:
            continue
        trades_count = int(bucket["trades"])
        breakdown.append({
            "option_type": side,
            "trades": trades_count,
            "win_rate": round(int(bucket["wins"]) / trades_count, 4) if trades_count else 0.0,
            "expired_worthless": int(bucket["expired_worthless"]),
            "total_pnl": round(float(bucket["total_pnl"]), 2),
            "avg_pnl_pct": round(float(bucket["sum_pnl_pct"]) / trades_count, 4) if trades_count else 0.0,
            "avg_cost_basis": round(float(bucket["total_cost_basis"]) / trades_count, 2) if trades_count else 0.0,
        })
    return breakdown


def _build_regime_breakdown(trades: list[TradeLeg]) -> list[dict[str, Any]]:
    regime_stats: dict[str, dict[str, float | int]] = {}
    regime_order = ["risk_on", "neutral", "risk_off"]
    for trade in trades:
        regime_mode = str(trade.regime_mode or "unclassified").lower()
        if regime_mode not in regime_stats:
            regime_stats[regime_mode] = {
                "trades": 0,
                "wins": 0,
                "expired_worthless": 0,
                "total_pnl": 0.0,
                "sum_pnl_pct": 0.0,
                "total_cost_basis": 0.0,
                "sum_regime_bias": 0.0,
                "regime_bias_observations": 0,
            }
        bucket = regime_stats[regime_mode]
        bucket["trades"] += 1
        bucket["total_pnl"] += trade.pnl
        bucket["sum_pnl_pct"] += trade.pnl_pct
        bucket["total_cost_basis"] += trade.cost_basis
        if trade.pnl > 0:
            bucket["wins"] += 1
        if trade.expired_worthless:
            bucket["expired_worthless"] += 1
        if trade.regime_bias is not None:
            bucket["sum_regime_bias"] += float(trade.regime_bias)
            bucket["regime_bias_observations"] += 1

    ordered_regimes = [mode for mode in regime_order if mode in regime_stats]
    ordered_regimes.extend(sorted(mode for mode in regime_stats if mode not in regime_order))

    breakdown: list[dict[str, Any]] = []
    for regime_mode in ordered_regimes:
        bucket = regime_stats[regime_mode]
        trades_count = int(bucket["trades"])
        bias_obs = int(bucket["regime_bias_observations"])
        breakdown.append({
            "regime_mode": regime_mode,
            "trades": trades_count,
            "win_rate": round(int(bucket["wins"]) / trades_count, 4) if trades_count else 0.0,
            "expired_worthless": int(bucket["expired_worthless"]),
            "total_pnl": round(float(bucket["total_pnl"]), 2),
            "avg_pnl_pct": round(float(bucket["sum_pnl_pct"]) / trades_count, 4) if trades_count else 0.0,
            "avg_cost_basis": round(float(bucket["total_cost_basis"]) / trades_count, 2) if trades_count else 0.0,
            "avg_regime_bias": (
                round(float(bucket["sum_regime_bias"]) / bias_obs, 4)
                if bias_obs
                else None
            ),
        })
    return breakdown


def _round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _trade_raw_cost_basis(t: TradeLeg) -> float | None:
    if t.entry_raw_price is None:
        return None
    return float(t.entry_raw_price) * 100.0 * int(t.contracts)


def _trade_raw_exit_value(t: TradeLeg) -> float | None:
    if t.exit_raw_price is None:
        return None
    return float(t.exit_raw_price) * 100.0 * int(t.contracts)


def _trade_raw_pnl(t: TradeLeg) -> float | None:
    raw_cost_basis = _trade_raw_cost_basis(t)
    raw_exit_value = _trade_raw_exit_value(t)
    if raw_cost_basis is None or raw_exit_value is None:
        return None
    return raw_exit_value - raw_cost_basis


def _trade_raw_pnl_pct(t: TradeLeg) -> float | None:
    raw_cost_basis = _trade_raw_cost_basis(t)
    raw_pnl = _trade_raw_pnl(t)
    if raw_cost_basis is None or raw_pnl is None or raw_cost_basis <= 0:
        return None
    return raw_pnl / raw_cost_basis


def _trade_entry_friction_cost_usd(t: TradeLeg) -> float:
    if t.entry_raw_price is None:
        return 0.0
    return max(float(t.entry_price) - float(t.entry_raw_price), 0.0) * 100.0 * int(t.contracts)


def _trade_exit_friction_cost_usd(t: TradeLeg) -> float:
    if t.exit_raw_price is None:
        return 0.0
    return max(float(t.exit_raw_price) - float(t.exit_price), 0.0) * 100.0 * int(t.contracts)


def option_outcome_row(t: TradeLeg) -> dict[str, Any]:
    raw_cost_basis = _trade_raw_cost_basis(t)
    raw_exit_value = _trade_raw_exit_value(t)
    raw_pnl = _trade_raw_pnl(t)
    raw_pnl_pct = _trade_raw_pnl_pct(t)
    entry_friction_cost_usd = _trade_entry_friction_cost_usd(t)
    exit_friction_cost_usd = _trade_exit_friction_cost_usd(t)
    total_friction_cost_usd = entry_friction_cost_usd + exit_friction_cost_usd
    friction_drag_pct = (
        float(raw_pnl_pct) - float(t.pnl_pct)
        if raw_pnl_pct is not None
        else float(t.entry_slippage_pct) + float(t.exit_slippage_pct)
    )
    positive_before_friction = raw_pnl_pct is not None and raw_pnl_pct > 0.0
    positive_after_friction = float(t.pnl_pct) > 0.0
    return {
        "symbol": t.symbol,
        "contract_symbol": t.contract_symbol,
        "option_type": t.option_type,
        "strike": t.strike,
        "expiry": t.expiry,
        "entry_date": t.entry_date.isoformat(),
        "exit_date": t.exit_date.isoformat() if t.exit_date else None,
        "entry_spot": t.entry_spot,
        "exit_spot": t.exit_spot,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "contracts": t.contracts,
        "entry_data_source": t.entry_data_source,
        "exit_data_source": t.exit_data_source,
        "entry_quote_type": t.entry_quote_type,
        "exit_quote_type": t.exit_quote_type,
        "options_data_coverage_pct": t.options_data_coverage_pct,
        "cost_basis": round(float(t.cost_basis), 2),
        "exit_value": round(float(t.exit_value), 2),
        "raw_cost_basis": _round_or_none(raw_cost_basis, 2),
        "raw_exit_value": _round_or_none(raw_exit_value, 2),
        "pnl": round(float(t.pnl), 2),
        "pnl_pct": round(float(t.pnl_pct), 4),
        "raw_pnl": _round_or_none(raw_pnl, 2),
        "raw_pnl_pct": _round_or_none(raw_pnl_pct, 4),
        "entry_friction_cost_usd": round(entry_friction_cost_usd, 2),
        "exit_friction_cost_usd": round(exit_friction_cost_usd, 2),
        "total_friction_cost_usd": round(total_friction_cost_usd, 2),
        "friction_drag_pct": round(float(friction_drag_pct), 4),
        "entry_slippage_pct": round(float(t.entry_slippage_pct), 4),
        "exit_slippage_pct": round(float(t.exit_slippage_pct), 4),
        "entry_spread_pct": _round_or_none(t.entry_spread_pct, 4),
        "exit_spread_pct": _round_or_none(t.exit_spread_pct, 4),
        "entry_open_interest": t.entry_open_interest,
        "entry_volume": t.entry_volume,
        "exit_open_interest": t.exit_open_interest,
        "exit_volume": t.exit_volume,
        "positive_pnl_before_friction": bool(positive_before_friction),
        "positive_pnl_after_friction": bool(positive_after_friction),
        "breakeven_before_friction": bool(positive_before_friction),
        "breakeven_after_friction": bool(positive_after_friction),
        "friction_flipped_winner_to_loser": bool(positive_before_friction and not positive_after_friction),
        "hold_period_return_before_friction_pct": _round_or_none(raw_pnl_pct, 4),
        "hold_period_return_after_friction_pct": round(float(t.pnl_pct), 4),
        "expired_worthless": bool(t.expired_worthless),
        "forge_score": t.forge_score,
        "scout_score": t.scout_score,
        "implied_volatility": t.implied_volatility,
        "delta": t.delta,
        "moneyness": t.moneyness,
        "projected_move_pct": t.projected_move_pct,
        "breakeven_move_pct": t.breakeven_move_pct,
        "expected_return_pct": t.expected_return_pct,
        "extrinsic_ratio": t.extrinsic_ratio,
        "iv_rank": t.iv_rank,
        "allocation_weight": t.allocation_weight,
        "realized_vol_20d": _round_or_none(t.realized_vol_20d, 4),
        "atr_pct_14d": _round_or_none(t.atr_pct_14d, 4),
        "premium_pct_of_spot": _round_or_none(t.premium_pct_of_spot, 4),
        "vrp_gap": _round_or_none(t.vrp_gap, 4),
        "regime_mode": t.regime_mode,
        "regime_bias": _round_or_none(t.regime_bias, 4),
        "regime_source_symbol": t.regime_source_symbol,
        "pre_payoff_forge_score": t.pre_payoff_forge_score,
        "directional_edge": t.directional_edge,
        "liquidity_score": t.liquidity_score,
        "regime_alignment_score": t.regime_alignment_score,
        "prob_positive_option_pnl": t.prob_positive_option_pnl,
        "expected_option_return_pct_model": t.expected_option_return_pct_model,
        "expected_option_return_pct_rank": t.expected_option_return_pct_rank,
        "prob_exceeds_breakeven": t.prob_exceeds_breakeven,
        "max_favorable_excursion_before_expiry": t.max_favorable_excursion_before_expiry,
        "adverse_excursion_risk": t.adverse_excursion_risk,
        "payoff_model_score": t.payoff_model_score,
        "final_candidate_score": t.final_candidate_score,
        "sentinel_holding_window_fit": _round_or_none(t.sentinel_holding_window_fit, 4),
        "sentinel_holding_window_label": t.sentinel_holding_window_label,
        "sentinel_decay_half_life": t.sentinel_decay_half_life,
        "sentinel_time_horizon": t.sentinel_time_horizon,
        "sentinel_confidence": _round_or_none(t.sentinel_confidence, 4),
        "sentinel_call_relevance": _round_or_none(t.sentinel_call_relevance, 4),
        "sentinel_put_relevance": _round_or_none(t.sentinel_put_relevance, 4),
        "sentinel_no_trade_relevance": _round_or_none(t.sentinel_no_trade_relevance, 4),
        "sentinel_spot_effect": _round_or_none(t.sentinel_spot_effect, 4),
        "sentinel_iv_effect": _round_or_none(t.sentinel_iv_effect, 4),
        "path_early_profit_take_prob": _round_or_none(t.path_early_profit_take_prob, 4),
        "path_expected_mfe_pct": _round_or_none(t.path_expected_mfe_pct, 4),
        "path_decay_risk": _round_or_none(t.path_decay_risk, 4),
        "path_holding_quality_score": _round_or_none(t.path_holding_quality_score, 4),
        "path_model_mode": t.path_model_mode,
        "path_model_artifact_sha256": t.path_model_artifact_sha256,
    }


def build_option_outcome_dataset(trades: list[TradeLeg]) -> list[dict[str, Any]]:
    return [option_outcome_row(t) for t in sorted(trades, key=lambda row: row.entry_date.isoformat())]


def build_option_outcome_dataset_summary(dataset: list[dict[str, Any]]) -> dict[str, Any]:
    if not dataset:
        return {
            "rows": 0,
            "positive_pnl_after_friction_rate": 0.0,
            "positive_pnl_before_friction_rate": 0.0,
            "breakeven_after_friction_rate": 0.0,
            "breakeven_before_friction_rate": 0.0,
            "friction_flip_count": 0,
            "avg_friction_drag_pct": 0.0,
            "avg_total_friction_cost_usd": 0.0,
        }
    rows = len(dataset)
    return {
        "rows": rows,
        "positive_pnl_after_friction_rate": round(sum(1 for row in dataset if row["positive_pnl_after_friction"]) / rows, 4),
        "positive_pnl_before_friction_rate": round(sum(1 for row in dataset if row["positive_pnl_before_friction"]) / rows, 4),
        "breakeven_after_friction_rate": round(sum(1 for row in dataset if row["breakeven_after_friction"]) / rows, 4),
        "breakeven_before_friction_rate": round(sum(1 for row in dataset if row["breakeven_before_friction"]) / rows, 4),
        "friction_flip_count": sum(1 for row in dataset if row["friction_flipped_winner_to_loser"]),
        "avg_friction_drag_pct": round(_mean([float(row["friction_drag_pct"]) for row in dataset]), 4),
        "avg_total_friction_cost_usd": round(_mean([float(row["total_friction_cost_usd"]) for row in dataset]), 2),
    }


def canonicalize_option_outcome_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("Expected option outcome row dict")
    option_type = str(row.get("option_type", "call"))
    exit_spot = _safe_float(row.get("exit_spot"), 0.0)
    strike = _safe_float(row.get("strike"), 0.0)
    entry_price = _safe_float(row.get("entry_price"), 0.0)

    def _breakeven_from_underlying() -> bool | None:
        if exit_spot <= 0 or strike <= 0 or entry_price <= 0:
            return None
        if option_type == "put":
            return exit_spot <= strike - entry_price
        return exit_spot >= strike + entry_price

    if row.get("positive_pnl_after_friction") is not None:
        normalized = dict(row)
        raw_pnl_pct = normalized.get("raw_pnl_pct")
        before_friction_pct = normalized.get("hold_period_return_before_friction_pct")
        after_friction_pct = normalized.get("hold_period_return_after_friction_pct")
        if after_friction_pct is None:
            after_friction_pct = normalized.get("pnl_pct")
        if before_friction_pct is None:
            before_friction_pct = raw_pnl_pct if raw_pnl_pct is not None else after_friction_pct
        before_friction_pct = _safe_float(before_friction_pct, _safe_float(after_friction_pct))
        after_friction_pct = _safe_float(after_friction_pct)
        breakeven_underlying = _breakeven_from_underlying()
        normalized["positive_pnl_after_friction"] = bool(normalized.get("positive_pnl_after_friction"))
        normalized["breakeven_after_friction"] = (
            bool(normalized.get("breakeven_after_friction"))
            if normalized.get("breakeven_after_friction") is not None
            else bool(breakeven_underlying if breakeven_underlying is not None else normalized["positive_pnl_after_friction"])
        )
        normalized["positive_pnl_before_friction"] = bool(normalized.get("positive_pnl_before_friction"))
        normalized["breakeven_before_friction"] = (
            bool(normalized.get("breakeven_before_friction"))
            if normalized.get("breakeven_before_friction") is not None
            else bool(breakeven_underlying if breakeven_underlying is not None else normalized["positive_pnl_before_friction"])
        )
        normalized["friction_flipped_winner_to_loser"] = bool(normalized.get("friction_flipped_winner_to_loser"))
        normalized["hold_period_return_before_friction_pct"] = _round_or_none(before_friction_pct, 4)
        normalized["hold_period_return_after_friction_pct"] = round(after_friction_pct, 4)
        normalized["friction_drag_pct"] = round(before_friction_pct - after_friction_pct, 4)
        normalized["total_friction_cost_usd"] = round(_safe_float(normalized.get("total_friction_cost_usd")), 2)
        normalized["entry_friction_cost_usd"] = round(_safe_float(normalized.get("entry_friction_cost_usd")), 2)
        normalized["exit_friction_cost_usd"] = round(_safe_float(normalized.get("exit_friction_cost_usd")), 2)
        normalized["regime_mode"] = str(normalized.get("regime_mode") or "").strip().lower() or None
        normalized["regime_bias"] = _round_or_none(
            _safe_float(normalized.get("regime_bias")),
            4,
        ) if normalized.get("regime_bias") is not None else None
        for field in (
            "realized_vol_20d",
            "atr_pct_14d",
            "premium_pct_of_spot",
            "vrp_gap",
            "sentinel_holding_window_fit",
            "sentinel_confidence",
            "sentinel_call_relevance",
            "sentinel_put_relevance",
            "sentinel_no_trade_relevance",
            "sentinel_spot_effect",
            "sentinel_iv_effect",
            "path_early_profit_take_prob",
            "path_expected_mfe_pct",
            "path_decay_risk",
            "path_holding_quality_score",
        ):
            normalized[field] = (
                _round_or_none(_safe_float(normalized.get(field)), 4)
                if normalized.get(field) is not None
                else None
            )
        return normalized

    raw_cost_basis = row.get("raw_cost_basis")
    if raw_cost_basis is None:
        entry_raw_price = row.get("entry_raw_price")
        contracts = _safe_float(row.get("contracts"), 0.0)
        if entry_raw_price is not None and contracts > 0:
            raw_cost_basis = _safe_float(entry_raw_price) * 100.0 * contracts

    raw_exit_value = row.get("raw_exit_value")
    if raw_exit_value is None:
        exit_raw_price = row.get("exit_raw_price")
        contracts = _safe_float(row.get("contracts"), 0.0)
        if exit_raw_price is not None and contracts > 0:
            raw_exit_value = _safe_float(exit_raw_price) * 100.0 * contracts

    raw_pnl = row.get("raw_pnl")
    if raw_pnl is None and raw_cost_basis is not None and raw_exit_value is not None:
        raw_pnl = _safe_float(raw_exit_value) - _safe_float(raw_cost_basis)

    raw_pnl_pct = row.get("raw_pnl_pct")
    if raw_pnl_pct is None and raw_pnl is not None and raw_cost_basis not in {None, 0}:
        raw_pnl_pct = _safe_float(raw_pnl) / _safe_float(raw_cost_basis)

    after_friction_pct = _safe_float(
        row.get("hold_period_return_after_friction_pct"),
        _safe_float(row.get("pnl_pct")),
    )
    before_friction_pct = _safe_float(raw_pnl_pct, after_friction_pct)
    positive_before = before_friction_pct > 0.0
    positive_after = after_friction_pct > 0.0
    breakeven_underlying = _breakeven_from_underlying()
    normalized = dict(row)
    normalized["cost_basis"] = round(_safe_float(row.get("cost_basis")), 2)
    normalized["exit_value"] = round(_safe_float(row.get("exit_value")), 2)
    normalized["raw_cost_basis"] = _round_or_none(_safe_float(raw_cost_basis), 2) if raw_cost_basis is not None else None
    normalized["raw_exit_value"] = _round_or_none(_safe_float(raw_exit_value), 2) if raw_exit_value is not None else None
    normalized["raw_pnl"] = _round_or_none(_safe_float(raw_pnl), 2) if raw_pnl is not None else None
    normalized["raw_pnl_pct"] = _round_or_none(_safe_float(raw_pnl_pct), 4) if raw_pnl_pct is not None else None
    normalized["hold_period_return_before_friction_pct"] = _round_or_none(before_friction_pct, 4)
    normalized["hold_period_return_after_friction_pct"] = round(after_friction_pct, 4)
    normalized["positive_pnl_before_friction"] = positive_before
    normalized["positive_pnl_after_friction"] = positive_after
    normalized["breakeven_before_friction"] = bool(
        breakeven_underlying if breakeven_underlying is not None else positive_before
    )
    normalized["breakeven_after_friction"] = bool(
        breakeven_underlying if breakeven_underlying is not None else positive_after
    )
    normalized["friction_flipped_winner_to_loser"] = positive_before and not positive_after
    total_friction_cost_usd = _safe_float(raw_pnl, 0.0) - _safe_float(row.get("pnl"), 0.0) if raw_pnl is not None else 0.0
    normalized["total_friction_cost_usd"] = round(max(total_friction_cost_usd, 0.0), 2)
    normalized["entry_friction_cost_usd"] = round(_safe_float(row.get("entry_friction_cost_usd")), 2)
    normalized["exit_friction_cost_usd"] = round(_safe_float(row.get("exit_friction_cost_usd")), 2)
    normalized["friction_drag_pct"] = round(before_friction_pct - after_friction_pct, 4)
    normalized["regime_mode"] = str(normalized.get("regime_mode") or "").strip().lower() or None
    normalized["regime_bias"] = _round_or_none(
        _safe_float(normalized.get("regime_bias")),
        4,
    ) if normalized.get("regime_bias") is not None else None
    for field in (
        "realized_vol_20d",
        "atr_pct_14d",
        "premium_pct_of_spot",
        "vrp_gap",
        "sentinel_holding_window_fit",
        "sentinel_confidence",
        "sentinel_call_relevance",
        "sentinel_put_relevance",
        "sentinel_no_trade_relevance",
        "sentinel_spot_effect",
        "sentinel_iv_effect",
        "path_early_profit_take_prob",
        "path_expected_mfe_pct",
        "path_decay_risk",
        "path_holding_quality_score",
    ):
        normalized[field] = (
            _round_or_none(_safe_float(normalized.get(field)), 4)
            if normalized.get(field) is not None
            else None
        )
    normalized["path_model_mode"] = str(normalized.get("path_model_mode") or "").strip().lower() or None
    return normalized


def canonicalize_option_outcome_dataset(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [canonicalize_option_outcome_row(row) for row in rows if isinstance(row, dict)]


def option_outcome_dataset_payload_from_results_payload(data: dict[str, Any]) -> dict[str, Any]:
    artifact = str(data.get("artifact") or "").strip()
    if artifact == "option_outcome_dataset":
        rows = canonicalize_option_outcome_dataset(data.get("rows", []))
        return {
            "artifact": "option_outcome_dataset",
            "generated_at": data.get("generated_at", date.today().isoformat()),
            "backtest_start": data.get("backtest_start"),
            "backtest_end": data.get("backtest_end"),
            "summary": build_option_outcome_dataset_summary(rows),
            "rows": rows,
        }
    rows = canonicalize_option_outcome_dataset(data.get("all_trades", []))
    return {
        "artifact": "option_outcome_dataset",
        "generated_at": data.get("generated_at", date.today().isoformat()),
        "backtest_start": data.get("backtest_start"),
        "backtest_end": data.get("backtest_end"),
        "summary": build_option_outcome_dataset_summary(rows),
        "rows": rows,
    }


# ── Main aggregator ─────────────────────────────────────────────────────────

def build_results(
    trades: list[TradeLeg],
    start_date: date,
    end_date: date,
    *,
    budget_per_trade_usd: float = BUDGET_PER_TRADE,
    hard_cost_ceiling_usd: float | None = HARD_COST_CEILING_USD,
    initial_account_equity_usd: float = 10_000.0,
) -> dict[str, Any]:
    """
    Convert a flat list of TradeLeg records into a rich results dict suitable
    for JSON serialisation and dashboard display.
    """
    if not trades:
        return _empty_results(
            start_date,
            end_date,
            budget_per_trade_usd=budget_per_trade_usd,
            hard_cost_ceiling_usd=hard_cost_ceiling_usd,
            initial_account_equity_usd=initial_account_equity_usd,
        )

    # ── Top-level stats ──
    winners = [t for t in trades if t.pnl > 0]
    losers  = [t for t in trades if t.pnl <= 0]
    worthless = [t for t in trades if t.expired_worthless]

    win_rate = len(winners) / len(trades)
    avg_winner_pct = _mean([t.pnl_pct for t in winners]) if winners else 0.0
    avg_loser_pct  = _mean([t.pnl_pct for t in losers]) if losers else 0.0
    total_pnl = sum(t.pnl for t in trades)
    total_deployed = sum(t.cost_basis for t in trades)
    net_return_pct = total_pnl / total_deployed if total_deployed > 0 else 0.0

    # ── Equity curve (weekly) ──
    # Group trades by exit week (Monday of week)
    from collections import defaultdict
    weekly: dict[str, float] = defaultdict(float)
    for t in trades:
        if t.exit_date:
            week_key = t.exit_date.isoformat()
            weekly[week_key] += t.pnl

    sorted_weeks = sorted(weekly.keys())
    equity = 0.0
    equity_curve: list[dict[str, Any]] = []
    weekly_returns: list[float] = []
    for week in sorted_weeks:
        deployed_this_week = sum(
            t.cost_basis for t in trades
            if t.exit_date and t.exit_date.isoformat() == week
        )
        week_pnl = weekly[week]
        weekly_return = week_pnl / deployed_this_week if deployed_this_week > 0 else 0.0
        weekly_returns.append(weekly_return)
        equity += week_pnl
        equity_curve.append({
            "week": week,
            "pnl": round(week_pnl, 2),
            "cumulative_pnl": round(equity, 2),
            "weekly_return_pct": round(weekly_return, 4),
        })

    compounded_equity = [1.0]
    for weekly_return in weekly_returns:
        compounded_equity.append(compounded_equity[-1] * (1.0 + weekly_return))
    initial_equity = max(float(initial_account_equity_usd), 0.0)
    account_equity = initial_equity
    account_equity_values = [account_equity]
    account_equity_curve: list[dict[str, Any]] = []
    for row in equity_curve:
        account_equity += float(row["pnl"])
        account_equity_values.append(account_equity)
        account_equity_curve.append({
            "week": row["week"],
            "pnl": row["pnl"],
            "account_equity": round(account_equity, 2),
            "account_return_pct": (
                round((account_equity - initial_equity) / initial_equity, 4)
                if initial_equity > 0
                else 0.0
            ),
        })

    # ── Best / worst trades ──
    sorted_by_pnl = sorted(trades, key=lambda t: t.pnl, reverse=True)
    best_trades = [option_outcome_row(t) for t in sorted_by_pnl[:3]]
    worst_trades = [option_outcome_row(t) for t in sorted_by_pnl[-3:]]

    # ── Per-symbol breakdown ──
    symbol_stats: dict[str, dict] = {}
    for t in trades:
        s = t.symbol
        if s not in symbol_stats:
            symbol_stats[s] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        symbol_stats[s]["trades"] += 1
        symbol_stats[s]["total_pnl"] += t.pnl
        if t.pnl > 0:
            symbol_stats[s]["wins"] += 1
    symbol_breakdown = [
        {
            "symbol": sym,
            "trades": v["trades"],
            "win_rate": round(v["wins"] / v["trades"], 4),
            "total_pnl": round(v["total_pnl"], 2),
        }
        for sym, v in sorted(symbol_stats.items(), key=lambda kv: kv[1]["total_pnl"], reverse=True)
    ]
    side_breakdown = _build_side_breakdown(trades)
    regime_breakdown = _build_regime_breakdown(trades)
    entry_source_counts = Counter(t.entry_data_source for t in trades)
    exit_source_counts = Counter(t.exit_data_source for t in trades)
    avg_options_data_coverage_pct = _mean([t.options_data_coverage_pct for t in trades])
    entry_real_trade_pct = sum(1 for t in trades if t.entry_data_source == "real_chain") / len(trades)
    exit_real_trade_pct = sum(1 for t in trades if t.exit_data_source == "real_chain") / len(trades)
    fully_real_trade_pct = sum(
        1 for t in trades
        if t.entry_data_source == "real_chain" and t.exit_data_source == "real_chain"
    ) / len(trades)
    entry_spreads = [t.entry_spread_pct for t in trades if t.entry_spread_pct is not None]
    exit_spreads = [t.exit_spread_pct for t in trades if t.exit_spread_pct is not None]
    exit_open_interest = [t.exit_open_interest for t in trades if t.exit_open_interest is not None]
    exit_volume = [t.exit_volume for t in trades if t.exit_volume is not None]
    option_outcome_dataset = build_option_outcome_dataset(trades)

    return {
        "generated_at": date.today().isoformat(),
        "backtest_start": start_date.isoformat(),
        "backtest_end": end_date.isoformat(),
        "budget_per_trade_usd": round(budget_per_trade_usd, 2),
        "hard_cost_ceiling_usd": round(hard_cost_ceiling_usd, 2) if hard_cost_ceiling_usd is not None else None,
        "sizing_policy": {
            "base_budget_per_trade_usd": round(budget_per_trade_usd, 2),
            "hard_cost_ceiling_usd": round(hard_cost_ceiling_usd, 2) if hard_cost_ceiling_usd is not None else None,
            "allocation_weight_range": [0.25, 3.0],
            "confidence_scale_range": [0.2, 1.0],
            "skip_when_underfunded": True,
            "max_observed_cost_basis_usd": round(max((t.cost_basis for t in trades), default=0.0), 2),
        },
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "expired_worthless": len(worthless),
        "win_rate": round(win_rate, 4),
        "avg_winner_pct": round(avg_winner_pct, 4),
        "avg_loser_pct": round(avg_loser_pct, 4),
        "total_pnl": round(total_pnl, 2),
        "total_deployed": round(total_deployed, 2),
        "net_return_pct": round(net_return_pct, 4),
        "sharpe_ratio": _sharpe(weekly_returns),
        "max_drawdown": _max_drawdown(compounded_equity),
        "capital_at_risk_max_drawdown": _max_drawdown(compounded_equity),
        "initial_account_equity_usd": round(initial_equity, 2),
        "account_return_pct": (
            round(total_pnl / initial_equity, 4) if initial_equity > 0 else 0.0
        ),
        "account_max_drawdown": _max_drawdown(account_equity_values),
        "options_data_coverage": {
            "avg_options_data_coverage_pct": round(avg_options_data_coverage_pct, 4),
            "entry_real_trade_pct": round(entry_real_trade_pct, 4),
            "exit_real_trade_pct": round(exit_real_trade_pct, 4),
            "fully_real_trade_pct": round(fully_real_trade_pct, 4),
            "entry_source_counts": dict(sorted(entry_source_counts.items())),
            "exit_source_counts": dict(sorted(exit_source_counts.items())),
        },
        "execution_quality": {
            "avg_entry_spread_pct": round(_mean(entry_spreads), 4),
            "avg_exit_spread_pct": round(_mean(exit_spreads), 4),
            "avg_entry_slippage_pct": round(_mean([t.entry_slippage_pct for t in trades]), 4),
            "avg_exit_slippage_pct": round(_mean([t.exit_slippage_pct for t in trades]), 4),
            "avg_exit_open_interest": round(_mean([float(v) for v in exit_open_interest]), 2),
            "avg_exit_volume": round(_mean([float(v) for v in exit_volume]), 2),
        },
        "option_outcome_dataset_summary": build_option_outcome_dataset_summary(option_outcome_dataset),
        "equity_curve": equity_curve,
        "account_equity_curve": account_equity_curve,
        "side_breakdown": side_breakdown,
        "regime_breakdown": regime_breakdown,
        "symbol_breakdown": symbol_breakdown,
        "best_trades": best_trades,
        "worst_trades": worst_trades,
        "all_trades": option_outcome_dataset,
    }


def _empty_results(
    start_date: date,
    end_date: date,
    *,
    budget_per_trade_usd: float = BUDGET_PER_TRADE,
    hard_cost_ceiling_usd: float | None = HARD_COST_CEILING_USD,
    initial_account_equity_usd: float = 10_000.0,
) -> dict[str, Any]:
    return {
        "generated_at": date.today().isoformat(),
        "backtest_start": start_date.isoformat(),
        "backtest_end": end_date.isoformat(),
        "budget_per_trade_usd": round(budget_per_trade_usd, 2),
        "hard_cost_ceiling_usd": round(hard_cost_ceiling_usd, 2) if hard_cost_ceiling_usd is not None else None,
        "sizing_policy": {
            "base_budget_per_trade_usd": round(budget_per_trade_usd, 2),
            "hard_cost_ceiling_usd": round(hard_cost_ceiling_usd, 2) if hard_cost_ceiling_usd is not None else None,
            "allocation_weight_range": [0.25, 3.0],
            "confidence_scale_range": [0.2, 1.0],
            "skip_when_underfunded": True,
            "max_observed_cost_basis_usd": 0.0,
        },
        "total_trades": 0,
        "winners": 0,
        "losers": 0,
        "expired_worthless": 0,
        "win_rate": 0.0,
        "avg_winner_pct": 0.0,
        "avg_loser_pct": 0.0,
        "total_pnl": 0.0,
        "total_deployed": 0.0,
        "net_return_pct": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "capital_at_risk_max_drawdown": 0.0,
        "initial_account_equity_usd": round(max(float(initial_account_equity_usd), 0.0), 2),
        "account_return_pct": 0.0,
        "account_max_drawdown": 0.0,
        "options_data_coverage": {
            "avg_options_data_coverage_pct": 0.0,
            "entry_real_trade_pct": 0.0,
            "exit_real_trade_pct": 0.0,
            "fully_real_trade_pct": 0.0,
            "entry_source_counts": {},
            "exit_source_counts": {},
        },
        "execution_quality": {
            "avg_entry_spread_pct": 0.0,
            "avg_exit_spread_pct": 0.0,
            "avg_entry_slippage_pct": 0.0,
            "avg_exit_slippage_pct": 0.0,
            "avg_exit_open_interest": 0.0,
            "avg_exit_volume": 0.0,
        },
        "option_outcome_dataset_summary": build_option_outcome_dataset_summary([]),
        "equity_curve": [],
        "account_equity_curve": [],
        "side_breakdown": [],
        "regime_breakdown": [],
        "symbol_breakdown": [],
        "best_trades": [],
        "worst_trades": [],
        "all_trades": [],
    }


def save_results(results: dict[str, Any], output_path: Path = DEFAULT_OUTPUT) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"✅  Saved backtest results → {output_path}")


def save_option_outcome_dataset(
    trades: list[TradeLeg],
    output_path: Path,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset = build_option_outcome_dataset(trades)
    payload = {
        "artifact": "option_outcome_dataset",
        "generated_at": date.today().isoformat(),
        "backtest_start": start_date.isoformat() if start_date else None,
        "backtest_end": end_date.isoformat() if end_date else None,
        "summary": build_option_outcome_dataset_summary(dataset),
        "rows": dataset,
    }
    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"✅  Saved option outcome dataset → {output_path}")


def print_summary(results: dict[str, Any]) -> None:
    """Pretty-print summary to console."""
    print("\n" + "═" * 60)
    print("  OROGRAPHIC BACKTEST RESULTS")
    print("═" * 60)
    print(f"  Period:        {results['backtest_start']} → {results['backtest_end']}")
    print(f"  Total trades:  {results['total_trades']}")
    print(f"  Win rate:      {results['win_rate']:.1%}")
    print(f"  Avg winner:    {results.get('avg_winner_pct', 0):.1%}")
    print(f"  Avg loser:     {results.get('avg_loser_pct', 0):.1%}")
    print(f"  Total P&L:     ${results['total_pnl']:+.2f}")
    print(f"  Net return:    {results.get('net_return_pct', 0):.1%}")
    print(f"  Sharpe ratio:  {results['sharpe_ratio']:.2f}")
    print(f"  Max drawdown:  {results['max_drawdown']:.1%}")
    coverage = results.get("options_data_coverage", {})
    print(f"  Entry real:    {coverage.get('entry_real_trade_pct', 0):.1%}")
    print(f"  Exit real:     {coverage.get('exit_real_trade_pct', 0):.1%}")
    print()
    if results.get("symbol_breakdown"):
        print("  By symbol:")
        for row in results["symbol_breakdown"]:
            print(f"    {row['symbol']:6s}  {row['trades']:3d} trades  "
                  f"win {row['win_rate']:.0%}  P&L ${row['total_pnl']:+.2f}")
    print("═" * 60 + "\n")
