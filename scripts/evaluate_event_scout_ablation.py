from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


SCHEMA_VERSION = 1
MIN_EVENT_ROWS = 150
MIN_SYMBOLS = 15
MIN_SIDE_ROWS = 50
MIN_REGIME_ROWS = 30
PURGE_DAYS = 5
BASELINE_FEATURES = ("scout_score", "forge_score", "payoff_model_score", "expected_edge_after_friction_pct")
EVENT_FEATURES = (
    "event_observation_count_lookback", "event_symbol_specific_count", "event_source_count",
    "event_sentiment_mean", "event_novelty_mean", "event_confidence_max",
    "narrative_attention_1d_at_entry", "narrative_attention_3d_at_entry",
    "narrative_source_diversity_1d_at_entry", "narrative_confirmation_score_1d_at_entry",
    "narrative_hype_pressure_at_entry",
)


def evaluate(frame: pd.DataFrame) -> dict[str, Any]:
    data = frame.copy()
    data["entry_at"] = pd.to_datetime(data.get("run_generated_at_utc"), utc=True, errors="coerce")
    data["realized_pnl"] = pd.to_numeric(data.get("friday_close_pnl_pct_from_emission"), errors="coerce")
    complete = data.loc[(data.get("outcome_status").astype(str).str.lower() == "complete") & data["entry_at"].notna() & data["realized_pnl"].notna()].copy()
    event_complete = complete.loc[pd.to_numeric(complete.get("event_observation_count_lookback"), errors="coerce").fillna(0).gt(0)].copy()
    side_counts = event_complete.get("option_type", pd.Series(dtype=str)).astype(str).str.lower().value_counts().to_dict()
    regime_counts = event_complete.get("regime_mode", pd.Series(dtype=str)).astype(str).value_counts().to_dict()
    gates = {
        "completed_event_rows": {"passed": len(event_complete) >= MIN_EVENT_ROWS, "actual": len(event_complete), "required_min": MIN_EVENT_ROWS},
        "symbol_coverage": {"passed": event_complete.get("symbol", pd.Series(dtype=str)).nunique() >= MIN_SYMBOLS, "actual": int(event_complete.get("symbol", pd.Series(dtype=str)).nunique()), "required_min": MIN_SYMBOLS},
        "side_coverage": {"passed": bool(side_counts) and min(side_counts.values()) >= MIN_SIDE_ROWS, "actual": side_counts, "required_min_per_side": MIN_SIDE_ROWS},
        "regime_coverage": {"passed": sum(count >= MIN_REGIME_ROWS for count in regime_counts.values()) >= 2, "actual": regime_counts, "required_min_rows": MIN_REGIME_ROWS},
    }
    ready = all(gate["passed"] for gate in gates.values())
    return {
        "artifact": "event_scout_ablation",
        "schema_version": SCHEMA_VERSION,
        "status": "ready_for_purged_walk_forward" if ready else "hold",
        "pre_registered_protocol": {
            "target": "friday_close_pnl_pct_from_emission > 0 after entry friction",
            "baseline_features": list(BASELINE_FEATURES),
            "event_features": list(EVENT_FEATURES),
            "split": "chronological purged walk-forward",
            "purge_days": PURGE_DAYS,
            "promotion_requirements": ["out_of_sample payoff lift", "non-worse Brier calibration", "non-worse drawdown", "stable call/put and regime results"],
        },
        "summary": {"complete_rows": len(complete), "completed_event_rows": len(event_complete), "event_symbols": int(event_complete.get("symbol", pd.Series(dtype=str)).nunique())},
        "gates": gates,
        "required_next_step": "Continue shadow collection; do not fit, retrain, or promote until every gate passes.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-register and gate Scout baseline-versus-event ablation evaluation.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(pd.read_parquet(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
