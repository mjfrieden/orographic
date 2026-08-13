from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import time as time_module
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .executable_outcomes import (
    ExecutableOutcomeRequest,
    FillObservation,
    OutcomeContractError,
    QuoteObservation,
    build_executable_option_outcome,
)
from .positions import DEFAULT_LIVE_BASE_URL, DEFAULT_SANDBOX_BASE_URL, _as_number, _env_truthy, _quote_mark, normalize_quotes


MARKET_TZ = ZoneInfo("America/Chicago")
MARK_CLOSE_BUFFER = time(15, 0)
DEFAULT_TRADIER_QUOTE_BATCH_SIZE = 75
DEFAULT_TRADIER_QUOTE_TIMEOUT_SECONDS = 20
DEFAULT_TRADIER_QUOTE_RETRIES = 2
CAPTURE_POLICY_VERSION = 2
CAPTURE_DELAY_LIMITS_SECONDS = {
    "one_hour": 15 * 60,
    "end_of_day": 30 * 60,
    "next_day_close": 30 * 60,
    "friday_close": 30 * 60,
}
MAX_BROKER_QUOTE_AGE_SECONDS = 15 * 60
MAX_TRAJECTORY_MARKS_PER_PICK = 160


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


def _quote_batch_size(source: dict[str, str]) -> int:
    raw = source.get("TRADIER_QUOTE_BATCH_SIZE") or source.get("OROGRAPHIC_TRADIER_QUOTE_BATCH_SIZE")
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        parsed = DEFAULT_TRADIER_QUOTE_BATCH_SIZE
    return max(parsed, 1)


def _quote_request_retries(source: dict[str, str]) -> int:
    raw = source.get("TRADIER_QUOTE_RETRIES") or source.get("OROGRAPHIC_TRADIER_QUOTE_RETRIES")
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        parsed = DEFAULT_TRADIER_QUOTE_RETRIES
    return max(parsed, 0)


def _is_transient_quote_error(exc: OSError) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return True
        message = str(reason).lower()
        return any(marker in message for marker in ("timed out", "temporarily unavailable", "connection reset"))
    return False


def _fixed_exit_targets(run_generated_at_utc: str) -> dict[str, datetime] | None:
    run_dt = _parse_dt(run_generated_at_utc)
    if run_dt is None:
        return None

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
        "one_hour": one_hour_due,
        "end_of_day": end_of_day_due,
        "next_day_close": next_day_close_due,
        "friday_close": friday_close_due,
    }


def due_fixed_exit_windows(run_generated_at_utc: str, now_utc: datetime | None = None) -> dict[str, bool]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    targets = _fixed_exit_targets(run_generated_at_utc)
    if targets is None:
        return {"one_hour": False, "end_of_day": False, "next_day_close": False, "friday_close": False}
    return {name: now >= target for name, target in targets.items()}


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
        "retrieved_at_utc": captured_at_utc,
        "bid_observed_at_utc": quote.get("bid_observed_at_utc"),
        "ask_observed_at_utc": quote.get("ask_observed_at_utc"),
        "trade_observed_at_utc": quote.get("trade_observed_at_utc"),
        "mark": mark,
        "mark_source": source,
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "last": quote.get("last"),
        "close": quote.get("close"),
        "pnl_pct_from_emission": pnl_pct,
    }


def _fill_observation(raw: object) -> FillObservation | None:
    if not isinstance(raw, dict):
        return None
    price = _as_number(raw.get("price"))
    filled_at = raw.get("filled_at_utc")
    if price is None or not filled_at:
        return None
    return FillObservation(price=price, filled_at_utc=str(filled_at), execution_id=raw.get("execution_id"))


