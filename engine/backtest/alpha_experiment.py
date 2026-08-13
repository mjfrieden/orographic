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
from engine.backtest.risk_controls import apply_candidate_concentration_caps
from engine.backtest.results import apply_coverage_policy, build_results
from engine.backtest.results import save_option_outcome_dataset
from engine.backtest.runner import _load_universe
from engine.orographic.council import select_board
from engine.orographic.event_features import load_event_feature_frame
from engine.orographic.schemas import ContractCandidate, MarketRegime
from engine.orographic.unified_stack import (
    CURRENT_GATED,
    UNIFIED_NO_COST_AWARE,
    UNIFIED_NO_HIERARCHICAL,
    UNIFIED_NO_PATH,
    UNIFIED_PRIMARY_ONLY,
    UNIFIED_RND,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path(__file__).parents[2] / "docs" / "alpha_experiment_results.json"
DEFAULT_OPTION_OUTCOME_DIR = Path("output")


@dataclass(frozen=True)
class VariantConfig:
    name: str
    council_only: bool
    max_estimated_cost_basis: float | None = None
    use_symbol_priors: bool = False
    use_path_tiebreaker: bool = False
    path_tiebreaker_max_swaps: int = 1
    path_tiebreaker_max_forge_gap: float = 0.03
    path_tiebreaker_min_path_quality_edge: float = 0.10
    live_size: int = 3
    shadow_size: int = 3
    model_stack: str = "current_gated"
    minimum_live_score: float = 0.57
    minimum_put_live_score: float | None = None
    max_live_extrinsic_ratio: float = 0.96


@dataclass(frozen=True)
class SymbolPrior:
    symbol: str
    trades: int
    win_rate: float
    total_pnl: float
    avg_pnl_pct: float
    score: float


def build_variants(
    cost_cap_usd: float | None,
    *,
    unified_comparison_only: bool = False,
    unified_ablation_only: bool = False,
    council_risk_ablation_only: bool = False,
) -> list[VariantConfig]:
    current = VariantConfig(
        name="council_cost_cap",
        council_only=True,
        max_estimated_cost_basis=cost_cap_usd,
        shadow_size=0,
        model_stack=CURRENT_GATED,
    )
    unified = VariantConfig(
        name="unified_council_cost_cap",
        council_only=True,
        max_estimated_cost_basis=cost_cap_usd,
        shadow_size=0,
        model_stack=UNIFIED_RND,
    )
    ablations = [
        VariantConfig(
            name=profile,
            council_only=True,
            max_estimated_cost_basis=cost_cap_usd,
            shadow_size=0,
            model_stack=profile,
        )
        for profile in (
            UNIFIED_NO_HIERARCHICAL,
            UNIFIED_NO_PATH,
            UNIFIED_NO_COST_AWARE,
            UNIFIED_PRIMARY_ONLY,
        )
    ]
    risk_ablations = [
        VariantConfig(
            name="unified_research_reference_live3_score57",
            council_only=True,
            max_estimated_cost_basis=cost_cap_usd,
            live_size=3,
            shadow_size=0,
            model_stack=UNIFIED_RND,
        ),
        *[
            VariantConfig(
                name=f"unified_live1_score{int(score * 100):02d}",
                council_only=True,
                max_estimated_cost_basis=cost_cap_usd,
                live_size=1,
                shadow_size=0,
                model_stack=UNIFIED_RND,
                minimum_live_score=score,
                minimum_put_live_score=max(score - 0.02, 0.0),
                max_live_extrinsic_ratio=0.90,
            )
            for score in (0.64, 0.68, 0.72, 0.76, 0.80)
        ],
        VariantConfig(
            name="unified_production_core_policy",
            council_only=True,
            max_estimated_cost_basis=cost_cap_usd,
            live_size=1,
            shadow_size=0,
            model_stack=UNIFIED_RND,
            minimum_live_score=0.86,
            minimum_put_live_score=0.84,
            max_live_extrinsic_ratio=0.90,
        ),
    ]
    if council_risk_ablation_only:
        return risk_ablations
    if unified_ablation_only:
        return [current, unified, *ablations]
    if unified_comparison_only:
        return [current, unified]
    return [
        VariantConfig(name="baseline_all_candidates", council_only=False),
        VariantConfig(name="council_only", council_only=True),
        current,
        VariantConfig(
            name="council_cost_cap_symbol_priors",
            council_only=True,
            max_estimated_cost_basis=cost_cap_usd,
            use_symbol_priors=True,
        ),
        VariantConfig(
            name="council_cost_cap_path_tiebreaker",
            council_only=True,
            max_estimated_cost_basis=cost_cap_usd,
            use_path_tiebreaker=True,
        ),
        VariantConfig(
            name="council_cost_cap_path_tiebreaker_loose",
            council_only=True,
            max_estimated_cost_basis=cost_cap_usd,
            use_path_tiebreaker=True,
            path_tiebreaker_max_forge_gap=0.08,
            path_tiebreaker_min_path_quality_edge=0.03,
        ),
        unified,
    ]


def default_variant_option_outcome_paths(
    output_path: Path,
    variant_names: list[str],
    *,
    output_dir: Path | None = None,
) -> dict[str, Path]:
    target_dir = output_dir or DEFAULT_OPTION_OUTCOME_DIR
    base_stem = output_path.stem
    if base_stem == "alpha_experiment_results":
        base_stem = "alpha_experiment"
    return {
        variant_name: target_dir / f"option_outcomes_{base_stem}_{variant_name}.json"
        for variant_name in variant_names
    }


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
    regime: MarketRegime,
    *,
    budget: float = BUDGET_PER_TRADE,
    hard_cost_ceiling: float | None = HARD_COST_CEILING_USD,
    strict_options_data: bool = False,
    entry_slippage_pct: float = 0.0,
    exit_slippage_pct: float = 0.0,
    max_entry_spread_pct: float | None = None,
    max_exit_spread_pct: float | None = None,
    min_entry_open_interest: int = 150,
    min_entry_volume: int = 25,
    min_exit_open_interest: int = 0,
    min_exit_volume: int = 0,
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
            regime=regime,
            budget=budget,
            hard_cost_ceiling=hard_cost_ceiling,
            strict_options_data=strict_options_data,
            entry_slippage_pct=entry_slippage_pct,
            exit_slippage_pct=exit_slippage_pct,
            max_entry_spread_pct=max_entry_spread_pct,
            max_exit_spread_pct=max_exit_spread_pct,
            min_entry_open_interest=min_entry_open_interest,
            min_entry_volume=min_entry_volume,
            min_exit_open_interest=min_exit_open_interest,
            min_exit_volume=min_exit_volume,
        )
        if leg is not None:
            legs.append(leg)
    return legs


