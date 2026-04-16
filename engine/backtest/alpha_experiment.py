"""
Walk-forward alpha experiments for Orographic.

This module compares several replay variants:
  - baseline_all_candidates
  - council_only
  - council_cost_cap
  - council_cost_cap_symbol_priors

The final variant is the intended "closer to deployable" experiment:
it replays only the Council live board, uses a hard estimated cost-basis cap,
and applies rolling symbol priors derived strictly from already-closed trades.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from engine.backtest.fetcher import fetch_equity_history, mondays_in_range
from engine.backtest.options_provider import HistoricalOptionsProvider
from engine.backtest.pricer import BUDGET_PER_TRADE, HARD_COST_CEILING_USD, TradeLeg, price_trade
from engine.backtest.replay import historical_corr_matrix_as_of, replay_week
from engine.backtest.results import apply_coverage_policy, build_results
from engine.backtest.runner import _load_universe
from engine.orographic.council import select_board
from engine.orographic.schemas import ContractCandidate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path(__file__).parents[2] / "docs" / "alpha_experiment_results.json"


@dataclass(frozen=True)
class VariantConfig:
    name: str
    council_only: bool
    max_estimated_cost_basis: float | None = None
    use_symbol_priors: bool = False
    live_size: int = 3
    shadow_size: int = 3


@dataclass(frozen=True)
class SymbolPrior:
    symbol: str
    trades: int
    win_rate: float
    total_pnl: float
    avg_pnl_pct: float
    score: float


def build_variants(cost_cap_usd: float | None) -> list[VariantConfig]:
    return [
        VariantConfig(name="baseline_all_candidates", council_only=False),
        VariantConfig(name="council_only", council_only=True),
        VariantConfig(name="council_cost_cap", council_only=True, max_estimated_cost_basis=cost_cap_usd),
        VariantConfig(
            name="council_cost_cap_symbol_priors",
            council_only=True,
            max_estimated_cost_basis=cost_cap_usd,
            use_symbol_priors=True,
        ),
    ]


def estimated_cost_basis(
    candidate: ContractCandidate,
    budget: float = BUDGET_PER_TRADE,
    hard_cost_ceiling: float | None = HARD_COST_CEILING_USD,
) -> float | None:
    entry_price = candidate.spread_cost if (candidate.is_spread and candidate.spread_cost) else candidate.ask
    if not entry_price or entry_price <= 0:
        return None

    confidence_scale = max(0.2, (candidate.scout_score + 1.0) / 2.0)
    target_budget = budget * candidate.allocation_weight * confidence_scale
    actual_budget = min(target_budget, hard_cost_ceiling) if hard_cost_ceiling is not None else target_budget
    contracts = int(actual_budget // (entry_price * 100.0))
    if contracts < 1:
        return None
    return round(contracts * entry_price * 100.0, 2)


def filter_by_cost_basis(
    candidates: list[ContractCandidate],
    max_cost_basis: float | None,
    *,
    budget: float = BUDGET_PER_TRADE,
    hard_cost_ceiling: float | None = HARD_COST_CEILING_USD,
) -> tuple[list[ContractCandidate], dict[str, Any]]:
    if max_cost_basis is None:
        return list(candidates), {
            "kept": len(candidates),
            "dropped": 0,
            "max_estimated_cost_basis": None,
        }

    kept: list[ContractCandidate] = []
    dropped = 0
    for candidate in candidates:
        est_cost = estimated_cost_basis(
            candidate,
            budget=budget,
            hard_cost_ceiling=hard_cost_ceiling,
        )
        if est_cost is None or est_cost > max_cost_basis:
            dropped += 1
            continue
        kept.append(candidate)

    return kept, {
        "kept": len(kept),
        "dropped": dropped,
        "max_estimated_cost_basis": max_cost_basis,
    }


def build_symbol_priors(
    trades: list[TradeLeg],
    monday: date,
    *,
    lookback_weeks: int = 12,
    min_trades: int = 5,
) -> dict[str, SymbolPrior]:
    cutoff = monday - timedelta(days=lookback_weeks * 7)
    recent = [
        trade for trade in trades
        if trade.exit_date is not None and cutoff <= trade.exit_date < monday
    ]
    grouped: dict[str, list[TradeLeg]] = {}
    for trade in recent:
        grouped.setdefault(trade.symbol, []).append(trade)

    priors: dict[str, SymbolPrior] = {}
    for symbol, rows in grouped.items():
        if len(rows) < min_trades:
            continue
        total_pnl = sum(row.pnl for row in rows)
        win_rate = sum(1 for row in rows if row.pnl > 0) / len(rows)
        avg_pnl_pct = sum(row.pnl_pct for row in rows) / len(rows)
        clamped_avg = max(min(avg_pnl_pct, 1.5), -1.5)
        score = round(clamped_avg + (win_rate - 0.5), 4)
        priors[symbol] = SymbolPrior(
            symbol=symbol,
            trades=len(rows),
            win_rate=round(win_rate, 4),
            total_pnl=round(total_pnl, 2),
            avg_pnl_pct=round(avg_pnl_pct, 4),
            score=score,
        )
    return priors


def apply_symbol_priors(
    candidates: list[ContractCandidate],
    priors: dict[str, SymbolPrior],
    *,
    top_n: int = 5,
    bottom_n: int = 5,
    boost: float = 0.03,
) -> tuple[list[ContractCandidate], dict[str, Any]]:
    if not priors:
        return list(candidates), {
            "boosted_symbols": [],
            "excluded_symbols": [],
            "available_priors": 0,
        }

    ranked = sorted(priors.values(), key=lambda row: row.score, reverse=True)
    top_symbols = {
        row.symbol
        for row in ranked[:top_n]
        if row.score > 0 and row.total_pnl > 0
    }
    bottom_symbols = {
        row.symbol
        for row in ranked[-bottom_n:]
        if row.score < 0 and row.total_pnl < 0
    }

    adjusted: list[ContractCandidate] = []
    for candidate in candidates:
        if candidate.symbol in bottom_symbols:
            continue
        if candidate.symbol in top_symbols:
            adjusted.append(
                replace(
                    candidate,
                    forge_score=round(min(candidate.forge_score + boost, 0.9999), 4),
                    notes=[*candidate.notes, f"walk-forward prior boost +{boost:.2f}"],
                )
            )
        else:
            adjusted.append(candidate)

    adjusted.sort(key=lambda row: row.forge_score, reverse=True)
    return adjusted, {
        "boosted_symbols": sorted(top_symbols),
        "excluded_symbols": sorted(bottom_symbols),
        "available_priors": len(priors),
    }


def _price_candidates(
    candidates: list[ContractCandidate],
    monday: date,
    friday: date,
    equity_histories: dict[str, Any],
    options_provider: HistoricalOptionsProvider,
    *,
    budget: float = BUDGET_PER_TRADE,
    hard_cost_ceiling: float | None = HARD_COST_CEILING_USD,
    strict_options_data: bool = False,
) -> list[TradeLeg]:
    legs: list[TradeLeg] = []
    for candidate in candidates:
        hist = equity_histories.get(candidate.symbol)
        if hist is None:
            continue
        leg = price_trade(
            candidate,
            monday,
            friday,
            hist,
            options_provider,
            budget=budget,
            hard_cost_ceiling=hard_cost_ceiling,
            strict_options_data=strict_options_data,
        )
        if leg is not None:
            legs.append(leg)
    return legs


def run_experiment(
    end_date: date,
    months: int,
    symbols: list[str],
    output_path: Path,
    force_refresh: bool = False,
    strict_options_data: bool = False,
    min_real_coverage_pct: float = 0.0,
    base_budget_usd: float = BUDGET_PER_TRADE,
    hard_cost_ceiling_usd: float | None = HARD_COST_CEILING_USD,
    cost_cap_usd: float | None = HARD_COST_CEILING_USD,
) -> dict[str, Any]:
    start_date = end_date - timedelta(days=months * 30)
    log.info("Alpha experiment window: %s → %s (%d months)", start_date, end_date, months)
    log.info("Universe: %s", ", ".join(symbols))

    data_dir = Path(__file__).parents[2] / "engine" / "data" / "optionsdx"
    options_provider = HistoricalOptionsProvider(data_dir=data_dir)

    log.info("Fetching equity history …")
    all_symbols = list(set(symbols + ["SPY", "^VIX"]))
    equity_histories: dict[str, Any] = {}
    for sym in all_symbols:
        try:
            equity_histories[sym] = fetch_equity_history(
                sym,
                start_date - timedelta(days=120),
                end_date,
                force_refresh=force_refresh,
            )
            log.info("  ✓ %s  (%d rows)", sym, len(equity_histories[sym]))
        except Exception as exc:
            log.warning("  ✗ %s  %s", sym, exc)

    spy_history = equity_histories.get("SPY")
    vix_history = equity_histories.get("^VIX")
    if spy_history is None or vix_history is None:
        raise RuntimeError("Could not fetch SPY or ^VIX histories for experiment.")

    user_histories = {s: equity_histories[s] for s in symbols if s in equity_histories}
    mondays = mondays_in_range(start_date, end_date)

    variants = build_variants(cost_cap_usd)
    variant_trades: dict[str, list[TradeLeg]] = {variant.name: [] for variant in variants}
    weekly_diagnostics: dict[str, list[dict[str, Any]]] = {variant.name: [] for variant in variants}
    research_trade_history: list[TradeLeg] = []

    for monday in mondays:
        week = replay_week(
            monday,
            symbols,
            user_histories,
            spy_history,
            vix_history,
            options_provider,
            strict_options_data=strict_options_data,
        )
        log.info(
            "Week %s → %d signal(s), %d candidate(s), regime=%s",
            monday,
            len(week.signals),
            len(week.candidates),
            week.regime.mode,
        )

        research_candidates, _ = filter_by_cost_basis(
            week.candidates,
            cost_cap_usd,
            budget=base_budget_usd,
            hard_cost_ceiling=hard_cost_ceiling_usd,
        )
        research_priors = build_symbol_priors(research_trade_history, monday)

        for variant in variants:
            candidate_pool = list(week.candidates)
            cost_diag = {"kept": len(candidate_pool), "dropped": 0, "max_estimated_cost_basis": None}
            prior_diag = {"boosted_symbols": [], "excluded_symbols": [], "available_priors": 0}

            candidate_pool, cost_diag = filter_by_cost_basis(
                candidate_pool,
                variant.max_estimated_cost_basis,
                budget=base_budget_usd,
                hard_cost_ceiling=hard_cost_ceiling_usd,
            )

            if variant.use_symbol_priors:
                candidate_pool, prior_diag = apply_symbol_priors(candidate_pool, research_priors)

            if variant.council_only:
                corr = historical_corr_matrix_as_of(
                    [candidate.symbol for candidate in candidate_pool],
                    user_histories,
                    monday,
                )
                council = select_board(
                    candidate_pool,
                    week.regime,
                    live_size=variant.live_size,
                    shadow_size=variant.shadow_size,
                    corr_matrix=corr,
                    fetch_live_corr=False,
                )
                chosen = list(council.live_board)
                summary = council.summary
                live_symbols = [row.symbol for row in council.live_board]
                shadow_symbols = [row.symbol for row in council.shadow_board]
            else:
                chosen = candidate_pool
                summary = {
                    "candidate_count": len(candidate_pool),
                    "live_count": len(candidate_pool),
                    "shadow_count": 0,
                    "regime_mode": week.regime.mode,
                    "notes": ["Baseline replay using all Forge candidates."],
                }
                live_symbols = [row.symbol for row in candidate_pool[:10]]
                shadow_symbols = []

            priced = _price_candidates(
                chosen,
                week.monday,
                week.friday,
                user_histories,
                options_provider,
                budget=base_budget_usd,
                hard_cost_ceiling=hard_cost_ceiling_usd,
                strict_options_data=strict_options_data,
            )
            variant_trades[variant.name].extend(priced)

            weekly_diagnostics[variant.name].append({
                "monday": week.monday.isoformat(),
                "regime": week.regime.mode,
                "signals": len(week.signals),
                "signal_side_mix": week.scout_diagnostics.get("final_direction_counts", {}),
                "raw_candidates": len(week.candidates),
                "candidate_side_mix": {
                    "call": sum(1 for row in week.candidates if row.option_type == "call"),
                    "put": sum(1 for row in week.candidates if row.option_type == "put"),
                },
                "post_cost_cap_candidates": cost_diag["kept"],
                "cost_cap_dropped": cost_diag["dropped"],
                "available_priors": prior_diag["available_priors"],
                "boosted_symbols": prior_diag["boosted_symbols"],
                "excluded_symbols": prior_diag["excluded_symbols"],
                "research_prior_symbols": sorted(research_priors.keys()),
                "selected_symbols": live_symbols,
                "shadow_symbols": shadow_symbols,
                "selected_count": len(chosen),
                "priced_count": len(priced),
                "week_pnl": round(sum(trade.pnl for trade in priced), 2),
                "council_notes": summary.get("notes", []),
            })

        research_trade_history.extend(
            _price_candidates(
                research_candidates,
                week.monday,
                week.friday,
                user_histories,
                options_provider,
                budget=base_budget_usd,
                hard_cost_ceiling=hard_cost_ceiling_usd,
                strict_options_data=strict_options_data,
            )
        )

    variant_results = {
        variant.name: apply_coverage_policy(
            build_results(
                variant_trades[variant.name],
                start_date,
                end_date,
                budget_per_trade_usd=base_budget_usd,
                hard_cost_ceiling_usd=hard_cost_ceiling_usd,
            ),
            strict_options_data=strict_options_data,
            min_real_coverage_pct=min_real_coverage_pct,
        )
        for variant in variants
    }
    summaries = {
        name: {
            "total_trades": result["total_trades"],
            "win_rate": result["win_rate"],
            "total_pnl": result["total_pnl"],
            "net_return_pct": result["net_return_pct"],
            "sharpe_ratio": result["sharpe_ratio"],
            "max_drawdown": result["max_drawdown"],
        }
        for name, result in variant_results.items()
    }

    payload = {
        "generated_at": date.today().isoformat(),
        "backtest_start": start_date.isoformat(),
        "backtest_end": end_date.isoformat(),
        "months": months,
        "symbols": symbols,
        "config": {
            "budget_per_trade_usd": base_budget_usd,
            "hard_cost_ceiling_usd": hard_cost_ceiling_usd,
            "cost_cap_usd": cost_cap_usd,
            "rolling_prior_lookback_weeks": 12,
            "rolling_prior_min_trades": 5,
            "rolling_prior_top_n": 5,
            "rolling_prior_bottom_n": 5,
            "rolling_prior_boost": 0.03,
            "strict_options_data": strict_options_data,
            "min_real_coverage_pct": min_real_coverage_pct,
        },
        "variant_summaries": summaries,
        "variant_results": variant_results,
        "weekly_diagnostics": weekly_diagnostics,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2))
    return payload


def print_experiment_summary(payload: dict[str, Any]) -> None:
    print("\n" + "═" * 68)
    print("  OROGRAPHIC WALK-FORWARD ALPHA EXPERIMENT")
    print("═" * 68)
    print(f"  Period: {payload['backtest_start']} → {payload['backtest_end']}")
    print()
    for name, summary in payload["variant_summaries"].items():
        print(f"  {name}")
        print(f"    trades     {summary['total_trades']}")
        print(f"    win rate   {summary['win_rate']:.1%}")
        print(f"    total pnl  ${summary['total_pnl']:+.2f}")
        print(f"    net return {summary['net_return_pct']:.1%}")
        print(f"    sharpe     {summary['sharpe_ratio']:.2f}")
        print(f"    drawdown   {summary['max_drawdown']:.1%}")
        print()
    print("═" * 68)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run walk-forward alpha experiments for Orographic.")
    parser.add_argument("--months", type=int, default=6, help="Look-back window in months (default: 6)")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbol list override")
    parser.add_argument("--universe", type=Path, default=None, help="Universe file with one symbol per line")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output JSON path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--refresh", action="store_true", help="Force re-download of cached equity history")
    parser.add_argument("--end-date", type=str, default=None, help="Override end date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument(
        "--strict-options-data",
        action="store_true",
        help="Skip trades when real historical option-chain data is unavailable.",
    )
    parser.add_argument(
        "--min-real-coverage-pct",
        type=float,
        default=0.0,
        help="Minimum required fraction of trades priced from real chains at both entry and exit.",
    )
    parser.add_argument(
        "--base-budget-usd",
        type=float,
        default=BUDGET_PER_TRADE,
        help=f"Base per-trade budget before scaling (default: {BUDGET_PER_TRADE:.0f})",
    )
    parser.add_argument(
        "--hard-cost-ceiling-usd",
        type=float,
        default=HARD_COST_CEILING_USD,
        help=f"True hard max cost basis per trade; set <= 0 to disable (default: {HARD_COST_CEILING_USD:.0f})",
    )
    parser.add_argument(
        "--cost-cap-usd",
        type=float,
        default=HARD_COST_CEILING_USD,
        help=f"Estimated cost cap for capped experiment variants; set <= 0 to disable (default: {HARD_COST_CEILING_USD:.0f})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()
    if args.symbols:
        symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    else:
        symbols = _load_universe(args.universe)

    payload = run_experiment(
        end_date=end_date,
        months=args.months,
        symbols=symbols,
        output_path=args.output,
        force_refresh=args.refresh,
        strict_options_data=args.strict_options_data,
        min_real_coverage_pct=max(0.0, min(args.min_real_coverage_pct, 1.0)),
        base_budget_usd=max(args.base_budget_usd, 0.0),
        hard_cost_ceiling_usd=args.hard_cost_ceiling_usd if args.hard_cost_ceiling_usd > 0 else None,
        cost_cap_usd=args.cost_cap_usd if args.cost_cap_usd > 0 else None,
    )
    print_experiment_summary(payload)
    print(f"Saved alpha experiment results → {args.output}")


if __name__ == "__main__":
    main()