def _executable_label(
    entry: dict[str, Any],
    pick: dict[str, Any],
    quote: dict[str, Any],
    *,
    window_name: str,
    horizon_target: datetime,
    captured_at_utc: str,
    max_capture_delay_seconds: float | None = None,
) -> dict[str, Any] | None:
    """Build v1 when required evidence exists; legacy ledgers fail closed."""

    emission = pick.get("emission_quote") if isinstance(pick.get("emission_quote"), dict) else {}
    entry_bid = _as_number(emission.get("bid"))
    entry_ask = _as_number(emission.get("ask"))
    exit_bid = _as_number(quote.get("bid"))
    exit_ask = _as_number(quote.get("ask"))
    entry_quote_at = emission.get("captured_at_utc")
    decision_at = pick.get("run_generated_at_utc") or entry.get("run_generated_at_utc")
    required_text = (
        pick.get("recommendation_id"),
        pick.get("symbol"),
        pick.get("contract_symbol"),
        decision_at,
        entry_quote_at,
    )
    if any(not str(value or "").strip() for value in required_text):
        return None
    if None in (entry_bid, entry_ask, exit_bid, exit_ask):
        return None

    realized = pick.get("outcomes", {}).get("realized_if_traded", {})
    if not isinstance(realized, dict):
        realized = {}
    contracts_raw = realized.get("contracts")
    contracts = contracts_raw if isinstance(contracts_raw, int) and not isinstance(contracts_raw, bool) and contracts_raw > 0 else 1
    try:
        return build_executable_option_outcome(ExecutableOutcomeRequest(
            recommendation_id=str(pick["recommendation_id"]),
            symbol=str(pick["symbol"]),
            contract_symbol=str(pick["contract_symbol"]),
            decision_at_utc=str(decision_at),
            horizon=window_name,
            horizon_target_at_utc=horizon_target,
            label_available_at_utc=captured_at_utc,
            entry_quote=QuoteObservation(
                bid=float(entry_bid),
                ask=float(entry_ask),
                observed_at_utc=str(entry_quote_at),
                source=str(emission.get("entry_data_source") or "unknown"),
            ),
            exit_quote=QuoteObservation(
                bid=float(exit_bid),
                ask=float(exit_ask),
                observed_at_utc=captured_at_utc,
                source=str(quote.get("source") or "tradier"),
            ),
            entry_fill=_fill_observation(realized.get("entry_fill")),
            exit_fill=_fill_observation(realized.get("exit_fill")),
            contracts=contracts,
            entry_fees_usd=_as_number(realized.get("entry_fees_usd")) or 0.0,
            exit_fees_usd=_as_number(realized.get("exit_fees_usd")) or 0.0,
            max_exit_capture_delay_seconds=max_capture_delay_seconds,
        ))
    except OutcomeContractError:
        return None


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


def _outcome_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "picks": 0,
        "pending": 0,
        "partial": 0,
        "complete": 0,
        "with_any_mark": 0,
        "with_all_fixed_marks": 0,
        "missing_outcome_quotes": 0,
        "payoff_shadow_scored": 0,
        "payoff_shadow_disagreements": 0,
        "payoff_shadow_resolved_friday": 0,
        "payoff_shadow_disagreement_net_return_sum": 0.0,
        "capture_windows_valid": 0,
        "capture_windows_quote_missing": 0,
        "capture_windows_stale_quote": 0,
        "capture_windows_missed": 0,
        "legacy_capture_policy_picks": 0,
        "capture_policy_v2_picks": 0,
        "trajectory_scored_picks": 0,
        "trajectory_marks": 0,
        "trajectory_picks_with_4_marks": 0,
    }
    fixed_names = ("one_hour", "end_of_day", "next_day_close", "friday_close")
    for entry in entries:
        picks = entry.get("picks") if isinstance(entry, dict) and isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            summary["picks"] += 1
            outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
            scores = pick.get("scores") if isinstance(pick.get("scores"), dict) else {}
            if isinstance(scores.get("payoff_shadow_prob_positive"), (int, float)):
                summary["payoff_shadow_scored"] += 1
            disagreement = bool(scores.get("payoff_shadow_disagreement"))
            if disagreement:
                summary["payoff_shadow_disagreements"] += 1
            executable_labels = outcomes.get("executable_labels") if isinstance(outcomes.get("executable_labels"), dict) else {}
            friday_label = executable_labels.get("friday_close") if isinstance(executable_labels.get("friday_close"), dict) else None
            if disagreement and friday_label is not None and isinstance(friday_label.get("net_executable_return"), (int, float)):
                summary["payoff_shadow_resolved_friday"] += 1
                summary["payoff_shadow_disagreement_net_return_sum"] += float(friday_label["net_executable_return"])
            status = str(outcomes.get("status") or "pending")
            if status in {"pending", "partial", "complete"}:
                summary[status] += 1
            fixed_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
            trajectory_marks = outcomes.get("trajectory_marks") if isinstance(outcomes.get("trajectory_marks"), list) else []
            if trajectory_marks:
                summary["trajectory_scored_picks"] += 1
                summary["trajectory_marks"] += len(trajectory_marks)
                if len(trajectory_marks) >= 4:
                    summary["trajectory_picks_with_4_marks"] += 1
            marked = [name for name in fixed_names if fixed_marks.get(name) is not None]
            if marked:
                summary["with_any_mark"] += 1
            if len(marked) == len(fixed_names):
                summary["with_all_fixed_marks"] += 1
            quote_verification = outcomes.get("quote_verification") if isinstance(outcomes.get("quote_verification"), dict) else {}
            if int(quote_verification.get("capture_policy_version") or 0) < CAPTURE_POLICY_VERSION:
                summary["legacy_capture_policy_picks"] += 1
            else:
                summary["capture_policy_v2_picks"] += 1
            capture_attempts = outcomes.get("capture_attempts") if isinstance(outcomes.get("capture_attempts"), dict) else {}
            for attempt in capture_attempts.values():
                if not isinstance(attempt, dict):
                    continue
                status_value = str(attempt.get("status") or "")
                if status_value == "captured_valid":
                    summary["capture_windows_valid"] += 1
                elif status_value == "quote_missing_retryable":
                    summary["capture_windows_quote_missing"] += 1
                elif status_value == "stale_quote_retryable":
                    summary["capture_windows_stale_quote"] += 1
                elif status_value == "missed_live_window":
                    summary["capture_windows_missed"] += 1
            if marked and not quote_verification.get("outcome_quotes_captured"):
                summary["missing_outcome_quotes"] += 1
    resolved = int(summary["payoff_shadow_resolved_friday"])
    summary["payoff_shadow_disagreement_avg_net_return"] = (
        round(float(summary["payoff_shadow_disagreement_net_return_sum"]) / resolved, 4)
        if resolved else None
    )
    summary["payoff_shadow_disagreement_net_return_sum"] = round(
        float(summary["payoff_shadow_disagreement_net_return_sum"]), 4
    )
    return summary


