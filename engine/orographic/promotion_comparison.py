from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
from statistics import mean, stdev
from typing import Any


CANONICAL_WINDOWS = (3, 6, 12)
LANES = ("active", "shadow")
MIN_TRADES_PER_LANE = 30
MIN_TRADING_DAYS_PER_LANE = 30
MIN_PAIRED_DAYS = 30
BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 20260808


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _ledger_rows(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict):
            continue
        entry_ts = _timestamp(entry.get("run_generated_at_utc"))
        for pick in entry.get("picks", []):
            if not isinstance(pick, dict) or pick.get("lane") not in {"live", "shadow"}:
                continue
            ts = _timestamp(pick.get("run_generated_at_utc")) or entry_ts
            emission = pick.get("emission_quote") if isinstance(pick.get("emission_quote"), dict) else {}
            outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
            marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
            friday = marks.get("friday_close") if isinstance(marks.get("friday_close"), dict) else {}
            scores = pick.get("scores") if isinstance(pick.get("scores"), dict) else {}
            entry_ask = _number(emission.get("ask"))
            exit_bid = _number(friday.get("bid"))
            predicted = _number(scores.get("prob_positive_option_pnl"))
            if ts is None or entry_ask is None or entry_ask <= 0 or exit_bid is None:
                continue
            gross_entry = _number(emission.get("mid"))
            gross_exit = _number(friday.get("mark"))
            cost_basis = entry_ask * 100.0
            pnl = (exit_bid - entry_ask) * 100.0
            rows.append({
                "timestamp": ts,
                "lane": "active" if pick.get("lane") == "live" else "shadow",
                "contract_symbol": pick.get("contract_symbol"),
                "cost_basis": cost_basis,
                "exit_value": exit_bid * 100.0,
                "net_pnl": pnl,
                "net_return": pnl / cost_basis,
                "gross_pnl": (
                    (gross_exit - gross_entry) * 100.0
                    if gross_entry is not None and gross_exit is not None
                    else None
                ),
                "spread_cost": (
                    ((entry_ask - gross_entry) + (gross_exit - exit_bid)) * 100.0
                    if gross_entry is not None and gross_exit is not None
                    else None
                ),
                "predicted_positive_pnl": predicted,
            })
    return sorted(rows, key=lambda row: (row["timestamp"], str(row.get("contract_symbol") or "")))


def _lane_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trades": 0, "cost_basis": 0.0, "gross_pnl": None, "spread_cost": None,
            "net_pnl": 0.0, "net_return_pct": None, "win_rate": None,
            "sharpe_ratio": None, "max_drawdown": None,
            "calibration": {"observations": 0, "brier_score": None, "expected_calibration_error": None},
        }
    daily: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        daily.setdefault(row["timestamp"].date().isoformat(), []).append(row)
    daily_returns = [
        sum(r["net_pnl"] for r in group) / sum(r["cost_basis"] for r in group)
        for group in daily.values()
        if sum(r["cost_basis"] for r in group) > 0
    ]
    sharpe = None
    if len(daily_returns) >= 2 and stdev(daily_returns) > 0:
        sharpe = mean(daily_returns) / stdev(daily_returns) * math.sqrt(252)

    # Compound each lane's daily return on one unit of capital. This normalizes
    # the very different active/shadow recommendation counts without hiding the
    # absolute one-contract cash P&L reported above.
    equity = 1.0
    peak = equity
    max_drawdown = 0.0
    for daily_return in daily_returns:
        equity *= 1.0 + daily_return
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, (equity - peak) / peak)

    calibrated = [r for r in rows if r["predicted_positive_pnl"] is not None]
    brier = None
    ece = None
    if calibrated:
        brier = mean((r["predicted_positive_pnl"] - (1.0 if r["net_pnl"] > 0 else 0.0)) ** 2 for r in calibrated)
        bins: list[tuple[int, float]] = []
        for lower in (0.0, 0.2, 0.4, 0.6, 0.8):
            bucket = [r for r in calibrated if lower <= r["predicted_positive_pnl"] < lower + 0.2 or (lower == 0.8 and r["predicted_positive_pnl"] == 1.0)]
            if bucket:
                bins.append((len(bucket), abs(mean(r["predicted_positive_pnl"] for r in bucket) - mean(1.0 if r["net_pnl"] > 0 else 0.0 for r in bucket))))
        ece = sum(count * error for count, error in bins) / len(calibrated)

    cost_basis = sum(r["cost_basis"] for r in rows)
    net_pnl = sum(r["net_pnl"] for r in rows)
    gross_values = [r["gross_pnl"] for r in rows if r["gross_pnl"] is not None]
    spread_values = [r["spread_cost"] for r in rows if r["spread_cost"] is not None]
    return {
        "trades": len(rows),
        "trading_days": len(daily),
        "cost_basis": round(cost_basis, 2),
        "gross_pnl": round(sum(gross_values), 2) if len(gross_values) == len(rows) else None,
        "spread_cost": round(sum(spread_values), 2) if len(spread_values) == len(rows) else None,
        "net_pnl": round(net_pnl, 2),
        "net_return_pct": round(net_pnl / cost_basis, 4) if cost_basis else None,
        "win_rate": round(sum(r["net_pnl"] > 0 for r in rows) / len(rows), 4),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(max_drawdown, 4),
        "calibration": {
            "observations": len(calibrated),
            "brier_score": round(brier, 4) if brier is not None else None,
            "expected_calibration_error": round(ece, 4) if ece is not None else None,
        },
    }


