from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


CIK_LOOKUP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
DEFAULT_USER_AGENT = "Mozilla/5.0 Orographic-SEC-Research/1.0 contact-local-research@example.com"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch SEC submission history for a symbol universe and save filing rows as CSV."
    )
    parser.add_argument("--start-date", type=str, required=True, help="Inclusive start date in YYYY-MM-DD.")
    parser.add_argument("--end-date", type=str, required=True, help="Inclusive end date in YYYY-MM-DD.")
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated symbol list override. Defaults to engine/sample_universe.txt.",
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=Path("engine/sample_universe.txt"),
        help="Universe file with one symbol per line.",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output CSV path.")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=0.2,
        help="Pause between SEC requests.",
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default=DEFAULT_USER_AGENT,
        help="User-Agent header sent to SEC endpoints.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Skip symbols that fail instead of aborting.",
    )
    return parser.parse_args()


def _load_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return [token.strip().upper() for token in args.symbols.split(",") if token.strip()]
    return [
        line.strip().upper()
        for line in args.universe.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _normalize_symbol(value: object) -> str:
    text = str(value or "").upper().strip()
    return "".join(ch for ch in text if ch.isalnum())


def _load_json(url: str, *, user_agent: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _load_cik_mapping(*, user_agent: str) -> dict[str, dict[str, Any]]:
    payload = _load_json(CIK_LOOKUP_URL, user_agent=user_agent)
    mapping: dict[str, dict[str, Any]] = {}
    for item in payload.values():
        ticker = str(item.get("ticker") or "").upper().strip()
        normalized = _normalize_symbol(ticker)
        if not normalized:
            continue
        mapping[normalized] = {
            "ticker": ticker,
            "title": item.get("title"),
            "cik": f"{int(item.get('cik_str')):010d}",
        }
    return mapping


def _iter_recent_rows(
    payload: dict[str, Any],
    *,
    symbol: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    acceptance_datetimes = recent.get("acceptanceDateTime", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])
    primary_descriptions = recent.get("primaryDocDescription", [])
    is_xbrl = recent.get("isXBRL", [])
    is_inline_xbrl = recent.get("isInlineXBRL", [])

    rows: list[dict[str, Any]] = []
    count = min(
        len(forms),
        len(filing_dates),
        len(acceptance_datetimes),
        len(accession_numbers),
        len(primary_documents),
        len(primary_descriptions),
        len(is_xbrl),
        len(is_inline_xbrl),
    )
    cik = str(payload.get("cik") or "")
    company_name = str(payload.get("name") or "")
    sec_tickers = ",".join(str(token).upper() for token in payload.get("tickers", []) if str(token).strip())
    exchanges = ",".join(str(token) for token in payload.get("exchanges", []) if str(token).strip())
    for idx in range(count):
        filing_date = str(filing_dates[idx] or "")
        if not filing_date:
            continue
        filed = date.fromisoformat(filing_date)
        if filed < start_date or filed > end_date:
            continue
        rows.append(
            {
                "symbol": symbol,
                "sec_tickers": sec_tickers,
                "company_name": company_name,
                "cik": cik,
                "form": str(forms[idx] or ""),
                "filing_date": filing_date,
                "acceptance_datetime": str(acceptance_datetimes[idx] or ""),
                "accession_number": str(accession_numbers[idx] or ""),
                "primary_document": str(primary_documents[idx] or ""),
                "primary_doc_description": str(primary_descriptions[idx] or ""),
                "is_xbrl": int(is_xbrl[idx] or 0),
                "is_inline_xbrl": int(is_inline_xbrl[idx] or 0),
                "exchanges": exchanges,
            }
        )
    return rows


def main() -> None:
    args = _parse_args()
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if end_date < start_date:
        raise ValueError("end-date must be on or after start-date")

    symbols = _load_symbols(args)
    cik_mapping = _load_cik_mapping(user_agent=args.user_agent)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        entry = cik_mapping.get(normalized)
        if entry is None:
            skipped.append(symbol)
            continue
        url = SUBMISSIONS_URL_TEMPLATE.format(cik=entry["cik"])
        try:
            payload = _load_json(url, user_agent=args.user_agent)
            rows = _iter_recent_rows(
                payload,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
            all_rows.extend(rows)
            print(f"{symbol}: kept {len(rows)} filing rows")
        except Exception as exc:
            if not args.continue_on_error:
                raise
            skipped.append(f"{symbol} ({exc})")
            print(f"{symbol}: skipped ({exc})")
        time.sleep(max(args.pause_seconds, 0.0))

    fieldnames = [
        "symbol",
        "sec_tickers",
        "company_name",
        "cik",
        "form",
        "filing_date",
        "acceptance_datetime",
        "accession_number",
        "primary_document",
        "primary_doc_description",
        "is_xbrl",
        "is_inline_xbrl",
        "exchanges",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Saved {len(all_rows)} filing rows -> {args.output}")
    if skipped:
        print(f"Skipped {len(skipped)} symbols")
        for item in skipped[:20]:
            print(f"  - {item}")


if __name__ == "__main__":
    main()
