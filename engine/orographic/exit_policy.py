"""Executable shadow evaluation for long-option exit policies."""

from __future__ import annotations

from datetime import datetime, timezone
from numbers import Number
from typing import Any


ARTIFACT = "orographic_exit_policy_shadow"
SCHEMA_VERSION = 1
POLICIES = (
    {"policy_id": "standing_limit_25", "kind": "standing_limit", "target_return": 0.25},
    {"policy_id": "standing_limit_40", "kind": "standing_limit", "target_return": 0.40},
    {"policy_id": "next_day_time_stop", "kind": "fixed_horizon", "horizon": "next_day_close"},
    {"policy_id": "eod_spread_guard_10", "kind": "spread_guard", "max_spread_pct": 0.10},
)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Number):
        return None
    parsed = float(value)
    return parsed if parsed == parsed else None


def _entry_ask(pick: dict[str, Any]) -> float | None:
    quote = pick.get("emission_quote") if isinstance(pick.get("emission_quote"), dict) else {}
    ask = _number(quote.get("ask"))
    if ask is not None and ask > 0:
        return ask
    cost = _number(quote.get("contract_cost"))
    return cost / 100.0 if cost is not None and cost > 0 else None


def _labels(pick: dict[str, Any]) -> dict[str, dict[str, Any]]:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    raw = outcomes.get("executable_labels") if isinstance(outcomes.get("executable_labels"), dict) else {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def _marks(pick: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    raw = outcomes.get("trajectory_marks") if isinstance(outcomes.get("trajectory_marks"), list) else []
    rows = [row for row in raw if isinstance(row, dict) and _number(row.get("bid")) is not None]
    return sorted(rows, key=lambda row: str(row.get("captured_at_utc") or ""))


def _label_exit(label: dict[str, Any]) -> tuple[float | None, str | None, str | None, float | None]:
    exit_payload = label.get("exit") if isinstance(label.get("exit"), dict) else {}
    quote = exit_payload.get("quote") if isinstance(exit_payload.get("quote"), dict) else {}
    price = _number(exit_payload.get("execution_price"))
    observed = str(quote.get("observed_at_utc") or label.get("label_available_at_utc") or "") or None
    bid = _number(quote.get("bid"))
    ask = _number(quote.get("ask"))
    spread_pct = None
    if bid is not None and ask is not None and bid >= 0 and ask > 0 and bid + ask > 0:
        spread_pct = (ask - bid) / ((ask + bid) / 2.0)
    return price, observed, str(exit_payload.get("execution_price_source") or "") or None, spread_pct


def _terminal_label(labels: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    for horizon in ("friday_close", "next_day_close", "end_of_day", "one_hour"):
        if horizon in labels:
            return horizon, labels[horizon]
    return None


def _result(
    pick: dict[str, Any],
    *,
    policy_id: str,
    entry_ask: float | None,
    exit_price: float | None,
    exit_at_utc: str | None,
    exit_reason: str,
    evidence_source: str,
    label_available_at_utc: str | None,
) -> dict[str, Any]:
    resolved = entry_ask is not None and entry_ask > 0 and exit_price is not None and exit_price >= 0
    net_return = exit_price / entry_ask - 1.0 if resolved else None
    return {
        "source_system": "orographic",
        "recommendation_id": pick.get("recommendation_id"),
        "contract_symbol": pick.get("contract_symbol"),
        "lane": pick.get("lane"),
        "decision_at_utc": pick.get("run_generated_at_utc"),
        "policy_id": policy_id,
        "entry_price": entry_ask,
        "entry_price_source": "emission_ask",
        "exit_price": exit_price,
        "exit_price_source": "executable_bid_or_limit",
        "exit_at_utc": exit_at_utc,
        "exit_reason": exit_reason,
        "evidence_source": evidence_source,
        "label_available_at_utc": label_available_at_utc,
        "is_executable": resolved,
        "is_resolved": resolved,
        "net_executable_return": round(net_return, 6) if net_return is not None else None,
        "net_executable_pnl_usd": round(net_return * entry_ask * 100.0, 2) if net_return is not None else None,
    }


def evaluate_pick_exit_policies(pick: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate registered policies without treating midpoint touches as fills."""
    entry_ask = _entry_ask(pick)
    labels = _labels(pick)
    marks = _marks(pick)
    terminal = _terminal_label(labels)
    rows: list[dict[str, Any]] = []

    for policy in POLICIES:
        policy_id = str(policy["policy_id"])
        if policy["kind"] == "standing_limit":
            target_return = float(policy["target_return"])
            limit_price = entry_ask * (1.0 + target_return) if entry_ask is not None else None
            hit = next(
                (mark for mark in marks if limit_price is not None and (_number(mark.get("bid")) or -1.0) >= limit_price),
                None,
            )
            if hit is not None:
                rows.append(_result(
                    pick,
                    policy_id=policy_id,
                    entry_ask=entry_ask,
                    exit_price=limit_price,
                    exit_at_utc=str(hit.get("captured_at_utc") or "") or None,
                    exit_reason="standing_limit_filled_at_recorded_bid",
                    evidence_source="trajectory_bid",
                    label_available_at_utc=str(hit.get("captured_at_utc") or "") or None,
                ))
                continue
            if terminal is None:
                rows.append(_result(
                    pick, policy_id=policy_id, entry_ask=entry_ask, exit_price=None,
                    exit_at_utc=None, exit_reason="unresolved_no_executable_terminal",
                    evidence_source="trajectory_bid", label_available_at_utc=None,
                ))
                continue
            horizon, label = terminal
            price, observed, source, _ = _label_exit(label)
            rows.append(_result(
                pick, policy_id=policy_id, entry_ask=entry_ask, exit_price=price,
                exit_at_utc=observed, exit_reason=f"{horizon}_fallback",
                evidence_source=source or "executable_label",
                label_available_at_utc=str(label.get("label_available_at_utc") or "") or observed,
            ))
        elif policy["kind"] == "fixed_horizon":
            horizon = str(policy["horizon"])
            label = labels.get(horizon)
            price, observed, source, _ = _label_exit(label or {})
            rows.append(_result(
                pick, policy_id=policy_id, entry_ask=entry_ask, exit_price=price,
                exit_at_utc=observed, exit_reason=horizon if label else f"unresolved_{horizon}",
                evidence_source=source or "executable_label",
                label_available_at_utc=str((label or {}).get("label_available_at_utc") or "") or observed,
            ))
        else:
            chosen: tuple[str, dict[str, Any], float, str | None, str | None] | None = None
            for horizon in ("end_of_day", "next_day_close", "friday_close"):
                label = labels.get(horizon)
                if label is None:
                    continue
                price, observed, source, spread_pct = _label_exit(label)
                if price is not None and spread_pct is not None and spread_pct <= float(policy["max_spread_pct"]):
                    chosen = (horizon, label, price, observed, source)
                    break
            if chosen is None:
                rows.append(_result(
                    pick, policy_id=policy_id, entry_ask=entry_ask, exit_price=None,
                    exit_at_utc=None, exit_reason="unresolved_no_exit_within_spread_guard",
                    evidence_source="executable_label", label_available_at_utc=None,
                ))
            else:
                horizon, label, price, observed, source = chosen
                rows.append(_result(
                    pick, policy_id=policy_id, entry_ask=entry_ask, exit_price=price,
                    exit_at_utc=observed, exit_reason=f"{horizon}_spread_guard_passed",
                    evidence_source=source or "executable_label",
                    label_available_at_utc=str(label.get("label_available_at_utc") or "") or observed,
                ))
    return rows


def build_exit_policy_shadow_artifact(ledger: dict[str, Any]) -> dict[str, Any]:
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        picks = entry.get("picks") if isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if isinstance(pick, dict):
                rows.extend(evaluate_pick_exit_policies(pick))
    def summarize(source_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for policy in POLICIES:
            policy_id = str(policy["policy_id"])
            policy_rows = [row for row in source_rows if row["policy_id"] == policy_id]
            resolved = [row for row in policy_rows if row["is_resolved"]]
            returns = [float(row["net_executable_return"]) for row in resolved]
            result[policy_id] = {
                "recommendations": len(policy_rows),
                "resolved": len(resolved),
                "coverage_pct": round(len(resolved) / len(policy_rows), 4) if policy_rows else 0.0,
                "win_rate": round(sum(value > 0 for value in returns) / len(returns), 4) if returns else None,
                "mean_net_executable_return": round(sum(returns) / len(returns), 6) if returns else None,
                "net_executable_pnl_usd": round(sum(float(row["net_executable_pnl_usd"]) for row in resolved), 2),
            }
        return result

    by_policy = summarize(rows)
    return {
        "artifact": ARTIFACT,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "shadow_only",
        "production_effect": "none",
        "fill_policy": "standing limits require recorded bid at or above limit; midpoint touches never count",
        "row_contract": {
            "primary_key": ["recommendation_id", "policy_id"],
            "foreign_key": {"recommendation_id": "recommendations.source_recommendation_id"},
            "time_semantics": "label_available_at_utc is the earliest safe training availability timestamp",
        },
        "summary": {
            "recommendations": len(rows) // len(POLICIES),
            "policy_rows": len(rows),
            "by_policy": by_policy,
            "live_by_policy": summarize([row for row in rows if row.get("lane") == "live"]),
        },
        "rows": rows,
    }
