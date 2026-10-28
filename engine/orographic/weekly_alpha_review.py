"""Weekly alpha review for the single production lane versus research and Cirrus."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
import re

from engine.orographic.shared_research_mart import OROGRAPHIC_FEATURE_SCHEMA_VERSION
from engine.orographic.trajectory_exit_overlay import (
    ARTIFACT as TRAJECTORY_EXIT_OVERLAY,
    EARLY_HARVEST_ARTIFACT,
    WINNER_RUN_ARTIFACT,
    evaluate_early_harvest_overlay,
    evaluate_trajectory_exit_overlay,
    evaluate_winner_run_overlay,
)


PRODUCTION_LANE = "production_v2_council_live_board"
HOLD_OUT_CHALLENGER = "holdout_top1_vs_live_v1"
TIGHT_SPREAD_CHALLENGER = "tight_spread_holdout_v1"
PAIRED_OPPOSITE_CHALLENGER = "paired_opposite_side_v1"
FRICTION_VETO_VALUE = "friction_veto_value_v1"
FRICTION_VETO_PAIRED = "friction_veto_paired_v1"
RESEARCH_SIDE_SPLIT = "research_side_split_v1"
SAME_SYMBOL_REPEAT = "same_symbol_repeat_v1"
MART_STALE_AFTER = timedelta(days=7)
KILL_SWITCH_RESOLVED = 10
REVIEW_SCHEMA_VERSION = 6


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _latest_mark(pick: dict[str, Any]) -> tuple[str | None, float | None]:
    marks = _as_dict(_as_dict(pick.get("outcomes")).get("fixed_exit_marks"))
    for window in ("friday_close", "next_day_close", "end_of_day", "one_hour"):
        pnl = _number(_as_dict(marks.get(window)).get("pnl_pct_from_emission"))
        if pnl is not None:
            return window, pnl
    return None, None


def _decision_score(pick: dict[str, Any]) -> float | None:
    scores = _as_dict(pick.get("scores"))
    for key in (
        "final_candidate_score",
        "expected_tail_utility",
        "forge_score",
        "learned_rank_score",
    ):
        value = _number(scores.get(key))
        if value is not None:
            return value
    for key in ("expected_tail_utility", "forge_score", "prob_big_win"):
        value = _number(pick.get(key))
        if value is not None:
            return value
    return None


def _summarize_returns(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "resolved": 0,
            "win_rate": None,
            "mean_return": None,
            "equal_weight_sum": None,
        }
    wins = sum(1 for value in values if value > 0)
    return {
        "resolved": len(values),
        "win_rate": round(wins / len(values), 4),
        "mean_return": round(sum(values) / len(values), 4),
        "equal_weight_sum": round(sum(values), 4),
    }


def week_bounds(as_of_utc: datetime) -> tuple[datetime, datetime]:
    end = as_of_utc.astimezone(UTC)
    return end - timedelta(days=7), end


def _in_week(value: object, start: datetime, end: datetime) -> bool:
    parsed = _parse_dt(value)
    return parsed is not None and start <= parsed <= end


def _week_entries(source: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]:
    return [
        entry
        for entry in _as_list(source.get("entries"))
        if isinstance(entry, dict) and _in_week(entry.get("run_generated_at_utc"), start, end)
    ]


def _lane_scorecard(entries: list[dict[str, Any]]) -> dict[str, Any]:
    week_picks: list[dict[str, Any]] = []
    for entry in entries:
        for pick in _as_list(entry.get("picks")):
            if isinstance(pick, dict):
                week_picks.append(pick)
    by_lane: dict[str, dict[str, Any]] = {}
    counts = Counter(str(pick.get("lane") or "unknown") for pick in week_picks)
    for lane, count in counts.items():
        lane_picks = [pick for pick in week_picks if str(pick.get("lane") or "unknown") == lane]
        resolved: list[float] = []
        windows: Counter[str] = Counter()
        for pick in lane_picks:
            window, pnl = _latest_mark(pick)
            if pnl is not None and window is not None:
                resolved.append(pnl)
                windows[window] += 1
        by_lane[lane] = {
            "picks": count,
            "mark_windows": dict(windows),
            **_summarize_returns(resolved),
            "by_option_type": _side_scorecard(lane_picks),
        }
    return {"picks": len(week_picks), "lanes": by_lane}


def _board_week(board: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    runs = _week_entries(board, start, end)
    live: list[dict[str, Any]] = []
    for entry in runs:
        for row in _as_list(entry.get("live_board")):
            if isinstance(row, dict):
                live.append({
                    "run_generated_at_utc": entry.get("run_generated_at_utc"),
                    "symbol": row.get("symbol"),
                    "contract_symbol": row.get("contract_symbol"),
                    "option_type": row.get("option_type"),
                    "ask": row.get("ask"),
                    "spread_pct": row.get("spread_pct"),
                })
    symbols = [str(row.get("symbol") or "") for row in live if row.get("symbol")]
    return {
        "scans": len(runs),
        "abstain_scans": sum(1 for entry in runs if bool(entry.get("abstain"))),
        "live_emissions": live,
        "unique_live_days": len({str(row["run_generated_at_utc"])[:10] for row in live}),
        "unique_live_symbols": len(set(symbols)),
        "live_symbol_counts": dict(Counter(symbols)),
    }


def _holdout_challenger(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare Council's live pick to the top holdout by decision-time score.

    Realized P&L is never used to choose the challenger contract. When scores
    are missing, the first holdout in emission order is treated as rank-1.
    """
    paired: list[dict[str, Any]] = []
    for entry in entries:
        live_rows = [pick for pick in _as_list(entry.get("picks")) if pick.get("lane") == "live"]
        holdouts = [pick for pick in _as_list(entry.get("picks")) if pick.get("lane") == "council_holdout"]
        live = live_rows[0] if live_rows else None
        live_window, live_pnl = _latest_mark(live) if isinstance(live, dict) else (None, None)
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for index, pick in enumerate(holdouts):
            window, pnl = _latest_mark(pick)
            if pnl is None:
                continue
            score = _decision_score(pick)
            # Higher decision score wins; missing scores keep emission order.
            rank_key = score if score is not None else -index
            scored.append((rank_key, index, pick))
        if live_pnl is None or not scored:
            continue
        scored.sort(key=lambda row: (-row[0], row[1]))
        best_pick = scored[0][2]
        best_window, best_pnl = _latest_mark(best_pick)
        paired.append({
            "run_generated_at_utc": entry.get("run_generated_at_utc"),
            "live_symbol": live.get("symbol") if isinstance(live, dict) else None,
            "live_return": live_pnl,
            "live_window": live_window,
            "holdout_top1_symbol": best_pick.get("symbol"),
            "holdout_top1_contract": best_pick.get("contract_symbol"),
            "holdout_top1_score": _decision_score(best_pick),
            "holdout_top1_return": best_pnl,
            "holdout_top1_window": best_window,
            "selection": "decision_time_score" if _decision_score(best_pick) is not None else "emission_order",
            "return_lift": round((best_pnl or 0.0) - live_pnl, 4),
        })
    lifts = [row["return_lift"] for row in paired]
    mean_lift = round(sum(lifts) / len(lifts), 4) if lifts else None
    promotion_ready = False
    if not paired:
        reason = (
            "No paired live/holdout scans with resolved marks this week. "
            "Keep collecting observation-only evidence; it cannot route orders."
        )
    elif len(paired) < 30:
        direction = "beat" if (mean_lift or 0) > 0 else "trailed"
        reason = (
            f"Holdout top-1 {direction} the live pick on {len(paired)} paired scans "
            f"(mean lift {mean_lift}). Friday-close labels and 30 paired days are "
            "still required before any production change."
        )
    else:
        reason = (
            "Paired-day count is still below the promotion bar after uncertainty controls."
        )
    return {
        "experiment_id": HOLD_OUT_CHALLENGER,
        "authority": "observation_only_never_used_for_routing",
        "selection_rule": "highest decision-time score among council_holdout; emission order if scores are absent",
        "paired_scans": len(paired),
        "mean_return_lift": mean_lift,
        "positive_lift_rate": round(sum(1 for value in lifts if value > 0) / len(lifts), 4) if lifts else None,
        "rows": paired,
        "promotion_ready": promotion_ready,
        "reason": reason,
    }


