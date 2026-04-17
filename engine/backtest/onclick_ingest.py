"""
Ingest no-key OnclickMedia historical option chains into Orographic's store.

The adapter writes the same partitioned parquet layout as DoltHub and OptionsDX
so backtests can blend sources without provider-specific replay logic.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from engine.backtest.fetcher import friday_of_week, mondays_in_range
from engine.backtest.options_provider import HistoricalOptionsProvider
from engine.backtest.options_store import (
    build_manifest_from_partitions,
    manifest_path,
    partition_root,
    write_partitioned_frames,
)
from engine.backtest.replay import select_expiry_from_chain
from engine.backtest.runner import _load_universe

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.onclickmedia.com/"
DEFAULT_OUTPUT_DIR = Path(__file__).parents[1] / "data" / "options" / "onclick"


@dataclass(frozen=True)
class OnclickTask:
    symbol: str
    quote_date: date
    expiration: date


@dataclass(frozen=True)
class OnclickFetchResult:
    task: OnclickTask
    rows: list[dict[str, Any]]
    status: str
    message: str = ""


def standardize_onclick_option_chain(frame: pd.DataFrame, quote_date: date) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    renamed = frame.rename(
        columns={
            "symbol": "underlying_symbol",
            "expiration": "expire_date",
            "type": "option_type",
            "implied_volatility": "implied_volatility",
            "open_interest": "open_interest",
            "volume": "trade_volume",
        }
    ).copy()

    renamed["quote_date"] = quote_date
    if "option_type" in renamed.columns:
        renamed["option_type"] = (
            renamed["option_type"]
            .astype(str)
            .str.strip()
            .str.upper()
            .map({"CALL": "C", "PUT": "P", "C": "C", "P": "P"})
        )

    for col in [
        "strike",
        "last",
        "bid",
        "ask",
        "change",
        "percent_change",
        "trade_volume",
        "open_interest",
        "implied_volatility",
        "delta",
        "gamma",
        "theta",
        "vega",
        "rho",
    ]:
        if col in renamed.columns:
            renamed[col] = pd.to_numeric(renamed[col], errors="coerce")

    renamed["source"] = "onclickmedia"
    keep = [
        "quote_date",
        "underlying_symbol",
        "expire_date",
        "strike",
        "option_type",
        "bid",
        "ask",
        "last",
        "implied_volatility",
        "delta",
        "gamma",
        "theta",
        "vega",
        "rho",
        "open_interest",
        "trade_volume",
        "source",
    ]
    return renamed[[col for col in keep if col in renamed.columns]]


class OnclickMediaClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        retries: int = 2,
        retry_sleep: float = 1.0,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.retry_sleep = retry_sleep

    def fetch_chain(self, task: OnclickTask) -> OnclickFetchResult:
        params = {
            "ticker": task.symbol.upper(),
            "date": task.quote_date.isoformat(),
            "expiration": task.expiration.isoformat(),
            "data": "greeks",
            "output": "json-v1",
        }
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "OrographicBacktest/1.0 (+https://orographic.pages.dev)",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, list):
                    return OnclickFetchResult(task, [], "error", f"Unexpected payload: {type(payload).__name__}")
                return OnclickFetchResult(task, payload, "success")
            except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(self.retry_sleep * (attempt + 1))
        return OnclickFetchResult(task, [], "exception", str(last_error))


def build_tasks_from_existing_store(
    *,
    symbols: list[str],
    start_date: date,
    end_date: date,
    source_data_dir: Path,
    expiry_policy: str,
    target_dte_min: int,
    target_dte_max: int,
    include_entry_dates: bool = True,
    include_exit_dates: bool = True,
) -> list[OnclickTask]:
    provider = HistoricalOptionsProvider(source_data_dir)
    tasks: set[OnclickTask] = set()
    for monday in mondays_in_range(start_date, end_date):
        friday = friday_of_week(monday)
        if friday > end_date:
            continue
        for symbol in symbols:
            chain, source = provider.get_chain_with_source(symbol, monday)
            if source != "real_chain" or chain.empty:
                continue
            expiration = select_expiry_from_chain(
                chain,
                monday,
                expiry_policy=expiry_policy,  # type: ignore[arg-type]
                target_dte_min=target_dte_min,
                target_dte_max=target_dte_max,
            )
            if expiration is None:
                continue
            if include_entry_dates:
                tasks.add(OnclickTask(symbol=symbol.upper(), quote_date=monday, expiration=expiration))
            if include_exit_dates:
                tasks.add(OnclickTask(symbol=symbol.upper(), quote_date=friday, expiration=expiration))
    return sorted(tasks, key=lambda row: (row.quote_date, row.symbol, row.expiration))


def ingest_onclick_options(
    *,
    tasks: list[OnclickTask],
    output_dir: Path,
    client: OnclickMediaClient,
    workers: int = 8,
    force: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    statuses: dict[str, int] = {}
    failed: list[dict[str, str]] = []
    empty: list[dict[str, str]] = []

    log.info("Fetching %d OnclickMedia chain request(s)", len(tasks))
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as executor:
        futures = {executor.submit(client.fetch_chain, task): task for task in tasks}
        for index, future in enumerate(as_completed(futures), start=1):
            task = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = OnclickFetchResult(task, [], "exception", str(exc))

            statuses[result.status] = statuses.get(result.status, 0) + 1
            if result.rows:
                frames.append(standardize_onclick_option_chain(pd.DataFrame(result.rows), result.task.quote_date))
            elif result.status == "success":
                empty.append(
                    {
                        "symbol": task.symbol,
                        "quote_date": task.quote_date.isoformat(),
                        "expiration": task.expiration.isoformat(),
                    }
                )
            else:
                failed.append(
                    {
                        "symbol": task.symbol,
                        "quote_date": task.quote_date.isoformat(),
                        "expiration": task.expiration.isoformat(),
                        "message": result.message or result.status,
                    }
                )

            if index % 100 == 0 or index == len(tasks):
                log.info("Fetched %d/%d OnclickMedia request(s)", index, len(tasks))

    metadata = {
        "provider": "onclickmedia",
        "request_count": len(tasks),
        "status_counts": statuses,
        "empty_results": empty[:100],
        "empty_result_count": len(empty),
        "failed_requests": failed[:100],
        "failed_request_count": len(failed),
    }
    write_partitioned_frames(
        output_dir,
        frames,
        source_files=["onclickmedia:no-key-api"],
        force=force,
        metadata=metadata,
    )
    return build_manifest_from_partitions(
        output_dir,
        source_files=["onclickmedia:no-key-api"],
        metadata=metadata,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest OnclickMedia option chains into partitioned parquet.")
    parser.add_argument("--months", type=int, default=12, help="Look-back window in months.")
    parser.add_argument("--start-date", type=str, default=None, help="Explicit start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", type=str, default=None, help="Explicit end date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbol list override.")
    parser.add_argument("--universe", type=Path, default=None, help="Universe file with one symbol per line.")
    parser.add_argument("--source-data-dir", type=Path, required=True, help="Existing store used to infer expirations.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Partitioned store output directory.")
    parser.add_argument(
        "--expiry-policy",
        choices=["same_week", "next_listed_weekly", "target_dte"],
        default="target_dte",
        help="Expiry selection policy used to infer OnclickMedia requests from the source store.",
    )
    parser.add_argument("--target-dte-min", type=int, default=7, help="Minimum DTE when --expiry-policy=target_dte.")
    parser.add_argument("--target-dte-max", type=int, default=14, help="Maximum DTE when --expiry-policy=target_dte.")
    parser.add_argument("--entry-only", action="store_true", help="Fetch only entry-date chains.")
    parser.add_argument("--exit-only", action="store_true", help="Fetch only exit-date chains.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent requests.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Per-request retry count.")
    parser.add_argument("--force", action="store_true", help="Overwrite touched partitions instead of merging.")
    parser.add_argument("--clear", action="store_true", help="Remove existing generated partitions before ingesting.")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
    args = parse_args()
    end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()
    start_date = date.fromisoformat(args.start_date) if args.start_date else end_date - timedelta(days=args.months * 30)
    if args.symbols:
        symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    else:
        symbols = _load_universe(args.universe)

    if args.clear:
        shutil.rmtree(partition_root(args.output_dir), ignore_errors=True)
        manifest_path(args.output_dir).unlink(missing_ok=True)

    include_entry = not args.exit_only
    include_exit = not args.entry_only
    tasks = build_tasks_from_existing_store(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        source_data_dir=args.source_data_dir,
        expiry_policy=args.expiry_policy,
        target_dte_min=max(args.target_dte_min, 0),
        target_dte_max=max(args.target_dte_max, 0),
        include_entry_dates=include_entry,
        include_exit_dates=include_exit,
    )
    client = OnclickMediaClient(timeout=args.timeout, retries=args.retries)
    manifest = ingest_onclick_options(
        tasks=tasks,
        output_dir=args.output_dir,
        client=client,
        workers=args.workers,
        force=args.force,
    )
    print(json.dumps(manifest["summary"], indent=2))
    print(f"Saved OnclickMedia coverage manifest -> {manifest_path(args.output_dir)}")
    print(f"Saved OnclickMedia partitioned store -> {partition_root(args.output_dir)}")


if __name__ == "__main__":
    main()