def _capture_window_state(now: datetime, target: datetime, window_name: str) -> tuple[str, float, float]:
    delay_seconds = (now - target).total_seconds()
    limit_seconds = float(CAPTURE_DELAY_LIMITS_SECONDS[window_name])
    if delay_seconds < 0:
        return "not_due", delay_seconds, limit_seconds
    if delay_seconds <= limit_seconds:
        return "capture_allowed", delay_seconds, limit_seconds
    return "missed_live_window", delay_seconds, limit_seconds


def _capture_attempt_payload(
    *,
    status: str,
    target: datetime,
    attempted_at: str,
    delay_seconds: float,
    limit_seconds: float,
    broker_quote_age_seconds: float | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "target_at_utc": target.isoformat(),
        "attempted_at_utc": attempted_at,
        "capture_delay_seconds": round(delay_seconds, 3),
        "max_capture_delay_seconds": limit_seconds,
        "retryable_live": status in {"quote_missing_retryable", "stale_quote_retryable"},
        "retryable_via_archive": status in {"quote_missing_retryable", "stale_quote_retryable", "missed_live_window"},
        "broker_quote_age_seconds": round(broker_quote_age_seconds, 3) if broker_quote_age_seconds is not None else None,
    }


def _broker_quote_age_seconds(quote: dict[str, Any], now: datetime) -> float | None:
    observed = _parse_dt(quote.get("bid_observed_at_utc") or quote.get("trade_observed_at_utc"))
    return (now - observed).total_seconds() if observed is not None else None


def _trajectory_capture_active(run_generated_at_utc: str, now: datetime) -> bool:
    run_dt = _parse_dt(run_generated_at_utc)
    targets = _fixed_exit_targets(run_generated_at_utc) or {}
    terminal = targets.get("friday_close")
    return run_dt is not None and terminal is not None and run_dt <= now <= terminal


