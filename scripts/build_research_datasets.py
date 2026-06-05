from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from numbers import Number
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _coerce_float(value: Any) -> float | None:
    if not isinstance(value, Number):
        return None
    as_float = float(value)
    return as_float if math.isfinite(as_float) else None


def _walk_symbol_spots(value: Any) -> dict[str, float]:
    spots: dict[str, float] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            symbol = str(node.get("symbol") or "").strip().upper()
            spot = _coerce_float(node.get("spot"))
            if symbol and spot is not None:
                spots[symbol] = spot
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                if isinstance(child, (dict, list)):
                    walk(child)

    walk(value)
    return spots


def diagnostic_spot_lookups(diagnostics_dir: Path) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    by_run: dict[tuple[str, str], float] = {}
    by_date: dict[tuple[str, str], float] = {}
    if not diagnostics_dir.exists():
        return by_run, by_date

    for path in diagnostics_dir.glob("forge_rejection_waterfall_2026-*.json"):
        artifact = _load_json(path)
        spots = _walk_symbol_spots(artifact)
        generated_at = str(artifact.get("generated_at_utc") or "")
        date = generated_at[:10] or path.stem.removeprefix("forge_rejection_waterfall_")
        for symbol, spot in spots.items():
            if generated_at:
                by_run[(generated_at, symbol)] = spot
            if date:
                by_date[(date, symbol)] = spot
    return by_run, by_date


def _infer_spot_from_premium_pct(emission_quote: dict[str, Any], risk: dict[str, Any]) -> float | None:
    premium_pct = _coerce_float(risk.get("premium_pct_of_spot"))
    ask = _coerce_float(emission_quote.get("ask"))
    if premium_pct is None or premium_pct <= 0 or ask is None or ask <= 0:
        return None
    return round(ask / premium_pct, 4)


def _underlying_spot(
    entry: dict[str, Any],
    pick: dict[str, Any],
    emission_quote: dict[str, Any],
    risk: dict[str, Any],
    *,
    spot_by_run: dict[tuple[str, str], float] | None = None,
    spot_by_date: dict[tuple[str, str], float] | None = None,
) -> float | None:
    underlying = pick.get("underlying") if isinstance(pick.get("underlying"), dict) else {}
    direct_spot = _coerce_float(underlying.get("spot"))
    if direct_spot is not None:
        return direct_spot

    legacy_spot = _coerce_float(pick.get("spot")) or _coerce_float(pick.get("underlying_spot"))
    if legacy_spot is not None:
        return legacy_spot

    run_generated_at = str(pick.get("run_generated_at_utc") or entry.get("run_generated_at_utc") or "")
    symbol = str(pick.get("symbol") or "").strip().upper()
    if symbol and spot_by_run:
        run_spot = spot_by_run.get((run_generated_at, symbol))
        if run_spot is not None:
            return run_spot

    inferred_spot = _infer_spot_from_premium_pct(emission_quote, risk)
    if inferred_spot is not None:
        return inferred_spot

    if symbol and spot_by_date and run_generated_at:
        return spot_by_date.get((run_generated_at[:10], symbol))
    return None


