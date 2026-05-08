from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from numbers import Number
from pathlib import Path
from typing import Any
import json
from zoneinfo import ZoneInfo

from .council import select_board
from .forge import rank_contracts_with_diagnostics, select_signals_for_forge
from .scout import scan_symbols_with_diagnostics


DEFAULT_UNIVERSE_FILE = Path(__file__).resolve().parents[1] / "sample_universe.txt"
DIAGNOSTIC_TIMEZONE = ZoneInfo("America/Chicago")


def _read_universe_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    symbols: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip().upper()
        if cleaned and not cleaned.startswith("#"):
            symbols.append(cleaned)
    return symbols


DEFAULT_UNIVERSE = _read_universe_file(DEFAULT_UNIVERSE_FILE) or [
    "SPY",
    "QQQ",
    "IWM",
    "NVDA",
    "AMD",
    "TSLA",
    "META",
    "AAPL",
    "MSFT",
]
MODEL_DIR = Path(__file__).resolve().parent / "models"


@dataclass
class PipelineConfig:
    universe: list[str]
    live_size: int = 3
    shadow_size: int = 3
    forge_intake: int = 6

log = logging.getLogger(__name__)


def _normalize_timestamp(raw: object) -> datetime:
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = datetime.fromisoformat(raw.strip())
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc).replace(microsecond=0)