def _append_trajectory_mark(
    outcomes: dict[str, Any],
    quote: dict[str, Any],
    *,
    captured_at_utc: str,
    entry_mark: float | None,
) -> bool:
    marks = outcomes.setdefault("trajectory_marks", [])
    if not isinstance(marks, list):
        marks = []
        outcomes["trajectory_marks"] = marks
    payload = _mark_payload(quote, captured_at_utc=captured_at_utc, entry_mark=entry_mark)
    if payload.get("mark") is None:
        return False
    # A retried job in the same minute must not manufacture an independent
    # path observation from the same market snapshot.
    minute_key = captured_at_utc[:16]
    if any(str(mark.get("captured_at_utc") or "")[:16] == minute_key for mark in marks if isinstance(mark, dict)):
        return False
    marks.append(payload)
    marks.sort(key=lambda mark: str(mark.get("captured_at_utc") or ""))
    if len(marks) > MAX_TRAJECTORY_MARKS_PER_PICK:
        del marks[: len(marks) - MAX_TRAJECTORY_MARKS_PER_PICK]
    return True


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
    stats = {
        "entries_seen": len(entries),
        "picks_seen": 0,
        "marks_written": 0,
        "executable_labels_written": 0,
        "executable_labels_skipped": 0,
        "quotes_missing": 0,
        "picks_completed": 0,
        "picks_partial": 0,
        "picks_pending": 0,
        "capture_windows_due": 0,
        "capture_windows_valid": 0,
        "capture_windows_quote_missing": 0,
        "capture_windows_stale_quote": 0,
        "capture_windows_missed": 0,
        "capture_windows_newly_missed": 0,
        "legacy_capture_policy_picks_skipped": 0,
        "trajectory_marks_written": 0,
        "trajectory_quotes_missing": 0,
        "trajectory_quotes_stale": 0,
        "trajectory_active_picks": 0,
        "ledger_changed": 0,
    }
    changed = False

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        due = due_fixed_exit_windows(str(entry.get("run_generated_at_utc") or ""), now)
        targets = _fixed_exit_targets(str(entry.get("run_generated_at_utc") or "")) or {}
        picks = entry.get("picks") if isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            stats["picks_seen"] += 1
            outcomes = pick.setdefault("outcomes", {})
            quote_verification = outcomes.setdefault("quote_verification", {})
            if int(quote_verification.get("capture_policy_version") or 0) < CAPTURE_POLICY_VERSION:
                stats["legacy_capture_policy_picks_skipped"] += 1
                fixed_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
                executable_labels = outcomes.setdefault("executable_labels", {})
                for window_name, stored_mark in fixed_marks.items():
                    if not isinstance(stored_mark, dict) or executable_labels.get(window_name) is not None:
                        continue
                    target = targets.get(window_name)
                    stored_at = str(stored_mark.get("captured_at_utc") or "")
                    label = _executable_label(
                        entry,
                        pick,
                        stored_mark,
                        window_name=window_name,
                        horizon_target=target,
                        captured_at_utc=stored_at,
                        max_capture_delay_seconds=CAPTURE_DELAY_LIMITS_SECONDS.get(window_name),
                    ) if target is not None and stored_at else None
                    if label is None:
                        stats["executable_labels_skipped"] += 1
                    else:
                        executable_labels[window_name] = label
                        stats["executable_labels_written"] += 1
                        changed = True
                continue
            symbol = str(pick.get("contract_symbol") or "").strip().upper()
            quote = quotes_by_symbol.get(symbol)
            fixed_marks = outcomes.setdefault("fixed_exit_marks", {})
            executable_labels = outcomes.setdefault("executable_labels", {})
            capture_attempts = outcomes.setdefault("capture_attempts", {})
            entry_mark = _entry_mark(pick)
            trajectory_active = _trajectory_capture_active(
                str(pick.get("run_generated_at_utc") or entry.get("run_generated_at_utc") or ""),
                now,
            )
            if trajectory_active:
                stats["trajectory_active_picks"] += 1
                if quote is None:
                    stats["trajectory_quotes_missing"] += 1
                else:
                    trajectory_quote_age = _broker_quote_age_seconds(quote, now)
                    if trajectory_quote_age is not None and (
                        trajectory_quote_age > MAX_BROKER_QUOTE_AGE_SECONDS or trajectory_quote_age < -60
                    ):
                        stats["trajectory_quotes_stale"] += 1
                    elif _append_trajectory_mark(
                        outcomes,
                        quote,
                        captured_at_utc=captured_at,
                        entry_mark=entry_mark,
                    ):
                        stats["trajectory_marks_written"] += 1
                        changed = True
            for window_name, is_due in due.items():
                if not is_due:
                    continue
                target = targets.get(window_name)
                if target is None:
                    continue
                stats["capture_windows_due"] += 1
                state, delay_seconds, limit_seconds = _capture_window_state(now, target, window_name)
                if state == "missed_live_window":
                    prior_attempt = capture_attempts.get(window_name)
                    prior_status = prior_attempt.get("status") if isinstance(prior_attempt, dict) else None
                    if fixed_marks.get(window_name) is None and prior_status != state:
                        capture_attempts[window_name] = _capture_attempt_payload(
                            status=state,
                            target=target,
                            attempted_at=captured_at,
                            delay_seconds=delay_seconds,
                            limit_seconds=limit_seconds,
                        )
                        changed = True
                        stats["capture_windows_newly_missed"] += 1
                    stats["capture_windows_missed"] += 1
                    continue
                if quote is None:
                    stats["quotes_missing"] += 1
                    stats["capture_windows_quote_missing"] += 1
                    capture_attempts[window_name] = _capture_attempt_payload(
                        status="quote_missing_retryable",
                        target=target,
                        attempted_at=captured_at,
                        delay_seconds=delay_seconds,
                        limit_seconds=limit_seconds,
                    )
                    changed = True
                    continue
                broker_quote_age = _broker_quote_age_seconds(quote, now)
                if broker_quote_age is not None and (
                    broker_quote_age > MAX_BROKER_QUOTE_AGE_SECONDS or broker_quote_age < -60
                ):
                    stats["capture_windows_stale_quote"] += 1
                    capture_attempts[window_name] = _capture_attempt_payload(
                        status="stale_quote_retryable",
                        target=target,
                        attempted_at=captured_at,
                        delay_seconds=delay_seconds,
                        limit_seconds=limit_seconds,
                        broker_quote_age_seconds=broker_quote_age,
                    )
                    changed = True
                    continue
                if fixed_marks.get(window_name) is None:
                    fixed_marks[window_name] = _mark_payload(quote, captured_at_utc=captured_at, entry_mark=entry_mark)
                    stats["marks_written"] += 1
                    changed = True
                if executable_labels.get(window_name) is not None:
                    continue
                stored_mark = fixed_marks.get(window_name) if isinstance(fixed_marks.get(window_name), dict) else {}
                stored_at = str(stored_mark.get("captured_at_utc") or "")
                label = _executable_label(
                    entry,
                    pick,
                    stored_mark,
                    window_name=window_name,
                    horizon_target=target,
                    captured_at_utc=stored_at,
                    max_capture_delay_seconds=limit_seconds,
                ) if stored_at else None
                if label is None:
                    stats["executable_labels_skipped"] += 1
                else:
                    executable_labels[window_name] = label
                    stats["executable_labels_written"] += 1
                    changed = True
                capture_attempts[window_name] = _capture_attempt_payload(
                    status="captured_valid",
                    target=target,
                    attempted_at=stored_at or captured_at,
                    delay_seconds=delay_seconds,
                    limit_seconds=limit_seconds,
                    broker_quote_age_seconds=broker_quote_age,
                )
                stats["capture_windows_valid"] += 1
                changed = True
            _update_path_rules(outcomes)
            if all(fixed_marks.get(name) is not None for name in ("one_hour", "end_of_day", "next_day_close", "friday_close")):
                outcomes["status"] = "complete"
            elif any(fixed_marks.get(name) is not None for name in ("one_hour", "end_of_day", "next_day_close", "friday_close")):
                outcomes["status"] = "partial"
            else:
                outcomes["status"] = "pending"
            quote_verification["outcome_quotes_captured"] = outcomes.get("status") in {"partial", "complete"}
            attempted = [attempt for attempt in capture_attempts.values() if isinstance(attempt, dict)]
            quote_verification["capture_integrity_passed"] = (
                all(attempt.get("status") == "captured_valid" for attempt in attempted)
                if attempted else None
            )

    outcome_summary = _outcome_summary(entries)
    stats["picks_completed"] = outcome_summary["complete"]
    stats["picks_partial"] = outcome_summary["partial"]
    stats["picks_pending"] = outcome_summary["pending"]
    stats["ledger_changed"] = int(changed)
    if changed:
        updated["updated_at_utc"] = captured_at
    # Persist an operational heartbeat even when a retried run writes no new
    # market mark.  Capture health must describe the latest scheduled attempt,
    # not whichever attempt most recently changed an outcome label.
    updated["last_capture_attempt_at_utc"] = captured_at
    updated["last_mark_summary"] = stats
    updated["outcome_summary"] = outcome_summary
    return updated, stats


