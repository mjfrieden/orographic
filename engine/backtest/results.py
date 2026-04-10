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


# ── Statistics helpers ──────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


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


# ── Main aggregator ─────────────────────────────────────────────────────────

def build_results(
    trades: list[TradeLeg],
    start_date: date,
    end_date: date,
    *,
    budget_per_trade_usd: float = BUDGET_PER_TRADE,
    hard_cost_ceiling_usd: float | None = HARD_COST_CEILING_USD,
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

    # ── Best / worst trades ──
    sorted_by_pnl = sorted(trades, key=lambda t: t.pnl, reverse=True)
    best_trades = [_trade_to_dict(t) for t in sorted_by_pnl[:3]]
    worst_trades = [_trade_to_dict(t) for t in sorted_by_pnl[-3:]]

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
    entry_source_counts = Counter(t.entry_data_source for t in trades)
    exit_source_counts = Counter(t.exit_data_source for t in trades)
    avg_options_data_coverage_pct = _mean([t.options_data_coverage_pct for t in trades])
    entry_real_trade_pct = sum(1 for t in trades if t.entry_data_source == "real_chain") / len(trades)
    exit_real_trade_pct = sum(1 for t in trades if t.exit_data_source == "real_chain") / len(trades)
    fully_real_trade_pct = sum(
        1 for t in trades
        if t.entry_data_source == "real_chain" and t.exit_data_source == "real_chain"
    ) / len(trades)

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
        "options_data_coverage": {
            "avg_options_data_coverage_pct": round(avg_options_data_coverage_pct, 4),
            "entry_real_trade_pct": round(entry_real_trade_pct, 4),
            "exit_real_trade_pct": round(exit_real_trade_pct, 4),
            "fully_real_trade_pct": round(fully_real_trade_pct, 4),
            "entry_source_counts": dict(sorted(entry_source_counts.items())),
            "exit_source_counts": dict(sorted(exit_source_counts.items())),
        },
        "equity_curve": equity_curve,
        "symbol_breakdown": symbol_breakdown,
        "best_trades": best_trades,
        "worst_trades": worst_trades,
        "all_trades": [_trade_to_dict(t) for t in sorted(trades, key=lambda t: t.entry_date.isoformat())],
    }


def _trade_to_dict(t: TradeLeg) -> dict[str, Any]:
    return {
        "symbol": t.symbol,
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
        "cost_basis": t.cost_basis,
        "exit_value": t.exit_value,
        "pnl": t.pnl,
        "pnl_pct": t.pnl_pct,
        "expired_worthless": t.expired_worthless,
        "forge_score": t.forge_score,
        "entry_data_source": t.entry_data_source,
        "exit_data_source": t.exit_data_source,
        "entry_quote_type": t.entry_quote_type,
        "exit_quote_type": t.exit_quote_type,
        "options_data_coverage_pct": t.options_data_coverage_pct,
    }


def _empty_results(
    start_date: date,
    end_date: date,
    *,
    budget_per_trade_usd: float = BUDGET_PER_TRADE,
    hard_cost_ceiling_usd: float | None = HARD_COST_CEILING_USD,
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
        "options_data_coverage": {
            "avg_options_data_coverage_pct": 0.0,
            "entry_real_trade_pct": 0.0,
            "exit_real_trade_pct": 0.0,
            "fully_real_trade_pct": 0.0,
            "entry_source_counts": {},
            "exit_source_counts": {},
        },
        "equity_curve": [],
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
