"""Deterministic live execution policy for long-option recommendations.

The policy is intentionally separate from model scoring.  It prevents a model
score from overriding facts that make a recommendation difficult to execute:
wide markets, weak participation, negative after-friction edge, or chasing the
same contract shortly after it was already emitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from numbers import Number
from pathlib import Path
from typing import Any, Iterable

from .schemas import ContractCandidate


POLICY_ID = "orographic.live_execution_policy.v1"


@dataclass(frozen=True)
class LiveExecutionPolicy:
    enabled: bool = True
    same_contract_cooldown_hours: float = 72.0
    reentry_edge_override_pct: float = 0.10
    max_reentry_ask_increase_pct: float = 0.10
    max_entry_spread_pct: float = 0.12
    min_open_interest: int = 200
    min_volume: int = 25
    min_expected_edge_after_friction_pct: float = 0.05
    max_last_trade_age_seconds: float = 1_800.0


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_recent_live_exposures(
    path: str | Path | None,
    *,
    as_of_utc: datetime,
    lookback_hours: float,
) -> list[dict[str, Any]]:
    """Load prior live picks without depending on a datamart implementation."""
    if not path or not isinstance(path, (str, Path)):
        return []
    target = Path(path)
    if not target.exists():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    cutoff = as_of_utc - timedelta(hours=max(float(lookback_hours), 0.0))
    exposures: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        emitted_at = _utc(entry.get("run_generated_at_utc"))
        if emitted_at is None or emitted_at < cutoff or emitted_at >= as_of_utc:
            continue
        rows = entry.get("live_board") if isinstance(entry.get("live_board"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            contract_symbol = str(row.get("contract_symbol") or "").strip().upper()
            if not contract_symbol:
                continue
            ask = _number(row.get("ask"))
            if ask is None:
                cost = _number(row.get("contract_cost"))
                ask = cost / 100.0 if cost is not None and cost > 0 else None
            exposures.append({
                "contract_symbol": contract_symbol,
                "symbol": str(row.get("symbol") or "").strip().upper(),
                "emitted_at_utc": emitted_at,
                "ask": ask,
                "expected_edge_after_friction_pct": _number(row.get("expected_edge_after_friction_pct")),
            })
    return exposures


def _latest_matching_exposure(
    candidate: ContractCandidate,
    exposures: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    contract = candidate.contract_symbol.strip().upper()
    matching = [row for row in exposures if row.get("contract_symbol") == contract]
    return max(matching, key=lambda row: row["emitted_at_utc"]) if matching else None


def apply_live_execution_policy(
    candidates: list[ContractCandidate],
    *,
    prior_exposures: Iterable[dict[str, Any]] = (),
    as_of_utc: datetime | None = None,
    policy: LiveExecutionPolicy | None = None,
) -> dict[str, Any]:
    """Annotate candidates with an auditable pass/veto decision.

    Candidates remain in the Forge result for counterfactual outcome capture.
    Council is responsible for excluding rows where ``execution_policy_passed``
    is false from the production board.
    """
    policy = policy or LiveExecutionPolicy()
    now = (as_of_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    prior_exposures = list(prior_exposures)
    rejections: list[dict[str, Any]] = []
    kept = 0

    for candidate in candidates:
        # Some callers use lightweight test doubles around the pipeline.  The
        # live Forge path always supplies ContractCandidate instances; unknown
        # objects are left eligible instead of manufacturing a policy veto from
        # absent quote fields.
        if not isinstance(candidate, ContractCandidate):
            try:
                candidate.execution_policy_passed = True
                candidate.execution_policy_reasons = []
            except (AttributeError, TypeError):
                pass
            kept += 1
            continue
        reasons: list[str] = []
        bid = _number(candidate.bid)
        ask = _number(candidate.ask)
        spread_pct = _number(candidate.spread_pct)
        edge = _number(candidate.expected_edge_after_friction_pct)
        last_trade_age = _number(candidate.last_trade_age_seconds)

        candidate.conservative_exit_bid = bid
        candidate.round_trip_spread_drag_pct = (
            round(max((ask - bid) / ask, 0.0), 4)
            if bid is not None and ask is not None and ask > 0
            else None
        )
        candidate.reentry_blocked = False
        candidate.prior_entry_ask = None

        if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
            reasons.append("invalid_two_sided_quote")
        if spread_pct is None or spread_pct > policy.max_entry_spread_pct:
            reasons.append("entry_spread")
        open_interest = _number(candidate.open_interest)
        volume = _number(candidate.volume)
        if open_interest is None or open_interest < policy.min_open_interest:
            reasons.append("open_interest")
        if volume is None or volume < policy.min_volume:
            reasons.append("volume")
        if edge is None or edge < policy.min_expected_edge_after_friction_pct:
            reasons.append("after_friction_edge")
        if last_trade_age is not None and last_trade_age > policy.max_last_trade_age_seconds:
            reasons.append("stale_last_trade")

        prior = _latest_matching_exposure(candidate, prior_exposures)
        if prior is not None:
            prior_at = prior["emitted_at_utc"]
            hours_since = max((now - prior_at).total_seconds() / 3_600.0, 0.0)
            prior_ask = _number(prior.get("ask"))
            prior_edge = _number(prior.get("expected_edge_after_friction_pct"))
            candidate.prior_entry_ask = prior_ask
            ask_increase = (
                ask / prior_ask - 1.0
                if ask is not None and prior_ask is not None and prior_ask > 0
                else None
            )
            edge_improvement = (
                edge - prior_edge
                if edge is not None and prior_edge is not None
                else None
            )
            override = (
                ask_increase is not None
                and edge_improvement is not None
                and ask_increase <= policy.max_reentry_ask_increase_pct
                and edge_improvement >= policy.reentry_edge_override_pct
            )
            if hours_since < policy.same_contract_cooldown_hours and not override:
                candidate.reentry_blocked = True
                reasons.append("same_contract_cooldown")

        passed = not reasons or not policy.enabled
        candidate.execution_policy_passed = passed
        candidate.execution_policy_reasons = sorted(set(reasons))
        if passed:
            kept += 1
        else:
            flags = set(candidate.council_risk_flags)
            flags.add("execution_policy")
            flags.update(f"execution:{reason}" for reason in reasons)
            candidate.council_risk_flags = sorted(flags)
            candidate.notes = [*candidate.notes, "Live execution policy veto"]
            rejections.append({
                "symbol": candidate.symbol,
                "contract_symbol": candidate.contract_symbol,
                "reasons": sorted(set(reasons)),
                "bid": bid,
                "ask": ask,
                "spread_pct": spread_pct,
                "open_interest": int(open_interest) if open_interest is not None else None,
                "volume": int(volume) if volume is not None else None,
                "expected_edge_after_friction_pct": edge,
                "prior_entry_ask": candidate.prior_entry_ask,
            })

    return {
        "policy_id": POLICY_ID,
        "enabled": policy.enabled,
        "evaluated": len(candidates),
        "kept": kept,
        "dropped": len(rejections),
        "settings": {
            "same_contract_cooldown_hours": policy.same_contract_cooldown_hours,
            "reentry_edge_override_pct": policy.reentry_edge_override_pct,
            "max_reentry_ask_increase_pct": policy.max_reentry_ask_increase_pct,
            "max_entry_spread_pct": policy.max_entry_spread_pct,
            "min_open_interest": policy.min_open_interest,
            "min_volume": policy.min_volume,
            "min_expected_edge_after_friction_pct": policy.min_expected_edge_after_friction_pct,
            "max_last_trade_age_seconds": policy.max_last_trade_age_seconds,
            "bid_size_gate": "pending_source_availability",
        },
        "rejections": rejections,
    }