def _candidate_sort_value(candidate: ContractCandidate, field: str, fallback: str = "forge_score") -> float:
    primary = getattr(candidate, field, None)
    if primary is not None:
        return float(primary)
    return float(getattr(candidate, fallback, 0.0) or 0.0)


def _path_shadow_board(
    candidates: list[ContractCandidate],
    *,
    board_size: int,
) -> list[ContractCandidate]:
    ranked = sorted(
        candidates,
        key=lambda row: (
            _candidate_sort_value(row, "path_holding_quality_score"),
            _candidate_sort_value(row, "path_early_profit_take_prob"),
            _candidate_sort_value(row, "forge_score"),
        ),
        reverse=True,
    )
    selected: list[ContractCandidate] = []
    seen_symbols: set[str] = set()
    for candidate in ranked:
        if len(selected) >= max(board_size, 1):
            break
        if candidate.symbol in seen_symbols:
            continue
        selected.append(candidate)
        seen_symbols.add(candidate.symbol)
    return selected


def apply_path_tiebreaker(
    chosen: list[ContractCandidate],
    candidate_pool: list[ContractCandidate],
    *,
    max_swaps: int = 1,
    max_forge_gap: float = 0.03,
    min_path_quality_edge: float = 0.10,
) -> tuple[list[ContractCandidate], dict[str, Any]]:
    if not chosen:
        return list(chosen), {
            "swaps": 0,
            "max_swaps": max_swaps,
            "max_forge_gap": max_forge_gap,
            "min_path_quality_edge": min_path_quality_edge,
            "top_symbols_before": [],
            "top_symbols_after": [],
            "swap_details": [],
        }
    top_before = [row.symbol for row in chosen[:5]]
    updated = list(chosen)
    chosen_symbols = {row.symbol for row in updated}
    swap_details: list[dict[str, Any]] = []
    near_miss_details: list[dict[str, Any]] = []

    alternatives = [
        row for row in sorted(
            candidate_pool,
            key=lambda row: (
                float(getattr(row, "path_holding_quality_score", 0.0) or 0.0),
                float(getattr(row, "forge_score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        if row.symbol not in chosen_symbols
    ]

    swaps = 0
    while swaps < max_swaps and alternatives and updated:
        incumbent = min(
            updated,
            key=lambda row: (
                float(getattr(row, "path_holding_quality_score", 0.0) or 0.0),
                float(getattr(row, "forge_score", 0.0) or 0.0),
            ),
        )
        replacement = alternatives.pop(0)
        incumbent_path = float(getattr(incumbent, "path_holding_quality_score", 0.0) or 0.0)
        replacement_path = float(getattr(replacement, "path_holding_quality_score", 0.0) or 0.0)
        forge_gap = float(getattr(incumbent, "forge_score", 0.0) or 0.0) - float(getattr(replacement, "forge_score", 0.0) or 0.0)
        path_edge = replacement_path - incumbent_path
        blocker: str | None = None
        if replacement_path < incumbent_path + min_path_quality_edge:
            blocker = "path_edge_below_minimum"
        elif forge_gap > max_forge_gap:
            blocker = "forge_gap_above_maximum"
        if blocker is not None:
            if len(near_miss_details) < 5:
                near_miss_details.append(
                    {
                        "out_symbol": incumbent.symbol,
                        "candidate_symbol": replacement.symbol,
                        "incumbent_path_holding_quality_score": round(incumbent_path, 4),
                        "candidate_path_holding_quality_score": round(replacement_path, 4),
                        "path_quality_edge": round(path_edge, 4),
                        "forge_gap": round(forge_gap, 4),
                        "blocker": blocker,
                    }
                )
            continue
        updated = [row for row in updated if row.symbol != incumbent.symbol]
        updated.append(
            replace(
                replacement,
                notes=[*replacement.notes, f"path tie-breaker swap over {incumbent.symbol}"],
            )
        )
        chosen_symbols.discard(incumbent.symbol)
        chosen_symbols.add(replacement.symbol)
        swap_details.append(
            {
                "out_symbol": incumbent.symbol,
                "in_symbol": replacement.symbol,
                "incumbent_path_holding_quality_score": round(incumbent_path, 4),
                "replacement_path_holding_quality_score": round(replacement_path, 4),
                "path_quality_edge": round(path_edge, 4),
                "forge_gap": round(forge_gap, 4),
            }
        )
        swaps += 1

    updated.sort(key=lambda row: float(getattr(row, "forge_score", 0.0) or 0.0), reverse=True)
    return updated, {
        "swaps": swaps,
        "max_swaps": max_swaps,
        "max_forge_gap": max_forge_gap,
        "min_path_quality_edge": min_path_quality_edge,
        "top_symbols_before": top_before,
        "top_symbols_after": [row.symbol for row in updated[:5]],
        "swap_details": swap_details,
        "near_miss_details": near_miss_details,
        "considered_candidates": len(swap_details) + len(near_miss_details),
    }


def _summarize_path_shadow_week(
    chosen: list[ContractCandidate],
    path_shadow: list[ContractCandidate],
    live_priced: list[TradeLeg],
    path_priced: list[TradeLeg],
) -> dict[str, Any]:
    chosen_contracts = [row.contract_symbol for row in chosen]
    path_contracts = [row.contract_symbol for row in path_shadow]
    disagreement = chosen_contracts != path_contracts
    return {
        "chosen_contracts": chosen_contracts,
        "path_shadow_contracts": path_contracts,
        "disagreement": disagreement,
        "chosen_avg_path_holding_quality_score": round(
            sum(float(row.path_holding_quality_score or 0.0) for row in chosen) / len(chosen),
            4,
        ) if chosen else None,
        "path_shadow_avg_holding_quality_score": round(
            sum(float(row.path_holding_quality_score or 0.0) for row in path_shadow) / len(path_shadow),
            4,
        ) if path_shadow else None,
        "chosen_week_pnl": round(sum(trade.pnl for trade in live_priced), 2),
        "path_shadow_week_pnl": round(sum(trade.pnl for trade in path_priced), 2),
        "chosen_priced_count": len(live_priced),
        "path_shadow_priced_count": len(path_priced),
    }


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
    options_data_dir: Path | None = None,
    expiry_policy: str = "same_week",
    target_dte_min: int = 7,
    target_dte_max: int = 14,
    entry_slippage_pct: float = 0.0,
    exit_slippage_pct: float = 0.0,
    max_entry_spread_pct: float | None = None,
    max_exit_spread_pct: float | None = None,
    min_entry_open_interest: int = 150,
    min_entry_volume: int = 25,
    min_exit_open_interest: int = 0,
    min_exit_volume: int = 0,
    max_symbol_candidates_per_week: int | None = None,
    max_sector_candidates_per_week: int | None = None,
    option_outcome_dir: Path | None = None,
    event_features_path: Path | None = None,
    unified_comparison_only: bool = False,
    unified_ablation_only: bool = False,
    council_risk_ablation_only: bool = False,
    initial_account_equity_usd: float = 10_000.0,
) -> dict[str, Any]:
    start_date = end_date - timedelta(days=months * 30)
    log.info("Alpha experiment window: %s → %s (%d months)", start_date, end_date, months)
    log.info("Universe: %s", ", ".join(symbols))

    data_dir = options_data_dir or Path(__file__).parents[2] / "engine" / "data" / "optionsdx"
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

    variants = build_variants(
        cost_cap_usd,
        unified_comparison_only=unified_comparison_only,
        unified_ablation_only=unified_ablation_only,
        council_risk_ablation_only=council_risk_ablation_only,
    )
    event_feature_store = load_event_feature_frame(event_features_path)
    variant_trades: dict[str, list[TradeLeg]] = {variant.name: [] for variant in variants}
    weekly_diagnostics: dict[str, list[dict[str, Any]]] = {variant.name: [] for variant in variants}
    research_trade_history: list[TradeLeg] = []

    for monday in mondays:
        week_by_stack = {
            model_stack: replay_week(
                monday,
                symbols,
                user_histories,
                spy_history,
                vix_history,
                options_provider,
                strict_options_data=strict_options_data,
                expiry_policy=expiry_policy,
                target_dte_min=target_dte_min,
                target_dte_max=target_dte_max,
                max_entry_spread_pct=max_entry_spread_pct,
                min_entry_open_interest=min_entry_open_interest,
                min_entry_volume=min_entry_volume,
                event_feature_store=event_feature_store,
                model_stack=model_stack,
            )
            for model_stack in {variant.model_stack for variant in variants}
        }
        week = week_by_stack.get(CURRENT_GATED) or week_by_stack[variants[0].model_stack]
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
            week = week_by_stack[variant.model_stack]
            candidate_pool = list(week.candidates)
            cost_diag = {"kept": len(candidate_pool), "dropped": 0, "max_estimated_cost_basis": None}
            prior_diag = {"boosted_symbols": [], "excluded_symbols": [], "available_priors": 0}
            path_tiebreaker_diag = {
                "swaps": 0,
                "top_symbols_before": [],
                "top_symbols_after": [],
                "swap_details": [],
                "near_miss_details": [],
                "considered_candidates": 0,
            }

            candidate_pool, cost_diag = filter_by_cost_basis(
                candidate_pool,
                variant.max_estimated_cost_basis,
                budget=base_budget_usd,
                hard_cost_ceiling=hard_cost_ceiling_usd,
            )

            if variant.use_symbol_priors:
                candidate_pool, prior_diag = apply_symbol_priors(candidate_pool, research_priors)
            candidate_pool, concentration_diag = apply_candidate_concentration_caps(
                candidate_pool,
                max_symbol_candidates=max_symbol_candidates_per_week,
                max_sector_candidates=max_sector_candidates_per_week,
            )

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
                    minimum_live_score=variant.minimum_live_score,
                    minimum_put_live_score=variant.minimum_put_live_score,
                    max_live_extrinsic_ratio=variant.max_live_extrinsic_ratio,
                    corr_matrix=corr,
                    fetch_live_corr=False,
                )
                chosen = list(council.live_board)
                if variant.use_path_tiebreaker:
                    chosen, path_tiebreaker_diag = apply_path_tiebreaker(
                        chosen,
                        candidate_pool,
                        max_swaps=variant.path_tiebreaker_max_swaps,
                        max_forge_gap=variant.path_tiebreaker_max_forge_gap,
                        min_path_quality_edge=variant.path_tiebreaker_min_path_quality_edge,
                    )
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
                week.regime,
                budget=base_budget_usd,
                hard_cost_ceiling=hard_cost_ceiling_usd,
                strict_options_data=strict_options_data,
                entry_slippage_pct=entry_slippage_pct,
                exit_slippage_pct=exit_slippage_pct,
                max_entry_spread_pct=max_entry_spread_pct,
                max_exit_spread_pct=max_exit_spread_pct,
                min_entry_open_interest=min_entry_open_interest,
                min_entry_volume=min_entry_volume,
                min_exit_open_interest=min_exit_open_interest,
                min_exit_volume=min_exit_volume,
            )
            path_shadow = _path_shadow_board(
                candidate_pool,
                board_size=len(chosen) if chosen else max(variant.live_size, 1),
            )
            path_shadow_priced = _price_candidates(
                path_shadow,
                week.monday,
                week.friday,
                user_histories,
                options_provider,
                week.regime,
                budget=base_budget_usd,
                hard_cost_ceiling=hard_cost_ceiling_usd,
                strict_options_data=strict_options_data,
                entry_slippage_pct=entry_slippage_pct,
                exit_slippage_pct=exit_slippage_pct,
                max_entry_spread_pct=max_entry_spread_pct,
                max_exit_spread_pct=max_exit_spread_pct,
                min_entry_open_interest=min_entry_open_interest,
                min_entry_volume=min_entry_volume,
                min_exit_open_interest=min_exit_open_interest,
                min_exit_volume=min_exit_volume,
            )
            path_shadow_diag = _summarize_path_shadow_week(chosen, path_shadow, priced, path_shadow_priced)
            variant_trades[variant.name].extend(priced)

            weekly_diagnostics[variant.name].append({
                "monday": week.monday.isoformat(),
                "model_stack": variant.model_stack,
                "council_policy": {
                    "live_size": variant.live_size,
                    "minimum_live_score": variant.minimum_live_score,
                    "minimum_put_live_score": variant.minimum_put_live_score,
                    "max_live_extrinsic_ratio": variant.max_live_extrinsic_ratio,
                },
                "regime": week.regime.mode,
                "regime_bias": round(float(week.regime.bias), 4),
                "regime_source_symbol": week.regime.source_symbol,
                "signals": len(week.signals),
                "signal_side_mix": week.scout_diagnostics.get("final_direction_counts", {}),
                "raw_candidates": len(week.candidates),
                "candidate_side_mix": {
                    "call": sum(1 for row in week.candidates if row.option_type == "call"),
                    "put": sum(1 for row in week.candidates if row.option_type == "put"),
                },
                "post_cost_cap_candidates": cost_diag["kept"],
                "cost_cap_dropped": cost_diag["dropped"],
                "post_concentration_candidates": concentration_diag["kept"],
                "symbol_cap_dropped": concentration_diag["dropped_symbol_cap"],
                "sector_cap_dropped": concentration_diag["dropped_sector_cap"],
                "available_priors": prior_diag["available_priors"],
                "boosted_symbols": prior_diag["boosted_symbols"],
                "excluded_symbols": prior_diag["excluded_symbols"],
                "path_tiebreaker": path_tiebreaker_diag,
                "research_prior_symbols": sorted(research_priors.keys()),
                "selected_symbols": live_symbols,
                "shadow_symbols": shadow_symbols,
                "path_shadow_symbols": [row.symbol for row in path_shadow],
                "selected_count": len(chosen),
                "priced_count": len(priced),
                "week_pnl": round(sum(trade.pnl for trade in priced), 2),
                "path_shadow": path_shadow_diag,
                "council_notes": summary.get("notes", []),
            })

        research_trade_history.extend(
            _price_candidates(
                research_candidates,
                week.monday,
                week.friday,
                user_histories,
                options_provider,
                week.regime,
                budget=base_budget_usd,
                hard_cost_ceiling=hard_cost_ceiling_usd,
                strict_options_data=strict_options_data,
                entry_slippage_pct=entry_slippage_pct,
                exit_slippage_pct=exit_slippage_pct,
                max_entry_spread_pct=max_entry_spread_pct,
                max_exit_spread_pct=max_exit_spread_pct,
                min_entry_open_interest=min_entry_open_interest,
                min_entry_volume=min_entry_volume,
                min_exit_open_interest=min_exit_open_interest,
                min_exit_volume=min_exit_volume,
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
                initial_account_equity_usd=initial_account_equity_usd,
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
            "capital_at_risk_max_drawdown": result["capital_at_risk_max_drawdown"],
            "account_max_drawdown": result["account_max_drawdown"],
            "account_return_pct": result["account_return_pct"],
            "path_shadow_disagreement_weeks": sum(
                1 for row in weekly_diagnostics[name]
                if bool((row.get("path_shadow") or {}).get("disagreement"))
            ),
            "path_shadow_live_pnl_on_disagreement_weeks": round(
                sum(
                    float((row.get("path_shadow") or {}).get("chosen_week_pnl") or 0.0)
                    for row in weekly_diagnostics[name]
                    if bool((row.get("path_shadow") or {}).get("disagreement"))
                ),
                2,
            ),
            "path_shadow_alt_pnl_on_disagreement_weeks": round(
                sum(
                    float((row.get("path_shadow") or {}).get("path_shadow_week_pnl") or 0.0)
                    for row in weekly_diagnostics[name]
                    if bool((row.get("path_shadow") or {}).get("disagreement"))
                ),
                2,
            ),
            "path_tiebreaker_swap_weeks": sum(
                1 for row in weekly_diagnostics[name]
                if int((row.get("path_tiebreaker") or {}).get("swaps", 0) or 0) > 0
            ),
            "path_tiebreaker_total_swaps": sum(
                int((row.get("path_tiebreaker") or {}).get("swaps", 0) or 0)
                for row in weekly_diagnostics[name]
            ),
            "path_tiebreaker_near_miss_weeks": sum(
                1 for row in weekly_diagnostics[name]
                if bool((row.get("path_tiebreaker") or {}).get("near_miss_details"))
            ),
        }
        for name, result in variant_results.items()
    }
    option_outcome_paths = default_variant_option_outcome_paths(
        output_path,
        [variant.name for variant in variants],
        output_dir=option_outcome_dir,
    )
    for variant in variants:
        dataset_path = option_outcome_paths[variant.name]
        save_option_outcome_dataset(
            variant_trades[variant.name],
            dataset_path,
            start_date=start_date,
            end_date=end_date,
        )
        variant_results[variant.name]["option_outcome_dataset_artifact_path"] = str(dataset_path)

    payload = {
        "generated_at": date.today().isoformat(),
        "backtest_start": start_date.isoformat(),
        "backtest_end": end_date.isoformat(),
        "months": months,
        "symbols": symbols,
        "recommended_default_variant": (
            "unified_production_core_policy"
            if council_risk_ablation_only
            else "council_cost_cap"
        ),
        "recommended_default_variant_label": (
            "Unified Production Core Council Policy"
            if council_risk_ablation_only
            else "Council + Cost Cap"
        ),
        "research_default_variant": (
            "unified_research_reference_live3_score57"
            if council_risk_ablation_only
            else "unified_council_cost_cap"
        ),
        "research_default_variant_label": (
            "Unified Three-Pick Research Reference"
            if council_risk_ablation_only
            else "Unified R&D + Council + Cost Cap"
        ),
        "promotion_decision": "hold_pending_leakage_safe_out_of_sample_validation",
        "experimental_variants": (
            [variant.name for variant in variants]
            if council_risk_ablation_only
            else [
                "unified_council_cost_cap",
                UNIFIED_NO_HIERARCHICAL,
                UNIFIED_NO_PATH,
                UNIFIED_NO_COST_AWARE,
                UNIFIED_PRIMARY_ONLY,
                "council_cost_cap_symbol_priors",
                "council_cost_cap_path_tiebreaker",
                "council_cost_cap_path_tiebreaker_loose",
            ]
        ),
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
            "expiry_policy": expiry_policy,
            "target_dte_min": target_dte_min,
            "target_dte_max": target_dte_max,
            "entry_slippage_pct": entry_slippage_pct,
            "exit_slippage_pct": exit_slippage_pct,
            "max_entry_spread_pct": max_entry_spread_pct,
            "max_exit_spread_pct": max_exit_spread_pct,
            "min_entry_open_interest": min_entry_open_interest,
            "min_entry_volume": min_entry_volume,
            "min_exit_open_interest": min_exit_open_interest,
            "min_exit_volume": min_exit_volume,
            "max_symbol_candidates_per_week": max_symbol_candidates_per_week,
            "max_sector_candidates_per_week": max_sector_candidates_per_week,
            "event_features_path": str(event_features_path) if event_features_path else None,
            "event_feature_rows": len(event_feature_store),
            "unified_comparison_only": unified_comparison_only,
            "unified_ablation_only": unified_ablation_only,
            "council_risk_ablation_only": council_risk_ablation_only,
            "initial_account_equity_usd": initial_account_equity_usd,
        },
        "variant_summaries": summaries,
        "option_outcome_datasets": {
            name: {
                "artifact": "option_outcome_dataset",
                "path": str(path),
            }
            for name, path in option_outcome_paths.items()
        },
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
        print(f"    acct dd    {summary['account_max_drawdown']:.1%}")
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
    parser.add_argument(
        "--options-data-dir",
        type=Path,
        default=None,
        help="Partitioned historical options store. Defaults to engine/data/optionsdx.",
    )
    parser.add_argument(
        "--expiry-policy",
        choices=["same_week", "next_listed_weekly", "target_dte"],
        default="same_week",
        help="Historical option expiry selection policy.",
    )
    parser.add_argument(
        "--target-dte-min",
        type=int,
        default=7,
        help="Minimum DTE when --expiry-policy=target_dte.",
    )
    parser.add_argument(
        "--target-dte-max",
        type=int,
        default=14,
        help="Maximum DTE when --expiry-policy=target_dte.",
    )
    parser.add_argument("--entry-slippage-pct", type=float, default=0.0, help="Extra entry premium stress, e.g. 0.03 for 3%%.")
    parser.add_argument("--exit-slippage-pct", type=float, default=0.0, help="Exit bid haircut stress, e.g. 0.03 for 3%%.")
    parser.add_argument("--max-entry-spread-pct", type=float, default=0.0, help="Reject entries wider than this bid/ask spread pct; <=0 disables.")
    parser.add_argument("--max-exit-spread-pct", type=float, default=0.0, help="Reject exits wider than this bid/ask spread pct; <=0 disables.")
    parser.add_argument("--min-entry-open-interest", type=int, default=150, help="Minimum entry open interest.")
    parser.add_argument("--min-entry-volume", type=int, default=25, help="Minimum entry trade volume.")
    parser.add_argument("--min-exit-open-interest", type=int, default=0, help="Minimum exit open interest; 0 disables.")
    parser.add_argument("--min-exit-volume", type=int, default=0, help="Minimum exit trade volume; 0 disables.")
    parser.add_argument("--max-symbol-candidates-per-week", type=int, default=0, help="Per-week symbol candidate cap; 0 disables.")
    parser.add_argument("--max-sector-candidates-per-week", type=int, default=0, help="Per-week sector candidate cap; 0 disables.")
    parser.add_argument(
        "--option-outcome-dir",
        type=Path,
        default=None,
        help="Directory for per-variant canonical option outcome datasets. Defaults to output/.",
    )
    parser.add_argument(
        "--event-features-path",
        type=Path,
        default=None,
        help="Point-in-time event feature store used by the unified historical Sentinel/Scout stack.",
    )
    parser.add_argument(
        "--unified-comparison-only",
        action="store_true",
        help="Run only the current gated Council baseline and the unified R&D Council stack.",
    )
    parser.add_argument(
        "--unified-ablation-only",
        action="store_true",
        help="Run the current baseline, full unified stack, and exact unified component ablations.",
    )
    parser.add_argument(
        "--council-risk-ablation-only",
        action="store_true",
        help="Run one-pick Council score-gate variants plus the prior three-pick research reference.",
    )
    parser.add_argument(
        "--initial-account-equity-usd",
        type=float,
        default=10_000.0,
        help="Initial account equity used for account-level return and drawdown reporting.",
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
        options_data_dir=args.options_data_dir,
        expiry_policy=args.expiry_policy,
        target_dte_min=max(args.target_dte_min, 0),
        target_dte_max=max(args.target_dte_max, 0),
        entry_slippage_pct=max(args.entry_slippage_pct, 0.0),
        exit_slippage_pct=max(args.exit_slippage_pct, 0.0),
        max_entry_spread_pct=args.max_entry_spread_pct if args.max_entry_spread_pct > 0 else None,
        max_exit_spread_pct=args.max_exit_spread_pct if args.max_exit_spread_pct > 0 else None,
        min_entry_open_interest=max(args.min_entry_open_interest, 0),
        min_entry_volume=max(args.min_entry_volume, 0),
        min_exit_open_interest=max(args.min_exit_open_interest, 0),
        min_exit_volume=max(args.min_exit_volume, 0),
        max_symbol_candidates_per_week=args.max_symbol_candidates_per_week if args.max_symbol_candidates_per_week > 0 else None,
        max_sector_candidates_per_week=args.max_sector_candidates_per_week if args.max_sector_candidates_per_week > 0 else None,
        option_outcome_dir=args.option_outcome_dir,
        event_features_path=args.event_features_path,
        unified_comparison_only=bool(args.unified_comparison_only),
        unified_ablation_only=bool(args.unified_ablation_only),
        council_risk_ablation_only=bool(args.council_risk_ablation_only),
        initial_account_equity_usd=max(float(args.initial_account_equity_usd), 0.0),
    )
    print_experiment_summary(payload)
    print(f"Saved alpha experiment results → {args.output}")


if __name__ == "__main__":
    main()