def _flatten_pick(
    entry: dict[str, Any],
    pick: dict[str, Any],
    *,
    source_artifact: str,
    spot_by_run: dict[tuple[str, str], float] | None = None,
    spot_by_date: dict[tuple[str, str], float] | None = None,
) -> dict[str, Any]:
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    fixed_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
    path_rules = outcomes.get("path_rules") if isinstance(outcomes.get("path_rules"), dict) else {}
    archived_path = outcomes.get("archived_quote_path") if isinstance(outcomes.get("archived_quote_path"), dict) else {}
    archived_first_hit = (
        archived_path.get("first_hit")
        if isinstance(archived_path.get("first_hit"), dict)
        else {}
    )
    emission_quote = pick.get("emission_quote") if isinstance(pick.get("emission_quote"), dict) else {}
    scores = pick.get("scores") if isinstance(pick.get("scores"), dict) else {}
    risk = pick.get("risk_features") if isinstance(pick.get("risk_features"), dict) else {}
    context = pick.get("context") if isinstance(pick.get("context"), dict) else {}
    moonshot = pick.get("moonshot") if isinstance(pick.get("moonshot"), dict) else {}
    underlying = pick.get("underlying") if isinstance(pick.get("underlying"), dict) else {}

    row: dict[str, Any] = {
        "source_artifact": source_artifact,
        "recommendation_id": pick.get("recommendation_id"),
        "run_generated_at_utc": entry.get("run_generated_at_utc"),
        "lane": pick.get("lane"),
        "lane_reason": pick.get("lane_reason"),
        "symbol": pick.get("symbol"),
        "contract_symbol": pick.get("contract_symbol"),
        "option_type": pick.get("option_type"),
        "expiry": pick.get("expiry"),
        "strike": pick.get("strike"),
        "days_to_expiry": pick.get("days_to_expiry"),
        "outcome_status": outcomes.get("status", "pending"),
        "underlying_spot": _underlying_spot(
            entry,
            pick,
            emission_quote,
            risk,
            spot_by_run=spot_by_run,
            spot_by_date=spot_by_date,
        ),
        "underlying_quote_captured_at_utc": underlying.get("quote_captured_at_utc"),
        "emission_quote_captured_at_utc": emission_quote.get("captured_at_utc"),
        "emission_bid": emission_quote.get("bid"),
        "emission_ask": emission_quote.get("ask"),
        "emission_mid": emission_quote.get("mid"),
        "emission_last": emission_quote.get("last"),
        "emission_spread": emission_quote.get("spread"),
        "emission_spread_pct": emission_quote.get("spread_pct"),
        "emission_open_interest": emission_quote.get("open_interest"),
        "emission_volume": emission_quote.get("volume"),
        "entry_quote_type": emission_quote.get("entry_quote_type"),
        "entry_data_source": emission_quote.get("entry_data_source"),
        "contract_cost": emission_quote.get("contract_cost"),
        "forge_score": scores.get("forge_score"),
        "learned_rank_score": scores.get("learned_rank_score"),
        "payoff_model_score": scores.get("payoff_model_score"),
        "path_holding_quality_score": scores.get("path_holding_quality_score"),
        "path_early_profit_take_prob": scores.get("path_early_profit_take_prob"),
        "path_decay_risk": scores.get("path_decay_risk"),
        "expected_edge_after_friction_pct": scores.get("expected_edge_after_friction_pct"),
        "delta": risk.get("delta"),
        "implied_volatility": risk.get("implied_volatility"),
        "iv_rank": risk.get("iv_rank"),
        "extrinsic_ratio": risk.get("extrinsic_ratio"),
        "moneyness": risk.get("moneyness"),
        "premium_pct_of_spot": risk.get("premium_pct_of_spot"),
        "breakeven_move_pct": risk.get("breakeven_move_pct"),
        "projected_move_pct": risk.get("projected_move_pct"),
        "friction_gate_passed": risk.get("friction_gate_passed"),
        "regime_mode": (entry.get("regime") or {}).get("mode") if isinstance(entry.get("regime"), dict) else None,
        "payoff_ranker_mode": (entry.get("model_modes") or {}).get("payoff_ranker") if isinstance(entry.get("model_modes"), dict) else None,
        "ranker_artifact_sha256": context.get("ranker_artifact_sha256"),
        "path_model_artifact_sha256": context.get("path_model_artifact_sha256"),
        "take_profit_40_pct_before_stop_50_pct": path_rules.get("take_profit_40_pct_before_stop_50_pct"),
        "take_profit_25_pct_before_stop_50_pct": path_rules.get("take_profit_25_pct_before_stop_50_pct"),
        "max_favorable_excursion_pct": path_rules.get("max_favorable_excursion_pct"),
        "max_adverse_excursion_pct": path_rules.get("max_adverse_excursion_pct"),
        "archive_path_status": archived_path.get("status"),
        "archive_path_observation_count": archived_path.get("observation_count"),
        "archive_path_entry_mark": archived_path.get("entry_mark"),
        "archive_path_mfe_pct": archived_path.get("max_favorable_excursion_pct"),
        "archive_path_mae_pct": archived_path.get("max_adverse_excursion_pct"),
        "archive_path_first_hit_rule": archived_first_hit.get("rule"),
        "archive_path_first_hit_at_utc": archived_first_hit.get("captured_at_utc"),
        "archive_path_first_hit_pnl_pct": archived_first_hit.get("pnl_pct_from_emission"),
        "archive_path_take_profit_25_before_stop_50": archived_path.get("take_profit_25_pct_before_stop_50_pct"),
        "archive_path_take_profit_40_before_stop_50": archived_path.get("take_profit_40_pct_before_stop_50_pct"),
        "moonshot_tail_upside_score": moonshot.get("tail_upside_score"),
        "moonshot_eligible": moonshot.get("eligible"),
        "moonshot_reasons": json.dumps(moonshot.get("reasons", [])),
    }
    for window_name, mark in fixed_marks.items():
        if isinstance(mark, dict):
            row[f"{window_name}_mark"] = mark.get("mark")
            row[f"{window_name}_pnl_pct_from_emission"] = mark.get("pnl_pct_from_emission")
            row[f"{window_name}_captured_at_utc"] = mark.get("captured_at_utc")
    return row