def _spread_pct(pick: dict[str, Any]) -> float | None:
    quote = _as_dict(pick.get("emission_quote"))
    return _number(quote.get("spread_pct")) if quote else _number(pick.get("spread_pct"))


def _option_type(pick: dict[str, Any]) -> str:
    explicit = str(pick.get("option_type") or "").strip().lower()
    if explicit in {"call", "put"}:
        return explicit
    contract = str(pick.get("contract_symbol") or "").upper()
    match = re.search(r"\d{6}([CP])\d+$", contract)
    if match:
        return "call" if match.group(1) == "C" else "put"
    return ""


def _side_scorecard(picks: list[dict[str, Any]]) -> dict[str, Any]:
    by_side: dict[str, dict[str, Any]] = {}
    for side in ("call", "put"):
        side_picks = [pick for pick in picks if _option_type(pick) == side]
        if not side_picks:
            continue
        resolved: list[float] = []
        windows: Counter[str] = Counter()
        for pick in side_picks:
            window, pnl = _latest_mark(pick)
            if pnl is not None and window is not None:
                resolved.append(pnl)
                windows[window] += 1
        by_side[side] = {
            "picks": len(side_picks),
            "mark_windows": dict(windows),
            **_summarize_returns(resolved),
        }
    return by_side


def _challenger_reason(paired: list[dict[str, Any]], mean_lift: float | None, *, empty: str) -> str:
    if not paired:
        return empty
    direction = "beat" if (mean_lift or 0) > 0 else "trailed"
    return (
        f"Challenger {direction} the live pick on {len(paired)} paired scans "
        f"(mean lift {mean_lift}). Friday-close labels and 30 paired days are "
        "still required before any production change."
    )


