from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


DEFAULT_COOLDOWN_HOURS = 6.0


def load_cooldown(path: Path, *, now: datetime | None = None) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        until = datetime.fromisoformat(str(payload["cooldown_until_utc"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return payload if until.astimezone(UTC) > current else None


def record_rate_limit(
    path: Path,
    *,
    source: str,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    retry_after_seconds: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    duration = max(float(cooldown_hours) * 3600.0, float(retry_after_seconds or 0.0))
    payload = {
        "artifact": "gdelt_provider_cooldown",
        "schema_version": 1,
        "provider": "gdelt",
        "reason": "http_429",
        "source": source,
        "rate_limited_at_utc": current.isoformat(),
        "cooldown_until_utc": (current + timedelta(seconds=duration)).isoformat(),
        "cooldown_seconds": round(duration, 3),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def retry_after_seconds(exc: Any) -> float | None:
    value = getattr(exc, "headers", {}).get("Retry-After") if getattr(exc, "headers", None) else None
    try:
        return max(float(value), 0.0) if value is not None else None
    except (TypeError, ValueError):
        return None
