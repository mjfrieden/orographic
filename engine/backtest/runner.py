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
) -> None:
    start_date = end_date - timedelta(days=months * 30)
    log.info("Backtest window: %s → %s (%d months)", start_date, end_date, months)
    log.info("Universe: %s", ", ".join(symbols))

    # Initialize Options Data Provider
    data_dir = Path(__file__).parents[2] / "engine" / "data" / "optionsdx"
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
            )
        except Exception as exc:
            log.warning("  replay_week failed: %s", exc)
            continue

        log.info("  %d signal(s), %d candidate(s), regime=%s",
                 len(week.signals), len(week.candidates), week.regime.mode)

        for candidate in week.candidates:
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
    )


if __name__ == "__main__":
    main()
