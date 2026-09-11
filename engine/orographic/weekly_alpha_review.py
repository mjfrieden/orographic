"""Weekly alpha review for the single production lane versus research and Cirrus."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from engine.orographic.shared_research_mart import OROGRAPHIC_FEATURE_SCHEMA_VERSION
from engine.orographic.trajectory_exit_overlay import (
    ARTIFACT as TRAJECTORY_EXIT_OVERLAY,
    evaluate_trajectory_exit_overlay,
)


PRODUCTION_LANE = "production_v2_council_live_board"
HOLD_OUT_CHALLENGER = "holdout_top1_vs_live_v1"
MART_STALE_AFTER = timedelta(days=7)


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
        resolved: list[float] = []
        windows: Counter[str] = Counter()
        for pick in week_picks:
            if str(pick.get("lane") or "unknown") != lane:
                continue
            window, pnl = _latest_mark(pick)
            if pnl is not None and window is not None:
                resolved.append(pnl)
                windows[window] += 1
        by_lane[lane] = {
            "picks": count,
            "mark_windows": dict(windows),
            **_summarize_returns(resolved),
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
    return {
        "scans": len(runs),
        "abstain_scans": sum(1 for entry in runs if bool(entry.get("abstain"))),
        "live_emissions": live,
        "unique_live_days": len({str(row["run_generated_at_utc"])[:10] for row in live}),
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


def _lane_decisions(
    *,
    challenger: dict[str, Any],
    overlay: dict[str, Any],
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
    holdout_action = (
        "keep_observation_only" if int(challenger.get("paired_scans") or 0) > 0 else "open_observation_only"
    )
    overlay_lift = overlay.get("overall", {}).get("mean_return_lift") if isinstance(overlay.get("overall"), dict) else None
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
            "action": "open_observation_only",
            "authority": "observation_only",
            "reason": (
                "Mechanical +25% bid harvest / -50% bid stop versus hold-to-Friday on current trajectory marks. "
                f"Mean lift versus hold {overlay_lift}. No production exit change."
            ),
            "mean_return_lift": overlay_lift,
            "resolved_picks": _as_dict(overlay.get("coverage")).get("resolved_picks"),
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
            "lane": "cirrus_paired_alpha",
            "action": "collect",
            "authority": "observation_only",
            "reason": (
                f"Only {paired_outcomes} paired executable Cirrus/Orographic outcomes exist; "
                "alpha versus Cirrus cannot be claimed until 30 paired market dates clear."
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
    generated = _parse_dt(mart_shadow.get("generated_at_utc") or mart_sync.get("generated_at_utc"))
    stale = generated is None or (as_of_utc - generated) > MART_STALE_AFTER
    paired = cross.get("paired_executable_outcomes")
    lift = _number(cross.get("avg_orographic_minus_cirrus_return"))
    if not paired:
        verdict = "insufficient_paired_evidence"
    elif stale:
        verdict = "stale_mart_insufficient_for_alpha"
    elif lift is not None and lift > 0:
        verdict = "orographic_ahead"
    else:
        verdict = "cirrus_ahead"
    return {
        "mart_id": mart_shadow.get("mart_id") or mart_sync.get("mart_id"),
        "mart_sync_status": mart_sync.get("status") or "missing",
        "mart_generated_at_utc": mart_shadow.get("generated_at_utc") or mart_sync.get("generated_at_utc"),
        "mart_stale": stale,
        "paired_executable_outcomes": paired,
        "paired_market_dates": cross.get("paired_market_dates"),
        "avg_orographic_minus_cirrus_return": cross.get("avg_orographic_minus_cirrus_return"),
        "orographic_only": cross.get("orographic_only"),
        "cirrus_only": cross.get("cirrus_only"),
        "orographic_executable_win_rate": execution.get("executable_win_rate"),
        "orographic_avg_executable_return": execution.get("avg_executable_return"),
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
    overlay = evaluate_trajectory_exit_overlay(
        {"entries": research_entries},
        start=start,
        end=end,
        as_of_utc=as_of_utc,
    )
    live_lane = _as_dict(lanes["lanes"].get("live"))
    cirrus = _cirrus_comparison(mart_shadow, mart_sync, as_of_utc)
    decisions = _lane_decisions(
        challenger=holdout,
        overlay=overlay,
        payoff=payoff_challenger,
        path_hazard=path_hazard,
        mart_shadow=mart_shadow,
    )
    council = _as_dict(snapshot.get("council"))
    live_board = _as_list(council.get("live_board"))
    current_pick = live_board[0] if live_board else None
    kill_watch = {
        "rule": "Disable active routing if 10 resolved live picks have negative cumulative after-friction return.",
        "status": "watch",
        "reason": (
            "Do not fire the kill switch on intraweek partials, and do not change production artifacts "
            "while rebuild readiness is blocked."
        ),
        "rebuild_production_change_allowed": bool(rebuild_readiness.get("production_model_change_allowed")),
    }
    return {
        "artifact": "orographic_weekly_alpha_review",
        "schema_version": 2,
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
            "week_live_marks": live_lane,
        },
        "research_evidence_source": evidence_source,
        "research_lanes": lanes,
        "challenger_to_open": holdout,
        "exit_overlay": overlay,
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
            "Rebuild the two-source mart after each scan when a Cirrus export is present.",
            f"Collect prospective evidence for {HOLD_OUT_CHALLENGER} using decision-time scores; do not promote on intraweek marks.",
            f"Keep {TRAJECTORY_EXIT_OVERLAY} as the exit-research replacement for path-hazard; do not change live exits.",
            "Do not claim Cirrus alpha until paired executable outcomes reach 30 independent dates on a fresh mart.",
        ],
    }
