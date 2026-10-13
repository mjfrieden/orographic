"""Pure, offline timestamp diagnostics; never a data-validity or trading gate.

Identity, export time, decision time and quote observation time are independent.
The caller supplies an aware clock and cutoff, including any session/holiday
policy. No path name, file mtime, export time or weekday implies fresh data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # Date-only and naive values have no trustworthy clock/zone semantics.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _range(
    values: Iterable[object] | None,
    *,
    as_of: datetime | None,
    cutoff: datetime | None,
    context_valid: bool,
) -> dict[str, Any]:
    """Summarize the supplied population without replacing missing clocks."""
    count = invalid = future = 0
    earliest = latest = None
    for value in values if values is not None else ():
        count += 1
        parsed = _timestamp(value)
        if parsed is None:
            invalid += 1
            continue
        if as_of is not None and parsed > as_of:
            future += 1
        earliest = parsed if earliest is None else min(earliest, parsed)
        latest = parsed if latest is None else max(latest, parsed)
    reason = None
    if values is None:
        reason = "coverage_not_supplied"
    elif count == 0:
        reason = "no_observations"
    elif invalid:
        reason = "missing_or_invalid_timestamps"
    elif future:
        reason = "future_timestamps"
    elif not context_valid:
        reason = "explicit_clock_and_cutoff_required"
    status = "unknown" if reason else "fresh" if latest >= cutoff else "stale"
    return {
        "status": status,
        "reason": reason,
        "row_count": count if values is not None else None,
        "invalid_timestamp_count": invalid if values is not None else None,
        "future_timestamp_count": future if as_of is not None and values is not None else None,
        "min_at_utc": _iso(earliest),
        "max_at_utc": _iso(latest),
    }


def assess_source_freshness(
    *,
    source_identity: Mapping[str, Any],
    export_generated_at: object,
    decision_timestamps: Iterable[object] | None,
    quote_observation_timestamps: Iterable[object] | None,
    as_of_utc: object,
    required_not_before_utc: object,
) -> dict[str, Any]:
    """Check recency of an explicitly supplied, identity-pinned population.

    Supplied iterables must cover the selected source bundle's full relevant
    population, not just its newest valid rows. Unknown/malformed/future clocks
    block positive claims but never drop or mutate source data. A fresh range
    means its latest observation reaches the cutoff, not that every row is new,
    every session is complete, or provider bid/ask clocks have been attested.
    """
    now = _timestamp(as_of_utc)
    cutoff = _timestamp(required_not_before_utc)
    context_valid = now is not None and cutoff is not None and cutoff <= now
    identity = {
        key: source_identity.get(key)
        for key in ("source_system", "bundle_id", "manifest_sha256")
    }
    identity_known = all(isinstance(value, str) and value.strip() for value in identity.values())
    export_time = _timestamp(export_generated_at)
    exported = _range(
        [export_generated_at], as_of=now, cutoff=cutoff, context_valid=context_valid,
    )
    decisions = _range(
        decision_timestamps, as_of=now, cutoff=cutoff, context_valid=context_valid,
    )
    quotes = _range(
        quote_observation_timestamps, as_of=now, cutoff=cutoff, context_valid=context_valid,
    )
    for observations in (decisions, quotes):
        latest = _timestamp(observations["max_at_utc"])
        if latest is not None and export_time is not None and latest > export_time:
            observations["status"] = "unknown"
            observations["reason"] = "observation_after_export"
    statuses = [exported["status"], decisions["status"], quotes["status"]]
    # Known staleness remains visible even when another population is unknown.
    status = "stale" if "stale" in statuses else "unknown" if (
        not identity_known or "unknown" in statuses
    ) else "fresh"
    return {
        "source_identity": identity,
        "identity_known": identity_known,
        "checked_at_utc": _iso(now),
        "required_not_before_utc": _iso(cutoff),
        "context_valid": context_valid,
        "export_generated_at_utc": _iso(export_time),
        "export_recency": {"status": exported["status"], "reason": exported["reason"]},
        "model_run_decision_range": decisions,
        "quote_observation_range": quotes,
        "source_freshness": status,
        "source_is_fresh": status == "fresh",
        "session_completeness": "not_assessed",
        "provider_quote_freshness": "not_assessed",
    }