def backfill_executable_labels_from_fixed_marks(
    ledger: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Recover strict labels from immutable, timely historical bid/ask marks only.

    This never fetches a current quote and never changes a stored mark. It is
    therefore safe for rebuilding research labels without contaminating a past
    horizon with present market data.
    """
    updated = json.loads(json.dumps(ledger))
    stats = {"marks_seen": 0, "labels_written": 0, "labels_skipped": 0}
    entries = updated.get("entries") if isinstance(updated.get("entries"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        targets = _fixed_exit_targets(str(entry.get("run_generated_at_utc") or "")) or {}
        picks = entry.get("picks") if isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
            fixed_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
            executable_labels = outcomes.setdefault("executable_labels", {})
            for window_name, stored_mark in fixed_marks.items():
                if not isinstance(stored_mark, dict) or executable_labels.get(window_name) is not None:
                    continue
                stats["marks_seen"] += 1
                target = targets.get(window_name)
                stored_at = str(stored_mark.get("captured_at_utc") or "")
                label = _executable_label(
                    entry,
                    pick,
                    stored_mark,
                    window_name=window_name,
                    horizon_target=target,
                    captured_at_utc=stored_at,
                    max_capture_delay_seconds=CAPTURE_DELAY_LIMITS_SECONDS.get(window_name),
                ) if target is not None and stored_at else None
                if label is None:
                    stats["labels_skipped"] += 1
                else:
                    executable_labels[window_name] = label
                    stats["labels_written"] += 1
    return updated, stats


def fetch_tradier_quotes(
    symbols: list[str],
    *,
    env: dict[str, str] | None = None,
    batch_size: int | None = None,
) -> dict[str, dict[str, Any]]:
    source = env or os.environ
    token = str(source.get("TRADIER_ACCESS_TOKEN") or source.get("OROGRAPHIC_TRADIER_ACCESS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Tradier access token is not configured.")
    requested_base_url = str(source.get("TRADIER_BASE_URL") or source.get("OROGRAPHIC_TRADIER_BASE_URL") or "").strip()
    use_sandbox = _env_truthy(source.get("TRADIER_SANDBOX_MODE")) or "sandbox.tradier.com" in requested_base_url
    base_url = (requested_base_url or (DEFAULT_SANDBOX_BASE_URL if use_sandbox else DEFAULT_LIVE_BASE_URL)).rstrip("/")
    cleaned = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if str(symbol).strip()))
    if not cleaned:
        return {}
    effective_batch_size = max(batch_size or _quote_batch_size(source), 1)
    retries = _quote_request_retries(source)
    quotes: dict[str, dict[str, Any]] = {}
    for start in range(0, len(cleaned), effective_batch_size):
        batch = cleaned[start : start + effective_batch_size]
        url = f"{base_url}/markets/quotes?{urlencode({'symbols': ','.join(batch), 'greeks': 'false'})}"
        request = Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
        for attempt in range(retries + 1):
            try:
                with urlopen(request, timeout=DEFAULT_TRADIER_QUOTE_TIMEOUT_SECONDS) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except OSError as exc:
                if not _is_transient_quote_error(exc) or attempt == retries:
                    raise
                time_module.sleep(attempt + 1)
        quotes.update(normalize_quotes(payload))
    return quotes


def mark_prospective_ledger_file(path: str | Path, *, max_symbols: int = 500) -> tuple[Path, dict[str, int]]:
    ledger_path = Path(path)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    symbols: list[str] = []
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict):
            continue
        targets = _fixed_exit_targets(str(entry.get("run_generated_at_utc") or "")) or {}
        for pick in entry.get("picks", []):
            if not isinstance(pick, dict) or not str(pick.get("contract_symbol") or "").strip():
                continue
            outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
            verification = outcomes.get("quote_verification") if isinstance(outcomes.get("quote_verification"), dict) else {}
            if int(verification.get("capture_policy_version") or 0) < CAPTURE_POLICY_VERSION:
                continue
            fixed_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
            capture_is_allowed = any(
                fixed_marks.get(window_name) is None
                and _capture_window_state(now, target, window_name)[0] == "capture_allowed"
                for window_name, target in targets.items()
            )
            trajectory_is_active = _trajectory_capture_active(
                str(pick.get("run_generated_at_utc") or entry.get("run_generated_at_utc") or ""),
                now,
            )
            if capture_is_allowed or trajectory_is_active:
                symbols.append(str(pick["contract_symbol"]).strip().upper())
    unique_symbols = list(dict.fromkeys(symbols))[:max(max_symbols, 1)]
    quotes = fetch_tradier_quotes(unique_symbols) if unique_symbols else {}
    updated, stats = mark_prospective_ledger(ledger, quotes, now_utc=now)
    if updated != ledger:
        ledger_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    return ledger_path, stats
