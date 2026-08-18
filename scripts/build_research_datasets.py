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

from engine.backtest.results import (
    build_option_outcome_dataset_summary,
    canonicalize_option_outcome_dataset,
)
from engine.orographic.path_outcomes import build_archived_quote_path_label
from engine.orographic.prospective import _entry_mark, backfill_executable_labels_from_fixed_marks


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


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _derived_scout_score(option_type: Any, forge_score: Any, scout_score: Any) -> float | None:
    direct = _coerce_float(scout_score)
    if direct is not None:
        return round(_clip(direct, -1.0, 1.0), 4)
    heuristic = _coerce_float(forge_score)
    if heuristic is None:
        return None
    heuristic = _clip(heuristic, 0.0, 1.0)
    if str(option_type or "").strip().lower() == "put":
        return round(_clip(1.0 - 2.0 * heuristic, -1.0, 1.0), 4)
    return round(_clip(2.0 * heuristic - 1.0, -1.0, 1.0), 4)


def _derived_side_probabilities(score: float | None) -> dict[str, float]:
    if score is None:
        return {"call_edge": 0.25, "put_edge": 0.25, "no_trade": 0.50}
    signed = _clip(float(score), -1.0, 1.0)
    strength = min(abs(signed), 1.0)
    no_trade = _clip(1.0 - strength * 1.8, 0.05, 0.90)
    active_mass = 1.0 - no_trade
    dominant = 0.50 + strength / 2.0
    if signed >= 0:
        call_edge = active_mass * dominant
        put_edge = active_mass - call_edge
    else:
        put_edge = active_mass * dominant
        call_edge = active_mass - put_edge
    return {
        "call_edge": round(call_edge, 4),
        "put_edge": round(put_edge, 4),
        "no_trade": round(no_trade, 4),
    }


def _fill_quality_proxy(spread_pct: Any, open_interest: Any, volume: Any, quote_coverage: Any = 1.0) -> float:
    spread = _coerce_float(spread_pct)
    oi = _coerce_float(open_interest) or 0.0
    vol = _coerce_float(volume) or 0.0
    coverage = _coerce_float(quote_coverage)
    coverage = coverage if coverage is not None else 1.0
    spread_component = 1.0 - min(max(spread or 0.18, 0.0) / 0.18, 1.0)
    oi_component = min(math.log1p(max(oi, 0.0)) / math.log1p(1000.0), 1.0)
    volume_component = min(math.log1p(max(vol, 0.0)) / math.log1p(250.0), 1.0)
    return round(_clip(0.55 * spread_component + 0.25 * oi_component + 0.20 * volume_component, 0.0, 1.0) * coverage, 4)


def _no_trade_proxy(
    *,
    option_type: Any,
    forge_score: Any,
    scout_score: Any,
    extrinsic_ratio: Any,
    spread_pct: Any,
    open_interest: Any,
    volume: Any,
    scout_no_trade_prob: Any,
    sentinel_no_trade_relevance: Any,
    sentinel_confidence: Any,
    quote_coverage: Any = 1.0,
) -> float:
    fill_quality = _fill_quality_proxy(spread_pct, open_interest, volume, quote_coverage)
    derived_score = _derived_scout_score(option_type, forge_score, scout_score)
    side_probs = _derived_side_probabilities(derived_score)
    side_no_trade = _coerce_float(scout_no_trade_prob)
    if side_no_trade is None:
        side_no_trade = side_probs["no_trade"]
    sentinel_pressure = (_coerce_float(sentinel_no_trade_relevance) or 0.0) * max((_coerce_float(sentinel_confidence) or 0.0), 0.35)
    directional_edge = 0.5 if derived_score is None else ((derived_score + 1.0) / 2.0 if str(option_type or "").strip().lower() != "put" else (1.0 - derived_score) / 2.0)
    no_trade = (
        0.18
        + 0.35 * (1.0 - fill_quality)
        + 0.20 * min(max(_coerce_float(extrinsic_ratio) or 0.0, 0.0), 1.0)
        + 0.18 * min(max(side_no_trade, 0.0), 1.0)
        + 0.15 * min(max(sentinel_pressure, 0.0), 1.0)
        - 0.20 * min(max(directional_edge, 0.0), 1.0)
    )
    return round(_clip(no_trade, 0.0, 1.0), 4)


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


