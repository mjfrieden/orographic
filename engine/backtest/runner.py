"""
engine/backtest/runner.py

CLI entry point for the Orographic backtest engine.

Usage:
    cd engine
    python -m backtest.runner                          # default: 3 months, sample universe
    python -m backtest.runner --months 6               # longer window
    python -m backtest.runner --symbols AAPL,MSFT,AMD  # custom universe
    python -m backtest.runner --refresh                # force re-download cached data
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from engine.backtest.fetcher import (
    fetch_equity_history,
    mondays_in_range,
)
from engine.backtest.pricer import BUDGET_PER_TRADE, HARD_COST_CEILING_USD, price_trade
from engine.backtest.replay import replay_week
from engine.backtest.risk_controls import apply_candidate_concentration_caps
from engine.backtest.results import (
    DEFAULT_OUTPUT,
    apply_coverage_policy,
    build_results,
    print_summary,
    save_results,
)
from engine.backtest.options_provider import HistoricalOptionsProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_SYMBOLS = [
    "SPY", "QQQ", "IWM", "DIA", "AAPL", "AMD", "NVDA", "GOOGL", "AMZN",
    "TSLA", "JPM", "BAC", "V", "XOM", "JNJ", "PG", "HD", "ABBV", "CVX", 
    "CRM", "NFLX", "WMT", "KO", "PEP", "IBM", "ORCL", "CSCO", "ACN", 
    "QCOM", "INTC", "TXN", "MCD", "DIS", "NKE", "BA", "GS"
]
# Removed: LLY, AVGO, UNH, ADBE, COST, MSFT, META (all > $250-300+) 
# They are too expensive for a $500 position size limit.


def _load_universe(path: Path | None) -> list[str]:
    fallback = Path(__file__).parents[1] / "sample_universe.txt"
    target = path or fallback
    if target.exists():
        return [
            line.strip().upper()
            for line in target.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return DEFAULT_SYMBOLS


def run(
    end_date: date,
    months: int,
    symbols: list[str],
    output_path: Path,
    force_refresh: bool = False,
    strict_options_data: bool = False,
    min_real_coverage_pct: float = 0.0,
    base_budget_usd: float = BUDGET_PER_TRADE,
    hard_cost_ceiling_usd: float | None = HARD_COST_CEILING_USD,
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
) -> None:
    start_date = end_date - timedelta(days=months * 30)
    log.info("Backtest window: %s → %s (%d months)", start_date, end_date, months)
    log.info("Universe: %s", ", ".join(symbols))

    # Initialize historical options provider. The partition layout is shared
    # across OptionsDX CSV imports and DoltHub/API-derived ingests.
    data_dir = options_data_dir or Path(__file__).parents[2] / "engine" / "data" / "optionsdx"
    options_provider = HistoricalOptionsProvider(data_dir=data_dir)

    # ── Prefetch all equity histories ──────────────────────────────────────
    log.info("Fetching equity history …")
    all_symbols = list(set(symbols + ["SPY", "^VIX"]))
    equity_histories: dict = {}
    for sym in all_symbols:
        try:
            equity_histories[sym] = fetch_equity_history(
                sym,
                start_date - timedelta(days=120),   # extra headroom for rolling calcs
                end_date,
                force_refresh=force_refresh,
            )
            log.info("  ✓ %s  (%d rows)", sym, len(equity_histories[sym]))
        except Exception as exc:
            log.warning("  ✗ %s  %s", sym, exc)

    spy_history = equity_histories.get("SPY")
    vix_history = equity_histories.get("^VIX")
    if spy_history is None or vix_history is None:
        log.error("Could not fetch SPY or ^VIX — aborting.")
        sys.exit(1)

    user_histories = {s: equity_histories[s] for s in symbols if s in equity_histories}

    # ── Iterate over Mondays ───────────────────────────────────────────────
    mondays = mondays_in_range(start_date, end_date)
    log.info("Found %d Mondays to replay", len(mondays))

    all_trades = []
    for monday in mondays:
        log.info("Replaying week of %s …", monday)
        try:
            week = replay_week(
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
            )
        except Exception as exc:
            log.warning("  replay_week failed: %s", exc)
            continue

        log.info("  %d signal(s), %d candidate(s), regime=%s",
                 len(week.signals), len(week.candidates), week.regime.mode)

        candidates, concentration_diag = apply_candidate_concentration_caps(
            week.candidates,
            max_symbol_candidates=max_symbol_candidates_per_week,
            max_sector_candidates=max_sector_candidates_per_week,
        )
        if concentration_diag["dropped_symbol_cap"] or concentration_diag["dropped_sector_cap"]:
            log.info(
                "  concentration caps kept %d, dropped %d symbol / %d sector candidate(s)",
                concentration_diag["kept"],
                concentration_diag["dropped_symbol_cap"],
                concentration_diag["dropped_sector_cap"],
            )

        for candidate in candidates:
            hist = user_histories.get(candidate.symbol)
            if hist is None:
                continue
            try:
                leg = price_trade(
                    candidate,
                    monday,
                    week.friday,
                    hist,
                    options_provider,
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
            except Exception as exc:
                log.warning("  pricer failed for %s: %s", candidate.symbol, exc)
                leg = None
            if leg is not None:
                all_trades.append(leg)
                log.debug(
                    "    %s %s $%.0f %s → %s  P&L $%+.2f (%.0f%%)",
                    leg.symbol, leg.option_type.upper(), leg.strike,
                    leg.entry_date, leg.exit_date, leg.pnl, leg.pnl_pct * 100,
                )

    log.info("Backtest complete — %d trades across %d weeks", len(all_trades), len(mondays))

    results = build_results(
        all_trades,
        start_date,
        end_date,
        budget_per_trade_usd=base_budget_usd,
        hard_cost_ceiling_usd=hard_cost_ceiling_usd,
    )
    results = apply_coverage_policy(
        results,
        strict_options_data=strict_options_data,
        min_real_coverage_pct=min_real_coverage_pct,
    )
    print_summary(results)
    save_results(results, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Orographic options backtest runner")
    parser.add_argument("--months", type=int, default=3, help="Look-back window in months (default: 3)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols, e.g. AAPL,MSFT,AMD. Defaults to sample_universe.txt")
    parser.add_argument("--universe", type=Path, default=None,
                        help="Path to a text file with one symbol per line")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Output JSON path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--refresh", action="store_true",
                        help="Force re-download of cached equity history")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Override end date (YYYY-MM-DD). Defaults to today.")
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
    parser.add_argument("--entry-slippage-pct", type=float, default=0.0, help="Extra entry premium stress, e.g. 0.03 for 3%.")
    parser.add_argument("--exit-slippage-pct", type=float, default=0.0, help="Exit bid haircut stress, e.g. 0.03 for 3%.")
    parser.add_argument("--max-entry-spread-pct", type=float, default=0.0, help="Reject entries wider than this bid/ask spread pct; <=0 disables.")
    parser.add_argument("--max-exit-spread-pct", type=float, default=0.0, help="Reject exits wider than this bid/ask spread pct; <=0 disables.")
    parser.add_argument("--min-entry-open-interest", type=int, default=150, help="Minimum entry open interest.")
    parser.add_argument("--min-entry-volume", type=int, default=25, help="Minimum entry trade volume.")
    parser.add_argument("--min-exit-open-interest", type=int, default=0, help="Minimum exit open interest; 0 disables.")
    parser.add_argument("--min-exit-volume", type=int, default=0, help="Minimum exit trade volume; 0 disables.")
    parser.add_argument("--max-symbol-candidates-per-week", type=int, default=0, help="Per-week symbol candidate cap; 0 disables.")
    parser.add_argument("--max-sector-candidates-per-week", type=int, default=0, help="Per-week sector candidate cap; 0 disables.")
    args = parser.parse_args()

    end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = _load_universe(args.universe)

    run(
        end_date=end_date,
        months=args.months,
        symbols=symbols,
        output_path=args.output,
        force_refresh=args.refresh,
        strict_options_data=args.strict_options_data,
        min_real_coverage_pct=max(0.0, min(args.min_real_coverage_pct, 1.0)),
        base_budget_usd=max(args.base_budget_usd, 0.0),
        hard_cost_ceiling_usd=args.hard_cost_ceiling_usd if args.hard_cost_ceiling_usd > 0 else None,
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
    )


if __name__ == "__main__":
    main()
