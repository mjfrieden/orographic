from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError


DEFAULT_USER_AGENT = "OrographicEventResearch/1.0"
DEFAULT_ALIAS_PATH = Path(__file__).resolve().parents[1] / "engine" / "company_aliases.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch free GDELT company news and map explicit aliases to Orographic symbols."
    )
    parser.add_argument("--start-date", required=True, help="Inclusive start date in YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Inclusive end date in YYYY-MM-DD.")
    parser.add_argument("--universe-file", type=Path, default=Path("engine/sample_universe.txt"))
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIAS_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observatory-output", type=Path, default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-records-per-batch", type=int, default=50)
    parser.add_argument("--pause-seconds", type=float, default=2.0)
    parser.add_argument("--retry-base-seconds", type=float, default=8.0)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser.parse_args()


def load_aliases(path: Path, universe: set[str]) -> dict[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[str]] = {}
    for raw_symbol, raw_aliases in payload.items():
        symbol = str(raw_symbol).strip().upper()
        if symbol not in universe or not isinstance(raw_aliases, list):
            continue
        aliases = [str(alias).strip() for alias in raw_aliases if str(alias).strip()]
        if aliases:
            result[symbol] = aliases
    return result


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


def map_symbols(text: str, aliases: dict[str, list[str]]) -> list[str]:
    return [
        symbol
        for symbol, names in aliases.items()
        if any(_alias_pattern(name).search(text) for name in names)
    ]


def _chunks(items: list[tuple[str, str]], size: int) -> Iterable[list[tuple[str, str]]]:
    for index in range(0, len(items), max(size, 1)):
        yield items[index : index + max(size, 1)]


def _window(day: date) -> tuple[str, str]:
    start = datetime.combine(day, datetime.min.time())
    return start.strftime("%Y%m%d%H%M%S"), (start + timedelta(days=1)).strftime("%Y%m%d%H%M%S")


def fetch_batch(
    day: date,
    batch: list[tuple[str, str]],
    *,
    max_records: int,
    pause_seconds: float,
    retry_base_seconds: float,
    max_retries: int,
) -> list[dict[str, Any]]:
    query = "(" + " OR ".join(f'\"{alias}\"' for _, alias in batch) + ") sourcelang:english"
    startdatetime, enddatetime = _window(day)
    params = {
        "query": query,
        "mode": "ArtList",
        "maxrecords": str(max(1, min(max_records, 250))),
        "format": "json",
        "startdatetime": startdatetime,
        "enddatetime": enddatetime,
    }
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                articles = json.load(response).get("articles", [])
            first_seen_at = datetime.now(UTC).isoformat()
            time.sleep(max(pause_seconds, 0.0))
            return [dict(article, first_seen_at=first_seen_at, query=query) for article in articles]
        except HTTPError as exc:
            if exc.code != 429 or attempt >= max_retries:
                raise
            time.sleep(retry_base_seconds * (attempt + 1))
    return []


def _load_existing(path: Path) -> tuple[list[dict[str, str]], set[tuple[str, str]]]:
    if not path.exists():
        return [], set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows, {(row.get("symbol", ""), row.get("url", "")) for row in rows}


def main() -> None:
    args = _parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if end < start:
        raise ValueError("end-date must be on or after start-date")
    universe = {
        line.strip().upper()
        for line in args.universe_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    aliases = load_aliases(args.aliases, universe)
    # Query one distinctive company name per symbol. All aliases remain available for attribution.
    query_aliases = [(symbol, names[0]) for symbol, names in aliases.items()]
    fields = [
        "source_event_id", "symbol", "published_at", "first_seen_at", "headline",
        "domain", "sourcecountry", "language", "url", "query",
    ]
    rows, seen = _load_existing(args.output) if args.resume else ([], set())
    day = start
    while day <= end:
        for batch in _chunks(query_aliases, args.batch_size):
            try:
                articles = fetch_batch(
                    day,
                    batch,
                    max_records=args.max_records_per_batch,
                    pause_seconds=args.pause_seconds,
                    retry_base_seconds=args.retry_base_seconds,
                    max_retries=args.max_retries,
                )
            except Exception as exc:
                if not args.continue_on_error:
                    raise
                print(f"{day}: skipped batch after retries ({exc})")
                continue
            batch_symbols = {symbol for symbol, _ in batch}
            scoped_aliases = {symbol: aliases[symbol] for symbol in batch_symbols}
            for article in articles:
                headline = str(article.get("title") or "").strip()
                url = str(article.get("url") or "").strip()
                for symbol in map_symbols(headline, scoped_aliases):
                    key = (symbol, url)
                    if not headline or not url or key in seen:
                        continue
                    seen.add(key)
                    rows.append({
                        "source_event_id": url,
                        "symbol": symbol,
                        "published_at": article.get("seendate"),
                        "first_seen_at": article.get("first_seen_at"),
                        "headline": headline,
                        "domain": article.get("domain"),
                        "sourcecountry": article.get("sourcecountry"),
                        "language": article.get("language"),
                        "url": url,
                        "query": article.get("query"),
                    })
        day += timedelta(days=1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} ticker-mapped company-news rows -> {args.output}")

    if args.observatory_output:
        from engine.orographic.event_observatory import build_observatory, write_observatory, write_quality_report

        observatory, report = build_observatory(
            [("gdelt_company_news", "news", args.output)], existing_path=args.observatory_output
        )
        write_observatory(observatory, args.observatory_output)
        quality_path = args.observatory_output.with_suffix(args.observatory_output.suffix + ".quality.json")
        write_quality_report(report, quality_path)
        print(f"Merged company news -> {args.observatory_output} ({report.rows} total rows)")


if __name__ == "__main__":
    main()
