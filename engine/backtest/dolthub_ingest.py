"""
Ingest historical option chains from DoltHub into Orographic's local store.

The adapter intentionally writes the same partitioned parquet layout used by
the existing OptionsDX path so replay code stays provider-agnostic.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
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
from engine.backtest.options_store import (
    manifest_path,
    partition_root,
    write_partitioned_frames,
)
from engine.backtest.runner import _load_universe

log = logging.getLogger(__name__)

DEFAULT_OWNER = "post-no-preference"
DEFAULT_DATABASE = "options"
DEFAULT_REF = "master"
DEFAULT_BASE_URL = "https://www.dolthub.com"
DEFAULT_OUTPUT_DIR = Path(__file__).parents[1] / "data" / "options" / "dolthub"

DOLTHUB_COLUMNS = [
    "date",
    "act_symbol",
    "expiration",
    "strike",
    "call_put",
    "bid",
    "ask",
    "vol",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
]


@dataclass(frozen=True)
class DoltHubFetchResult:
    symbol: str
    quote_date: date
    rows: list[dict[str, Any]]
    status: str
    message: str = ""


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def standardize_dolthub_option_chain(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize DoltHub option_chain rows to Orographic's shared schema."""
    if frame.empty:
        return pd.DataFrame()

    renamed = frame.rename(
        columns={
            "date": "quote_date",
            "act_symbol": "underlying_symbol",
            "expiration": "expire_date",
            "call_put": "option_type",
            "vol": "implied_volatility",
        }
    ).copy()

    if "option_type" in renamed.columns:
        renamed["option_type"] = (
            renamed["option_type"]
            .astype(str)
            .str.strip()
            .str.upper()
            .map({"CALL": "C", "PUT": "P", "C": "C", "P": "P"})
        )

    for col in ["strike", "bid", "ask", "implied_volatility", "delta", "gamma", "theta", "vega", "rho"]:
        if col in renamed.columns:
            renamed[col] = pd.to_numeric(renamed[col], errors="coerce")

    renamed["open_interest"] = pd.NA
    renamed["trade_volume"] = pd.NA
    renamed["source"] = "dolthub:post-no-preference/options"

    keep = [
        "quote_date",
        "underlying_symbol",
        "expire_date",
        "strike",
        "option_type",
        "bid",
        "ask",
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


class DoltHubOptionsClient:
    def __init__(
        self,
        *,
        owner: str = DEFAULT_OWNER,
        database: str = DEFAULT_DATABASE,
        ref: str = DEFAULT_REF,
        base_url: str = DEFAULT_BASE_URL,
        token: str | None = None,
        timeout: float = 45.0,
        retries: int = 2,
        retry_sleep: float = 1.0,
    ) -> None:
        self.owner = owner
        self.database = database
        self.ref = ref
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.retry_sleep = retry_sleep

    def query(self, sql: str) -> dict[str, Any]:
        path = f"/api/v1alpha1/{self.owner}/{self.database}/{self.ref}"
        url = f"{self.base_url}{path}?{urllib.parse.urlencode({'q': sql})}"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["authorization"] = f"token {self.token}"

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(self.retry_sleep * (attempt + 1))
        raise RuntimeError(f"DoltHub query failed after {self.retries + 1} attempt(s): {last_error}")

    def fetch_chain(self, symbol: str, quote_date: date) -> DoltHubFetchResult:
        symbol = symbol.upper()
        select_cols = ", ".join(DOLTHUB_COLUMNS)
        sql = (
            f"select {select_cols} from option_chain "
            f"where act_symbol = {_sql_literal(symbol)} "
            f"and date = {_sql_literal(quote_date.isoformat())}"
        )
        payload = self.query(sql)
        status = str(payload.get("query_execution_status", ""))
        message = str(payload.get("query_execution_message", ""))
        if status not in {"Success", "RowLimit"}:
            return DoltHubFetchResult(symbol, quote_date, [], status, message)
        return DoltHubFetchResult(symbol, quote_date, list(payload.get("rows", [])), status, message)


def target_quote_dates(start_date: date, end_date: date) -> list[date]:
    dates: set[date] = set()
    for monday in mondays_in_range(start_date, end_date):
        friday = friday_of_week(monday)
        if start_date <= monday <= end_date:
            dates.add(monday)
        if start_date <= friday <= end_date:
            dates.add(friday)
    return sorted(dates)


def ingest_dolthub_options(
    *,
    symbols: list[str],
    quote_dates: list[date],
    output_dir: Path,
    client: DoltHubOptionsClient,
    workers: int = 6,
    force: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks = [(symbol.upper(), quote_date) for quote_date in quote_dates for symbol in symbols]
    frames: list[pd.DataFrame] = []
    statuses: dict[str, int] = {}
    failed: list[dict[str, str]] = []
    row_limit_results: list[dict[str, str]] = []

    log.info("Fetching %d DoltHub symbol/date chain(s)", len(tasks))
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as executor:
        futures = {
            executor.submit(client.fetch_chain, symbol, quote_date): (symbol, quote_date)
            for symbol, quote_date in tasks
        }
        for index, future in enumerate(as_completed(futures), start=1):
            symbol, quote_date = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                statuses["exception"] = statuses.get("exception", 0) + 1
                failed.append({"symbol": symbol, "quote_date": quote_date.isoformat(), "message": str(exc)})
                continue

            statuses[result.status] = statuses.get(result.status, 0) + 1
            if result.status == "RowLimit":
                row_limit_results.append({"symbol": symbol, "quote_date": quote_date.isoformat()})
            if result.rows:
                frames.append(standardize_dolthub_option_chain(pd.DataFrame(result.rows)))
            elif result.status not in {"Success", "RowLimit"}:
                failed.append(
                    {
                        "symbol": symbol,
                        "quote_date": quote_date.isoformat(),
                        "message": result.message or result.status,
                    }
                )

            if index % 100 == 0 or index == len(tasks):
                log.info("Fetched %d/%d chain request(s)", index, len(tasks))

    metadata = {
        "provider": "dolthub",
        "owner": client.owner,
        "database": client.database,
        "ref": client.ref,
        "requested_symbols": sorted(set(symbols)),
        "requested_quote_dates": [row.isoformat() for row in quote_dates],
        "request_count": len(tasks),
        "status_counts": statuses,
        "failed_requests": failed[:100],
        "failed_request_count": len(failed),
        "row_limit_results": row_limit_results[:100],
        "row_limit_count": len(row_limit_results),
    }
    manifest = write_partitioned_frames(
        output_dir,
        frames,
        source_files=[f"dolthub:{client.owner}/{client.database}@{client.ref}"],
        force=force,
        metadata=metadata,
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest DoltHub option chains into partitioned parquet.")
    parser.add_argument("--months", type=int, default=12, help="Look-back window in months.")
    parser.add_argument("--start-date", type=str, default=None, help="Explicit start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", type=str, default=None, help="Explicit end date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbol list override.")
    parser.add_argument("--universe", type=Path, default=None, help="Universe file with one symbol per line.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Partitioned store output directory.")
    parser.add_argument("--owner", type=str, default=DEFAULT_OWNER, help="DoltHub owner.")
    parser.add_argument("--database", type=str, default=DEFAULT_DATABASE, help="DoltHub database.")
    parser.add_argument("--ref", type=str, default=DEFAULT_REF, help="DoltHub branch/ref.")
    parser.add_argument("--workers", type=int, default=6, help="Concurrent DoltHub requests.")
    parser.add_argument("--timeout", type=float, default=45.0, help="Per-request timeout in seconds.")
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
    quote_dates = target_quote_dates(start_date, end_date)

    if args.clear:
        shutil.rmtree(partition_root(args.output_dir), ignore_errors=True)
        manifest_path(args.output_dir).unlink(missing_ok=True)

    client = DoltHubOptionsClient(
        owner=args.owner,
        database=args.database,
        ref=args.ref,
        token=os.environ.get("DOLTHUB_TOKEN"),
        timeout=args.timeout,
        retries=args.retries,
    )
    manifest = ingest_dolthub_options(
        symbols=symbols,
        quote_dates=quote_dates,
        output_dir=args.output_dir,
        client=client,
        workers=args.workers,
        force=args.force,
    )
    print(json.dumps(manifest["summary"], indent=2))
    print(f"Saved DoltHub coverage manifest -> {manifest_path(args.output_dir)}")
    print(f"Saved DoltHub partitioned store -> {partition_root(args.output_dir)}")


if __name__ == "__main__":
    main()
