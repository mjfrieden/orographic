from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .market_data import option_chain, option_expiries


@dataclass(frozen=True)
class ArchiveResult:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]


def _safe_numeric(series: object) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _normalize_leg(
    frame: pd.DataFrame,
    *,
    symbol: str,
    quote_date: date,
    run_started_at_utc: str,
    expiry: str,
    option_type: str,
    provider: str,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    raw_last_trade = frame.get("lastTradeDate")
    last_trade = (
        pd.to_datetime(raw_last_trade, errors="coerce", utc=True)
        if raw_last_trade is not None
        else pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    )
    captured_at = pd.Timestamp(run_started_at_utc)
    if captured_at.tzinfo is None:
        captured_at = captured_at.tz_localize("UTC")
    else:
        captured_at = captured_at.tz_convert("UTC")
    normalized = pd.DataFrame(
        {
            "quote_date": quote_date,
            "run_started_at_utc": run_started_at_utc,
            "underlying_symbol": symbol.upper(),
            "expire_date": pd.to_datetime(expiry, errors="coerce").date(),
            "option_type": option_type,
            "contract_symbol": frame.get("contractSymbol", ""),
            "strike": _safe_numeric(frame.get("strike")),
            "bid": _safe_numeric(frame.get("bid")),
            "ask": _safe_numeric(frame.get("ask")),
            "last": _safe_numeric(frame.get("lastPrice")),
            "chain_snapshot_at_utc": run_started_at_utc,
            "last_trade_at_utc": last_trade,
            "last_trade_age_seconds": (captured_at - last_trade).dt.total_seconds().clip(lower=0),
            "implied_volatility": _safe_numeric(frame.get("impliedVolatility")),
            "open_interest": _safe_numeric(frame.get("openInterest")),
            "volume": _safe_numeric(frame.get("volume")),
            "source": frame.get("dataSource", provider),
        }
    )
    normalized = normalized.dropna(subset=["strike"])
    normalized = normalized[(normalized["bid"].fillna(0) > 0) | (normalized["ask"].fillna(0) > 0)]
    normalized["quote_mid"] = (normalized["bid"] + normalized["ask"]) / 2.0
    normalized["quote_spread_dollars"] = normalized["ask"] - normalized["bid"]
    normalized["quote_spread_pct"] = normalized["quote_spread_dollars"] / normalized["quote_mid"].where(
        normalized["quote_mid"] > 0
    )
    normalized["has_two_sided_quote"] = (normalized["bid"] > 0) & (normalized["ask"] > 0)
    normalized["has_valid_iv"] = normalized["implied_volatility"].between(0.03, 5.0)
    return normalized


def _run_time_partition(run_started_at_utc: str) -> str:
    parsed = datetime.fromisoformat(run_started_at_utc.replace("Z", "+00:00")).astimezone(UTC)
    return parsed.strftime("%H%M%S")


def archive_live_option_chains(
    symbols: list[str],
    *,
    output_dir: str | Path = "engine/data/live_options_archive",
    min_dte: int = 1,
    max_dte: int = 45,
    max_expiries_per_symbol: int = 6,
    provider: str = "configured_market_adapter",
    today: date | None = None,
    run_started_at_utc: str | None = None,
) -> ArchiveResult:
    output = Path(output_dir)
    quote_date = today or date.today()
    run_started = run_started_at_utc or datetime.now(UTC).replace(microsecond=0).isoformat()
    run_time = _run_time_partition(run_started)
    cleaned_symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    manifest: dict[str, Any] = {
        "artifact": "live_options_archive_manifest",
        "schema_version": 2,
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "run_started_at_utc": run_started,
        "quote_date": quote_date.isoformat(),
        "run_time_utc": run_time,
        "output_dir": str(output.resolve()),
        "settings": {
            "min_dte": min_dte,
            "max_dte": max_dte,
            "max_expiries_per_symbol": max_expiries_per_symbol,
            "provider": provider,
        },
        "summary": {
            "symbols_requested": len(cleaned_symbols),
            "symbols_archived": 0,
            "expiries_archived": 0,
            "rows_archived": 0,
            "errors": 0,
            "rows_with_two_sided_quotes": 0,
            "rows_with_valid_iv": 0,
            "rows_with_last_trade_timestamp": 0,
        },
        "symbols": {},
    }

    for symbol in cleaned_symbols:
        symbol_entry: dict[str, Any] = {
            "status": "missing",
            "expiries": [],
            "row_count": 0,
            "path": None,
            "errors": [],
        }
        frames: list[pd.DataFrame] = []
        try:
            eligible_expiries: list[str] = []
            for raw_expiry in option_expiries(symbol):
                try:
                    expiry_date = date.fromisoformat(raw_expiry)
                except ValueError:
                    continue
                dte = (expiry_date - quote_date).days
                if min_dte <= dte <= max_dte:
                    eligible_expiries.append(raw_expiry)
                if len(eligible_expiries) >= max_expiries_per_symbol:
                    break

            for expiry in eligible_expiries:
                calls, puts = option_chain(symbol, expiry)
                call_rows = _normalize_leg(
                    calls,
                    symbol=symbol,
                    quote_date=quote_date,
                    run_started_at_utc=run_started,
                    expiry=expiry,
                    option_type="C",
                    provider=provider,
                )
                put_rows = _normalize_leg(
                    puts,
                    symbol=symbol,
                    quote_date=quote_date,
                    run_started_at_utc=run_started,
                    expiry=expiry,
                    option_type="P",
                    provider=provider,
                )
                combined_expiry = pd.concat([call_rows, put_rows], ignore_index=True)
                if not combined_expiry.empty:
                    frames.append(combined_expiry)
                    symbol_entry["expiries"].append(expiry)

            if frames:
                symbol_frame = pd.concat(frames, ignore_index=True)
                out_path = (
                    output
                    / "partitioned"
                    / f"quote_date={quote_date.isoformat()}"
                    / f"run_time_utc={run_time}"
                    / f"underlying_symbol={symbol}"
                    / "chain.parquet"
                )
                out_path.parent.mkdir(parents=True, exist_ok=True)
                symbol_frame.to_parquet(out_path, index=False)
                row_count = int(len(symbol_frame))
                symbol_entry.update(
                    {
                        "status": "archived",
                        "row_count": row_count,
                        "path": str(out_path),
                    }
                )
                manifest["summary"]["symbols_archived"] += 1
                manifest["summary"]["expiries_archived"] += len(symbol_entry["expiries"])
                manifest["summary"]["rows_archived"] += row_count
                manifest["summary"]["rows_with_two_sided_quotes"] += int(symbol_frame["has_two_sided_quote"].sum())
                manifest["summary"]["rows_with_valid_iv"] += int(symbol_frame["has_valid_iv"].sum())
                manifest["summary"]["rows_with_last_trade_timestamp"] += int(symbol_frame["last_trade_at_utc"].notna().sum())
        except Exception as exc:
            symbol_entry["status"] = "error"
            symbol_entry["errors"].append(str(exc))
            manifest["summary"]["errors"] += 1

        manifest["symbols"][symbol] = symbol_entry

    manifest_path = output / "manifests" / f"live_options_archive_{quote_date.isoformat()}_{run_time}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    latest_path = output / "coverage_manifest.json"
    latest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return ArchiveResult(root=output, manifest_path=manifest_path, manifest=manifest)