def _deduplicate_daily_contracts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first daily observation for each lane/contract exposure.

    Intraday scans can repeat the same contract many times. Treating every scan
    as an independent trade inflates sample size and can make one exposure
    dominate P&L, calibration, and significance. The canonical report retains
    raw recommendation metrics but also requires this cluster-adjusted view.
    """
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            row["timestamp"].date().isoformat(),
            row["lane"],
            str(row.get("contract_symbol") or ""),
        )
        unique.setdefault(key, row)
    return list(unique.values())


def _daily_lane_returns(rows: list[dict[str, Any]]) -> dict[str, float]:
    daily: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        daily.setdefault(row["timestamp"].date().isoformat(), []).append(row)
    return {
        day: sum(row["net_pnl"] for row in group) / cost_basis
        for day, group in daily.items()
        if (cost_basis := sum(row["cost_basis"] for row in group)) > 0
    }


def _paired_day_inference(
    active_rows: list[dict[str, Any]],
    shadow_rows: list[dict[str, Any]],
    *,
    seed: int,
) -> dict[str, Any]:
    """Estimate challenger lift on market days observed in both lanes.

    Pairing controls for broad market-day effects. Resampling whole paired days,
    rather than individual recommendations, preserves within-day dependence
    between contracts and prevents repeated scans from narrowing uncertainty.
    """
    active_daily = _daily_lane_returns(active_rows)
    shadow_daily = _daily_lane_returns(shadow_rows)
    paired_days = sorted(set(active_daily).intersection(shadow_daily))
    differences = [shadow_daily[day] - active_daily[day] for day in paired_days]
    if not differences:
        return {
            "paired_days": 0,
            "mean_return_lift": None,
            "confidence_interval_95": {"lower": None, "upper": None},
            "probability_positive": None,
            "positive_paired_day_rate": None,
            "method": "paired-day nonparametric bootstrap",
            "resamples": BOOTSTRAP_RESAMPLES,
        }

    rng = random.Random(seed)
    sample_size = len(differences)
    bootstrap_means = sorted(
        mean(rng.choice(differences) for _ in range(sample_size))
        for _ in range(BOOTSTRAP_RESAMPLES)
    )
    lower_index = int(0.025 * (BOOTSTRAP_RESAMPLES - 1))
    upper_index = int(0.975 * (BOOTSTRAP_RESAMPLES - 1))
    return {
        "paired_days": sample_size,
        "mean_return_lift": round(mean(differences), 6),
        "confidence_interval_95": {
            "lower": round(bootstrap_means[lower_index], 6),
            "upper": round(bootstrap_means[upper_index], 6),
        },
        "probability_positive": round(
            sum(value > 0 for value in bootstrap_means) / BOOTSTRAP_RESAMPLES,
            4,
        ),
        "positive_paired_day_rate": round(
            sum(value > 0 for value in differences) / sample_size,
            4,
        ),
        "method": "paired-day nonparametric bootstrap",
        "resamples": BOOTSTRAP_RESAMPLES,
    }


def build_promotion_comparison(
    prospective_ledger: dict[str, Any],
    shadow_ledger: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    rows = _ledger_rows(prospective_ledger)
    latest = max((row["timestamp"] for row in rows), default=None)
    as_of = (as_of or latest or datetime.now(timezone.utc)).astimezone(timezone.utc)
    earliest_by_lane = {
        lane: min(
            (row["timestamp"] for row in rows if row["lane"] == lane),
            default=None,
        )
        for lane in LANES
    }
    windows = []
    for months in CANONICAL_WINDOWS:
        start = as_of - timedelta(days=months * 365.2425 / 12)
        window_rows = [row for row in rows if start <= row["timestamp"] <= as_of]
        active_rows = [row for row in window_rows if row["lane"] == "active"]
        shadow_rows = [row for row in window_rows if row["lane"] == "shadow"]
        active = _lane_metrics(active_rows)
        shadow = _lane_metrics(shadow_rows)
        active_clustered = _lane_metrics(_deduplicate_daily_contracts(active_rows))
        shadow_clustered = _lane_metrics(_deduplicate_daily_contracts(shadow_rows))
        paired_inference = _paired_day_inference(
            _deduplicate_daily_contracts(active_rows),
            _deduplicate_daily_contracts(shadow_rows),
            seed=BOOTSTRAP_SEED + months,
        )
        coverage_by_lane = {
            lane: earliest is not None and earliest <= start
            for lane, earliest in earliest_by_lane.items()
        }
        coverage_complete = all(coverage_by_lane.values())
        comparable = active["trades"] > 0 and shadow["trades"] > 0
        minimum_evidence = all(
            lane["trades"] >= MIN_TRADES_PER_LANE
            and lane["trading_days"] >= MIN_TRADING_DAYS_PER_LANE
            for lane in (active_clustered, shadow_clustered)
        )
        pnl_lift = (
            comparable
            and shadow_clustered["net_return_pct"] is not None
            and active_clustered["net_return_pct"] is not None
            and shadow_clustered["net_return_pct"] > active_clustered["net_return_pct"]
        )
        absolute_profitability = (
            shadow_clustered["net_return_pct"] is not None
            and shadow_clustered["net_return_pct"] > 0
        )
        sharpe_pass = comparable and shadow["sharpe_ratio"] is not None and active["sharpe_ratio"] is not None and shadow["sharpe_ratio"] >= active["sharpe_ratio"]
        risk_pass = comparable and shadow["max_drawdown"] is not None and active["max_drawdown"] is not None and shadow["max_drawdown"] >= active["max_drawdown"]
        calibration_pass = comparable and shadow["calibration"]["brier_score"] is not None and active["calibration"]["brier_score"] is not None and shadow["calibration"]["brier_score"] <= active["calibration"]["brier_score"]
        cluster_robustness = (
            active_clustered["net_return_pct"] is not None
            and shadow_clustered["net_return_pct"] is not None
            and shadow_clustered["net_return_pct"] > active_clustered["net_return_pct"]
            and active_clustered["max_drawdown"] is not None
            and shadow_clustered["max_drawdown"] is not None
            and shadow_clustered["max_drawdown"] >= active_clustered["max_drawdown"]
        )
        inference_lower = paired_inference["confidence_interval_95"]["lower"]
        uncertainty_robustness = (
            paired_inference["paired_days"] >= MIN_PAIRED_DAYS
            and inference_lower is not None
            and inference_lower > 0
            and paired_inference["probability_positive"] is not None
            and paired_inference["probability_positive"] >= 0.95
        )
        passed = (
            coverage_complete
            and minimum_evidence
            and pnl_lift
            and absolute_profitability
            and sharpe_pass
            and risk_pass
            and calibration_pass
            and cluster_robustness
            and uncertainty_robustness
        )
        windows.append({
            "window": f"{months}_month", "start_utc": start.isoformat(), "end_utc": as_of.isoformat(),
            "coverage_complete": coverage_complete,
            "coverage_by_lane": coverage_by_lane,
            "status": "pass" if passed else ("fail" if coverage_complete and comparable else "insufficient_data"),
            "active": active, "shadow": shadow,
            "cluster_adjusted": {
                "active": active_clustered,
                "shadow": shadow_clustered,
                "raw_recommendations": len(window_rows),
                "independent_daily_contracts": len(_deduplicate_daily_contracts(window_rows)),
            },
            "paired_day_inference": paired_inference,
            "checks": {
                "minimum_evidence": minimum_evidence,
                "after_cost_pnl_lift": pnl_lift,
                "absolute_profitability": absolute_profitability,
                "calibration_non_worse": calibration_pass,
                "sharpe_non_worse": sharpe_pass,
                "drawdown_non_worse": risk_pass,
                "cluster_robustness": cluster_robustness,
                "uncertainty_robustness": uncertainty_robustness,
            },
        })
    all_pass = all(row["status"] == "pass" for row in windows)
    shadow_aggregate = shadow_ledger.get("aggregate") if isinstance(shadow_ledger.get("aggregate"), dict) else {}
    return {
        "artifact": "promotion_shadow_active_comparison", "schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "as_of_utc": as_of.isoformat(), "decision": "pass" if all_pass else "not_ready",
        "replay_command": "python scripts/build_promotion_comparison.py",
        "policy": {
            "windows_months": list(CANONICAL_WINDOWS), "active_lane": "prospective live", "shadow_lane": "prospective shadow",
            "position_size": "one contract per recommendation", "entry": "emission ask", "exit": "Friday close bid",
            "costs": "bid/ask spread is included; gross mid-to-mid P&L and spread cost are also reported",
            "calibration_target": "prob_positive_option_pnl versus positive after-cost P&L",
            "sharpe": "annualized from daily lane returns using sqrt(252), zero risk-free rate",
            "drawdown": "peak-to-trough drawdown of compounded daily lane returns",
            "minimum_evidence": {
                "trades_per_lane": MIN_TRADES_PER_LANE,
                "trading_days_per_lane": MIN_TRADING_DAYS_PER_LANE,
                "paired_days": MIN_PAIRED_DAYS,
            },
            "dependence_control": "promotion must also pass after collapsing repeated intraday scans to one daily lane/contract exposure",
            "absolute_profitability": "the cluster-adjusted challenger return must be positive, not merely less negative than active",
            "uncertainty": "paired market-day return lift must have a positive 95% nonparametric bootstrap lower bound and at least 95% bootstrap probability of improvement",
        },
        "source_summary": {
            "prospective_ledger_updated_at_utc": prospective_ledger.get("updated_at_utc"),
            "eligible_marked_recommendations": len(rows),
            "shadow_ledger_updated_at_utc": shadow_ledger.get("updated_at_utc"),
            "shadow_runs": shadow_aggregate.get("runs", 0), "shadow_disagreements": shadow_aggregate.get("disagreements", 0),
        },
        "windows": windows,
    }


def write_promotion_comparison(prospective_path: Path, shadow_path: Path, output_path: Path) -> dict[str, Any]:
    prospective = json.loads(prospective_path.read_text(encoding="utf-8"))
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    artifact = build_promotion_comparison(prospective, shadow)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact
