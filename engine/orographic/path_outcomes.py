from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .prospective import _entry_mark


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


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
        if parsed != parsed:
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _quote_mark(row: pd.Series) -> tuple[float | None, str]:
    bid = _safe_float(row.get("bid"))
    ask = _safe_float(row.get("ask"))
    last = _safe_float(row.get("last"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return round((bid + ask) / 2.0, 4), "mid"
    if last is not None and last > 0:
        return last, "last"
    if bid is not None and bid > 0:
        return bid, "bid"
    if ask is not None and ask > 0:
        return ask, "ask"
    return None, "missing"


def _matches_pick(frame: pd.DataFrame, pick: dict[str, Any]) -> pd.DataFrame:
    contract_symbol = str(pick.get("contract_symbol") or "").strip().upper()
    if "contract_symbol" in frame.columns and contract_symbol:
        matched = frame[frame["contract_symbol"].astype(str).str.upper() == contract_symbol]
        if not matched.empty:
            return matched

    expiry = str(pick.get("expiry") or "").strip()
    option_type = str(pick.get("option_type") or "").strip().upper()[:1]
    strike = _safe_float(pick.get("strike"))
    if not expiry or not option_type or strike is None:
        return pd.DataFrame()
    expire_dates = pd.to_datetime(frame.get("expire_date"), errors="coerce").dt.date.astype(str)
    option_types = frame.get("option_type", pd.Series(dtype=object)).astype(str).str.upper().str[0]
    strikes = pd.to_numeric(frame.get("strike"), errors="coerce").round(4)
    return frame[
        (expire_dates == expiry)
        & (option_types == option_type)
        & (strikes == round(strike, 4))
    ]


def _archive_paths_for_symbol(archive_dir: str | Path, symbol: str) -> list[Path]:
    root = Path(archive_dir) / "partitioned"
    return sorted(root.glob(f"quote_date=*/run_time_utc=*/underlying_symbol={symbol.upper()}/chain.parquet"))


def archived_marks_for_pick(
    pick: dict[str, Any],
    *,
    archive_dir: str | Path,
    max_marks: int = 500,
) -> list[dict[str, Any]]:
    symbol = str(pick.get("symbol") or "").strip().upper()
    run_dt = _parse_dt(pick.get("run_generated_at_utc"))
    if not symbol or run_dt is None:
        return []
    marks: list[dict[str, Any]] = []
    for path in _archive_paths_for_symbol(archive_dir, symbol):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        if frame.empty:
            continue
        if "run_started_at_utc" in frame.columns:
            frame = frame.copy()
            frame["_captured_at"] = pd.to_datetime(frame["run_started_at_utc"], errors="coerce", utc=True)
            frame = frame[frame["_captured_at"] >= pd.Timestamp(run_dt)]
            if frame.empty:
                continue
        matched = _matches_pick(frame, pick)
        for _, row in matched.iterrows():
            mark, source = _quote_mark(row)
            if mark is None or mark <= 0:
                continue
            captured_at = str(row.get("run_started_at_utc") or row.get("quote_date") or "")
            marks.append(
                {
                    "captured_at_utc": captured_at,
                    "quote_date": str(row.get("quote_date") or ""),
                    "mark": mark,
                    "mark_source": source,
                    "bid": _safe_float(row.get("bid")),
                    "ask": _safe_float(row.get("ask")),
                    "last": _safe_float(row.get("last")),
                    "source_path": str(path),
                }
            )
    marks.sort(key=lambda row: row.get("captured_at_utc") or "")
    terminal_dt: datetime | None = None
    try:
        terminal_dt = datetime.combine(
            date.fromisoformat(str(pick.get("exit_date") or pick.get("expiry"))),
            time.max,
            tzinfo=timezone.utc,
        )
    except (TypeError, ValueError):
        terminal_dt = None
    bounded = [
        row for row in marks
        if (captured := _parse_dt(row.get("captured_at_utc"))) is not None
        and captured >= run_dt
        and (terminal_dt is None or captured <= terminal_dt)
    ]
    return bounded[: max(max_marks, 1)]


def prospective_trajectory_marks_for_pick(pick: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    stored = outcomes.get("trajectory_marks") if isinstance(outcomes.get("trajectory_marks"), list) else []
    run_dt = _parse_dt(pick.get("run_generated_at_utc"))
    if run_dt is None:
        return []
    try:
        terminal_dt = datetime.combine(
            date.fromisoformat(str(pick.get("exit_date") or pick.get("expiry"))),
            time.max,
            tzinfo=timezone.utc,
        )
    except (TypeError, ValueError):
        terminal_dt = None
    marks: list[dict[str, Any]] = []
    for row in stored:
        if not isinstance(row, dict):
            continue
        captured = _parse_dt(row.get("captured_at_utc"))
        mark = _safe_float(row.get("mark"))
        if captured is None or mark is None or mark <= 0 or captured < run_dt:
            continue
        if terminal_dt is not None and captured > terminal_dt:
            continue
        rendered = dict(row)
        rendered["source_path"] = "prospective_trajectory_marks"
        marks.append(rendered)
    marks.sort(key=lambda row: row.get("captured_at_utc") or "")
    return marks


def build_archived_quote_path_label(
    pick: dict[str, Any],
    *,
    archive_dir: str | Path,
    take_profit_thresholds: tuple[float, ...] = (0.25, 0.40),
    stop_threshold: float = -0.50,
) -> dict[str, Any]:
    entry_mark = _entry_mark(pick)
    marks = prospective_trajectory_marks_for_pick(pick)
    archived = archived_marks_for_pick(pick, archive_dir=archive_dir)
    seen_minutes = {str(mark.get("captured_at_utc") or "")[:16] for mark in marks}
    marks.extend(
        mark for mark in archived
        if str(mark.get("captured_at_utc") or "")[:16] not in seen_minutes
    )
    marks.sort(key=lambda row: row.get("captured_at_utc") or "")
    label: dict[str, Any] = {
        "status": "missing",
        "entry_mark": entry_mark,
        "observation_count": len(marks),
        "max_favorable_excursion_pct": None,
        "max_adverse_excursion_pct": None,
        "first_hit": None,
        "take_profit_25_pct_before_stop_50_pct": None,
        "take_profit_40_pct_before_stop_50_pct": None,
        "marks": [],
    }
    if entry_mark is None or entry_mark <= 0:
        label["status"] = "missing_entry_mark"
        return label
    if not marks:
        return label

    first_hit: dict[str, Any] | None = None
    returns: list[float] = []
    rendered_marks: list[dict[str, Any]] = []
    threshold_names = {threshold: f"take_profit_{int(threshold * 100)}_pct" for threshold in take_profit_thresholds}
    for mark in marks:
        pnl_pct = round(float(mark["mark"]) / entry_mark - 1.0, 4)
        returns.append(pnl_pct)
        rendered = dict(mark)
        rendered["pnl_pct_from_emission"] = pnl_pct
        rendered_marks.append(rendered)
        if first_hit is None:
            for threshold in sorted(take_profit_thresholds):
                if pnl_pct >= threshold:
                    first_hit = {
                        "captured_at_utc": mark.get("captured_at_utc"),
                        "rule": threshold_names[threshold],
                        "pnl_pct_from_emission": pnl_pct,
                    }
                    break
            if first_hit is None and pnl_pct <= stop_threshold:
                first_hit = {
                    "captured_at_utc": mark.get("captured_at_utc"),
                    "rule": "stop_50_pct",
                    "pnl_pct_from_emission": pnl_pct,
                }

    first_rule = str((first_hit or {}).get("rule") or "")
    label.update(
        {
            "status": "observed",
            "max_favorable_excursion_pct": round(max(returns), 4),
            "max_adverse_excursion_pct": round(min(returns), 4),
            "first_hit": first_hit,
            "take_profit_25_pct_before_stop_50_pct": (
                True if first_rule == "take_profit_25_pct" else False if first_rule == "stop_50_pct" else None
            ),
            "take_profit_40_pct_before_stop_50_pct": (
                True if first_rule == "take_profit_40_pct" else False if first_rule == "stop_50_pct" else None
            ),
            "marks": rendered_marks,
        }
    )
    return label


def apply_archived_quote_path_labels(
    ledger: dict[str, Any],
    *,
    archive_dir: str | Path,
) -> tuple[dict[str, Any], dict[str, int]]:
    updated = json.loads(json.dumps(ledger))
    entries = updated.get("entries") if isinstance(updated.get("entries"), list) else []
    stats = {
        "entries_seen": len(entries),
        "picks_seen": 0,
        "labels_observed": 0,
        "labels_missing": 0,
        "labels_missing_entry_mark": 0,
    }
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        picks = entry.get("picks") if isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            stats["picks_seen"] += 1
            pick.setdefault("run_generated_at_utc", entry.get("run_generated_at_utc"))
            outcomes = pick.setdefault("outcomes", {})
            label = build_archived_quote_path_label(pick, archive_dir=archive_dir)
            outcomes["archived_quote_path"] = label
            status = str(label.get("status") or "")
            if status == "observed":
                stats["labels_observed"] += 1
            elif status == "missing_entry_mark":
                stats["labels_missing_entry_mark"] += 1
            else:
                stats["labels_missing"] += 1
    updated["archived_quote_path_summary"] = stats
    return updated, stats
