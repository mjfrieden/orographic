from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_LIVE_BASE_URL = "https://api.tradier.com/v1"
DEFAULT_SANDBOX_BASE_URL = "https://sandbox.tradier.com/v1"
TRUE_VALUES = {"1", "true", "yes", "on"}
OPTION_SYMBOL_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def _env_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def _trim_trailing_slash(value: str) -> str:
    return value.rstrip("/")


def _as_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _is_option_symbol(symbol: str) -> bool:
    return bool(OPTION_SYMBOL_RE.match(str(symbol or "").strip().upper()))


def _quote_mark(quote: dict[str, Any] | None) -> tuple[float | None, str | None]:
    bid = _as_number((quote or {}).get("bid"))
    ask = _as_number((quote or {}).get("ask"))
    last = _as_number((quote or {}).get("last"))
    close = _as_number((quote or {}).get("close"))

    if bid is not None and bid > 0 and ask is not None and ask > 0:
        return round((bid + ask) / 2.0, 4), "mid"
    if last is not None and last > 0:
        return last, "last"
    if close is not None and close > 0:
        return close, "close"
    if bid is not None and bid > 0:
        return bid, "bid"
    if ask is not None and ask > 0:
        return ask, "ask"
    return None, None


def tradier_runtime_settings(env: dict[str, str] | None = None) -> dict[str, Any]:
    source = env or os.environ
    access_token = str(
        source.get("TRADIER_ACCESS_TOKEN") or source.get("OROGRAPHIC_TRADIER_ACCESS_TOKEN") or ""
    ).strip()
    account_id = str(
        source.get("TRADIER_ACCOUNT_ID") or source.get("OROGRAPHIC_TRADIER_ACCOUNT_ID") or ""
    ).strip()
    requested_base_url = str(
        source.get("TRADIER_BASE_URL") or source.get("OROGRAPHIC_TRADIER_BASE_URL") or ""
    ).strip()
    use_sandbox = _env_truthy(source.get("TRADIER_SANDBOX_MODE")) or "sandbox.tradier.com" in requested_base_url
    base_url = _trim_trailing_slash(
        requested_base_url or (DEFAULT_SANDBOX_BASE_URL if use_sandbox else DEFAULT_LIVE_BASE_URL)
    )
    return {
        "configured": bool(access_token and account_id),
        "access_token": access_token,
        "account_id": account_id,
        "base_url": base_url,
        "sandbox": use_sandbox,
    }


def _tradier_get_json(settings: dict[str, Any], path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.get("configured"):
        raise RuntimeError("Tradier is not configured.")
    url = f"{settings['base_url']}{path if path.startswith('/') else f'/{path}'}"
    if query:
        filtered = {key: value for key, value in query.items() if value not in {None, ""}}
        if filtered:
            url = f"{url}?{urlencode(filtered)}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {settings['access_token']}",
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_positions(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (payload or {}).get("positions", {}).get("position")
    if not raw:
        return []
    positions = raw if isinstance(raw, list) else [raw]
    normalized: list[dict[str, Any]] = []
    for position in positions:
        normalized.append(
            {
                "symbol": str(position.get("symbol") or "").strip().upper(),
                "quantity": _as_number(position.get("quantity")) or 0.0,
                "cost_basis": _as_number(position.get("cost_basis")),
                "current_value": _as_number(position.get("current_value")),
                "date_acquired": str(position.get("date_acquired") or ""),
            }
        )
    return normalized


def normalize_quotes(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = (payload or {}).get("quotes", {}).get("quote")
    if not raw:
        return {}
    quotes = raw if isinstance(raw, list) else [raw]
    normalized: dict[str, dict[str, Any]] = {}
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        normalized[symbol] = {
            "symbol": symbol,
            "bid": _as_number(quote.get("bid")),
            "ask": _as_number(quote.get("ask")),
            "last": _as_number(quote.get("last")),
            "close": _as_number(quote.get("close")),
        }
    return normalized


def enrich_positions(positions: list[dict[str, Any]], quotes_by_symbol: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    quotes = quotes_by_symbol or {}
    enriched: list[dict[str, Any]] = []
    for position in positions:
        row = dict(position)
        symbol = str(row.get("symbol") or "").strip().upper()
        current_value = _as_number(row.get("current_value"))
        if current_value is not None:
            row["current_value"] = round(current_value, 2)
            row["current_value_source"] = "broker"

        if _is_option_symbol(symbol):
            mark_price, mark_source = _quote_mark(quotes.get(symbol))
            if mark_price is not None:
                row["mark_price"] = mark_price
                row["mark_source"] = mark_source
                if row.get("current_value") is None:
                    quantity = _as_number(row.get("quantity")) or 0.0
                    row["current_value"] = round(quantity * 100.0 * mark_price, 2)
                    row["current_value_source"] = f"quote_{mark_source}"

        cost_basis = _as_number(row.get("cost_basis"))
        current_value = _as_number(row.get("current_value"))
        row["open_pl"] = (
            round(current_value - cost_basis, 2)
            if current_value is not None and cost_basis is not None
            else None
        )
        enriched.append(row)
    return enriched


def fetch_position_snapshot(
    *,
    env: dict[str, str] | None = None,
    run_generated_at_utc: str | None = None,
) -> dict[str, Any]:
    settings = tradier_runtime_settings(env)
    captured_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not settings["configured"]:
        return {
            "captured_at_utc": captured_at_utc,
            "run_generated_at_utc": run_generated_at_utc,
            "configured": False,
            "positions_count": 0,
            "positions": [],
            "status": "tradier_not_configured",
        }

    positions = normalize_positions(
        _tradier_get_json(settings, f"/accounts/{settings['account_id']}/positions")
    )
    option_symbols = [
        row["symbol"]
        for row in positions
        if _is_option_symbol(row["symbol"]) and row.get("current_value") is None
    ]
    quotes_by_symbol: dict[str, dict[str, Any]] = {}
    if option_symbols:
        quotes_by_symbol = normalize_quotes(
            _tradier_get_json(
                settings,
                "/markets/quotes",
                {"symbols": ",".join(option_symbols), "greeks": "false"},
            )
        )
    enriched = enrich_positions(positions, quotes_by_symbol)
    return {
        "captured_at_utc": captured_at_utc,
        "run_generated_at_utc": run_generated_at_utc,
        "configured": True,
        "positions_count": len(enriched),
        "positions": enriched,
        "status": "ok",
    }


def append_position_history(
    path: str | Path,
    snapshot: dict[str, Any],
    *,
    max_entries: int = 500,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {"updated_at_utc": snapshot.get("captured_at_utc"), "entries": []}
    if output.exists():
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"updated_at_utc": snapshot.get("captured_at_utc"), "entries": []}

    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = []
    entries.append(snapshot)
    payload["updated_at_utc"] = snapshot.get("captured_at_utc")
    payload["entries"] = entries[-max(int(max_entries), 1) :]
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
