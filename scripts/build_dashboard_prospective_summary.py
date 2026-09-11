from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any


DEFAULT_INPUT = Path("web/data/diagnostics/prospective_pick_ledger.json")
DEFAULT_OUTPUT = Path("web/data/diagnostics/prospective_dashboard_summary_latest.json")
DEFAULT_RECENT_ENTRIES = 18
DEFAULT_MIN_CALENDAR_DAYS = 7
MAX_COMPACT_ENTRIES = 24


def _number(value: Any) -> float | None:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return None
    return rendered


def _return_values(pick: dict[str, Any]) -> list[float]:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    fixed = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
    values: list[float] = []
    for mark in fixed.values():
        if not isinstance(mark, dict):
            continue
        value = _number(mark.get("pnl_pct_from_emission"))
        if value is not None:
            values.append(value)
    return values


def summarize_picks(picks: list[dict[str, Any]]) -> dict[str, Any]:
    marked = [pick for pick in picks if _return_values(pick)]
    best = [max(_return_values(pick)) for pick in marked]
    worst = [min(_return_values(pick)) for pick in marked]
    latest_run = max(
        (str(pick.get("run_generated_at_utc")) for pick in picks if pick.get("run_generated_at_utc")),
        default=None,
    )
    def outcomes(pick: dict[str, Any]) -> dict[str, Any]:
        return pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}

    def path_rules(pick: dict[str, Any]) -> dict[str, Any]:
        rendered = outcomes(pick).get("path_rules")
        return rendered if isinstance(rendered, dict) else {}

    def first_hit(pick: dict[str, Any]) -> dict[str, Any]:
        rendered = path_rules(pick).get("first_hit")
        return rendered if isinstance(rendered, dict) else {}

    return {
        "picks": len(picks),
        "marked": len(marked),
        "complete": sum(1 for pick in picks if outcomes(pick).get("status") == "complete"),
        "pending": sum(1 for pick in picks if outcomes(pick).get("status", "pending") == "pending"),
        "live": sum(1 for pick in picks if pick.get("lane") == "live"),
        "shadow": sum(1 for pick in picks if pick.get("lane") == "shadow"),
        "take_profit_hits": sum(
            1
            for pick in marked
            if path_rules(pick).get("take_profit_40_pct_before_stop_50_pct") is True
        ),
        "stop_hits": sum(
            1
            for pick in marked
            if "stop_50" in str(first_hit(pick).get("rule", ""))
        ),
        "latest_run": latest_run,
        "avg_best": sum(best) / len(best) if best else None,
        "avg_worst": sum(worst) / len(worst) if worst else None,
    }


def _compact_scores(pick: dict[str, Any]) -> dict[str, float]:
    scores = pick.get("scores") if isinstance(pick.get("scores"), dict) else {}
    compact: dict[str, float] = {}
    for key in (
        "final_candidate_score",
        "expected_tail_utility",
        "forge_score",
        "learned_rank_score",
        "prob_big_win",
    ):
        value = _number(scores.get(key) if key in scores else pick.get(key))
        if value is not None:
            compact[key] = value
    return compact


def _compact_trajectory(pick: dict[str, Any]) -> dict[str, Any]:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    raw = outcomes.get("trajectory_marks") if isinstance(outcomes.get("trajectory_marks"), list) else []
    quote = pick.get("emission_quote") if isinstance(pick.get("emission_quote"), dict) else {}
    ask = _number(quote.get("ask"))
    first_hit = None
    valid = 0
    for mark in raw:
        if not isinstance(mark, dict):
            continue
        pnl = _number(mark.get("pnl_pct_from_emission"))
        if pnl is None and ask is not None and ask > 0:
            bid = _number(mark.get("bid"))
            if bid is not None and bid >= 0:
                pnl = bid / ask - 1.0
        if pnl is None:
            continue
        valid += 1
        if first_hit is None and (pnl >= 0.25 or pnl <= -0.50):
            first_hit = {
                "captured_at_utc": mark.get("captured_at_utc"),
                "bid": mark.get("bid"),
                "pnl_pct_from_emission": pnl,
                "event": "target_25_bid" if pnl >= 0.25 else "stop_50_bid",
            }
    return {"mark_count": valid, "first_hit": first_hit}


