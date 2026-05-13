from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .positions import DEFAULT_LIVE_BASE_URL, DEFAULT_SANDBOX_BASE_URL, _as_number, _env_truthy, _quote_mark, normalize_quotes


MARKET_TZ = ZoneInfo("America/Chicago")
MARK_CLOSE_BUFFER = time(15, 5)


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _market_close_for(local_day: datetime) -> datetime:
    return datetime.combine(local_day.date(), MARK_CLOSE_BUFFER, tzinfo=MARKET_TZ).astimezone(timezone.utc)


def _next_weekday(local_day: datetime) -> datetime:
    candidate = local_day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _friday_of_week(local_day: datetime) -> datetime:
    return local_day + timedelta(days=(4 - local_day.weekday()) % 7)


def due_fixed_exit_windows(run_generated_at_utc: str, now_utc: datetime | None = None) -> dict[str, bool]:
    run_dt = _parse_dt(run_generated_at_utc)
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if run_dt is None:
        return {"one_hour": False, "end_of_day": False, "next_day_close": False, "friday_close": False}

    local_run = run_dt.astimezone(MARKET_TZ)
    one_hour_due = run_dt + timedelta(hours=1)
    end_of_day_due = _market_close_for(local_run)
    if run_dt > end_of_day_due:
        end_of_day_due = _market_close_for(_next_weekday(local_run))
    next_day_close_due = _market_close_for(_next_weekday(local_run))
    friday_close_due = _market_close_for(_friday_of_week(local_run))
    if run_dt > friday_close_due:
        friday_close_due = _market_close_for(_friday_of_week(local_run + timedelta(days=7)))

    return {
        "one_hour": now >= one_hour_due,
        "end_of_day": now >= end_of_day_due,
        "next_day_close": now >= next_day_close_due,
        "friday_close": now >= friday_close_due,
    }


def _entry_mark(pick: dict[str, Any]) -> float | None:
    quote = pick.get("emission_quote") if isinstance(pick.get("emission_quote"), dict) else {}
    for key in ("mid", "last", "ask", "bid"):
        value = _as_number(quote.get(key))
        if value is not None and value > 0:
            return value
    cost = _as_number(quote.get("contract_cost"))
    if cost is not None and cost > 0:
        return cost / 100.0
    return None


def _mark_payload(quote: dict[str, Any], *, captured_at_utc: str, entry_mark: float | None) -> dict[str, Any]:
    mark, source = _quote_mark(quote)
    pnl_pct = round(mark / entry_mark - 1.0, 4) if mark is not None and entry_mark and entry_mark > 0 else None
    return {
        "captured_at_utc": captured_at_utc,
        "mark": mark,
        "mark_source": source,
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "last": quote.get("last"),
        "close": quote.get("close"),
        "pnl_pct_from_emission": pnl_pct,
    }


def _update_path_rules(outcomes: dict[str, Any]) -> None:
    fixed_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
    observed = [
        (name, mark)
        for name, mark in fixed_marks.items()
        if isinstance(mark, dict) and isinstance(mark.get("pnl_pct_from_emission"), (int, float))
    ]
    path_rules = outcomes.setdefault("path_rules", {})
    if not observed:
        return
    returns = [float(mark["pnl_pct_from_emission"]) for _, mark in observed]
    path_rules["max_favorable_excursion_pct"] = round(max(returns), 4)
    path_rules["max_adverse_excursion_pct"] = round(min(returns), 4)
    if path_rules.get("take_profit_40_pct_before_stop_50_pct") is None:
        path_rules["take_profit_40_pct_before_stop_50_pct"] = any(value >= 0.40 for value in returns)
    if path_rules.get("take_profit_25_pct_before_stop_50_pct") is None:
        path_rules["take_profit_25_pct_before_stop_50_pct"] = any(value >= 0.25 for value in returns)
    if path_rules.get("first_hit") is None:
        for name, mark in observed:
            value = float(mark["pnl_pct_from_emission"])
            if value >= 0.40:
                path_rules["first_hit"] = {"window": name, "rule": "take_profit_40_pct_fixed_mark_proxy"}
                break
            if value <= -0.50:
                path_rules["first_hit"] = {"window": name, "rule": "stop_50_pct_fixed_mark_proxy"}
                break


