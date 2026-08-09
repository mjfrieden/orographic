#!/usr/bin/env python3
"""Reproduce the July 2026 Orographic performance-forensics tables.

The analysis is intentionally recommendation-level, not account-level. It uses
the checked-in prospective ledger, assumes entry at the emission ask and exit
at the fixed-window bid, and keeps repeated intraday scans as separate rows
while reporting their clustering explicitly.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "web/data/diagnostics/prospective_pick_ledger.json"
MOONSHOT_LEDGER = ROOT / "web/data/diagnostics/moonshot_prospective_ledger.json"
PROMOTION = (
    ROOT
    / "web/data/diagnostics/promotion_shadow_active_comparison_latest.json"
)
OUTPUT = ROOT / "output/orographic_july_review_metrics.json"


def after_cost_return(pick: dict, exit_name: str = "friday_close") -> float | None:
    ask = (pick.get("emission_quote") or {}).get("ask")
    mark = (((pick.get("outcomes") or {}).get("fixed_exit_marks") or {}).get(exit_name) or {})
    bid = mark.get("bid")
    if not isinstance(ask, (int, float)) or not isinstance(bid, (int, float)) or ask <= 0:
        return None
    return bid / ask - 1.0


def mark_return(pick: dict, exit_name: str = "friday_close") -> float | None:
    mark = (((pick.get("outcomes") or {}).get("fixed_exit_marks") or {}).get(exit_name) or {})
    value = mark.get("pnl_pct_from_emission")
    return value if isinstance(value, (int, float)) else None


def summarize(rows: list[dict]) -> dict:
    returns = [row["after_cost_return"] for row in rows if row["after_cost_return"] is not None]
    pnl = [row["after_cost_pnl_dollars"] for row in rows if row["after_cost_pnl_dollars"] is not None]
    if not returns:
        return {
            "recommendations": len(rows),
            "completed": 0,
            "win_rate": None,
            "mean_return": None,
            "median_return": None,
            "one_contract_pnl": None,
        }
    return {
        "recommendations": len(rows),
        "completed": len(returns),
        "win_rate": sum(value > 0 for value in returns) / len(returns),
        "mean_return": mean(returns),
        "median_return": median(returns),
        "one_contract_pnl": sum(pnl),
    }


def finite_or_none(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def main() -> None:
    ledger = json.loads(LEDGER.read_text())
    promotion = json.loads(PROMOTION.read_text())
    rows: list[dict] = []
    for entry in ledger["entries"]:
        run = entry["run_generated_at_utc"]
        if not run.startswith("2026-07"):
            continue
        for pick in entry["picks"]:
            ret = after_cost_return(pick)
            ask = (pick.get("emission_quote") or {}).get("ask")
            score = pick.get("scores") or {}
            risk = pick.get("risk_features") or {}
            rows.append(
                {
                    "date": run[:10],
                    "run_generated_at_utc": run,
                    "lane": pick["lane"],
                    "symbol": pick["symbol"],
                    "contract_symbol": pick["contract_symbol"],
                    "option_type": pick["option_type"],
                    "regime": (entry.get("regime") or {}).get("mode", "unknown"),
                    "status": (pick.get("outcomes") or {}).get("status"),
                    "entry_ask": ask,
                    "predicted_win_probability": score.get("prob_positive_option_pnl"),
                    "expected_edge_after_friction": score.get("expected_edge_after_friction_pct"),
                    "final_candidate_score": score.get("final_candidate_score"),
                    "spread_pct": (pick.get("emission_quote") or {}).get("spread_pct"),
                    "extrinsic_ratio": risk.get("extrinsic_ratio"),
                    "after_cost_return": finite_or_none(ret),
                    "after_cost_pnl_dollars": finite_or_none(ret * ask * 100 if ret is not None else None),
                }
            )

    completed = [row for row in rows if row["after_cost_return"] is not None]
    lane_summary = []
    for lane in ("live", "shadow", "council_holdout", "friction_veto"):
        lane_rows = [row for row in rows if row["lane"] == lane]
        lane_summary.append({"lane": lane, **summarize(lane_rows)})

    side_summary = []
    for side in ("call", "put"):
        side_rows = [row for row in completed if row["option_type"] == side]
        side_summary.append({"option_type": side, **summarize(side_rows)})

    regime_summary = []
    for regime in sorted({row["regime"] for row in rows}):
        regime_rows = [row for row in rows if row["regime"] == regime]
        regime_summary.append({"regime": regime, **summarize(regime_rows)})

    symbol_rows: dict[str, list[dict]] = defaultdict(list)
    for row in completed:
        symbol_rows[row["symbol"]].append(row)
    symbol_summary = [
        {"symbol": symbol, **summarize(values)}
        for symbol, values in symbol_rows.items()
        if len(values) >= 3
    ]
    symbol_summary.sort(key=lambda row: row["one_contract_pnl"] or 0)

    cluster_counts = Counter((row["date"], row["contract_symbol"], row["lane"]) for row in rows)
    repeated = [count for count in cluster_counts.values() if count > 1]
    live_completed = [row for row in completed if row["lane"] == "live"]
    prediction_error = [
        {
            "date": row["date"],
            "symbol": row["symbol"],
            "contract_symbol": row["contract_symbol"],
            "predicted_win_probability": row["predicted_win_probability"],
            "after_cost_return": row["after_cost_return"],
            "after_cost_pnl_dollars": row["after_cost_pnl_dollars"],
        }
        for row in live_completed
    ]

    active_window = promotion["windows"][0]["active"]
    shadow_window = promotion["windows"][0]["shadow"]

    moonshot = json.loads(MOONSHOT_LEDGER.read_text())
    moonshot_rows: list[dict] = []
    for entry in moonshot["entries"]:
        run = entry["run_generated_at_utc"]
        if not run.startswith("2026-07"):
            continue
        for pick in entry["picks"]:
            ret = after_cost_return(pick)
            ask = (pick.get("emission_quote") or {}).get("ask")
            moonshot_rows.append(
                {
                    "date": run[:10],
                    "run_generated_at_utc": run,
                    "lane": pick["lane"],
                    "symbol": pick["symbol"],
                    "contract_symbol": pick["contract_symbol"],
                    "option_type": pick["option_type"],
                    "regime": (entry.get("regime") or {}).get("mode", "unknown"),
                    "status": (pick.get("outcomes") or {}).get("status"),
                    "entry_ask": ask,
                    "after_cost_return": finite_or_none(ret),
                    "after_cost_pnl_dollars": finite_or_none(ret * ask * 100 if ret is not None else None),
                }
            )
    all_rows = rows + moonshot_rows
    all_completed = [row for row in all_rows if row["after_cost_return"] is not None]
    combined_lane_summary = []
    for lane in sorted({row["lane"] for row in all_rows}):
        combined_lane_summary.append(
            {"lane": lane, **summarize([row for row in all_rows if row["lane"] == lane])}
        )

    combined_symbol_rows: dict[str, list[dict]] = defaultdict(list)
    for row in all_completed:
        combined_symbol_rows[row["symbol"]].append(row)
    combined_symbol_summary = [
        {"symbol": symbol, **summarize(values)}
        for symbol, values in combined_symbol_rows.items()
        if len(values) >= 3
    ]
    combined_symbol_summary.sort(key=lambda row: row["one_contract_pnl"] or 0)

    monthly_rows: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"mark": [], "executable": []}
    )
    for source_ledger in (ledger, moonshot):
        for entry in source_ledger["entries"]:
            month = entry["run_generated_at_utc"][:7]
            if month not in {"2026-05", "2026-06", "2026-07"}:
                continue
            for pick in entry["picks"]:
                marked = mark_return(pick)
                executable = after_cost_return(pick)
                if marked is not None:
                    monthly_rows[month]["mark"].append(marked)
                if executable is not None:
                    monthly_rows[month]["executable"].append(executable)
    monthly_comparison = []
    for month in sorted(monthly_rows):
        mark_values = monthly_rows[month]["mark"]
        executable_values = monthly_rows[month]["executable"]
        monthly_comparison.append(
            {
                "month": month,
                "completed": len(executable_values),
                "mark_mean_return": mean(mark_values),
                "executable_mean_return": mean(executable_values),
                "implementation_drag": mean(mark_values) - mean(executable_values),
                "mark_win_rate": sum(value > 0 for value in mark_values) / len(mark_values),
                "executable_win_rate": sum(value > 0 for value in executable_values)
                / len(executable_values),
            }
        )
    output = {
        "scope": {
            "ledger_updated_at_utc": ledger["updated_at_utc"],
            "july_observed_through": max(row["date"] for row in rows),
            "completed_outcomes_through": max(row["date"] for row in completed),
            "recommendation_rows": len(rows),
            "completed_rows": len(completed),
            "pending_rows": sum(row["status"] == "pending" for row in rows),
            "unique_contracts": len({row["contract_symbol"] for row in rows}),
            "unique_date_contract_lane_clusters": len(cluster_counts),
            "repeated_clusters": len(repeated),
            "rows_inside_repeated_clusters": sum(repeated),
            "actual_account_fills_available": False,
        },
        "all_completed": summarize(completed),
        "combined_all_completed": summarize(all_completed),
        "lane_summary": lane_summary,
        "combined_lane_summary": combined_lane_summary,
        "side_summary": side_summary,
        "regime_summary": regime_summary,
        "symbol_summary": symbol_summary,
        "combined_symbol_summary": combined_symbol_summary,
        "monthly_comparison": monthly_comparison,
        "live_completed_recommendations": prediction_error,
        "promotion_window": {
            "as_of_utc": promotion["as_of_utc"],
            "decision": promotion["decision"],
            "active": active_window,
            "shadow": shadow_window,
            "friction_share_of_active_gross": (
                active_window["spread_cost"] / active_window["gross_pnl"]
                if active_window["gross_pnl"]
                else None
            ),
        },
        "data_quality": {
            "ledger_total_picks": ledger["outcome_summary"]["picks"],
            "ledger_pending": ledger["outcome_summary"]["pending"],
            "archived_path_labels_observed": ledger["archived_quote_path_summary"]["labels_observed"],
            "archived_path_labels_missing": ledger["archived_quote_path_summary"]["labels_missing"],
            "last_mark_quotes_missing": ledger["last_mark_summary"]["quotes_missing"],
            "last_mark_marks_written": ledger["last_mark_summary"]["marks_written"],
        },
    }
    OUTPUT.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