def side_aware_shadow_lookups(diagnostics_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    target = diagnostics_dir / "side_aware_scout_shadow_ledger.json"
    if not target.exists():
        return {}
    payload = _load_json(target)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in payload.get("entries", []):
        if not isinstance(entry, dict):
            continue
        run_generated_at = str(entry.get("run_generated_at_utc") or "")
        if not run_generated_at:
            continue
        observation_rows = entry.get("observations") if isinstance(entry.get("observations"), list) else entry.get("disagreements", [])
        for row in observation_rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            rows[(run_generated_at, symbol)] = {
                "scout_call_edge_prob": _coerce_float(row.get("call_edge")),
                "scout_put_edge_prob": _coerce_float(row.get("put_edge")),
                "scout_no_trade_prob": _coerce_float(row.get("no_trade")),
                "scout_model_mode": row.get("model_mode"),
                "scout_hierarchical_trade_prob": _coerce_float(row.get("hierarchical_trade_probability")),
                "scout_hierarchical_call_conditional_prob": _coerce_float(row.get("hierarchical_conditional_call_probability")),
                "scout_hierarchical_call_edge_prob": _coerce_float(row.get("hierarchical_call_edge")),
                "scout_hierarchical_put_edge_prob": _coerce_float(row.get("hierarchical_put_edge")),
                "scout_hierarchical_no_trade_prob": _coerce_float(row.get("hierarchical_no_trade")),
                "scout_hierarchical_preferred_side": row.get("hierarchical_preferred_side"),
                "scout_hierarchical_execution_effect": row.get("hierarchical_execution_effect"),
            }
    return rows


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


def _apply_model_backfills(
    row: dict[str, Any],
    *,
    side_aware_by_run: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    run_generated_at = str(row.get("run_generated_at_utc") or "").strip()
    symbol = str(row.get("symbol") or "").strip().upper()
    option_type = row.get("option_type")
    forge_score = row.get("forge_score")
    scout_score = _derived_scout_score(option_type, forge_score, row.get("scout_score"))
    if row.get("scout_score") is None and scout_score is not None:
        row["scout_score"] = scout_score

    side_lookup = side_aware_by_run.get((run_generated_at, symbol)) if side_aware_by_run and run_generated_at and symbol else None
    side_probs = {
        "scout_call_edge_prob": _coerce_float(row.get("scout_call_edge_prob")),
        "scout_put_edge_prob": _coerce_float(row.get("scout_put_edge_prob")),
        "scout_no_trade_prob": _coerce_float(row.get("scout_no_trade_prob")),
    }
    if side_lookup:
        for key, value in side_lookup.items():
            if key == "scout_model_mode":
                row.setdefault("scout_model_mode", value)
            elif key in {"scout_hierarchical_preferred_side", "scout_hierarchical_execution_effect"}:
                row.setdefault(key, value)
            elif value is not None:
                row[key] = round(float(value), 4)
                if key in side_probs:
                    side_probs[key] = float(value)

    if any(value is None for value in side_probs.values()):
        derived = _derived_side_probabilities(scout_score)
        row["scout_call_edge_prob"] = round(float(side_probs["scout_call_edge_prob"]) if side_probs["scout_call_edge_prob"] is not None else derived["call_edge"], 4)
        row["scout_put_edge_prob"] = round(float(side_probs["scout_put_edge_prob"]) if side_probs["scout_put_edge_prob"] is not None else derived["put_edge"], 4)
        row["scout_no_trade_prob"] = round(float(side_probs["scout_no_trade_prob"]) if side_probs["scout_no_trade_prob"] is not None else derived["no_trade"], 4)

    row["sentinel_event_type"] = row.get("sentinel_event_type") or "none"
    row["sentinel_source_reliability"] = row.get("sentinel_source_reliability") or "unknown"
    row["sentinel_novelty"] = row.get("sentinel_novelty") or "unknown"
    row["sentinel_holding_window_label"] = row.get("sentinel_holding_window_label") or "unknown"
    row["sentinel_time_horizon"] = row.get("sentinel_time_horizon") or "unknown"
    row["sentinel_decay_half_life"] = row.get("sentinel_decay_half_life") or "unknown"
    row["sentinel_holding_window_fit"] = round(_coerce_float(row.get("sentinel_holding_window_fit")) or 0.0, 4)
    row["sentinel_confidence"] = round(_coerce_float(row.get("sentinel_confidence")) or 0.0, 4)
    row["sentinel_call_relevance"] = round(_coerce_float(row.get("sentinel_call_relevance")) or 0.0, 4)
    row["sentinel_put_relevance"] = round(_coerce_float(row.get("sentinel_put_relevance")) or 0.0, 4)
    row["sentinel_no_trade_relevance"] = round(_coerce_float(row.get("sentinel_no_trade_relevance")) or 1.0, 4)
    row["sentinel_spot_effect"] = round(_coerce_float(row.get("sentinel_spot_effect")) or 0.0, 4)
    row["sentinel_iv_effect"] = round(_coerce_float(row.get("sentinel_iv_effect")) or 0.0, 4)

    if row.get("prob_fill_quality_ok") is None:
        row["prob_fill_quality_ok"] = _fill_quality_proxy(
            row.get("emission_spread_pct") if row.get("emission_spread_pct") is not None else row.get("entry_spread_pct"),
            row.get("emission_open_interest") if row.get("emission_open_interest") is not None else row.get("entry_open_interest"),
            row.get("emission_volume") if row.get("emission_volume") is not None else row.get("entry_volume"),
            row.get("options_data_coverage_pct"),
        )

    if row.get("prob_no_trade") is None:
        row["prob_no_trade"] = _no_trade_proxy(
            option_type=option_type,
            forge_score=forge_score,
            scout_score=row.get("scout_score"),
            extrinsic_ratio=row.get("extrinsic_ratio"),
            spread_pct=row.get("emission_spread_pct") if row.get("emission_spread_pct") is not None else row.get("entry_spread_pct"),
            open_interest=row.get("emission_open_interest") if row.get("emission_open_interest") is not None else row.get("entry_open_interest"),
            volume=row.get("emission_volume") if row.get("emission_volume") is not None else row.get("entry_volume"),
            scout_no_trade_prob=row.get("scout_no_trade_prob"),
            sentinel_no_trade_relevance=row.get("sentinel_no_trade_relevance"),
            sentinel_confidence=row.get("sentinel_confidence"),
            quote_coverage=row.get("options_data_coverage_pct"),
        )
    return row


def _flatten_pick(
    entry: dict[str, Any],
    pick: dict[str, Any],
    *,
    source_artifact: str,
    spot_by_run: dict[tuple[str, str], float] | None = None,
    spot_by_date: dict[tuple[str, str], float] | None = None,
    side_aware_by_run: dict[tuple[str, str], dict[str, Any]] | None = None,
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
    paired_observation = (
        pick.get("paired_side_observation")
        if isinstance(pick.get("paired_side_observation"), dict)
        else {}
    )
    underlying = pick.get("underlying") if isinstance(pick.get("underlying"), dict) else {}

    row: dict[str, Any] = {
        "source_artifact": source_artifact,
        "recommendation_id": pick.get("recommendation_id"),
        "run_generated_at_utc": entry.get("run_generated_at_utc"),
        "lane": pick.get("lane"),
        "lane_reason": pick.get("lane_reason"),
        "paired_observation_id": paired_observation.get("pair_id"),
        "paired_observation_method": paired_observation.get("method"),
        "paired_observation_target_abs_delta": paired_observation.get("target_abs_delta"),
        "paired_observation_source_scout_direction": paired_observation.get(
            "source_scout_direction"
        ),
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
        "payoff_shadow_prob_positive": scores.get("payoff_shadow_prob_positive"),
        "payoff_shadow_rank": scores.get("payoff_shadow_rank"),
        "payoff_shadow_probability_delta": scores.get("payoff_shadow_probability_delta"),
        "payoff_shadow_rank_delta": scores.get("payoff_shadow_rank_delta"),
        "payoff_shadow_disagreement": scores.get("payoff_shadow_disagreement"),
        "payoff_shadow_mode": scores.get("payoff_shadow_mode"),
        "payoff_shadow_artifact_sha256": scores.get("payoff_shadow_artifact_sha256"),
        "payoff_shadow_return_q10": scores.get("payoff_shadow_return_q10"),
        "payoff_shadow_return_q50": scores.get("payoff_shadow_return_q50"),
        "payoff_shadow_return_q90": scores.get("payoff_shadow_return_q90"),
        "payoff_shadow_prob_fill_quality": scores.get("payoff_shadow_prob_fill_quality"),
        "payoff_shadow_prob_target_before_stop": scores.get("payoff_shadow_prob_target_before_stop"),
        "payoff_shadow_conservative_utility": scores.get("payoff_shadow_conservative_utility"),
        "prob_no_trade": scores.get("prob_no_trade"),
        "prob_fill_quality_ok": scores.get("prob_fill_quality_ok"),
        "prob_exceeds_breakeven": scores.get("prob_exceeds_breakeven"),
        "path_holding_quality_score": scores.get("path_holding_quality_score"),
        "path_early_profit_take_prob": scores.get("path_early_profit_take_prob"),
        "path_decay_risk": scores.get("path_decay_risk"),
        "path_hazard_target_probability": scores.get("path_hazard_target_probability"),
        "path_hazard_stop_probability": scores.get("path_hazard_stop_probability"),
        "path_hazard_expiry_probability": scores.get("path_hazard_expiry_probability"),
        "path_exit_shadow_action": scores.get("path_exit_shadow_action"),
        "path_hazard_artifact_sha256": scores.get("path_hazard_artifact_sha256"),
        "expected_edge_after_friction_pct": scores.get("expected_edge_after_friction_pct"),
        "scout_call_edge_prob": risk.get("scout_call_edge_prob"),
        "scout_put_edge_prob": risk.get("scout_put_edge_prob"),
        "scout_no_trade_prob": risk.get("scout_no_trade_prob"),
        "sentinel_event_type": risk.get("sentinel_event_type"),
        "sentinel_source_reliability": risk.get("sentinel_source_reliability"),
        "sentinel_novelty": risk.get("sentinel_novelty"),
        "sentinel_holding_window_fit": risk.get("sentinel_holding_window_fit"),
        "sentinel_holding_window_label": risk.get("sentinel_holding_window_label"),
        "sentinel_time_horizon": risk.get("sentinel_time_horizon"),
        "sentinel_decay_half_life": risk.get("sentinel_decay_half_life"),
        "delta": risk.get("delta"),
        "implied_volatility": risk.get("implied_volatility"),
        "iv_rank": risk.get("iv_rank"),
        "surface_atm_iv": risk.get("surface_atm_iv"),
        "surface_skew_slope": risk.get("surface_skew_slope"),
        "surface_curvature": risk.get("surface_curvature"),
        "surface_put_call_wing_skew": risk.get("surface_put_call_wing_skew"),
        "surface_term_slope_30d": risk.get("surface_term_slope_30d"),
        "surface_fit_rmse": risk.get("surface_fit_rmse"),
        "surface_observation_count": risk.get("surface_observation_count"),
        "iv_relative_to_atm": risk.get("iv_relative_to_atm"),
        "iv_minus_realized_vol": risk.get("iv_minus_realized_vol"),
        "chain_snapshot_at_utc": emission_quote.get("chain_snapshot_at_utc"),
        "last_trade_age_seconds": emission_quote.get("last_trade_age_seconds"),
        "quote_mid": emission_quote.get("quote_mid"),
        "quote_spread_dollars": emission_quote.get("quote_spread_dollars"),
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
    executable_labels = outcomes.get("executable_labels") if isinstance(outcomes.get("executable_labels"), dict) else {}
    for window_name, label in executable_labels.items():
        if not isinstance(label, dict):
            continue
        contract = label.get("label_contract") if isinstance(label.get("label_contract"), dict) else {}
        label_entry = label.get("entry") if isinstance(label.get("entry"), dict) else {}
        label_exit = label.get("exit") if isinstance(label.get("exit"), dict) else {}
        entry_quote = label_entry.get("quote") if isinstance(label_entry.get("quote"), dict) else {}
        exit_quote = label_exit.get("quote") if isinstance(label_exit.get("quote"), dict) else {}
        prefix = f"{window_name}_executable_"
        row.update({
            f"{prefix}label_contract_id": contract.get("id"),
            f"{prefix}label_contract_version": contract.get("version"),
            f"{prefix}label_available_at_utc": label.get("label_available_at_utc"),
            f"{prefix}entry_price": label_entry.get("execution_price"),
            f"{prefix}entry_price_source": label_entry.get("execution_price_source"),
            f"{prefix}entry_quote_age_seconds": entry_quote.get("age_at_decision_seconds"),
            f"{prefix}exit_price": label_exit.get("execution_price"),
            f"{prefix}exit_price_source": label_exit.get("execution_price_source"),
            f"{prefix}exit_capture_delay_seconds": exit_quote.get("capture_delay_seconds"),
            f"{prefix}total_fees_usd": label.get("total_fees_usd"),
            f"{prefix}total_signed_adverse_slippage_usd": label.get("total_signed_adverse_slippage_usd"),
            f"{prefix}net_pnl_usd": label.get("net_executable_pnl_usd"),
            f"{prefix}net_return": label.get("net_executable_return"),
        })
    return _apply_model_backfills(row, side_aware_by_run=side_aware_by_run)


def _apply_executable_outcome_to_canonical_row(row: dict[str, Any], label: object) -> None:
    """Make a valid v1 label canonical while retaining all legacy columns."""

    if not isinstance(label, dict):
        return
    contract = label.get("label_contract") if isinstance(label.get("label_contract"), dict) else {}
    entry = label.get("entry") if isinstance(label.get("entry"), dict) else {}
    exit_data = label.get("exit") if isinstance(label.get("exit"), dict) else {}
    entry_quote = entry.get("quote") if isinstance(entry.get("quote"), dict) else {}
    exit_quote = exit_data.get("quote") if isinstance(exit_data.get("quote"), dict) else {}
    entry_price = _coerce_float(entry.get("execution_price"))
    exit_price = _coerce_float(exit_data.get("execution_price"))
    gross_pnl = _coerce_float(label.get("gross_executable_pnl_usd"))
    counterfactual_pnl = _coerce_float(label.get("midpoint_counterfactual_pnl_usd"))
    counterfactual_cost_basis = _coerce_float(label.get("midpoint_counterfactual_cost_basis_usd"))
    net_pnl = _coerce_float(label.get("net_executable_pnl_usd"))
    net_return = _coerce_float(label.get("net_executable_return"))
    cost_basis = _coerce_float(label.get("cost_basis_usd"))
    fees = _coerce_float(label.get("total_fees_usd"))
    if not contract.get("id") or None in (entry_price, exit_price, gross_pnl, net_pnl, net_return, cost_basis):
        return

    contracts = int(label.get("contracts") or 1)
    multiplier = _coerce_float(label.get("contract_multiplier")) or 100.0
    raw_cost_basis = counterfactual_cost_basis or (float(entry_price) * contracts * multiplier)
    raw_exit_value = float(exit_price) * contracts * multiplier
    before_friction_pnl = counterfactual_pnl if counterfactual_pnl is not None else float(gross_pnl)
    gross_return = before_friction_pnl / raw_cost_basis if raw_cost_basis > 0 else None
    entry_slippage = _coerce_float(entry.get("signed_adverse_slippage_usd")) or 0.0
    exit_slippage = _coerce_float(exit_data.get("signed_adverse_slippage_usd")) or 0.0
    label_available = str(label.get("label_available_at_utc") or "")
    exit_observed = str(exit_quote.get("observed_at_utc") or label_available)
    row.update({
        "decision_at_utc": label.get("decision_at_utc"),
        "entry_date": str(label.get("decision_at_utc") or row.get("entry_date") or "")[:10],
        "exit_date": exit_observed[:10] or row.get("exit_date"),
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "entry_bid": _coerce_float(entry_quote.get("bid")),
        "entry_ask": _coerce_float(entry_quote.get("ask")),
        "exit_bid": _coerce_float(exit_quote.get("bid")),
        "exit_ask": _coerce_float(exit_quote.get("ask")),
        "entry_quote_observed_at_utc": entry_quote.get("observed_at_utc"),
        "exit_quote_observed_at_utc": exit_quote.get("observed_at_utc"),
        "entry_quote_source": entry_quote.get("source"),
        "exit_quote_source": exit_quote.get("source"),
        "entry_execution_price_source": entry.get("execution_price_source"),
        "exit_execution_price_source": exit_data.get("execution_price_source"),
        "contracts": contracts,
        "entry_data_source": entry_quote.get("source"),
        "exit_data_source": exit_quote.get("source"),
        "entry_quote_type": entry.get("execution_price_source"),
        "exit_quote_type": exit_data.get("execution_price_source"),
        "cost_basis": float(cost_basis),
        "exit_value": raw_exit_value,
        "raw_cost_basis": raw_cost_basis,
        "raw_exit_value": raw_exit_value,
        "pnl": float(net_pnl),
        "pnl_pct": float(net_return),
        "raw_pnl": before_friction_pnl,
        "raw_pnl_pct": gross_return,
        "entry_friction_cost_usd": _coerce_float(entry.get("fees_usd")) or 0.0,
        "exit_friction_cost_usd": _coerce_float(exit_data.get("fees_usd")) or 0.0,
        "total_friction_cost_usd": before_friction_pnl - float(net_pnl),
        "friction_drag_pct": (before_friction_pnl - float(net_pnl)) / raw_cost_basis if raw_cost_basis > 0 else None,
        "entry_slippage_pct": entry_slippage / raw_cost_basis if raw_cost_basis > 0 else None,
        "exit_slippage_pct": exit_slippage / raw_cost_basis if raw_cost_basis > 0 else None,
        "positive_pnl_before_friction": before_friction_pnl > 0.0,
        "positive_pnl_after_friction": float(net_pnl) > 0.0,
        "breakeven_before_friction": before_friction_pnl >= 0.0,
        "breakeven_after_friction": float(net_pnl) >= 0.0,
        "friction_flipped_winner_to_loser": before_friction_pnl > 0.0 and float(net_pnl) <= 0.0,
        "hold_period_return_before_friction_pct": gross_return,
        "hold_period_return_after_friction_pct": float(net_return),
        "executable_label_contract_id": contract.get("id"),
        "executable_label_contract_version": contract.get("version"),
        "executable_label_available_at_utc": label_available,
        "label_source": "executable_quote_or_fill",
        "executable_entry_quote_age_seconds": entry_quote.get("age_at_decision_seconds"),
        "executable_exit_capture_delay_seconds": exit_quote.get("capture_delay_seconds"),
        "executable_total_signed_adverse_slippage_usd": label.get("total_signed_adverse_slippage_usd"),
    })


def _pick_with_archive_label(
    pick: dict[str, Any],
    *,
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    if archive_dir is None:
        return pick
    outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
    archived = outcomes.get("archived_quote_path") if isinstance(outcomes.get("archived_quote_path"), dict) else {}
    if archived.get("status") == "observed":
        return pick
    updated = json.loads(json.dumps(pick))
    updated_outcomes = updated.setdefault("outcomes", {})
    updated_outcomes["archived_quote_path"] = build_archived_quote_path_label(updated, archive_dir=archive_dir)
    return updated


def _exit_date_from_mark(mark: dict[str, Any], fallback: str) -> str:
    for key in ("captured_at_utc", "quote_date"):
        raw = str(mark.get(key) or "").strip()
        if raw:
            return raw[:10]
    return fallback


def _is_valid_executable_label(label: object) -> bool:
    if not isinstance(label, dict):
        return False
    contract = label.get("label_contract") if isinstance(label.get("label_contract"), dict) else {}
    entry = label.get("entry") if isinstance(label.get("entry"), dict) else {}
    exit_data = label.get("exit") if isinstance(label.get("exit"), dict) else {}
    entry_quote = entry.get("quote") if isinstance(entry.get("quote"), dict) else {}
    exit_quote = exit_data.get("quote") if isinstance(exit_data.get("quote"), dict) else {}
    return bool(
        str(contract.get("id") or "").startswith("orographic.executable_option_outcome.")
        and _coerce_float(entry_quote.get("ask")) is not None
        and (_coerce_float(entry_quote.get("ask")) or 0.0) > 0
        and _coerce_float(exit_quote.get("bid")) is not None
        and (_coerce_float(exit_quote.get("bid")) or 0.0) >= 0
        and _coerce_float(entry.get("execution_price")) is not None
        and _coerce_float(exit_data.get("execution_price")) is not None
        and label.get("decision_at_utc")
        and entry_quote.get("observed_at_utc")
        and exit_quote.get("observed_at_utc")
        and label.get("label_available_at_utc")
    )


def _option_outcome_row_from_pick(
    entry: dict[str, Any],
    pick: dict[str, Any],
    *,
    exit_window: str,
    archive_dir: Path | None = None,
    side_aware_by_run: dict[tuple[str, str], dict[str, Any]] | None = None,
    require_executable_label: bool = False,
) -> dict[str, Any] | None:
    enriched_pick = _pick_with_archive_label(pick, archive_dir=archive_dir)
    outcomes = enriched_pick.get("outcomes") if isinstance(enriched_pick.get("outcomes"), dict) else {}
    executable_labels = outcomes.get("executable_labels") if isinstance(outcomes.get("executable_labels"), dict) else {}
    executable_label = executable_labels.get(exit_window)
    if require_executable_label and not _is_valid_executable_label(executable_label):
        return None
    fixed_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
    exit_mark = fixed_marks.get(exit_window) if isinstance(fixed_marks.get(exit_window), dict) else None
    if not isinstance(exit_mark, dict):
        return None

    entry_mark = _entry_mark(enriched_pick)
    mark = _coerce_float(exit_mark.get("mark"))
    if entry_mark is None or entry_mark <= 0 or mark is None or mark <= 0:
        return None

    entry_date = str(enriched_pick.get("run_generated_at_utc") or entry.get("run_generated_at_utc") or "")[:10]
    if not entry_date:
        return None

    underlying = enriched_pick.get("underlying") if isinstance(enriched_pick.get("underlying"), dict) else {}
    emission_quote = enriched_pick.get("emission_quote") if isinstance(enriched_pick.get("emission_quote"), dict) else {}
    scores = enriched_pick.get("scores") if isinstance(enriched_pick.get("scores"), dict) else {}
    risk = enriched_pick.get("risk_features") if isinstance(enriched_pick.get("risk_features"), dict) else {}
    context = enriched_pick.get("context") if isinstance(enriched_pick.get("context"), dict) else {}
    paired_observation = (
        enriched_pick.get("paired_side_observation")
        if isinstance(enriched_pick.get("paired_side_observation"), dict)
        else {}
    )
    archived_path = outcomes.get("archived_quote_path") if isinstance(outcomes.get("archived_quote_path"), dict) else {}
    path_rules = outcomes.get("path_rules") if isinstance(outcomes.get("path_rules"), dict) else {}

    cost_basis = round(float(entry_mark) * 100.0, 2)
    exit_value = round(float(mark) * 100.0, 2)
    pnl = round(exit_value - cost_basis, 2)
    pnl_pct = round(float(mark) / float(entry_mark) - 1.0, 4)
    regime = entry.get("regime") if isinstance(entry.get("regime"), dict) else {}
    model_modes = context.get("model_modes") if isinstance(context.get("model_modes"), dict) else {}

    row = {
        "symbol": str(enriched_pick.get("symbol") or "").upper(),
        "contract_symbol": enriched_pick.get("contract_symbol"),
        "option_type": str(enriched_pick.get("option_type") or "").lower(),
        "strike": enriched_pick.get("strike"),
        "expiry": enriched_pick.get("expiry"),
        "entry_date": entry_date,
        "exit_date": _exit_date_from_mark(exit_mark, entry_date),
        "entry_spot": _coerce_float(underlying.get("spot")),
        "exit_spot": None,
        "entry_price": round(float(entry_mark), 4),
        "exit_price": round(float(mark), 4),
        "contracts": 1,
        "entry_data_source": emission_quote.get("entry_data_source", "real_chain"),
        "exit_data_source": "prospective_fixed_exit_mark",
        "entry_quote_type": emission_quote.get("entry_quote_type", "mid"),
        "exit_quote_type": exit_mark.get("mark_source"),
        "options_data_coverage_pct": 1.0 if bool((outcomes.get("quote_verification") or {}).get("outcome_quotes_captured")) else 0.0,
        "cost_basis": cost_basis,
        "exit_value": exit_value,
        "raw_cost_basis": cost_basis,
        "raw_exit_value": exit_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "raw_pnl": pnl,
        "raw_pnl_pct": pnl_pct,
        "entry_friction_cost_usd": 0.0,
        "exit_friction_cost_usd": 0.0,
        "total_friction_cost_usd": 0.0,
        "friction_drag_pct": 0.0,
        "entry_slippage_pct": 0.0,
        "exit_slippage_pct": 0.0,
        "entry_spread_pct": emission_quote.get("spread_pct"),
        "exit_spread_pct": None,
        "entry_open_interest": emission_quote.get("open_interest"),
        "entry_volume": emission_quote.get("volume"),
        "exit_open_interest": None,
        "exit_volume": None,
        "positive_pnl_before_friction": pnl_pct > 0.0,
        "positive_pnl_after_friction": pnl_pct > 0.0,
        "breakeven_before_friction": pnl_pct > 0.0,
        "breakeven_after_friction": pnl_pct > 0.0,
        "friction_flipped_winner_to_loser": False,
        "hold_period_return_before_friction_pct": pnl_pct,
        "hold_period_return_after_friction_pct": pnl_pct,
        "expired_worthless": mark <= 0.01,
        "forge_score": scores.get("forge_score"),
        "scout_score": scores.get("scout_score"),
        "implied_volatility": risk.get("implied_volatility"),
        "delta": risk.get("delta"),
        "moneyness": risk.get("moneyness"),
        "projected_move_pct": risk.get("projected_move_pct"),
        "breakeven_move_pct": risk.get("breakeven_move_pct"),
        "expected_return_pct": scores.get("expected_option_return_pct_model"),
        "extrinsic_ratio": risk.get("extrinsic_ratio"),
        "iv_rank": risk.get("iv_rank"),
        "surface_atm_iv": risk.get("surface_atm_iv"),
        "surface_skew_slope": risk.get("surface_skew_slope"),
        "surface_curvature": risk.get("surface_curvature"),
        "surface_put_call_wing_skew": risk.get("surface_put_call_wing_skew"),
        "surface_term_slope_30d": risk.get("surface_term_slope_30d"),
        "surface_fit_rmse": risk.get("surface_fit_rmse"),
        "surface_observation_count": risk.get("surface_observation_count"),
        "iv_relative_to_atm": risk.get("iv_relative_to_atm"),
        "iv_minus_realized_vol": risk.get("iv_minus_realized_vol"),
        "chain_snapshot_at_utc": emission_quote.get("chain_snapshot_at_utc"),
        "last_trade_age_seconds": emission_quote.get("last_trade_age_seconds"),
        "quote_mid": emission_quote.get("quote_mid"),
        "quote_spread_dollars": emission_quote.get("quote_spread_dollars"),
        "allocation_weight": 1.0,
        "realized_vol_20d": risk.get("realized_vol_20d"),
        "atr_pct_14d": risk.get("atr_pct_14d"),
        "premium_pct_of_spot": risk.get("premium_pct_of_spot"),
        "vrp_gap": (
            round(float(risk.get("implied_volatility")) - float(risk.get("realized_vol_20d")), 4)
            if _coerce_float(risk.get("implied_volatility")) is not None and _coerce_float(risk.get("realized_vol_20d")) is not None
            else None
        ),
        "regime_mode": regime.get("mode"),
        "regime_bias": regime.get("bias"),
        "regime_source_symbol": regime.get("source_symbol"),
        "regime_observed_at_utc": enriched_pick.get("run_generated_at_utc") or entry.get("run_generated_at_utc"),
        "regime_label_source": "signal_time_snapshot" if regime.get("mode") else None,
        "pre_payoff_forge_score": scores.get("forge_score"),
        "directional_edge": None,
        "liquidity_score": None,
        "regime_alignment_score": None,
        "prob_positive_option_pnl": scores.get("prob_positive_option_pnl"),
        "prob_no_trade": scores.get("prob_no_trade"),
        "prob_fill_quality_ok": scores.get("prob_fill_quality_ok"),
        "expected_option_return_pct_model": scores.get("expected_option_return_pct_model"),
        "expected_option_return_pct_rank": scores.get("expected_option_return_pct_model"),
        "prob_exceeds_breakeven": scores.get("prob_exceeds_breakeven"),
        "max_favorable_excursion_before_expiry": archived_path.get("max_favorable_excursion_pct"),
        "adverse_excursion_risk": archived_path.get("max_adverse_excursion_pct"),
        "payoff_model_score": scores.get("payoff_model_score"),
        "payoff_shadow_prob_positive": scores.get("payoff_shadow_prob_positive"),
        "payoff_shadow_rank": scores.get("payoff_shadow_rank"),
        "payoff_shadow_probability_delta": scores.get("payoff_shadow_probability_delta"),
        "payoff_shadow_rank_delta": scores.get("payoff_shadow_rank_delta"),
        "payoff_shadow_disagreement": scores.get("payoff_shadow_disagreement"),
        "payoff_shadow_mode": scores.get("payoff_shadow_mode"),
        "payoff_shadow_artifact_sha256": scores.get("payoff_shadow_artifact_sha256"),
        "payoff_shadow_return_q10": scores.get("payoff_shadow_return_q10"),
        "payoff_shadow_return_q50": scores.get("payoff_shadow_return_q50"),
        "payoff_shadow_return_q90": scores.get("payoff_shadow_return_q90"),
        "payoff_shadow_prob_fill_quality": scores.get("payoff_shadow_prob_fill_quality"),
        "payoff_shadow_prob_target_before_stop": scores.get("payoff_shadow_prob_target_before_stop"),
        "payoff_shadow_conservative_utility": scores.get("payoff_shadow_conservative_utility"),
        "payoff_shadow_disagreement_realized_pnl_pct": pnl_pct if scores.get("payoff_shadow_disagreement") else None,
        "final_candidate_score": scores.get("final_candidate_score"),
        "path_early_profit_take_prob": scores.get("path_early_profit_take_prob"),
        "path_expected_mfe_pct": archived_path.get("max_favorable_excursion_pct") or path_rules.get("max_favorable_excursion_pct"),
        "path_decay_risk": scores.get("path_decay_risk"),
        "path_holding_quality_score": scores.get("path_holding_quality_score"),
        "path_hazard_target_probability": scores.get("path_hazard_target_probability"),
        "path_hazard_stop_probability": scores.get("path_hazard_stop_probability"),
        "path_hazard_expiry_probability": scores.get("path_hazard_expiry_probability"),
        "path_exit_shadow_action": scores.get("path_exit_shadow_action"),
        "path_hazard_artifact_sha256": scores.get("path_hazard_artifact_sha256"),
        "path_model_mode": model_modes.get("path_model"),
        "path_model_artifact_sha256": context.get("path_model_artifact_sha256"),
        "scout_call_edge_prob": risk.get("scout_call_edge_prob"),
        "scout_put_edge_prob": risk.get("scout_put_edge_prob"),
        "scout_no_trade_prob": risk.get("scout_no_trade_prob"),
        "sentinel_event_type": risk.get("sentinel_event_type"),
        "sentinel_source_reliability": risk.get("sentinel_source_reliability"),
        "sentinel_novelty": risk.get("sentinel_novelty"),
        "sentinel_holding_window_fit": risk.get("sentinel_holding_window_fit"),
        "sentinel_holding_window_label": risk.get("sentinel_holding_window_label"),
        "sentinel_confidence": risk.get("sentinel_confidence"),
        "sentinel_call_relevance": risk.get("sentinel_call_relevance"),
        "sentinel_put_relevance": risk.get("sentinel_put_relevance"),
        "sentinel_no_trade_relevance": risk.get("sentinel_no_trade_relevance"),
        "sentinel_spot_effect": risk.get("sentinel_spot_effect"),
        "sentinel_iv_effect": risk.get("sentinel_iv_effect"),
        "sentinel_time_horizon": risk.get("sentinel_time_horizon"),
        "sentinel_decay_half_life": risk.get("sentinel_decay_half_life"),
        "source_artifact": outcomes.get("source_artifact", "prospective_pick_ledger"),
        "recommendation_id": enriched_pick.get("recommendation_id"),
        "lane": enriched_pick.get("lane"),
        "paired_observation_id": paired_observation.get("pair_id"),
        "paired_observation_method": paired_observation.get("method"),
        "paired_observation_target_abs_delta": paired_observation.get("target_abs_delta"),
        "paired_observation_source_scout_direction": paired_observation.get(
            "source_scout_direction"
        ),
        "run_generated_at_utc": enriched_pick.get("run_generated_at_utc") or entry.get("run_generated_at_utc"),
        "fixed_exit_window": exit_window,
        "archived_quote_path": archived_path,
    }
    _apply_executable_outcome_to_canonical_row(row, executable_label)
    return _apply_model_backfills(row, side_aware_by_run=side_aware_by_run)


def canonical_option_outcome_rows(
    path: Path,
    *,
    source_artifact: str,
    exit_window: str,
    archive_dir: Path | None = None,
    side_aware_by_run: dict[tuple[str, str], dict[str, Any]] | None = None,
    require_executable_label: bool = False,
) -> list[dict[str, Any]]:
    ledger = _load_json(path)
    if require_executable_label:
        ledger, _ = backfill_executable_labels_from_fixed_marks(ledger)
    rows: list[dict[str, Any]] = []
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict):
            continue
        for pick in entry.get("picks", []):
            if not isinstance(pick, dict):
                continue
            row = _option_outcome_row_from_pick(
                entry,
                pick,
                exit_window=exit_window,
                archive_dir=archive_dir,
                side_aware_by_run=side_aware_by_run,
                require_executable_label=require_executable_label,
            )
            if row is None:
                continue
            row["source_artifact"] = source_artifact
            rows.append(row)
    return rows


def ledger_rows(path: Path, *, source_artifact: str) -> list[dict[str, Any]]:
    return ledger_rows_with_spots(path, source_artifact=source_artifact)


def ledger_rows_with_spots(
    path: Path,
    *,
    source_artifact: str,
    spot_by_run: dict[tuple[str, str], float] | None = None,
    spot_by_date: dict[tuple[str, str], float] | None = None,
    side_aware_by_run: dict[tuple[str, str], dict[str, Any]] | None = None,
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
                        side_aware_by_run=side_aware_by_run,
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
    parser.add_argument("--archive-dir", type=Path, default=Path("engine/data/live_options_archive"))
    parser.add_argument(
        "--canonical-option-outcomes-output",
        type=Path,
        default=Path("output/option_outcomes_live_recommendations.json"),
        help="Optional canonical option_outcome_dataset artifact built from completed prospective outcomes.",
    )
    parser.add_argument(
        "--canonical-exit-window",
        choices=["one_hour", "end_of_day", "next_day_close", "friday_close"],
        default="friday_close",
        help="Which fixed exit window to convert into canonical option outcome labels.",
    )
    parser.add_argument("--format", choices=["parquet", "csv", "json"], default="parquet")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suffix = {"parquet": ".parquet", "csv": ".csv", "json": ".json"}[args.format]
    spot_by_run, spot_by_date = diagnostic_spot_lookups(args.diagnostics_dir)
    side_aware_by_run = side_aware_shadow_lookups(args.diagnostics_dir)
    recommendation_rows = ledger_rows_with_spots(
        args.prospective_ledger,
        source_artifact="prospective_pick_ledger",
        spot_by_run=spot_by_run,
        spot_by_date=spot_by_date,
        side_aware_by_run=side_aware_by_run,
    )
    moonshot_rows = ledger_rows_with_spots(
        args.moonshot_ledger,
        source_artifact="moonshot_prospective_ledger",
        spot_by_run=spot_by_run,
        spot_by_date=spot_by_date,
        side_aware_by_run=side_aware_by_run,
    )

    recommendation_path = args.output_dir / f"option_recommendation_outcomes{suffix}"
    moonshot_path = args.output_dir / f"moonshot_outcomes{suffix}"
    combined_path = args.output_dir / f"all_recommendation_outcomes{suffix}"
    write_dataset(recommendation_rows, recommendation_path)
    write_dataset(moonshot_rows, moonshot_path)
    write_dataset([*recommendation_rows, *moonshot_rows], combined_path)
    canonical_rows = canonicalize_option_outcome_dataset(
        [
            *canonical_option_outcome_rows(
                args.prospective_ledger,
                source_artifact="prospective_pick_ledger",
                exit_window=args.canonical_exit_window,
                archive_dir=args.archive_dir,
                side_aware_by_run=side_aware_by_run,
                require_executable_label=True,
            ),
            *canonical_option_outcome_rows(
                args.moonshot_ledger,
                source_artifact="moonshot_prospective_ledger",
                exit_window=args.canonical_exit_window,
                archive_dir=args.archive_dir,
                side_aware_by_run=side_aware_by_run,
                require_executable_label=True,
            ),
        ]
    )
    if args.canonical_option_outcomes_output:
        args.canonical_option_outcomes_output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "artifact": "option_outcome_dataset",
            "label_policy": "strict_executable_quote_or_fill_v2",
            "generated_at": pd.Timestamp.now("UTC").date().isoformat(),
            "backtest_start": min((row["entry_date"] for row in canonical_rows), default=None),
            "backtest_end": max((row["exit_date"] for row in canonical_rows), default=None),
            "summary": build_option_outcome_dataset_summary(canonical_rows),
            "rows": canonical_rows,
        }
        args.canonical_option_outcomes_output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "option_recommendation_rows": len(recommendation_rows),
                "moonshot_rows": len(moonshot_rows),
                "canonical_option_outcome_rows": len(canonical_rows),
                "canonical_option_outcomes_output": str(args.canonical_option_outcomes_output) if args.canonical_option_outcomes_output else None,
                "output_dir": str(args.output_dir),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