def mark_prospective_ledger(
    ledger: dict[str, Any],
    quotes_by_symbol: dict[str, dict[str, Any]],
    *,
    now_utc: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    captured_at = now.replace(microsecond=0).isoformat()
    updated = json.loads(json.dumps(ledger))
    entries = updated.get("entries") if isinstance(updated.get("entries"), list) else []
    stats = {"entries_seen": len(entries), "picks_seen": 0, "marks_written": 0, "quotes_missing": 0}

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        due = due_fixed_exit_windows(str(entry.get("run_generated_at_utc") or ""), now)
        picks = entry.get("picks") if isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            stats["picks_seen"] += 1
            symbol = str(pick.get("contract_symbol") or "").strip().upper()
            quote = quotes_by_symbol.get(symbol)
            if quote is None:
                if any(due.values()):
                    stats["quotes_missing"] += 1
                continue
            outcomes = pick.setdefault("outcomes", {})
            fixed_marks = outcomes.setdefault("fixed_exit_marks", {})
            entry_mark = _entry_mark(pick)
            for window_name, is_due in due.items():
                if not is_due or fixed_marks.get(window_name) is not None:
                    continue
                fixed_marks[window_name] = _mark_payload(quote, captured_at_utc=captured_at, entry_mark=entry_mark)
                stats["marks_written"] += 1
            _update_path_rules(outcomes)
            if all(fixed_marks.get(name) is not None for name in ("one_hour", "end_of_day", "next_day_close", "friday_close")):
                outcomes["status"] = "complete"
            elif any(fixed_marks.get(name) is not None for name in ("one_hour", "end_of_day", "next_day_close", "friday_close")):
                outcomes["status"] = "partial"
            quote_verification = outcomes.setdefault("quote_verification", {})
            quote_verification["outcome_quotes_captured"] = outcomes.get("status") in {"partial", "complete"}

    updated["updated_at_utc"] = captured_at
    updated["last_mark_summary"] = stats
    return updated, stats


def fetch_tradier_quotes(symbols: list[str], *, env: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    source = env or os.environ
    token = str(source.get("TRADIER_ACCESS_TOKEN") or source.get("OROGRAPHIC_TRADIER_ACCESS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Tradier access token is not configured.")
    requested_base_url = str(source.get("TRADIER_BASE_URL") or source.get("OROGRAPHIC_TRADIER_BASE_URL") or "").strip()
    use_sandbox = _env_truthy(source.get("TRADIER_SANDBOX_MODE")) or "sandbox.tradier.com" in requested_base_url
    base_url = (requested_base_url or (DEFAULT_SANDBOX_BASE_URL if use_sandbox else DEFAULT_LIVE_BASE_URL)).rstrip("/")
    cleaned = [symbol.strip().upper() for symbol in symbols if str(symbol).strip()]
    if not cleaned:
        return {}
    url = f"{base_url}/markets/quotes?{urlencode({'symbols': ','.join(cleaned), 'greeks': 'false'})}"
    request = Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return normalize_quotes(payload)


def mark_prospective_ledger_file(path: str | Path, *, max_symbols: int = 500) -> tuple[Path, dict[str, int]]:
    ledger_path = Path(path)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    symbols: list[str] = []
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict):
            continue
        for pick in entry.get("picks", []):
            if isinstance(pick, dict) and str(pick.get("contract_symbol") or "").strip():
                symbols.append(str(pick["contract_symbol"]).strip().upper())
    unique_symbols = list(dict.fromkeys(symbols))[:max(max_symbols, 1)]
    quotes = fetch_tradier_quotes(unique_symbols)
    updated, stats = mark_prospective_ledger(ledger, quotes)
    ledger_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    return ledger_path, stats