def _coerce_int(value: object) -> int:
    if isinstance(value, Number):
        return int(value)
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _sorted_reason_counts(rows: list[dict[str, Any]], *, reason_key: str) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get(reason_key) or "unknown").strip() or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _compact_contract_view(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    compact: list[dict[str, object]] = []
    for row in rows:
        compact.append(
            {
                "symbol": row.get("symbol"),
                "option_type": row.get("option_type"),
                "expiry": row.get("expiry"),
                "strike": row.get("strike"),
                "forge_score": row.get("forge_score"),
                "learned_rank_score": row.get("learned_rank_score"),
                "ranker_mode": row.get("ranker_mode"),
                "contract_cost": row.get("contract_cost"),
                "sector": row.get("sector"),
                "risk_adjusted_score": row.get("risk_adjusted_score"),
                "is_spread": bool(row.get("is_spread")),
            }
        )
    return compact


def _compact_attribution_contract_view(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    compact: list[dict[str, object]] = []
    for row in rows:
        compact.append(
            {
                "symbol": row.get("symbol"),
                "contract_symbol": row.get("contract_symbol"),
                "option_type": row.get("option_type"),
                "expiry": row.get("expiry"),
                "strike": row.get("strike"),
                "forge_score": row.get("forge_score"),
                "payoff_edge_score": row.get("payoff_edge_score"),
                "expected_edge_after_friction_pct": row.get("expected_edge_after_friction_pct"),
                "contract_cost": row.get("contract_cost"),
                "council_risk_flags": row.get("council_risk_flags", []),
                "notes": row.get("notes", []),
            }
        )
    return compact


def _count_side_mix(rows: list[dict[str, Any]], *, key: str) -> dict[str, int]:
    counts = {"call": 0, "put": 0}
    for row in rows:
        side = str(row.get(key) or "").strip().lower()
        if side in counts:
            counts[side] += 1
    return counts


def _average_metric(rows: list[dict[str, Any]], key: str) -> float | None:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, Number):
            values.append(float(value))
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_artifact_status() -> dict[str, Any]:
    artifacts = {
        "scout_model": MODEL_DIR / "scout_model.pkl",
        "scout_scaler": MODEL_DIR / "scout_scaler.pkl",
        "scout_side_model": MODEL_DIR / "scout_side_model.pkl",
        "payoff_model": MODEL_DIR / "payoff_model.pkl",
        "scout_model_card": MODEL_DIR / "scout_model_card.json",
        "payoff_model_card": MODEL_DIR / "payoff_model_card.json",
    }
    return {
        name: {
            "present": path.exists(),
            "sha256": _sha256_file(path),
        }
        for name, path in artifacts.items()
    }


def _validate_snapshot_contract(payload: dict[str, Any]) -> None:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    council_summary = council.get("summary") if isinstance(council.get("summary"), dict) else {}
    scout_signals = payload.get("scout_signals") if isinstance(payload.get("scout_signals"), list) else []
    forge_candidates = payload.get("forge_candidates") if isinstance(payload.get("forge_candidates"), list) else []
    live_board = council.get("live_board") if isinstance(council.get("live_board"), list) else []
    shadow_board = council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []

    expected = {
        "scout_signal_count": len(scout_signals),
        "forge_candidate_count": len(forge_candidates),
        "candidate_count": len(forge_candidates),
        "live_count": len(live_board),
        "shadow_count": len(shadow_board),
    }
    observed = {
        "scout_signal_count": _coerce_int(summary.get("scout_signal_count")),
        "forge_candidate_count": _coerce_int(summary.get("forge_candidate_count")),
        "candidate_count": _coerce_int(council_summary.get("candidate_count")),
        "live_count": _coerce_int(council_summary.get("live_count")),
        "shadow_count": _coerce_int(council_summary.get("shadow_count")),
    }
    mismatches = {
        key: {"expected": expected[key], "observed": observed[key]}
        for key in expected
        if expected[key] != observed[key]
    }
    if mismatches:
        raise ValueError(f"Snapshot contract mismatch: {mismatches}")


def _shadow_preferred_side(row: dict[str, Any]) -> str:
    scores = {
        "call": row.get("call_edge"),
        "put": row.get("put_edge"),
        "no_trade": row.get("no_trade"),
    }
    numeric_scores = {
        key: float(value)
        for key, value in scores.items()
        if isinstance(value, Number)
    }
    if not numeric_scores:
        return "unknown"
    return max(numeric_scores.items(), key=lambda item: item[1])[0]


def build_promotion_readiness(payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    scout = diagnostics.get("scout") if isinstance(diagnostics.get("scout"), dict) else {}
    forge = diagnostics.get("forge") if isinstance(diagnostics.get("forge"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    council_summary = council.get("summary") if isinstance(council.get("summary"), dict) else {}

    scout_signals = payload.get("scout_signals") if isinstance(payload.get("scout_signals"), list) else []
    signal_direction_by_symbol = {
        str(row.get("symbol") or "").upper(): str(row.get("direction") or "").lower()
        for row in scout_signals
        if isinstance(row, dict)
    }
    side_rows = scout.get("side_aware_scores") if isinstance(scout.get("side_aware_scores"), list) else []
    side_mix: dict[str, int] = {"call": 0, "put": 0, "no_trade": 0, "unknown": 0}
    side_disagreements = 0
    side_model_modes: dict[str, int] = {}
    for row in side_rows:
        if not isinstance(row, dict):
            continue
        preferred = _shadow_preferred_side(row)
        side_mix[preferred] = side_mix.get(preferred, 0) + 1
        model_mode = str(row.get("model_mode") or "unknown")
        side_model_modes[model_mode] = side_model_modes.get(model_mode, 0) + 1
        active_direction = str(row.get("active_direction") or "").lower()
        if active_direction not in {"call", "put"}:
            active_direction = signal_direction_by_symbol.get(str(row.get("symbol") or "").upper())
        if active_direction in {"call", "put"} and preferred in {"call", "put", "no_trade"} and preferred != active_direction:
            side_disagreements += 1

    sentinel_rows = scout.get("sentinel_scores") if isinstance(scout.get("sentinel_scores"), list) else []
    sentinel_modes: dict[str, int] = {}
    sentinel_events: dict[str, int] = {}
    sentinel_non_neutral = 0
    for row in sentinel_rows:
        if not isinstance(row, dict):
            continue
        mode = str(row.get("mode") or "shadow")
        sentinel_modes[mode] = sentinel_modes.get(mode, 0) + 1
        event_type = str(row.get("event_type") or "none")
        sentinel_events[event_type] = sentinel_events.get(event_type, 0) + 1
        shadow_multiplier = row.get("shadow_multiplier")
        if isinstance(shadow_multiplier, Number) and abs(float(shadow_multiplier) - 1.0) > 0.0001:
            sentinel_non_neutral += 1

    learned_ranker = forge.get("learned_ranker") if isinstance(forge.get("learned_ranker"), dict) else {}
    ranker_modes = learned_ranker.get("mode_counts") if isinstance(learned_ranker.get("mode_counts"), dict) else {}
    live_rows = council.get("live_board") if isinstance(council.get("live_board"), list) else []
    shadow_rows = council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []
    live_risk_flags = sum(
        len(row.get("council_risk_flags") or [])
        for row in live_rows
        if isinstance(row, dict)
    )
    shadow_risk_flags = sum(
        len(row.get("council_risk_flags") or [])
        for row in shadow_rows
        if isinstance(row, dict)
    )

    gates = [
        {
            "name": "Disagreement P&L",
            "status": "pending",
            "target": "Shadow beats active when they disagree, after costs.",
        },
        {
            "name": "Live Shadow Window",
            "status": "pending",
            "target": "At least 30 trading days, preferably 60.",
        },
        {
            "name": "Backtest Windows",
            "status": "pending",
            "target": "Shadow beats active over 3, 6, and 12 month windows.",
        },
        {
            "name": "Calibration",
            "status": "pending",
            "target": "Brier score improves or stays close while P&L improves.",
        },
        {
            "name": "Risk Shape",
            "status": "pending",
            "target": "Sharpe is no worse and drawdown does not materially increase.",
        },
        {
            "name": "Coverage",
            "status": "pending",
            "target": "Option-chain coverage remains stable and representative.",
        },
    ]

    models = [
        {
            "name": "Side-Aware Scout",
            "mode": "shadow",
            "role": "call / put / no-trade probabilities",
            "status": "collecting_evidence",
            "recommendation": "Keep shadow until disagreement P&L is positive out of sample.",
            "observations": len(side_rows),
            "disagreements": side_disagreements,
            "side_mix": side_mix,
            "model_modes": side_model_modes,
            "promotion_step": "shadow",
        },
        {
            "name": "Sentinel Event Extractor",
            "mode": "shadow" if "active" not in sentinel_modes else "mixed",
            "role": "event extraction and direction-aware risk tags",
            "status": "collecting_evidence",
            "recommendation": "Keep as event intelligence until event tags prove risk-adjusted lift.",
            "observations": len(sentinel_rows),
            "non_neutral_events": sentinel_non_neutral,
            "mode_counts": sentinel_modes,
            "event_type_counts": sentinel_events,
            "promotion_step": "shadow",
        },
        {
            "name": "Payoff Ranker",
            "mode": "active" if "active" in ranker_modes else "shadow",
            "role": "option payoff-aware ranking",
            "status": "production_monitor",
            "recommendation": "Monitor calibration and drift; this is the recovered edge-bearing model.",
            "observations": int(learned_ranker.get("scored_candidates") or 0),
            "mode_counts": ranker_modes,
            "avg_learned_rank_score": learned_ranker.get("avg_learned_rank_score"),
            "promotion_step": "active",
        },
        {
            "name": "Council Risk Intelligence",
            "mode": "observe",
            "role": "correlation, sector exposure, sizing, no-trade discipline",
            "status": "observe_only",
            "recommendation": "Keep warnings visible; promote hard demotions only after shadow P&L improves.",
            "observations": int(council_summary.get("candidate_count") or 0),
            "live_risk_flags": live_risk_flags,
            "shadow_risk_flags": shadow_risk_flags,
            "avg_pairwise_correlation": council_summary.get("avg_pairwise_correlation"),
            "live_sector_counts": council_summary.get("live_sector_counts", {}),
            "promotion_step": "observe",
        },
    ]

    return {
        "decision": "keep_shadow",
        "decision_label": "Keep new ML/AI layers in shadow",
        "promotion_path": ["shadow", "tie_breaker", "small_weight", "limited_active", "active"],
        "policy": {
            "minimum_shadow_trading_days": 30,
            "preferred_shadow_trading_days": 60,
            "minimum_disagreement_trades": 30,
            "minimum_pnl_lift_pct": 0.10,
            "required_windows": ["3_month", "6_month", "12_month"],
            "promotion_rule": "Promote only if shadow beats active when they disagree, after costs, out of sample, without worse drawdown.",
        },
        "gates": gates,
        "models": models,
    }


def build_side_aware_shadow_ledger_entry(payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    scout = diagnostics.get("scout") if isinstance(diagnostics.get("scout"), dict) else {}
    side_rows = scout.get("side_aware_scores") if isinstance(scout.get("side_aware_scores"), list) else []
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    live_board = council.get("live_board") if isinstance(council.get("live_board"), list) else []
    shadow_board = council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []
    forge_candidates = payload.get("forge_candidates") if isinstance(payload.get("forge_candidates"), list) else []
    live_symbols = {str(row.get("symbol") or "").upper() for row in live_board if isinstance(row, dict)}
    shadow_symbols = {str(row.get("symbol") or "").upper() for row in shadow_board if isinstance(row, dict)}
    forge_symbols = {str(row.get("symbol") or "").upper() for row in forge_candidates if isinstance(row, dict)}

    disagreements: list[dict[str, Any]] = []
    side_mix: dict[str, int] = {"call": 0, "put": 0, "no_trade": 0, "unknown": 0}
    mode_counts: dict[str, int] = {}
    for row in side_rows:
        if not isinstance(row, dict):
            continue
        preferred = _shadow_preferred_side(row)
        side_mix[preferred] = side_mix.get(preferred, 0) + 1
        model_mode = str(row.get("model_mode") or "unknown")
        mode_counts[model_mode] = mode_counts.get(model_mode, 0) + 1
        active_direction = str(row.get("active_direction") or "").lower()
        if active_direction not in {"call", "put"}:
            continue
        if preferred == active_direction:
            continue
        symbol = str(row.get("symbol") or "").upper()
        disagreements.append(
            {
                "symbol": symbol,
                "active_direction": active_direction,
                "shadow_preferred_side": preferred,
                "active_scout_score": row.get("active_scout_score"),
                "call_edge": row.get("call_edge"),
                "put_edge": row.get("put_edge"),
                "no_trade": row.get("no_trade"),
                "model_mode": model_mode,
                "was_forge_candidate_symbol": symbol in forge_symbols,
                "was_live_symbol": symbol in live_symbols,
                "was_shadow_symbol": symbol in shadow_symbols,
            }
        )

    generated_at = _normalize_timestamp(payload.get("generated_at_utc")).replace(microsecond=0).isoformat()
    return {
        "run_generated_at_utc": generated_at,
        "regime": payload.get("regime", {}),
        "model_artifacts": payload.get("model_artifacts", {}),
        "summary": {
            "observations": len(side_rows),
            "disagreements": len(disagreements),
            "directional_disagreements": sum(
                1 for row in disagreements
                if row["shadow_preferred_side"] in {"call", "put"}
            ),
            "no_trade_disagreements": sum(
                1 for row in disagreements
                if row["shadow_preferred_side"] == "no_trade"
            ),
            "side_mix": side_mix,
            "model_modes": mode_counts,
        },
        "disagreements": disagreements,
    }


def build_board_recommendation_history_entry(payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = _normalize_timestamp(payload.get("generated_at_utc")).replace(microsecond=0).isoformat()
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    council_summary = council.get("summary") if isinstance(council.get("summary"), dict) else {}
    live_board = council.get("live_board") if isinstance(council.get("live_board"), list) else []
    shadow_board = council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []

    return {
        "run_generated_at_utc": generated_at,
        "regime": payload.get("regime", {}),
        "abstain": bool(council.get("abstain", summary.get("abstain", False))),
        "summary": {
            "universe_size": _coerce_int(summary.get("universe_size")),
            "scout_signal_count": _coerce_int(summary.get("scout_signal_count")),
            "pre_forge_signal_count": _coerce_int(summary.get("pre_forge_signal_count")),
            "forge_candidate_count": _coerce_int(summary.get("forge_candidate_count")),
            "live_count": _coerce_int(council_summary.get("live_count")),
            "shadow_count": _coerce_int(council_summary.get("shadow_count")),
            "abstain_audit": council_summary.get("abstain_audit", {}),
            "live_side_mix": _count_side_mix(live_board, key="option_type"),
            "shadow_side_mix": _count_side_mix(shadow_board, key="option_type"),
        },
        "live_board": _compact_attribution_contract_view(live_board),
        "shadow_board": _compact_attribution_contract_view(shadow_board),
    }


def append_side_aware_shadow_ledger(
    path: str | Path,
    payload: dict[str, Any],
    *,
    max_entries: int = 500,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    entry = build_side_aware_shadow_ledger_entry(payload)
    ledger: dict[str, Any]
    if output.exists():
        try:
            loaded = json.loads(output.read_text(encoding="utf-8"))
            ledger = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            ledger = {}
    else:
        ledger = {}
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    entries.append(entry)
    entries = entries[-max(max_entries, 1):]
    aggregate = {
        "runs": len(entries),
        "observations": sum(_coerce_int(row.get("summary", {}).get("observations")) for row in entries),
        "disagreements": sum(_coerce_int(row.get("summary", {}).get("disagreements")) for row in entries),
        "directional_disagreements": sum(_coerce_int(row.get("summary", {}).get("directional_disagreements")) for row in entries),
        "no_trade_disagreements": sum(_coerce_int(row.get("summary", {}).get("no_trade_disagreements")) for row in entries),
    }
    rendered = {
        "artifact": "side_aware_scout_shadow_ledger",
        "schema_version": 1,
        "updated_at_utc": entry["run_generated_at_utc"],
        "max_entries": max(max_entries, 1),
        "aggregate": aggregate,
        "entries": entries,
    }
    output.write_text(json.dumps(rendered, indent=2), encoding="utf-8")
    return output


def append_board_recommendation_history(
    path: str | Path,
    payload: dict[str, Any],
    *,
    max_entries: int = 500,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    entry = build_board_recommendation_history_entry(payload)
    history: dict[str, Any]
    if output.exists():
        try:
            loaded = json.loads(output.read_text(encoding="utf-8"))
            history = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            history = {}
    else:
        history = {}
    entries = history.get("entries") if isinstance(history.get("entries"), list) else []
    entries.append(entry)
    entries = entries[-max(max_entries, 1):]
    abstain_reason_counts = _sorted_reason_counts(
        [
            {
                "reason": (
                    row.get("summary", {})
                    .get("abstain_audit", {})
                    .get("primary_reason", "unknown")
                )
            }
            for row in entries
            if bool(row.get("abstain"))
        ],
        reason_key="reason",
    )
    aggregate = {
        "runs": len(entries),
        "live_picks_emitted": sum(_coerce_int(row.get("summary", {}).get("live_count")) for row in entries),
        "shadow_picks_emitted": sum(_coerce_int(row.get("summary", {}).get("shadow_count")) for row in entries),
        "abstain_runs": sum(1 for row in entries if bool(row.get("abstain"))),
        "abstain_primary_reasons": abstain_reason_counts,
    }
    rendered = {
        "artifact": "board_recommendation_history",
        "schema_version": 2,
        "updated_at_utc": entry["run_generated_at_utc"],
        "max_entries": max(max_entries, 1),
        "aggregate": aggregate,
        "entries": entries,
    }
    output.write_text(json.dumps(rendered, indent=2), encoding="utf-8")
    return output


def build_forge_rejection_waterfall_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = _normalize_timestamp(payload.get("generated_at_utc"))
    generated_at_utc = generated_at.replace(microsecond=0).isoformat()
    trading_day = generated_at.astimezone(DIAGNOSTIC_TIMEZONE).date().isoformat()

    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    scout = diagnostics.get("scout") if isinstance(diagnostics.get("scout"), dict) else {}
    pre_forge = diagnostics.get("pre_forge") if isinstance(diagnostics.get("pre_forge"), dict) else {}
    forge = diagnostics.get("forge") if isinstance(diagnostics.get("forge"), dict) else {}
    per_symbol = forge.get("per_symbol") if isinstance(forge.get("per_symbol"), list) else []
    scout_rejections = scout.get("rejections") if isinstance(scout.get("rejections"), list) else []
    pre_forge_rejections = pre_forge.get("rejections") if isinstance(pre_forge.get("rejections"), list) else []
    forge_rejections = [
        row for row in per_symbol
        if _coerce_int(row.get("final_candidates")) <= 0
    ]
    passed_symbols = sum(1 for row in per_symbol if _coerce_int(row.get("final_candidates")) > 0)
    signals_considered = _coerce_int(forge.get("waterfall", {}).get("signals_considered"))
    pass_rate = round(passed_symbols / signals_considered, 4) if signals_considered > 0 else None

    council_summary = council.get("summary") if isinstance(council.get("summary"), dict) else {}
    council_notes = council_summary.get("notes") if isinstance(council_summary.get("notes"), list) else []
    abstain_reasons = [
        str(note)
        for note in council_notes
        if council.get("abstain") and "abstain" in str(note).lower()
    ]

    return {
        "artifact": "forge_rejection_waterfall",
        "product": payload.get("product", "Orographic"),
        "generated_at_utc": generated_at_utc,
        "trading_day": trading_day,
        "timezone": "America/Chicago",
        "summary": {
            "universe_size": _coerce_int(summary.get("universe_size")),
            "scout_signal_count": _coerce_int(summary.get("scout_signal_count")),
            "pre_forge_signal_count": _coerce_int(summary.get("pre_forge_signal_count")),
            "forge_candidate_count": _coerce_int(summary.get("forge_candidate_count")),
            "passed_symbol_count": passed_symbols,
            "forge_symbol_pass_rate": pass_rate,
            "live_count": _coerce_int(council_summary.get("live_count")),
            "shadow_count": _coerce_int(council_summary.get("shadow_count")),
            "abstain": bool(summary.get("abstain", council.get("abstain", False))),
            "scout_pre_veto_direction_counts": scout.get("pre_veto_direction_counts", {}),
            "scout_final_direction_counts": scout.get("final_direction_counts", {}),
            "scout_counter_regime_survivors": _coerce_int(scout.get("counter_regime_survivors")),
            "forge_candidate_side_mix": _count_side_mix(
                payload.get("forge_candidates") if isinstance(payload.get("forge_candidates"), list) else [],
                key="option_type",
            ),
            "live_board_side_mix": _count_side_mix(
                council.get("live_board") if isinstance(council.get("live_board"), list) else [],
                key="option_type",
            ),
            "abstain_primary_reason": (
                council_summary.get("abstain_audit", {}) if isinstance(council_summary.get("abstain_audit"), dict) else {}
            ).get("primary_reason"),
        },
        "top_scout_names": [
            {
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "scout_score": row.get("scout_score"),
                "spot": row.get("spot"),
            }
            for row in (payload.get("scout_signals") if isinstance(payload.get("scout_signals"), list) else [])
        ],
        "pre_forge": {
            "selected_symbols": pre_forge.get("selected_symbols", []),
            "settings": pre_forge.get("settings", {}),
            "rejection_counts": _sorted_reason_counts(pre_forge_rejections, reason_key="reason"),
            "rejections": pre_forge_rejections,
        },
        "scout": {
            "settings": scout.get("settings", {}),
            "pre_veto_direction_counts": scout.get("pre_veto_direction_counts", {}),
            "final_direction_counts": scout.get("final_direction_counts", {}),
            "counter_regime_survivors": _coerce_int(scout.get("counter_regime_survivors")),
            "sentinel_scores": scout.get("sentinel_scores", []),
            "side_aware_scores": scout.get("side_aware_scores", []),
            "rejection_counts": _sorted_reason_counts(scout_rejections, reason_key="reason"),
            "rejections": scout_rejections,
        },
        "forge": {
            "waterfall": forge.get("waterfall", {}),
            "learned_ranker": forge.get("learned_ranker", {}),
            "settings": forge.get("settings", {}),
            "rejection_counts": _sorted_reason_counts(forge_rejections, reason_key="rejection_reason"),
            "per_symbol": per_symbol,
        },
        "final_board": {
            "abstain": bool(council.get("abstain", False)),
            "abstain_reasons": abstain_reasons,
            "abstain_audit": council_summary.get("abstain_audit", {}),
            "council_notes": council_notes,
            "live_board": _compact_contract_view(
                council.get("live_board") if isinstance(council.get("live_board"), list) else []
            ),
            "shadow_board": _compact_contract_view(
                council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []
            ),
        },
        "promotion_readiness": payload.get("promotion_readiness") or build_promotion_readiness(payload),
    }


def build_live_shadow_attribution_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = _normalize_timestamp(payload.get("generated_at_utc"))
    generated_at_utc = generated_at.replace(microsecond=0).isoformat()
    trading_day = generated_at.astimezone(DIAGNOSTIC_TIMEZONE).date().isoformat()

    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    council_summary = council.get("summary") if isinstance(council.get("summary"), dict) else {}
    pre_forge = diagnostics.get("pre_forge") if isinstance(diagnostics.get("pre_forge"), dict) else {}
    forge = diagnostics.get("forge") if isinstance(diagnostics.get("forge"), dict) else {}

    scout_signals = payload.get("scout_signals") if isinstance(payload.get("scout_signals"), list) else []
    forge_candidates = payload.get("forge_candidates") if isinstance(payload.get("forge_candidates"), list) else []
    live_board = council.get("live_board") if isinstance(council.get("live_board"), list) else []
    shadow_board = council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []
    friction_gate = forge.get("pre_council_gate") if isinstance(forge.get("pre_council_gate"), dict) else {}
    deduplication = forge.get("deduplication") if isinstance(forge.get("deduplication"), dict) else {}
    pre_forge_rejections = pre_forge.get("rejections") if isinstance(pre_forge.get("rejections"), list) else []
    friction_rejections = friction_gate.get("rejections") if isinstance(friction_gate.get("rejections"), list) else []

    live_contracts = {str(row.get("contract_symbol") or "") for row in live_board if isinstance(row, dict)}
    shadow_contracts = {str(row.get("contract_symbol") or "") for row in shadow_board if isinstance(row, dict)}
    council_holdouts = [
        row
        for row in forge_candidates
        if isinstance(row, dict)
        and str(row.get("contract_symbol") or "")
        and str(row.get("contract_symbol") or "") not in live_contracts
        and str(row.get("contract_symbol") or "") not in shadow_contracts
    ]
    council_holdouts.sort(
        key=lambda row: (
            float(row.get("learned_rank_score") or row.get("forge_score") or 0.0),
            float(row.get("forge_score") or 0.0),
        ),
        reverse=True,
    )

    return {
        "artifact": "live_shadow_attribution",
        "product": payload.get("product", "Orographic"),
        "generated_at_utc": generated_at_utc,
        "trading_day": trading_day,
        "timezone": "America/Chicago",
        "summary": {
            "scout_signal_count": _coerce_int(summary.get("scout_signal_count")),
            "pre_forge_signal_count": _coerce_int(summary.get("pre_forge_signal_count")),
            "forge_candidate_count": _coerce_int(summary.get("forge_candidate_count")),
            "live_count": _coerce_int(council_summary.get("live_count")),
            "shadow_count": _coerce_int(council_summary.get("shadow_count")),
            "abstain": bool(council.get("abstain", False)),
            "live_side_mix": _count_side_mix(live_board, key="option_type"),
            "shadow_side_mix": _count_side_mix(shadow_board, key="option_type"),
            "forge_candidate_side_mix": _count_side_mix(forge_candidates, key="option_type"),
            "friction_veto_count": len(friction_rejections),
            "dedupe_removed_count": _coerce_int(deduplication.get("removed_candidates")),
            "pre_forge_rejection_count": len(pre_forge_rejections),
            "council_holdout_count": len(council_holdouts),
            "live_avg_forge_score": _average_metric(live_board, "forge_score"),
            "shadow_avg_forge_score": _average_metric(shadow_board, "forge_score"),
            "live_avg_edge_after_friction_pct": _average_metric(live_board, "expected_edge_after_friction_pct"),
            "shadow_avg_edge_after_friction_pct": _average_metric(shadow_board, "expected_edge_after_friction_pct"),
            "side_aware_directional_disagreements": _coerce_int(summary.get("scout_side_aware_directional_disagreements")),
            "side_aware_no_trade_disagreements": _coerce_int(summary.get("scout_side_aware_no_trade_disagreements")),
            "shadow_side_veto_rejections": _coerce_int(summary.get("scout_shadow_side_veto_rejections")),
            "abstain_primary_reason": (
                council_summary.get("abstain_audit", {}) if isinstance(council_summary.get("abstain_audit"), dict) else {}
            ).get("primary_reason"),
        },
        "layer_breakdown": {
            "scout": {
                "pre_veto_direction_counts": summary.get("scout_pre_veto_direction_counts", {}),
                "final_direction_counts": summary.get("scout_final_direction_counts", {}),
                "counter_regime_survivors": _coerce_int(summary.get("scout_counter_regime_survivors")),
                "side_aware_directional_disagreements": _coerce_int(summary.get("scout_side_aware_directional_disagreements")),
                "side_aware_no_trade_disagreements": _coerce_int(summary.get("scout_side_aware_no_trade_disagreements")),
                "shadow_side_veto_rejections": _coerce_int(summary.get("scout_shadow_side_veto_rejections")),
            },
            "forge": {
                "waterfall": forge.get("waterfall", {}),
                "pre_council_gate": {
                    "kept": _coerce_int(friction_gate.get("kept")),
                    "dropped": _coerce_int(friction_gate.get("dropped")),
                    "min_expected_edge_after_friction_pct": friction_gate.get("min_expected_edge_after_friction_pct"),
                },
                "deduplication": {
                    "removed_candidates": _coerce_int(deduplication.get("removed_candidates")),
                    "kept_candidates": _coerce_int(deduplication.get("kept_candidates")),
                    "max_structures_per_symbol_side": deduplication.get("max_structures_per_symbol_side"),
                },
            },
            "council": {
                "candidate_count": _coerce_int(council_summary.get("candidate_count")),
                "live_count": _coerce_int(council_summary.get("live_count")),
                "shadow_count": _coerce_int(council_summary.get("shadow_count")),
                "avg_pairwise_correlation": council_summary.get("avg_pairwise_correlation"),
                "abstain_audit": council_summary.get("abstain_audit", {}),
                "notes": council_summary.get("notes", []),
            },
        },
        "top_live_board": _compact_attribution_contract_view(live_board[:3]),
        "top_shadow_board": _compact_attribution_contract_view(shadow_board[:3]),
        "council_holdouts": _compact_attribution_contract_view(council_holdouts[:5]),
        "friction_vetoes": friction_rejections[:5],
        "pre_forge_rejections": pre_forge_rejections[:5],
    }


def write_forge_rejection_waterfall_artifacts(snapshot_path: str, payload: dict[str, Any]) -> list[Path]:
    snapshot = Path(snapshot_path)
    diagnostics_dir = snapshot.parent / "diagnostics"
    artifact = build_forge_rejection_waterfall_artifact(payload)
    trading_day = str(artifact["trading_day"])
    latest_path = diagnostics_dir / "forge_rejection_waterfall_latest.json"
    dated_path = diagnostics_dir / f"forge_rejection_waterfall_{trading_day}.json"

    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(artifact, indent=2)
    latest_path.write_text(rendered, encoding="utf-8")
    dated_path.write_text(rendered, encoding="utf-8")
    return [latest_path, dated_path]


def write_live_shadow_attribution_artifacts(snapshot_path: str, payload: dict[str, Any]) -> list[Path]:
    snapshot = Path(snapshot_path)
    diagnostics_dir = snapshot.parent / "diagnostics"
    artifact = build_live_shadow_attribution_artifact(payload)
    trading_day = str(artifact["trading_day"])
    latest_path = diagnostics_dir / "live_shadow_attribution_latest.json"
    dated_path = diagnostics_dir / f"live_shadow_attribution_{trading_day}.json"

    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(artifact, indent=2)
    latest_path.write_text(rendered, encoding="utf-8")
    dated_path.write_text(rendered, encoding="utf-8")
    return [latest_path, dated_path]


def run_scan(config: PipelineConfig) -> dict[str, Any]:
    log.info("Orographic pipeline started with universe of %d symbols.", len(config.universe))
    try:
        regime, scout_signals, scout_diagnostics = scan_symbols_with_diagnostics(config.universe)
        log.info("Scout signal generation complete. Evaluating candidates...")

        forge_input_signals, pre_forge_diagnostics = select_signals_for_forge(
            scout_signals,
            target_count=max(int(config.forge_intake), 1),
        )
        log.info(
            "Pre-Forge liquidity gate selected %d/%d signals for contract ranking.",
            len(forge_input_signals),
            len(scout_signals),
        )

        forge_candidates, forge_diagnostics = rank_contracts_with_diagnostics(
            forge_input_signals,
            regime,
        )
        log.info("Contract ranking complete. %d candidates found.", len(forge_candidates))

        council = select_board(
            forge_candidates,
            regime,
            live_size=config.live_size,
            shadow_size=config.shadow_size,
        )
        log.info("Council selection complete. Abstain: %s", council.abstain)

        live_avg_score = (
            round(sum(row.forge_score for row in council.live_board) / len(council.live_board), 4)
            if council.live_board
            else 0.0
        )

        payload = {
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "product": "Orographic",
            "regime": regime.to_dict(),
            "scout_signals": [row.to_dict() for row in scout_signals],
            "forge_candidates": [row.to_dict() for row in forge_candidates],
            "council": council.to_dict(),
            "diagnostics": {
                "scout": scout_diagnostics,
                "pre_forge": pre_forge_diagnostics,
                "forge": forge_diagnostics,
            },
            "model_artifacts": _model_artifact_status(),
            "summary": {
                "universe_size": len(config.universe),
                "scout_signal_count": len(scout_signals),
                "scout_pre_veto_direction_counts": scout_diagnostics.get("pre_veto_direction_counts", {}),
                "scout_final_direction_counts": scout_diagnostics.get("final_direction_counts", {}),
                "scout_counter_regime_survivors": scout_diagnostics.get("counter_regime_survivors", 0),
                "scout_side_aware_directional_disagreements": scout_diagnostics.get("side_aware_directional_disagreements", 0),
                "scout_side_aware_no_trade_disagreements": scout_diagnostics.get("side_aware_no_trade_disagreements", 0),
                "scout_shadow_side_veto_rejections": scout_diagnostics.get("shadow_side_veto_rejections", 0),
                "pre_forge_signal_count": len(forge_input_signals),
                "forge_candidate_count": len(forge_candidates),
                "forge_learned_ranker": forge_diagnostics.get("learned_ranker", {}),
                "abstain": council.abstain,
                "live_avg_score": live_avg_score,
                "forge_input_symbols": [row.symbol for row in forge_input_signals],
                "forge_waterfall": forge_diagnostics.get("waterfall", {}),
            },
        }
        payload["promotion_readiness"] = build_promotion_readiness(payload)
        payload["attribution"] = build_live_shadow_attribution_artifact(payload)
        _validate_snapshot_contract(payload)
        return payload
    except Exception as exc:
        log.error("Pipeline crashed: %s", exc, exc_info=True)
        # Return a safe "abstain" payload so Cloudflare still receives a status update
        return {
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "product": "Orographic",
            "error": str(exc),
            "summary": { "abstain": True, "error": True }
        }


def load_universe(universe_file: str | None) -> list[str]:
    if not universe_file:
        return list(DEFAULT_UNIVERSE)
    path = Path(universe_file)
    if not path.exists():
        raise FileNotFoundError(f"Universe file not found: {path}")
    symbols = _read_universe_file(path)
    return symbols or list(DEFAULT_UNIVERSE)


def write_snapshot(path: str, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