def ledger_rows(path: Path, *, source_artifact: str) -> list[dict[str, Any]]:
    return ledger_rows_with_spots(path, source_artifact=source_artifact)


def ledger_rows_with_spots(
    path: Path,
    *,
    source_artifact: str,
    spot_by_run: dict[tuple[str, str], float] | None = None,
    spot_by_date: dict[tuple[str, str], float] | None = None,
) -> list[dict[str, Any]]:
    ledger = _load_json(path)
    rows: list[dict[str, Any]] = []
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict):
            continue
        for pick in entry.get("picks", []):
            if isinstance(pick, dict):
                rows.append(
                    _flatten_pick(
                        entry,
                        pick,
                        source_artifact=source_artifact,
                        spot_by_run=spot_by_run,
                        spot_by_date=spot_by_date,
                    )
                )
    return rows


def write_dataset(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    elif path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
    else:
        frame.to_parquet(path, index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canonical Orographic research datasets from prospective ledgers.")
    parser.add_argument("--prospective-ledger", type=Path, default=Path("web/data/diagnostics/prospective_pick_ledger.json"))
    parser.add_argument("--moonshot-ledger", type=Path, default=Path("web/data/diagnostics/moonshot_prospective_ledger.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/research_datasets"))
    parser.add_argument("--diagnostics-dir", type=Path, default=Path("web/data/diagnostics"))
    parser.add_argument("--format", choices=["parquet", "csv", "json"], default="parquet")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suffix = {"parquet": ".parquet", "csv": ".csv", "json": ".json"}[args.format]
    spot_by_run, spot_by_date = diagnostic_spot_lookups(args.diagnostics_dir)
    recommendation_rows = ledger_rows_with_spots(
        args.prospective_ledger,
        source_artifact="prospective_pick_ledger",
        spot_by_run=spot_by_run,
        spot_by_date=spot_by_date,
    )
    moonshot_rows = ledger_rows_with_spots(
        args.moonshot_ledger,
        source_artifact="moonshot_prospective_ledger",
        spot_by_run=spot_by_run,
        spot_by_date=spot_by_date,
    )

    recommendation_path = args.output_dir / f"option_recommendation_outcomes{suffix}"
    moonshot_path = args.output_dir / f"moonshot_outcomes{suffix}"
    combined_path = args.output_dir / f"all_recommendation_outcomes{suffix}"
    write_dataset(recommendation_rows, recommendation_path)
    write_dataset(moonshot_rows, moonshot_path)
    write_dataset([*recommendation_rows, *moonshot_rows], combined_path)
    print(
        json.dumps(
            {
                "option_recommendation_rows": len(recommendation_rows),
                "moonshot_rows": len(moonshot_rows),
                "output_dir": str(args.output_dir),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
