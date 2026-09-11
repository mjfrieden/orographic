"""Observation-only trajectory-mark exit overlay.

Replaces the stale path-hazard fit, which was trained on a 740-row terminal
file with zero valid pre-exit paths. This overlay uses the live trajectory
marks captured after entry and never routes orders.
"""

from __future__ import annotations

from datetime import UTC, datetime
from numbers import Number
from typing import Any


ARTIFACT = "trajectory_exit_overlay_v1"
SCHEMA_VERSION = 1
TARGET_RETURN = 0.25
STOP_RETURN = -0.50
AUTHORITY = "observation_only_never_used_for_routing"


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Number):
        return None
    parsed = float(value)
    return parsed if parsed == parsed else None


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _entry_ask(pick: dict[str, Any]) -> float | None:
    quote = _as_dict(pick.get("emission_quote"))
    ask = _number(quote.get("ask"))
    if ask is not None and ask > 0:
        return ask
    cost = _number(quote.get("contract_cost"))
    return cost / 100.0 if cost is not None and cost > 0 else None


def _mark_return(entry_ask: float, mark: dict[str, Any]) -> float | None:
    explicit = _number(mark.get("pnl_pct_from_emission"))
    if explicit is not None:
        return explicit
    bid = _number(mark.get("bid"))
    if bid is None or bid < 0:
        return None
    return bid / entry_ask - 1.0


def _trajectory_marks(pick: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = _as_dict(pick.get("outcomes"))
    raw = _as_list(outcomes.get("trajectory_marks"))
    rows = [row for row in raw if isinstance(row, dict)]
    if rows:
        return sorted(rows, key=lambda row: str(row.get("captured_at_utc") or ""))
    overlay = _as_dict(outcomes.get("trajectory_overlay"))
    hit = overlay.get("first_hit") if isinstance(overlay.get("first_hit"), dict) else None
    return [hit] if hit is not None else []


def _friday_return(pick: dict[str, Any]) -> tuple[float | None, str | None]:
    outcomes = _as_dict(pick.get("outcomes"))
    marks = _as_dict(outcomes.get("fixed_exit_marks"))
    for window in ("friday_close", "next_day_close", "end_of_day", "one_hour"):
        pnl = _number(_as_dict(marks.get(window)).get("pnl_pct_from_emission"))
        if pnl is not None:
            return pnl, window
    labels = _as_dict(outcomes.get("executable_labels"))
    friday = _as_dict(_as_dict(labels.get("friday_close")).get("exit"))
    price = _number(friday.get("execution_price"))
    ask = _entry_ask(pick)
    if price is not None and ask is not None and ask > 0:
        return price / ask - 1.0, "friday_close"
    return None, None


def evaluate_pick_overlay(pick: dict[str, Any]) -> dict[str, Any] | None:
    """Score one pick: harvest at +25% bid, stop at -50% bid, else hold to Friday."""
    ask = _entry_ask(pick)
    if ask is None:
        return None
    hold_return, hold_window = _friday_return(pick)
    overlay_return = None
    overlay_reason = "unresolved"
    overlay_at = None
    valid_marks = 0
    for mark in _trajectory_marks(pick):
        value = _mark_return(ask, mark)
        if value is None:
            continue
        valid_marks += 1
        captured = str(mark.get("captured_at_utc") or "") or None
        if value >= TARGET_RETURN:
            overlay_return = TARGET_RETURN
            overlay_reason = "target_25_bid"
            overlay_at = captured
            break
        if value <= STOP_RETURN:
            overlay_return = STOP_RETURN
            overlay_reason = "stop_50_bid"
            overlay_at = captured
            break
    if overlay_return is None and hold_return is not None:
        overlay_return = hold_return
        overlay_reason = f"{hold_window}_hold"
        overlay_at = None
    if overlay_return is None or hold_return is None:
        return None
    return {
        "lane": pick.get("lane"),
        "symbol": pick.get("symbol"),
        "contract_symbol": pick.get("contract_symbol"),
        "run_generated_at_utc": pick.get("run_generated_at_utc"),
        "valid_trajectory_marks": valid_marks,
        "hold_return": round(hold_return, 6),
        "hold_window": hold_window,
        "overlay_return": round(overlay_return, 6),
        "overlay_reason": overlay_reason,
        "overlay_at_utc": overlay_at,
        "return_lift": round(overlay_return - hold_return, 6),
    }


def evaluate_trajectory_exit_overlay(
    ledger: dict[str, Any],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    as_of_utc: datetime | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    marks_seen = 0
    for entry in _as_list(ledger.get("entries")):
        if not isinstance(entry, dict):
            continue
        run_at = _parse_dt(entry.get("run_generated_at_utc"))
        if start is not None and (run_at is None or run_at < start):
            continue
        if end is not None and (run_at is None or run_at > end):
            continue
        for pick in _as_list(entry.get("picks")):
            if not isinstance(pick, dict):
                continue
            marks_seen += len(_trajectory_marks(pick))
            scored = evaluate_pick_overlay(pick)
            if scored is not None:
                rows.append(scored)

    lifts = [float(row["return_lift"]) for row in rows]
    overlay_returns = [float(row["overlay_return"]) for row in rows]
    hold_returns = [float(row["hold_return"]) for row in rows]
    target_hits = sum(1 for row in rows if row["overlay_reason"] == "target_25_bid")
    stop_hits = sum(1 for row in rows if row["overlay_reason"] == "stop_50_bid")
    mean_lift = round(sum(lifts) / len(lifts), 4) if lifts else None
    live_rows = [row for row in rows if row.get("lane") == "live"]
    live_lifts = [float(row["return_lift"]) for row in live_rows]
    generated = (as_of_utc or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    promotion_ready = (
        len(rows) >= 30
        and mean_lift is not None
        and mean_lift > 0
        and target_hits >= 10
        and stop_hits >= 10
    )
    return {
        "artifact": ARTIFACT,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated.isoformat(),
        "authority": AUTHORITY,
        "production_effect": "none",
        "replaces": "path_hazard_challenger",
        "policy": {
            "target_return": TARGET_RETURN,
            "stop_return": STOP_RETURN,
            "fill_rule": "recorded bid at or beyond the target/stop; midpoint touches never count",
            "fallback": "hold to the latest resolved fixed-exit window, preferring Friday close",
        },
        "coverage": {
            "resolved_picks": len(rows),
            "trajectory_marks_seen": marks_seen,
            "target_hits": target_hits,
            "stop_hits": stop_hits,
            "hold_fallbacks": sum(1 for row in rows if str(row["overlay_reason"]).endswith("_hold")),
        },
        "overall": {
            "mean_overlay_return": round(sum(overlay_returns) / len(overlay_returns), 4) if overlay_returns else None,
            "mean_hold_return": round(sum(hold_returns) / len(hold_returns), 4) if hold_returns else None,
            "mean_return_lift": mean_lift,
            "positive_lift_rate": round(sum(1 for value in lifts if value > 0) / len(lifts), 4) if lifts else None,
        },
        "live": {
            "resolved_picks": len(live_rows),
            "mean_return_lift": round(sum(live_lifts) / len(live_lifts), 4) if live_lifts else None,
        },
        "promotion_ready": promotion_ready,
        "reason": (
            "Overlay has enough target/stop hits and a positive paired lift versus hold-to-Friday."
            if promotion_ready
            else (
                "Observation-only. Needs 30 resolved picks, 10 target hits, 10 stop hits, "
                "and a positive mean lift versus hold-to-Friday before any production exit change."
            )
        ),
    }
