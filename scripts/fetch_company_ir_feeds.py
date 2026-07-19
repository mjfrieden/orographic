from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.headline_intelligence import normalize_headlines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect approved company IR and press-release RSS/Atom feeds.")
    parser.add_argument("--feeds", type=Path, required=True, help="Allowlisted JSON feed configuration.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, default=None)
    parser.add_argument("--health-output", type=Path, required=True)
    parser.add_argument("--observatory-output", type=Path, default=None)
    return parser.parse_args()


def _text(node: ET.Element, *names: str) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def _read_feed(url: str, *, symbol: str, source: str, quality: float) -> list[dict[str, object]]:
    request = urllib.request.Request(url, headers={"User-Agent": "OrographicEventResearch/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        root = ET.fromstring(response.read())
    seen_at = datetime.now(UTC).isoformat()
    rows: list[dict[str, object]] = []
    for item in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = _text(item, "title", "{http://www.w3.org/2005/Atom}title")
        published = _text(item, "pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated")
        link = _text(item, "link", "{http://www.w3.org/2005/Atom}link")
        if not link:
            link_node = item.find("{http://www.w3.org/2005/Atom}link")
            link = str(link_node.get("href") or "") if link_node is not None else ""
        if title and published and link:
            rows.append({"source_event_id": link, "symbol": symbol, "published_at": published, "first_seen_at": seen_at, "headline": title, "url": link, "source_quality": quality, "feed_url": url, "feed_source": source})
    return rows


def main() -> None:
    args = parse_args()
    config = json.loads(args.feeds.read_text(encoding="utf-8"))
    feeds = config.get("feeds") if isinstance(config.get("feeds"), list) else []
    rows: list[dict[str, object]] = []
    errors: list[str] = []
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        symbol, url = str(feed.get("symbol") or "").upper().strip(), str(feed.get("url") or "").strip()
        if not symbol or not url.startswith("https://"):
            errors.append(f"invalid feed configuration for {symbol or 'unknown'}")
            continue
        try:
            rows.extend(_read_feed(url, symbol=symbol, source=str(feed.get("source") or "company_ir"), quality=float(feed.get("source_quality") or 0.9)))
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
    normalized, review = normalize_headlines(pd.DataFrame(rows), source="company_ir", default_source_quality=0.9)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(args.output, index=False)
    if args.review_queue and not review.empty:
        args.review_queue.parent.mkdir(parents=True, exist_ok=True)
        review.to_json(args.review_queue, orient="records", lines=True, mode="a")
    health = {"artifact": "company_ir_feed_health", "schema_version": 1, "status": "healthy" if rows and not errors else ("empty" if not feeds else "degraded"), "configured_feeds": len(feeds), "rows": len(normalized), "review_rows": len(review), "errors": errors[:20]}
    args.health_output.parent.mkdir(parents=True, exist_ok=True)
    args.health_output.write_text(json.dumps(health, indent=2), encoding="utf-8")
    if args.observatory_output and not normalized.empty:
        from engine.orographic.event_observatory import build_observatory, write_observatory, write_quality_report
        observatory, report = build_observatory([("company_ir", "news", args.output)], existing_path=args.observatory_output)
        write_observatory(observatory, args.observatory_output)
        write_quality_report(report, args.observatory_output.with_suffix(args.observatory_output.suffix + ".quality.json"))
    print(json.dumps(health, indent=2))


if __name__ == "__main__":
    main()
