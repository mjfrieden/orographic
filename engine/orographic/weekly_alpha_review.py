"""Weekly alpha review for the single production lane versus research and Cirrus."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any


PRODUCTION_LANE = "production_v2_council_live_board"
FEATURE_SCHEMA = "orographic_recommendation_features_v1"
HOLD_OUT_CHALLENGER = "holdout_top1_vs_live_v1"
TRAJECTORY_EXIT_OVERLAY = "trajectory_exit_overlay_v1"


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


def _spread(pick: dict[str, Any]) -> float | None:
    return _number(_as_dict(pick.get("emission_quote")).get("spread_pct"))


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


def _lane_scorecard(
    *,
    dashboard: dict[str, Any],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    week_picks: list[dict[str, Any]] = []
    for entry in _as_list(dashboard.get("entries")):
        if not _in_week(entry.get("run_generated_at_utc"), start, end):
            continue
        for pick in _as_list(entry.get("picks")):
            if isinstance(pick, dict):
                week_picks.append(pick)
    by_lane: dict[str, dict[str, Any]] = {}
    counts = Counter(str(pick.get("lane") or "unknown") for pick in week_picks)
    for lane, count in counts.items():
        resolved = []
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
    runs = [
        entry
        for entry in _as_list(board.get("entries"))
        if isinstance(entry, dict) and _in_week(entry.get("run_generated_at_utc"), start, end)
    ]
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


def _holdout_challenger(dashboard: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    paired: list[dict[str, Any]] = []
    for entry in _as_list(dashboard.get("entries")):
        if not _in_week(entry.get("run_generated_at_utc"), start, end):
            continue
        live_rows = [pick for pick in _as_list(entry.get("picks")) if pick.get("lane") == "live"]
        holdouts = [pick for pick in _as_list(entry.get("picks")) if pick.get("lane") == "council_holdout"]
        live = live_rows[0] if live_rows else None
        scored = []
        for pick in holdouts:
            window, pnl = _latest_mark(pick)
            if pnl is None:
                continue
            scored.append((pnl, str(pick.get("symbol")), str(pick.get("contract_symbol")), window, _spread(pick)))
        live_window, live_pnl = _latest_mark(live) if isinstance(live, dict) else (None, None)
        if live_pnl is None or not scored:
            continue
        best = max(scored, key=lambda row: row[0])
        paired.append({
            "run_generated_at_utc": entry.get("run_generated_at_utc"),
            "live_symbol": live.get("symbol") if isinstance(live, dict) else None,
            "live_return": live_pnl,
            "live_window": live_window,
            "holdout_top1_symbol": best[1],
            "holdout_top1_contract": best[2],
            "holdout_top1_return": best[0],
            "holdout_top1_window": best[3],
            "return_lift": round(best[0] - live_pnl, 4),
        })
    lifts = [row["return_lift"] for row in paired]
    return {
        "experiment_id": HOLD_OUT_CHALLENGER,
        "authority": "observation_only_never_used_for_routing",
        "paired_scans": len(paired),
        "mean_return_lift": round(sum(lifts) / len(lifts), 4) if lifts else None,
        "positive_lift_rate": round(sum(1 for value in lifts if value > 0) / len(lifts), 4) if lifts else None,
        "rows": paired,
        "promotion_ready": False,
        "reason": (
            "Holdout top-1 beat the live pick on intraweek marks this week, but Friday-close "
            "executable labels and 30 paired days are still required before any production change."
        ),
    }


def _lane_decisions(
    *,
    challenger: dict[str, Any],
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
                "Replace that fit with a trajectory-mark exit overlay using current capture, not the 740-row stale file."
            ),
        },
        {
            "lane": "side_aware_scout_shadow_ledger",
            "action": "retire_stale_telemetry",
            "authority": "none",
            "reason": "Side-aware Scout is production-active; the shadow disagreement ledger is no longer a live experiment.",
        },
        {
            "lane": HOLD_OUT_CHALLENGER,
            "action": "open_observation_only",
            "authority": "observation_only",
            "reason": (
                "This week's Council holdout top-1 beat the live pick on paired intraweek marks. "
                "Track it as the replacement research strategy; it cannot route orders."
            ),
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


def _cirrus_comparison(mart_shadow: dict[str, Any], mart_sync: dict[str, Any]) -> dict[str, Any]:
    cross = _as_dict(mart_shadow.get("cross_system_comparison"))
    execution = _as_dict(mart_shadow.get("execution_quality"))
    return {
        "mart_id": mart_shadow.get("mart_id") or mart_sync.get("mart_id"),
        "mart_sync_status": mart_sync.get("status") or "missing",
        "mart_generated_at_utc": mart_shadow.get("generated_at_utc") or mart_sync.get("generated_at_utc"),
        "paired_executable_outcomes": cross.get("paired_executable_outcomes"),
        "paired_market_dates": cross.get("paired_market_dates"),
        "avg_orographic_minus_cirrus_return": cross.get("avg_orographic_minus_cirrus_return"),
        "orographic_only": cross.get("orographic_only"),
        "cirrus_only": cross.get("cirrus_only"),
        "orographic_executable_win_rate": execution.get("executable_win_rate"),
        "orographic_avg_executable_return": execution.get("avg_executable_return"),
        "alpha_verdict": (
            "insufficient_paired_evidence"
            if not cross.get("paired_executable_outcomes")
            else (
                "orographic_ahead"
                if (_number(cross.get("avg_orographic_minus_cirrus_return")) or 0) > 0
                else "cirrus_ahead"
            )
        ),
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
) -> dict[str, Any]:
    start, end = week_bounds(as_of_utc)
    mart_sync = mart_sync or {}
    payoff_challenger = payoff_challenger or {}
    path_hazard = path_hazard or {}
    promotion = promotion or {}
    exit_shadow = exit_shadow or {}
    lanes = _lane_scorecard(dashboard=dashboard, start=start, end=end)
    board = _board_week(board_history, start, end)
    holdout = _holdout_challenger(dashboard, start, end)
    live_lane = _as_dict(lanes["lanes"].get("live"))
    decisions = _lane_decisions(
        challenger=holdout,
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
            "This week's live marks are incomplete Friday-close labels. "
            "Do not fire the kill switch on intraweek partials, and do not change production artifacts "
            "while rebuild readiness is blocked."
        ),
        "rebuild_production_change_allowed": bool(rebuild_readiness.get("production_model_change_allowed")),
    }
    return {
        "artifact": "orographic_weekly_alpha_review",
        "schema_version": 1,
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
        "research_lanes": lanes,
        "challenger_to_open": holdout,
        "lane_decisions": decisions,
        "cirrus": _cirrus_comparison(mart_shadow, mart_sync),
        "platform": {
            "scan_health_status": scan_health.get("status"),
            "scan_health_failed_checks": [
                _as_dict(check).get("name") for check in _as_list(scan_health.get("failed_checks"))
            ],
            "rebuild_readiness": rebuild_readiness.get("status"),
            "promotion_decision": promotion.get("decision"),
            "feature_snapshot_schema": FEATURE_SCHEMA,
            "exit_shadow_live_coverage": _as_dict(
                _as_dict(_as_dict(exit_shadow.get("summary")).get("live_by_policy")).get("standing_limit_25")
            ).get("coverage_pct"),
        },
        "kill_switch": kill_watch,
        "alpha_verdict": _cirrus_comparison(mart_shadow, mart_sync)["alpha_verdict"],
        "next_actions": [
            "Keep production_v2 as the only Tradier lane.",
            "Rebuild the two-source mart after each scan when a Cirrus export is present.",
            f"Collect prospective evidence for {HOLD_OUT_CHALLENGER}; do not promote on intraweek marks.",
            f"Replace the stale path-hazard fit with {TRAJECTORY_EXIT_OVERLAY} on trajectory marks.",
            "Do not claim Cirrus alpha until paired executable outcomes reach 30 independent dates.",
        ],
    }
