from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError


DEFAULT_QUERY = (
    "(war OR attack OR sanction OR ceasefire OR oil OR opec OR diplomatic "
    "OR missile OR protest OR military OR conflict OR tariff OR embargo)"
)
DEFAULT_USER_AGENT = "OrographicEventResearch/1.0"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch GDELT article rows into a local raw CSV for MIRAI-style macro overlay features."
    )
    parser.add_argument("--start-date", type=str, required=True, help="Inclusive start date in YYYY-MM-DD.")
    parser.add_argument("--end-date", type=str, required=True, help="Inclusive end date in YYYY-MM-DD.")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output CSV path for raw GDELT article rows.",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=DEFAULT_QUERY,
        help="GDELT Doc API query string.",
    )
    parser.add_argument(
        "--max-records-per-day",
        type=int,
        default=25,
        help="Maximum number of articles to request per day window.",
    )
    parser.add_argument(
        "--mondays-only",
        action="store_true",
        help="Only fetch Monday windows inside the requested date range.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=2.0,
        help="Pause between successful requests.",
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=8.0,
        help="Base wait before retrying HTTP 429 responses.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=4,
        help="Maximum retries per day window on HTTP 429.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Preserve and deduplicate rows from an existing output file.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Skip day windows that still fail after retries instead of aborting the full run.",
    )
    return parser.parse_args()


def _iter_days(start: date, end: date, *, mondays_only: bool) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if not mondays_only or current.weekday() == 0:
            days.append(current)
        current += timedelta(days=1)
    return days


def _window_strings(day: date) -> tuple[str, str]:
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    return start.strftime("%Y%m%d%H%M%S"), end.strftime("%Y%m%d%H%M%S")


def _fetch_articles_for_day(
    day: date,
    *,
    query: str,
    max_records_per_day: int,
    pause_seconds: float,
    retry_base_seconds: float,
    max_retries: int,
) -> list[dict[str, Any]]:
    startdatetime, enddatetime = _window_strings(day)
    params = {
        "query": query,
        "mode": "ArtList",
        "maxrecords": str(max(max_records_per_day, 1)),
        "format": "json",
        "startdatetime": startdatetime,
        "enddatetime": enddatetime,
    }
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            articles = payload.get("articles", [])
            time.sleep(max(pause_seconds, 0.0))
            return [
                {
                    "date": article.get("seendate"),
                    "headline": article.get("title"),
                    "domain": article.get("domain"),
                    "sourcecountry": article.get("sourcecountry"),
                    "language": article.get("language"),
                    "url": article.get("url"),
                    "query_date": day.isoformat(),
                    "query": query,
                }
                for article in articles
            ]
        except HTTPError as exc:
            if exc.code != 429 or attempt >= max_retries:
                raise
            wait_seconds = retry_base_seconds * (attempt + 1)
            print(f"{day.isoformat()}: HTTP 429, retrying in {wait_seconds:.1f}s")
            time.sleep(wait_seconds)
    return []


def _load_existing_rows(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.exists():
        return [], set()
    rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            url = str(row.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            rows.append(row)
    return rows, seen_urls


def main() -> None:
    args = _parse_args()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise ValueError("end-date must be on or after start-date")

    days = _iter_days(start, end, mondays_only=bool(args.mondays_only))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "headline", "domain", "sourcecountry", "language", "url", "query_date", "query"]

    existing_rows, seen_urls = _load_existing_rows(args.output) if args.resume else ([], set())
    rows = existing_rows[:]

    print(f"Fetching {len(days)} day windows from {start.isoformat()} to {end.isoformat()}")
    print(f"Query: {args.query}")
    for day in days:
        before = len(rows)
        try:
            fetched = _fetch_articles_for_day(
                day,
                query=args.query,
                max_records_per_day=args.max_records_per_day,
                pause_seconds=args.pause_seconds,
                retry_base_seconds=args.retry_base_seconds,
                max_retries=args.max_retries,
            )
        except Exception as exc:
            if not args.continue_on_error:
                raise
            print(f"{day.isoformat()}: skipped after retries ({exc})")
            continue
        for row in fetched:
            url = str(row.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            rows.append(row)
        print(
            f"{day.isoformat()}: fetched {len(fetched)} rows, kept {len(rows) - before} new rows "
            f"({len(rows)} cumulative)"
        )
        with args.output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows -> {args.output}")


if __name__ == "__main__":
    main()