def _tight_spread_challenger(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace score-rank holdout as the execution-quality watch: lowest entry spread."""
    paired: list[dict[str, Any]] = []
    for entry in entries:
        live_rows = [pick for pick in _as_list(entry.get("picks")) if pick.get("lane") == "live"]
        holdouts = [pick for pick in _as_list(entry.get("picks")) if pick.get("lane") == "council_holdout"]
        live = live_rows[0] if live_rows else None
        live_window, live_pnl = _latest_mark(live) if isinstance(live, dict) else (None, None)
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for index, pick in enumerate(holdouts):
            window, pnl = _latest_mark(pick)
            if pnl is None:
                continue
            spread = _spread_pct(pick)
            if spread is None:
                continue
            scored.append((spread, index, pick))
        if live_pnl is None or not scored:
            continue
        scored.sort(key=lambda row: (row[0], row[1]))
        best_pick = scored[0][2]
        best_window, best_pnl = _latest_mark(best_pick)
        paired.append({
            "run_generated_at_utc": entry.get("run_generated_at_utc"),
            "live_symbol": live.get("symbol") if isinstance(live, dict) else None,
            "live_return": live_pnl,
            "live_window": live_window,
            "live_spread_pct": _spread_pct(live) if isinstance(live, dict) else None,
            "holdout_symbol": best_pick.get("symbol"),
            "holdout_contract": best_pick.get("contract_symbol"),
            "holdout_spread_pct": scored[0][0],
            "holdout_return": best_pnl,
            "holdout_window": best_window,
            "return_lift": round((best_pnl or 0.0) - live_pnl, 4),
        })
    lifts = [row["return_lift"] for row in paired]
    mean_lift = round(sum(lifts) / len(lifts), 4) if lifts else None
    return {
        "experiment_id": TIGHT_SPREAD_CHALLENGER,
        "authority": "observation_only_never_used_for_routing",
        "selection_rule": "lowest decision-time entry spread_pct among council_holdout",
        "paired_scans": len(paired),
        "mean_return_lift": mean_lift,
        "positive_lift_rate": round(sum(1 for value in lifts if value > 0) / len(lifts), 4) if lifts else None,
        "rows": paired,
        "promotion_ready": False,
        "reason": _challenger_reason(
            paired,
            mean_lift,
            empty=(
                "No paired live/holdout scans with both resolved marks and entry spreads. "
                "Keep collecting observation-only evidence; it cannot route orders."
            ),
        ),
    }


def _paired_opposite_challenger(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Same-symbol opposite-side observation versus the live contract.

    Decision-time rule: take the matched paired_side_observation of the other
    option type. Realized P&L is never used to choose the contract.
    """
    paired: list[dict[str, Any]] = []
    for entry in entries:
        picks = [pick for pick in _as_list(entry.get("picks")) if isinstance(pick, dict)]
        live_rows = [pick for pick in picks if pick.get("lane") == "live"]
        live = live_rows[0] if live_rows else None
        if not isinstance(live, dict):
            continue
        live_window, live_pnl = _latest_mark(live)
        live_side = _option_type(live)
        live_symbol = str(live.get("symbol") or "")
        if live_pnl is None or not live_side or not live_symbol:
            continue
        opposites = [
            pick
            for pick in picks
            if pick.get("lane") == "paired_side_observation"
            and str(pick.get("symbol") or "") == live_symbol
            and _option_type(pick)
            and _option_type(pick) != live_side
        ]
        scored: list[tuple[int, dict[str, Any]]] = []
        for index, pick in enumerate(opposites):
            _window, pnl = _latest_mark(pick)
            if pnl is None:
                continue
            scored.append((index, pick))
        if not scored:
            continue
        best_pick = scored[0][1]
        best_window, best_pnl = _latest_mark(best_pick)
        paired.append({
            "run_generated_at_utc": entry.get("run_generated_at_utc"),
            "live_symbol": live.get("symbol"),
            "live_side": live_side,
            "live_return": live_pnl,
            "live_window": live_window,
            "opposite_side": _option_type(best_pick),
            "opposite_contract": best_pick.get("contract_symbol"),
            "opposite_return": best_pnl,
            "opposite_window": best_window,
            "return_lift": round((best_pnl or 0.0) - live_pnl, 4),
        })
    lifts = [row["return_lift"] for row in paired]
    mean_lift = round(sum(lifts) / len(lifts), 4) if lifts else None
    return {
        "experiment_id": PAIRED_OPPOSITE_CHALLENGER,
        "authority": "observation_only_never_used_for_routing",
        "selection_rule": "same-symbol paired_side_observation of the opposite option type",
        "paired_scans": len(paired),
        "mean_return_lift": mean_lift,
        "positive_lift_rate": round(sum(1 for value in lifts if value > 0) / len(lifts), 4) if lifts else None,
        "rows": paired,
        "promotion_ready": False,
        "reason": _challenger_reason(
            paired,
            mean_lift,
            empty=(
                "No live picks this week had a resolved opposite-side paired observation. "
                "Keep capturing matched call/put rows; they cannot route orders."
            ),
        ),
    }


def _research_side_split(lanes: dict[str, Any]) -> dict[str, Any]:
    """Puts versus calls on the only research surface that made money this week.

    Decision-time production still emits one Council contract. This split is
    observation-only: a one-week put/call gap is a tape, not a Scout rewrite.
    """
    paired = _as_dict(_as_dict(lanes.get("paired_side_observation")).get("by_option_type"))
    live = _as_dict(_as_dict(lanes.get("live")).get("by_option_type"))
    call = _as_dict(paired.get("call"))
    put = _as_dict(paired.get("put"))
    live_call = int(_as_dict(live.get("call")).get("picks") or 0)
    live_put = int(_as_dict(live.get("put")).get("picks") or 0)
    call_mean = _number(call.get("mean_return"))
    put_mean = _number(put.get("mean_return"))
    if call_mean is None:
        call_mean = _number(_as_dict(live.get("call")).get("mean_return"))
    if put_mean is None:
        put_mean = _number(_as_dict(live.get("put")).get("mean_return"))
    put_beats_call = (
        call_mean is not None
        and put_mean is not None
        and put_mean > call_mean
    )
    live_on_losing_side = bool(
        put_beats_call and live_call > 0 and live_put == 0 and (call_mean or 0) < 0
    )
    if call_mean is None or put_mean is None:
        reason = (
            "Paired-side observations are not yet split into resolved calls and puts. "
            "Keep capturing both OCC sides; do not change Scout."
        )
        action = "open_observation_only"
    else:
        if call.get("resolved") and put.get("resolved"):
            reason = (
                f"Paired-side puts returned {put_mean:.2%} ({put.get('resolved')} resolved, "
                f"win rate {put.get('win_rate')}) versus calls {call_mean:.2%} "
                f"({call.get('resolved')} resolved, win rate {call.get('win_rate')}). "
            )
        else:
            reason = (
                f"Resolved puts returned {put_mean:.2%} versus calls {call_mean:.2%} "
                "after filling a missing OCC side from the live lane. "
            )
        if live_on_losing_side:
            reason += (
                "This week's live emission was a call on the losing side. "
                "Keep as observation-only; a one-week tape is not a production side change."
            )
        else:
            reason += "Keep collecting Friday-close labels; do not change Scout side."
        action = "keep_observation_only"
    return {
        "experiment_id": RESEARCH_SIDE_SPLIT,
        "authority": "observation_only_never_used_for_routing",
        "selection_rule": "split paired_side_observation marks by inferred OCC option_type",
        "paired_side_calls": call or None,
        "paired_side_puts": put or None,
        "live_call_picks": live_call,
        "live_put_picks": live_put,
        "put_minus_call_return": (
            round(put_mean - call_mean, 4) if put_mean is not None and call_mean is not None else None
        ),
        "live_emitted_losing_side": live_on_losing_side,
        "action": action,
        "promotion_ready": False,
        "reason": reason,
    }


def _friction_veto_value(lanes: dict[str, Any]) -> dict[str, Any]:
    veto = _as_dict(lanes.get("friction_veto"))
    mean = _number(veto.get("mean_return"))
    avoided = round(-mean, 4) if mean is not None else None
    working = mean is not None and mean < 0
    if mean is None:
        reason = "No resolved friction-veto marks this week. Keep the execution gate; it is not a product lane."
        action = "keep_as_gate"
    elif working:
        reason = (
            f"Vetoed names returned {mean:.2%} after friction ({veto.get('resolved')} resolved, "
            f"win rate {veto.get('win_rate')}). Negative veto P&L is avoided loss; keep the gate."
        )
        action = "keep_as_gate"
    else:
        reason = (
            f"Vetoed names returned {mean:.2%} after friction. The gate may be blocking winners; "
            "keep observation-only and do not loosen production friction."
        )
        action = "review_do_not_loosen"
    return {
        "experiment_id": FRICTION_VETO_VALUE,
        "authority": "observation_only_never_used_for_routing",
        "interpretation": "negative_veto_returns_are_avoided_loss",
        "picks": veto.get("picks"),
        "resolved": veto.get("resolved"),
        "win_rate": veto.get("win_rate"),
        "veto_mean_return": mean,
        "avoided_mean_return": avoided,
        "gate_working": working,
        "action": action,
        "promotion_ready": False,
        "reason": reason,
    }


def _friction_veto_paired(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Same-scan highest-score friction veto versus the live pick.

    Unconditional veto means mix winners the gate never saw at decision time
    with names Council actually had to choose against. This estimand uses only
    the highest decision-time score on the veto list in the same scan as a
    live emission. Realized P&L is never used to choose the vetoed name.
    """
    paired: list[dict[str, Any]] = []
    for entry in entries:
        picks = [pick for pick in _as_list(entry.get("picks")) if isinstance(pick, dict)]
        live_rows = [pick for pick in picks if pick.get("lane") == "live"]
        live = live_rows[0] if live_rows else None
        live_window, live_pnl = _latest_mark(live) if isinstance(live, dict) else (None, None)
        if live_pnl is None:
            continue
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for index, pick in enumerate(picks):
            if pick.get("lane") != "friction_veto":
                continue
            window, pnl = _latest_mark(pick)
            if pnl is None:
                continue
            score = _decision_score(pick)
            rank_key = score if score is not None else -index
            scored.append((rank_key, index, pick))
        if not scored:
            continue
        scored.sort(key=lambda row: (-row[0], row[1]))
        best_pick = scored[0][2]
        best_window, best_pnl = _latest_mark(best_pick)
        paired.append({
            "run_generated_at_utc": entry.get("run_generated_at_utc"),
            "live_symbol": live.get("symbol") if isinstance(live, dict) else None,
            "live_return": live_pnl,
            "live_window": live_window,
            "veto_symbol": best_pick.get("symbol"),
            "veto_contract": best_pick.get("contract_symbol"),
            "veto_score": _decision_score(best_pick),
            "veto_return": best_pnl,
            "veto_window": best_window,
            "return_lift": round((best_pnl or 0.0) - live_pnl, 4),
        })
    lifts = [row["return_lift"] for row in paired]
    mean_lift = round(sum(lifts) / len(lifts), 4) if lifts else None
    blocking = mean_lift is not None and mean_lift > 0
    if not paired:
        reason = (
            "No same-scan live/veto pairs with resolved marks this week. "
            "Keep the friction gate; the paired estimand cannot loosen it."
        )
        action = "keep_as_gate"
    elif blocking:
        reason = (
            f"Highest-score veto beat the live pick on {len(paired)} paired scans "
            f"(mean lift {mean_lift}). The gate may be blocking winners; keep "
            "observation-only and do not loosen production friction."
        )
        action = "review_do_not_loosen"
    else:
        reason = (
            f"Highest-score veto trailed the live pick on {len(paired)} paired scans "
            f"(mean lift {mean_lift}). Negative paired lift is avoided loss; keep the gate."
        )
        action = "keep_as_gate"
    return {
        "experiment_id": FRICTION_VETO_PAIRED,
        "authority": "observation_only_never_used_for_routing",
        "selection_rule": "highest decision-time score among same-scan friction_veto names",
        "paired_scans": len(paired),
        "mean_return_lift": mean_lift,
        "positive_lift_rate": round(sum(1 for value in lifts if value > 0) / len(lifts), 4) if lifts else None,
        "rows": paired,
        "gate_may_block_winners": blocking,
        "action": action,
        "promotion_ready": False,
        "reason": reason,
    }


def _same_symbol_repeat(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Skip later live prints of an underlying already taken this week.

    Production cooldown is per contract, not per symbol, so a new strike or
    expiry of the same name can reprint inside 72 hours. Decision-time rule:
    keep the first live emission of a symbol; later same-symbol prints are
    skipped to cash (return 0). Realized P&L is never used to pick a substitute.
    """
    seen: dict[str, str] = {}
    paired: list[dict[str, Any]] = []
    ordered = sorted(entries, key=lambda entry: str(entry.get("run_generated_at_utc") or ""))
    for entry in ordered:
        run_at = str(entry.get("run_generated_at_utc") or "")
        live_rows = [
            pick
            for pick in _as_list(entry.get("picks"))
            if isinstance(pick, dict) and pick.get("lane") == "live"
        ]
        live = live_rows[0] if live_rows else None
        if not isinstance(live, dict):
            continue
        window, pnl = _latest_mark(live)
        symbol = str(live.get("symbol") or "")
        if pnl is None or not symbol:
            continue
        first_at = seen.get(symbol)
        if first_at is None:
            seen[symbol] = run_at
            continue
        paired.append({
            "run_generated_at_utc": run_at,
            "live_symbol": symbol,
            "live_contract": live.get("contract_symbol"),
            "live_return": pnl,
            "live_window": window,
            "first_live_at_utc": first_at,
            "skip_return": 0.0,
            "return_lift": round(0.0 - pnl, 4),
        })
    lifts = [row["return_lift"] for row in paired]
    mean_lift = round(sum(lifts) / len(lifts), 4) if lifts else None
    repeats = len(paired)
    unique_live = len(seen)
    if not paired:
        reason = (
            "No same-symbol live reprints this week. Keep the per-contract cooldown; "
            "do not add a production symbol cooldown from an empty tape."
        )
        action = "open_observation_only"
    else:
        direction = "beat" if (mean_lift or 0) > 0 else "trailed"
        reason = (
            f"Skipping later same-symbol live prints {direction} taking them on "
            f"{repeats} reprints ({unique_live} unique live names, mean lift {mean_lift}). "
            "Production cooldown remains per contract; this is observation-only."
        )
        action = "keep_observation_only"
    return {
        "experiment_id": SAME_SYMBOL_REPEAT,
        "authority": "observation_only_never_used_for_routing",
        "selection_rule": "skip later live emissions of a symbol already taken this week; cash return 0",
        "paired_scans": repeats,
        "unique_live_symbols": unique_live,
        "mean_return_lift": mean_lift,
        "positive_lift_rate": round(sum(1 for value in lifts if value > 0) / len(lifts), 4) if lifts else None,
        "rows": paired,
        "action": action,
        "promotion_ready": False,
        "reason": reason,
    }


def _kill_switch(
    *,
    week_live_marks: dict[str, Any],
    evidence: dict[str, Any],
    rebuild_readiness: dict[str, Any],
) -> dict[str, Any]:
    """Count rolling resolved live P&L. Never disable routing from this review."""
    recent: list[tuple[str, float, str | None, object, object]] = []
    for entry in _as_list(evidence.get("entries")):
        if not isinstance(entry, dict):
            continue
        run_at = str(entry.get("run_generated_at_utc") or "")
        for pick in _as_list(entry.get("picks")):
            if not isinstance(pick, dict) or pick.get("lane") != "live":
                continue
            window, pnl = _latest_mark(pick)
            if pnl is None:
                continue
            recent.append((run_at, pnl, window, pick.get("symbol"), pick.get("contract_symbol")))
    recent.sort(key=lambda row: row[0])
    last = recent[-KILL_SWITCH_RESOLVED:]
    pnls = [row[1] for row in last]
    rolling_sum = round(sum(pnls), 4) if pnls else None
    resolved = len(pnls)
    remaining = max(0, KILL_SWITCH_RESOLVED - resolved)
    week_resolved = int(week_live_marks.get("resolved") or 0)
    week_sum = _number(week_live_marks.get("equal_weight_sum"))
    rebuild_ok = bool(rebuild_readiness.get("production_model_change_allowed"))
    fire_bar = (
        resolved >= KILL_SWITCH_RESOLVED
        and rolling_sum is not None
        and rolling_sum < 0
    )
    if fire_bar and rebuild_ok:
        status = "fire_eligible"
        reason = (
            f"Last {KILL_SWITCH_RESOLVED} resolved live marks have cumulative after-friction "
            f"return {rolling_sum}. Rebuild readiness is unblocked, but this review still "
            "does not disable Tradier; a human must arm the kill switch."
        )
    elif fire_bar:
        status = "watch"
        reason = (
            f"Last {KILL_SWITCH_RESOLVED} resolved live marks have cumulative after-friction "
            f"return {rolling_sum}, but rebuild readiness is blocked so production stays frozen "
            "rather than kill-switched. This week "
            f"{week_resolved} resolved (equal-weight sum {week_sum})."
        )
    elif resolved >= KILL_SWITCH_RESOLVED:
        status = "watch"
        reason = (
            f"{resolved}/{KILL_SWITCH_RESOLVED} resolved live marks on the rolling tape "
            f"(cumulative {rolling_sum} is not negative); this week {week_resolved} resolved "
            f"(equal-weight sum {week_sum}). Do not fire on a single losing week while the "
            "rolling ten stay non-negative, and do not change production artifacts while "
            "rebuild readiness is blocked."
        )
    else:
        status = "watch"
        reason = (
            f"{resolved}/{KILL_SWITCH_RESOLVED} resolved live marks on the rolling tape "
            f"(cumulative {rolling_sum}); this week {week_resolved} resolved "
            f"(equal-weight sum {week_sum}). Remaining {remaining} to the fire bar. "
            "Do not fire on intraweek partials, and do not change production artifacts "
            "while rebuild readiness is blocked."
        )
    return {
        "rule": (
            "Disable active routing if 10 resolved live picks have negative "
            "cumulative after-friction return."
        ),
        "status": status,
        "resolved_live_marks": resolved,
        "remaining_to_fire": remaining,
        "rolling_equal_weight_sum": rolling_sum,
        "week_resolved": week_resolved,
        "week_equal_weight_sum": week_sum,
        "reason": reason,
        "rebuild_production_change_allowed": rebuild_ok,
    }


def _join_live_marks(
    emissions: list[dict[str, Any]],
    research_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Join each live emission to its own scan mark.

    Repeated same-contract emissions (cooldown re-prints) must not overwrite
    each other. Key is (run_generated_at_utc, contract_symbol).
    """
    by_scan_contract: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in research_entries:
        run_at = str(entry.get("run_generated_at_utc") or "")
        for pick in _as_list(entry.get("picks")):
            if not isinstance(pick, dict) or pick.get("lane") != "live":
                continue
            contract = str(pick.get("contract_symbol") or "")
            window, pnl = _latest_mark(pick)
            if not contract or pnl is None:
                continue
            pick_run = str(pick.get("run_generated_at_utc") or run_at)
            by_scan_contract[(pick_run, contract)] = {
                "mark_window": window,
                "pnl_pct_from_emission": pnl,
                "option_type": pick.get("option_type"),
            }
    rows: list[dict[str, Any]] = []
    for emission in emissions:
        contract = str(emission.get("contract_symbol") or "")
        run_at = str(emission.get("run_generated_at_utc") or "")
        row = dict(emission)
        row.update(by_scan_contract.get((run_at, contract)) or {})
        rows.append(row)
    pnls = [
        float(row["pnl_pct_from_emission"])
        for row in rows
        if _number(row.get("pnl_pct_from_emission")) is not None
    ]
    return {"emissions_with_marks": rows, **_summarize_returns(pnls)}


def _primary_challenger(*candidates: dict[str, Any]) -> dict[str, Any]:
    """Feature the challenger with the most algebraic lift, not the largest |lift|.

    Absolute lift promoted the worst loser whenever a large negative print
    outranked a smaller (or positive) alternative. Algebraic lift is the alpha
    ranking: less negative beats more negative; positive beats both.
    """
    scored = [row for row in candidates if int(row.get("paired_scans") or 0) > 0]
    if not scored:
        return candidates[0] if candidates else {}

    def _algebraic_key(row: dict[str, Any]) -> tuple[float, int]:
        lift = _number(row.get("mean_return_lift"))
        lift_val = lift if lift is not None else float("-inf")
        return (lift_val, int(row.get("paired_scans") or 0))

    return max(scored, key=_algebraic_key)


def _lane_decisions(
    *,
    challenger: dict[str, Any],
    tight_spread: dict[str, Any],
    opposite: dict[str, Any],
    friction: dict[str, Any],
    friction_paired: dict[str, Any],
    side_split: dict[str, Any],
    same_symbol: dict[str, Any],
    overlay: dict[str, Any],
    early_harvest: dict[str, Any],
    winner_run: dict[str, Any],
    payoff: dict[str, Any],
    path_hazard: dict[str, Any],
    mart_shadow: dict[str, Any],
) -> list[dict[str, Any]]:
    payoff_replay = _as_dict(payoff.get("rank_replay"))
    active_top1 = _number(payoff_replay.get("active_top1_avg_net_return"))
    shadow_top1 = _number(payoff_replay.get("shadow_top1_avg_net_return"))
    path_status = str(path_hazard.get("status") or "hold")
    cross = _as_dict(mart_shadow.get("cross_system_comparison"))
    paired_raw = cross.get("paired_executable_outcomes")
    if paired_raw is None:
        paired_raw = _as_dict(
            _as_dict(mart_shadow.get("shadow_entry_gates")).get("paired_executable_outcomes")
        ).get("actual")
    paired_outcomes = int(paired_raw or 0)
    comparable_pairs = int(cross.get("live_direct_return_comparable_pairs") or 0)
    holdout_action = (
        "keep_observation_only" if int(challenger.get("paired_scans") or 0) > 0 else "open_observation_only"
    )
    overlay_lift = overlay.get("overall", {}).get("mean_return_lift") if isinstance(overlay.get("overall"), dict) else None
    overlay_hits = int(_as_dict(overlay.get("coverage")).get("target_hits") or 0) + int(
        _as_dict(overlay.get("coverage")).get("stop_hits") or 0
    )
    early_lift = (
        early_harvest.get("overall", {}).get("mean_return_lift")
        if isinstance(early_harvest.get("overall"), dict)
        else None
    )
    winner_lift = (
        winner_run.get("overall", {}).get("mean_return_lift")
        if isinstance(winner_run.get("overall"), dict)
        else None
    )
    winner_live_lift = (
        winner_run.get("live", {}).get("mean_return_lift")
        if isinstance(winner_run.get("live"), dict)
        else None
    )
    harvest_destroyed = (
        (overlay_lift is not None and overlay_lift <= 0)
        or (early_lift is not None and early_lift <= 0)
        or overlay_hits == 0
    )
    if overlay_hits == 0:
        overlay_action = "replace"
        overlay_replacement = WINNER_RUN_ARTIFACT
        overlay_note = (
            f"Zero target/stop fills this week; {WINNER_RUN_ARTIFACT} is the active exit experiment."
        )
    elif overlay_lift is not None and overlay_lift <= 0:
        overlay_action = "hold_do_not_promote"
        overlay_replacement = None
        overlay_note = "Fills exist but mean lift versus hold is not positive. No production exit change."
    else:
        overlay_action = "hold_do_not_promote"
        overlay_replacement = None
        overlay_note = "No production exit change."
    if harvest_destroyed:
        early_action = "replace"
        early_replacement = WINNER_RUN_ARTIFACT
        early_note = (
            "Mechanical +10% bid harvest / -40% bid stop versus hold-to-Friday. "
            f"Mean lift versus hold {early_lift}. Harvest overlays trail hold; "
            f"{WINNER_RUN_ARTIFACT} is the active exit experiment."
        )
    else:
        early_action = "hold_do_not_promote"
        early_replacement = None
        early_note = (
            "Mechanical +10% bid harvest / -40% bid stop versus hold-to-Friday. "
            f"Mean lift versus hold {early_lift}. Do not change live exits."
        )
    winner_action = "keep_observation_only"
    winner_note = (
        "Stop −50% bid and never harvest versus hold-to-Friday. "
        f"Mean lift versus hold {winner_lift}; live lift {winner_live_lift}. "
        "Observation-only; it cannot route exits. Promotion also requires "
        "non-negative live lift."
    )
    return [
        {
            "lane": PRODUCTION_LANE,
            "action": "keep",
            "authority": "production",
            "reason": (
                "Single production path remains Scout → tail-utility ranker → Council. "
                "No second live lane. Kill-switch watch stays on Friday after-friction live P&L."
            ),
        },
        {
            "lane": "moonshot",
            "action": "remain_retired",
            "authority": "none",
            "reason": "Moonshot has no Council, sizing, or broker authority and is not rebuilt into combined research datasets.",
        },
        {
            "lane": "shadow_board",
            "action": "remain_retired",
            "authority": "none",
            "reason": "Production scans force shadow allocation to zero; historical shadow fields are archive telemetry only.",
        },
        {
            "lane": "cost_aware_payoff_challenger",
            "action": "hold_do_not_promote",
            "authority": "observation_only",
            "reason": (
                "Challenger calibration is better than the active ranker, but rank replay is not: "
                f"active top-1 {active_top1} vs challenger top-1 {shadow_top1}."
            ),
        },
        {
            "lane": "path_hazard_challenger",
            "action": "replace",
            "replacement": TRAJECTORY_EXIT_OVERLAY,
            "authority": "observation_only",
            "reason": (
                f"The stored hazard fit remains `{path_status}` on zero valid pre-exit marks. "
                "It is replaced by a trajectory-mark exit overlay on current capture, not the 740-row stale file."
            ),
        },
        {
            "lane": TRAJECTORY_EXIT_OVERLAY,
            "action": overlay_action,
            "replacement": overlay_replacement,
            "authority": "observation_only",
            "reason": (
                "Mechanical +25% bid harvest / -50% bid stop versus hold-to-Friday on current trajectory marks. "
                f"Mean lift versus hold {overlay_lift}. "
                + overlay_note
            ),
            "mean_return_lift": overlay_lift,
            "resolved_picks": _as_dict(overlay.get("coverage")).get("resolved_picks"),
        },
        {
            "lane": EARLY_HARVEST_ARTIFACT,
            "action": early_action,
            "replacement": early_replacement,
            "authority": "observation_only",
            "reason": early_note,
            "mean_return_lift": early_lift,
            "resolved_picks": _as_dict(early_harvest.get("coverage")).get("resolved_picks"),
        },
        {
            "lane": WINNER_RUN_ARTIFACT,
            "action": winner_action,
            "authority": "observation_only",
            "reason": winner_note,
            "mean_return_lift": winner_lift,
            "live_mean_return_lift": winner_live_lift,
            "resolved_picks": _as_dict(winner_run.get("coverage")).get("resolved_picks"),
        },
        {
            "lane": "side_aware_scout_shadow_ledger",
            "action": "retire_stale_telemetry",
            "authority": "none",
            "reason": "Side-aware Scout is production-active; the shadow disagreement ledger is no longer a live experiment.",
        },
        {
            "lane": HOLD_OUT_CHALLENGER,
            "action": holdout_action,
            "authority": "observation_only",
            "reason": challenger.get("reason"),
            "paired_scans": challenger.get("paired_scans"),
            "mean_return_lift": challenger.get("mean_return_lift"),
        },
        {
            "lane": TIGHT_SPREAD_CHALLENGER,
            "action": "open_observation_only" if int(tight_spread.get("paired_scans") or 0) == 0 else "keep_observation_only",
            "authority": "observation_only",
            "reason": tight_spread.get("reason"),
            "paired_scans": tight_spread.get("paired_scans"),
            "mean_return_lift": tight_spread.get("mean_return_lift"),
        },
        {
            "lane": PAIRED_OPPOSITE_CHALLENGER,
            "action": "open_observation_only" if int(opposite.get("paired_scans") or 0) == 0 else "keep_observation_only",
            "authority": "observation_only",
            "reason": opposite.get("reason"),
            "paired_scans": opposite.get("paired_scans"),
            "mean_return_lift": opposite.get("mean_return_lift"),
        },
        {
            "lane": FRICTION_VETO_VALUE,
            "action": friction.get("action") or "keep_as_gate",
            "authority": "observation_only",
            "reason": friction.get("reason"),
            "avoided_mean_return": friction.get("avoided_mean_return"),
            "gate_working": friction.get("gate_working"),
        },
        {
            "lane": FRICTION_VETO_PAIRED,
            "action": friction_paired.get("action") or "keep_as_gate",
            "authority": "observation_only",
            "reason": friction_paired.get("reason"),
            "paired_scans": friction_paired.get("paired_scans"),
            "mean_return_lift": friction_paired.get("mean_return_lift"),
            "gate_may_block_winners": friction_paired.get("gate_may_block_winners"),
        },
        {
            "lane": RESEARCH_SIDE_SPLIT,
            "action": side_split.get("action") or "keep_observation_only",
            "authority": "observation_only",
            "reason": side_split.get("reason"),
            "put_minus_call_return": side_split.get("put_minus_call_return"),
            "live_emitted_losing_side": side_split.get("live_emitted_losing_side"),
        },
        {
            "lane": SAME_SYMBOL_REPEAT,
            "action": same_symbol.get("action") or "open_observation_only",
            "authority": "observation_only",
            "reason": same_symbol.get("reason"),
            "paired_scans": same_symbol.get("paired_scans"),
            "mean_return_lift": same_symbol.get("mean_return_lift"),
            "unique_live_symbols": same_symbol.get("unique_live_symbols"),
        },
        {
            "lane": "cirrus_paired_alpha",
            "action": "collect",
            "authority": "observation_only",
            "reason": (
                f"Only {comparable_pairs} directly comparable live-lane Cirrus/Orographic outcomes exist "
                f"({paired_outcomes} raw pairs); alpha cannot be claimed until 30 comparable "
                "pairs across 30 independent market dates clear."
            ),
        },
    ]


def _cirrus_comparison(
    mart_shadow: dict[str, Any],
    mart_sync: dict[str, Any],
    as_of_utc: datetime,
) -> dict[str, Any]:
    cross = _as_dict(mart_shadow.get("cross_system_comparison"))
    execution = _as_dict(mart_shadow.get("execution_quality"))
    iceberg = _as_dict(mart_sync.get("iceberg"))
    generated = _parse_dt(mart_shadow.get("generated_at_utc") or mart_sync.get("generated_at_utc"))
    stale = generated is None or (as_of_utc - generated) > MART_STALE_AFTER
    cirrus_pin = str(mart_sync.get("cirrus_pin") or "")
    if cirrus_pin == "fallback" or mart_sync.get("cirrus_export_is_current") is False:
        stale = True
    paired = cross.get("paired_executable_outcomes")
    comparable_pairs = int(cross.get("risk_normalized_live_comparable_pairs") or 0)
    comparable_dates = int(cross.get("risk_normalized_live_comparable_market_dates") or 0)
    design_id = str(cross.get("alpha_comparison_design_id") or "")
    lift = _number(cross.get("avg_risk_normalized_live_return_difference"))
    if stale and paired:
        verdict = "stale_mart_insufficient_for_alpha"
    elif not design_id or comparable_pairs < 30 or comparable_dates < 30 or lift is None:
        verdict = "insufficient_paired_evidence"
    elif lift is not None and lift > 0:
        verdict = "orographic_ahead"
    else:
        verdict = "cirrus_ahead"
    return {
        "mart_id": mart_shadow.get("mart_id") or mart_sync.get("mart_id"),
        "mart_sync_status": mart_sync.get("status") or "missing",
        "mart_generated_at_utc": mart_shadow.get("generated_at_utc") or mart_sync.get("generated_at_utc"),
        "mart_stale": stale,
        "cirrus_pin": cirrus_pin or None,
        "cirrus_export_is_current": False if stale and cirrus_pin == "fallback" else mart_sync.get("cirrus_export_is_current"),
        "orographic_refreshed": mart_sync.get("orographic_refreshed"),
        "training_rows": mart_sync.get("training_rows"),
        "paired_executable_outcomes": paired,
        "paired_market_dates": cross.get("paired_market_dates"),
        "direct_return_comparable_pairs": cross.get("direct_return_comparable_pairs"),
        "direct_return_comparable_market_dates": cross.get("direct_return_comparable_market_dates"),
        "live_direct_return_comparable_pairs": cross.get("live_direct_return_comparable_pairs"),
        "live_direct_return_comparable_market_dates": cross.get("live_direct_return_comparable_market_dates"),
        "risk_normalized_live_comparable_pairs": comparable_pairs,
        "risk_normalized_live_comparable_market_dates": comparable_dates,
        "alpha_comparison_design_id": design_id or None,
        "fixed_24h_exploratory_shadow_pairs": cross.get("fixed_24h_exploratory_shadow_pairs"),
        "fixed_24h_exploratory_shadow_market_dates": cross.get("fixed_24h_exploratory_shadow_market_dates"),
        "avg_orographic_minus_cirrus_return": lift,
        "orographic_only": cross.get("orographic_only"),
        "cirrus_only": cross.get("cirrus_only"),
        "orographic_executable_win_rate": execution.get("executable_win_rate"),
        "orographic_avg_executable_return": execution.get("avg_executable_return"),
        "iceberg_current_export": iceberg.get("current_export"),
        "iceberg_max_decision_at_utc": iceberg.get("max_decision_at_utc"),
        "iceberg_latest_publication_mart_id": iceberg.get("latest_publication_mart_id"),
        "alpha_verdict": verdict,
    }


def build_weekly_alpha_review(
    *,
    as_of_utc: datetime,
    snapshot: dict[str, Any],
    board_history: dict[str, Any],
    dashboard: dict[str, Any],
    scan_health: dict[str, Any],
    rebuild_readiness: dict[str, Any],
    mart_shadow: dict[str, Any],
    mart_sync: dict[str, Any] | None = None,
    payoff_challenger: dict[str, Any] | None = None,
    path_hazard: dict[str, Any] | None = None,
    promotion: dict[str, Any] | None = None,
    exit_shadow: dict[str, Any] | None = None,
    prospective_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start, end = week_bounds(as_of_utc)
    mart_sync = mart_sync or {}
    payoff_challenger = payoff_challenger or {}
    path_hazard = path_hazard or {}
    promotion = promotion or {}
    exit_shadow = exit_shadow or {}
    ledger_entries = _week_entries(prospective_ledger or {}, start, end)
    dashboard_entries = _week_entries(dashboard, start, end)
    # Prefer the full prospective ledger when it actually covers the week;
    # otherwise the compact dashboard summary is the published evidence.
    research_entries = ledger_entries or dashboard_entries
    evidence_source = "prospective_ledger" if ledger_entries else "dashboard_summary"
    lanes = _lane_scorecard(research_entries)
    board = _board_week(board_history, start, end)
    holdout = _holdout_challenger(research_entries)
    tight_spread = _tight_spread_challenger(research_entries)
    opposite = _paired_opposite_challenger(research_entries)
    friction = _friction_veto_value(lanes["lanes"])
    friction_paired = _friction_veto_paired(research_entries)
    side_split = _research_side_split(lanes["lanes"])
    same_symbol = _same_symbol_repeat(research_entries)
    overlay = evaluate_trajectory_exit_overlay(
        {"entries": research_entries},
        start=start,
        end=end,
        as_of_utc=as_of_utc,
    )
    early_harvest = evaluate_early_harvest_overlay(
        {"entries": research_entries},
        start=start,
        end=end,
        as_of_utc=as_of_utc,
    )
    winner_run = evaluate_winner_run_overlay(
        {"entries": research_entries},
        start=start,
        end=end,
        as_of_utc=as_of_utc,
    )
    live_marks = _join_live_marks(board["live_emissions"], research_entries)
    live_lane = _as_dict(lanes["lanes"].get("live"))
    cirrus = _cirrus_comparison(mart_shadow, mart_sync, as_of_utc)
    decisions = _lane_decisions(
        challenger=holdout,
        tight_spread=tight_spread,
        opposite=opposite,
        friction=friction,
        friction_paired=friction_paired,
        side_split=side_split,
        same_symbol=same_symbol,
        overlay=overlay,
        early_harvest=early_harvest,
        winner_run=winner_run,
        payoff=payoff_challenger,
        path_hazard=path_hazard,
        mart_shadow=mart_shadow,
    )
    council = _as_dict(snapshot.get("council"))
    live_board = _as_list(council.get("live_board"))
    current_pick = live_board[0] if live_board else None
    kill_watch = _kill_switch(
        week_live_marks=live_marks if live_marks.get("resolved") else live_lane,
        evidence=prospective_ledger or dashboard,
        rebuild_readiness=rebuild_readiness,
    )
    return {
        "artifact": "orographic_weekly_alpha_review",
        "schema_version": REVIEW_SCHEMA_VERSION,
        "generated_at_utc": as_of_utc.astimezone(UTC).replace(microsecond=0).isoformat(),
        "week_start_utc": start.replace(microsecond=0).isoformat(),
        "week_end_utc": end.replace(microsecond=0).isoformat(),
        "objective": "after_cost_alpha_versus_cirrus_and_internal_challengers",
        "production": {
            "lane": PRODUCTION_LANE,
            "model_stack": _as_dict(snapshot.get("scan_settings")).get("model_stack"),
            "regime": _as_dict(snapshot.get("regime")).get("mode"),
            "current_pick": {
                "symbol": _as_dict(current_pick).get("symbol"),
                "contract_symbol": _as_dict(current_pick).get("contract_symbol"),
                "option_type": _as_dict(current_pick).get("option_type"),
                "ask": _as_dict(current_pick).get("ask"),
                "spread_pct": _as_dict(current_pick).get("spread_pct"),
                "prob_big_win": _as_dict(current_pick).get("prob_big_win"),
                "expected_tail_utility": _as_dict(current_pick).get("expected_tail_utility"),
            } if current_pick else None,
            "week": board,
            "week_live_marks": live_marks if live_marks.get("resolved") else live_lane,
        },
        "research_evidence_source": evidence_source,
        "research_lanes": lanes,
        "challenger_to_open": _primary_challenger(opposite, tight_spread, holdout, same_symbol),
        "tight_spread_challenger": tight_spread,
        "paired_opposite_challenger": opposite,
        "friction_veto_value": friction,
        "friction_veto_paired": friction_paired,
        "research_side_split": side_split,
        "same_symbol_repeat": same_symbol,
        "exit_overlay": overlay,
        "early_harvest_overlay": early_harvest,
        "winner_run_overlay": winner_run,
        "lane_decisions": decisions,
        "cirrus": cirrus,
        "platform": {
            "scan_health_status": scan_health.get("status"),
            "scan_health_failed_checks": [
                _as_dict(check).get("name") for check in _as_list(scan_health.get("failed_checks"))
            ],
            "rebuild_readiness": rebuild_readiness.get("status"),
            "promotion_decision": promotion.get("decision"),
            "feature_snapshot_schema": OROGRAPHIC_FEATURE_SCHEMA_VERSION,
            "exit_shadow_live_coverage": _as_dict(
                _as_dict(_as_dict(exit_shadow.get("summary")).get("live_by_policy")).get("standing_limit_25")
            ).get("coverage_pct"),
        },
        "kill_switch": kill_watch,
        "alpha_verdict": cirrus["alpha_verdict"],
        "next_actions": [
            "Keep production_v2 as the only Tradier lane.",
            "Rebuild the two-source mart after each scan when a Cirrus export is present; use restored canonical evidence if consolidate has not written yet.",
            "Publish Cirrus with python scripts/upload_research_artifacts_to_r2.py --mode cirrus <bundle-dir> so scans restore r2://$OROGRAPHIC_RESEARCH_R2_BUCKET/cirrus/options_research_bundle/current.",
            "Republish the two-source mart to Iceberg when iceberg_current_export is false so catalog backtests stay on the same mart_id as the repo diagnostics.",
            f"Keep {FRICTION_VETO_VALUE} as a production execution gate; score {FRICTION_VETO_PAIRED} as the same-scan estimand and do not loosen the gate.",
            f"Collect {PAIRED_OPPOSITE_CHALLENGER}, {TIGHT_SPREAD_CHALLENGER}, and {SAME_SYMBOL_REPEAT} as observation-only; challenger_to_open ranks algebraic lift, not |lift|.",
            f"Replace harvest overlays with {WINNER_RUN_ARTIFACT} (stop −50%, never harvest); do not change live exits.",
            f"Keep {RESEARCH_SIDE_SPLIT} observation-only; a one-week put/call gap is not a Scout rewrite.",
            "If Iceberg MERGE 404s on missing delete files, recreate the table from the staged parquet so catalog backtests stay on the same mart_id as the repo diagnostics.",
            "Do not claim Cirrus alpha until paired executable outcomes reach 30 independent dates on a fresh mart.",
        ],
    }