def _compact_pick(pick: dict[str, Any], entry_run: Any) -> dict[str, Any]:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    fixed = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
    compact_marks = {
        name: {"pnl_pct_from_emission": mark.get("pnl_pct_from_emission")}
        for name, mark in fixed.items()
        if isinstance(mark, dict) and _number(mark.get("pnl_pct_from_emission")) is not None
    }
    compact: dict[str, Any] = {
        "run_generated_at_utc": pick.get("run_generated_at_utc") or entry_run,
        "lane": pick.get("lane"),
        "symbol": pick.get("symbol"),
        "option_type": _compact_option_type(pick),
        "contract_symbol": pick.get("contract_symbol"),
        "emission_quote": pick.get("emission_quote") if isinstance(pick.get("emission_quote"), dict) else {},
        "outcomes": {
            "status": outcomes.get("status", "pending"),
            "fixed_exit_marks": compact_marks,
            "path_rules": outcomes.get("path_rules") if isinstance(outcomes.get("path_rules"), dict) else {},
            "trajectory_overlay": _compact_trajectory(pick),
        },
    }
    scores = _compact_scores(pick)
    if scores:
        compact["scores"] = scores
    return compact


def _compact_option_type(pick: dict[str, Any]) -> str | None:
    explicit = pick.get("option_type")
    if explicit in {"call", "put"}:
        return explicit
    contract = str(pick.get("contract_symbol") or "").upper()
    match = re.search(r"\d{6}([CP])\d+$", contract)
    if match:
        return "call" if match.group(1) == "C" else "put"
    return explicit if isinstance(explicit, str) and explicit else None


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def select_recent_entries(
    entries: list[Any],
    *,
    recent_entries: int = DEFAULT_RECENT_ENTRIES,
    min_calendar_days: int = DEFAULT_MIN_CALENDAR_DAYS,
    max_entries: int = MAX_COMPACT_ENTRIES,
) -> list[Any]:
    """Keep a full calendar week of scans, not just the last handful of jobs."""
    valid = [entry for entry in entries if isinstance(entry, dict)]
    if not valid:
        return []
    floor = max(int(recent_entries), 1)
    cap = max(int(max_entries), floor)
    last = _parse_dt(valid[-1].get("run_generated_at_utc"))
    if last is None:
        return valid[-floor:]
    cutoff = last - timedelta(days=max(int(min_calendar_days), 0))
    if int(min_calendar_days) <= 0:
        return valid[-floor:]
    weekish = [
        entry
        for entry in valid
        if (_parse_dt(entry.get("run_generated_at_utc")) or last) >= cutoff
    ]
    if len(weekish) >= floor:
        return weekish[-cap:]
    return valid[-floor:]


def build_dashboard_summary(
    ledger: dict[str, Any],
    *,
    recent_entries: int = DEFAULT_RECENT_ENTRIES,
    min_calendar_days: int = DEFAULT_MIN_CALENDAR_DAYS,
) -> dict[str, Any]:
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    all_picks: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_run = entry.get("run_generated_at_utc")
        for pick in entry.get("picks", []):
            if isinstance(pick, dict):
                rendered = dict(pick)
                rendered["run_generated_at_utc"] = pick.get("run_generated_at_utc") or entry_run
                all_picks.append(rendered)

    compact_entries = []
    for entry in select_recent_entries(
        entries,
        recent_entries=recent_entries,
        min_calendar_days=min_calendar_days,
    ):
        if not isinstance(entry, dict):
            continue
        compact_entries.append(
            {
                "run_generated_at_utc": entry.get("run_generated_at_utc"),
                "picks": [
                    _compact_pick(pick, entry.get("run_generated_at_utc"))
                    for pick in entry.get("picks", [])
                    if isinstance(pick, dict)
                ],
            }
        )

    return {
        "artifact": "prospective_dashboard_summary",
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "updated_at_utc": ledger.get("updated_at_utc"),
        "source_artifact": ledger.get("artifact", "prospective_pick_ledger"),
        "source_entry_count": len(entries),
        "dashboard_summary": {"runs": len(entries), **summarize_picks(all_picks)},
        "aggregate": ledger.get("aggregate") if isinstance(ledger.get("aggregate"), dict) else {},
        "outcome_policy": ledger.get("outcome_policy") if isinstance(ledger.get("outcome_policy"), dict) else {},
        "last_mark_summary": ledger.get("last_mark_summary") if isinstance(ledger.get("last_mark_summary"), dict) else {},
        "entries": compact_entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the compact prospective artifact published to Cloudflare Pages.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--recent-entries", type=int, default=DEFAULT_RECENT_ENTRIES)
    parser.add_argument("--min-calendar-days", type=int, default=DEFAULT_MIN_CALENDAR_DAYS)
    args = parser.parse_args()

    ledger = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict):
        raise SystemExit(f"Expected a JSON object in {args.input}")
    rendered = build_dashboard_summary(
        ledger,
        recent_entries=args.recent_entries,
        min_calendar_days=args.min_calendar_days,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.output} with {len(rendered['entries'])} recent entries "
        f"and {rendered['dashboard_summary']['picks']} aggregate picks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
