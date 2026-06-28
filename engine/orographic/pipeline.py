from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import logging
from numbers import Number
import os
from pathlib import Path
from typing import Any
import json
from zoneinfo import ZoneInfo

from .council import select_board
from .forge import rank_contracts_with_diagnostics, select_signals_for_forge
from .market_shock import classify_current_market_shock
from .moonshot import select_moonshot_lane
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
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIAGNOSTICS_DIR = REPO_ROOT / "web" / "data" / "diagnostics"


@dataclass
class PipelineConfig:
    universe: list[str]
    live_size: int = 3
    shadow_size: int = 3
    forge_intake: int = 12
    minimum_days_to_expiry: int = 7
    maximum_days_to_expiry: int = 14
    minimum_live_score: float = 0.76
    minimum_put_live_score: float = 0.84
    max_live_extrinsic_ratio: float = 0.90
    moonshot_size: int = 1
    moonshot_threshold: float = 0.68
    moonshot_max_cost_basis: float = 225.0
    enforce_pre_council_friction_gate: bool = False
    market_shock_control_mode: str = "active"
    board_history_path: str | Path | None = Path("web/data/diagnostics/board_recommendation_history.json")

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


def _parse_date(raw: object) -> date | None:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str) and raw.strip():
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            return None
    return None


def _coerce_int(value: object) -> int:
    if isinstance(value, Number):
        return int(value)
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: object) -> float | None:
    if isinstance(value, Number):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _row_policy_score(row: dict[str, Any]) -> float | None:
    for key in ("risk_adjusted_score", "learned_rank_score", "final_candidate_score", "forge_score"):
        value = _coerce_float(row.get(key))
        if value is not None:
            return round(value, 4)
    return None


def _row_sentinel_no_trade_pressure(row: dict[str, Any]) -> float | None:
    relevance = _coerce_float(row.get("sentinel_no_trade_relevance"))
    confidence = _coerce_float(row.get("sentinel_confidence"))
    if relevance is None and confidence is None:
        return None
    return round(max(relevance or 0.0, 0.0) * max(confidence or 0.0, 0.35), 4)


def _row_no_trade_pressure(row: dict[str, Any]) -> float | None:
    candidates = [
        _coerce_float(row.get("prob_no_trade")),
        _coerce_float(row.get("scout_no_trade_prob")),
        _row_sentinel_no_trade_pressure(row),
    ]
    values = [value for value in candidates if value is not None]
    if not values:
        return None
    return round(max(values), 4)


def _recommendation_id(run_generated_at_utc: str, contract_symbol: object, lane: str) -> str:
    contract = str(contract_symbol or "").strip().upper()
    return f"{run_generated_at_utc}|{contract}|{lane}"


def _days_to_expiry(run_generated_at_utc: str, expiry: object) -> int | None:
    expiry_date = _parse_date(expiry)
    if expiry_date is None:
        return None
    return (expiry_date - _normalize_timestamp(run_generated_at_utc).date()).days


def _dollar_spread(bid: object, ask: object) -> float | None:
    if isinstance(bid, Number) and isinstance(ask, Number) and bid > 0 and ask > 0:
        return round(float(ask) - float(bid), 4)
    return None


def _config_int(config: object, name: str, default: int, *, minimum: int = 0) -> int:
    value = getattr(config, name, default)
    if not isinstance(value, Number):
        try:
            value = int(float(str(value)))
        except (TypeError, ValueError):
            value = default
    return max(int(value), minimum)


def _config_bool(config: object, name: str, default: bool) -> bool:
    value = getattr(config, name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, Number):
        return bool(value)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return default


def _config_float(config: object, name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    value = getattr(config, name, default)
    if not isinstance(value, Number):
        try:
            value = float(str(value))
        except (TypeError, ValueError):
            value = default
    parsed = float(value)
    if minimum is not None:
        parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _sorted_reason_counts(rows: list[dict[str, Any]], *, reason_key: str) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get(reason_key) or "unknown").strip() or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _load_prior_live_board_symbols(path: str | Path | None) -> list[str]:
    if not path or not isinstance(path, (str, os.PathLike, Path)):
        return []
    target = Path(path)
    if not target.exists():
        return []
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entries = loaded.get("entries") if isinstance(loaded, dict) else None
    if not isinstance(entries, list) or not entries:
        return []
    latest = entries[-1] if isinstance(entries[-1], dict) else {}
    live_board = latest.get("live_board") if isinstance(latest.get("live_board"), list) else []
    symbols: list[str] = []
    for row in live_board:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol:
            symbols.append(symbol)
    return symbols


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
                "policy_score": _row_policy_score(row),
                "learned_rank_score": row.get("learned_rank_score"),
                "final_candidate_score": row.get("final_candidate_score"),
                "ranker_mode": row.get("ranker_mode"),
                "contract_cost": row.get("contract_cost"),
                "sector": row.get("sector"),
                "risk_adjusted_score": row.get("risk_adjusted_score"),
                "utility_after_friction_score": row.get("utility_after_friction_score"),
                "expected_edge_after_friction_pct": row.get("expected_edge_after_friction_pct"),
                "prob_fill_quality_ok": row.get("prob_fill_quality_ok"),
                "prob_no_trade": row.get("prob_no_trade"),
                "scout_no_trade_prob": row.get("scout_no_trade_prob"),
                "no_trade_pressure": _row_no_trade_pressure(row),
                "sentinel_confidence": row.get("sentinel_confidence"),
                "sentinel_no_trade_relevance": row.get("sentinel_no_trade_relevance"),
                "sentinel_no_trade_pressure": _row_sentinel_no_trade_pressure(row),
                "path_holding_quality_score": row.get("path_holding_quality_score"),
                "path_early_profit_take_prob": row.get("path_early_profit_take_prob"),
                "path_decay_risk": row.get("path_decay_risk"),
                "path_model_mode": row.get("path_model_mode"),
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
                "policy_score": _row_policy_score(row),
                "risk_adjusted_score": row.get("risk_adjusted_score"),
                "final_candidate_score": row.get("final_candidate_score"),
                "utility_after_friction_score": row.get("utility_after_friction_score"),
                "payoff_edge_score": row.get("payoff_edge_score"),
                "expected_edge_after_friction_pct": row.get("expected_edge_after_friction_pct"),
                "prob_fill_quality_ok": row.get("prob_fill_quality_ok"),
                "prob_no_trade": row.get("prob_no_trade"),
                "scout_no_trade_prob": row.get("scout_no_trade_prob"),
                "no_trade_pressure": _row_no_trade_pressure(row),
                "sentinel_confidence": row.get("sentinel_confidence"),
                "sentinel_no_trade_relevance": row.get("sentinel_no_trade_relevance"),
                "sentinel_no_trade_pressure": _row_sentinel_no_trade_pressure(row),
                "path_holding_quality_score": row.get("path_holding_quality_score"),
                "path_early_profit_take_prob": row.get("path_early_profit_take_prob"),
                "path_decay_risk": row.get("path_decay_risk"),
                "path_model_mode": row.get("path_model_mode"),
                "contract_cost": row.get("contract_cost"),
                "council_risk_flags": row.get("council_risk_flags", []),
                "notes": row.get("notes", []),
            }
        )
    return compact


def _prospective_outcome_template() -> dict[str, Any]:
    return {
        "status": "pending",
        "quote_verification": {
            "emission_quote_captured": True,
            "outcome_quotes_captured": False,
        },
        "fixed_exit_marks": {
            "one_hour": None,
            "end_of_day": None,
            "next_day_close": None,
            "friday_close": None,
        },
        "path_rules": {
            "take_profit_40_pct_before_stop_50_pct": None,
            "take_profit_25_pct_before_stop_50_pct": None,
            "max_favorable_excursion_pct": None,
            "max_adverse_excursion_pct": None,
            "first_hit": None,
        },
        "realized_if_traded": {
            "entry_fill": None,
            "exit_fill": None,
            "contracts": None,
            "pnl": None,
            "pnl_pct": None,
        },
    }


def _prospective_pick_row(
    row: dict[str, Any],
    *,
    lane: str,
    lane_reason: str,
    run_generated_at_utc: str,
    regime: dict[str, Any],
    scan_settings: dict[str, Any],
    model_modes: dict[str, Any],
    model_artifacts: dict[str, Any],
    scout_spot: float | None = None,
) -> dict[str, Any]:
    bid = row.get("bid")
    ask = row.get("ask")
    mid = None
    if isinstance(bid, Number) and isinstance(ask, Number) and bid > 0 and ask > 0:
        mid = round((float(bid) + float(ask)) / 2.0, 4)
    underlying_spot = row.get("spot") if isinstance(row.get("spot"), Number) else scout_spot
    dte = _days_to_expiry(run_generated_at_utc, row.get("expiry"))
    recommendation_id = _recommendation_id(run_generated_at_utc, row.get("contract_symbol"), lane)
    return {
        "recommendation_id": recommendation_id,
        "run_generated_at_utc": run_generated_at_utc,
        "lane": lane,
        "lane_reason": lane_reason,
        "symbol": row.get("symbol"),
        "contract_symbol": row.get("contract_symbol"),
        "option_type": row.get("option_type"),
        "expiry": row.get("expiry"),
        "strike": row.get("strike"),
        "days_to_expiry": dte,
        "underlying": {
            "symbol": row.get("symbol"),
            "spot": underlying_spot,
            "quote_captured_at_utc": run_generated_at_utc,
        },
        "emission_quote": {
            "captured_at_utc": run_generated_at_utc,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "last": row.get("last"),
            "spread": _dollar_spread(bid, ask),
            "spread_pct": row.get("spread_pct"),
            "open_interest": row.get("open_interest"),
            "volume": row.get("volume"),
            "contract_cost": row.get("contract_cost"),
            "entry_quote_type": row.get("entry_quote_type"),
            "entry_data_source": row.get("entry_data_source"),
        },
        "scores": {
            "forge_score": row.get("forge_score"),
            "learned_rank_score": row.get("learned_rank_score"),
            "payoff_model_score": row.get("payoff_model_score"),
            "final_candidate_score": row.get("final_candidate_score"),
            "prob_positive_option_pnl": row.get("prob_positive_option_pnl"),
            "prob_no_trade": row.get("prob_no_trade"),
            "prob_fill_quality_ok": row.get("prob_fill_quality_ok"),
            "prob_exceeds_breakeven": row.get("prob_exceeds_breakeven"),
            "expected_option_return_pct_model": row.get("expected_option_return_pct_model"),
            "expected_edge_after_friction_pct": row.get("expected_edge_after_friction_pct"),
            "friction_buffer_pct": row.get("friction_buffer_pct"),
            "path_holding_quality_score": row.get("path_holding_quality_score"),
            "path_early_profit_take_prob": row.get("path_early_profit_take_prob"),
            "path_decay_risk": row.get("path_decay_risk"),
        },
        "risk_features": {
            "delta": row.get("delta"),
            "implied_volatility": row.get("implied_volatility"),
            "iv_rank": row.get("iv_rank"),
            "extrinsic_ratio": row.get("extrinsic_ratio"),
            "moneyness": row.get("moneyness"),
            "premium_pct_of_spot": row.get("premium_pct_of_spot"),
            "breakeven_move_pct": row.get("breakeven_move_pct"),
            "projected_move_pct": row.get("projected_move_pct"),
            "realized_vol_20d": row.get("realized_vol_20d"),
            "atr_pct_14d": row.get("atr_pct_14d"),
            "scout_call_edge_prob": row.get("scout_call_edge_prob"),
            "scout_put_edge_prob": row.get("scout_put_edge_prob"),
            "scout_no_trade_prob": row.get("scout_no_trade_prob"),
            "sentinel_event_type": row.get("sentinel_event_type"),
            "sentinel_source_reliability": row.get("sentinel_source_reliability"),
            "sentinel_novelty": row.get("sentinel_novelty"),
            "sentinel_holding_window_fit": row.get("sentinel_holding_window_fit"),
            "sentinel_holding_window_label": row.get("sentinel_holding_window_label"),
            "sentinel_time_horizon": row.get("sentinel_time_horizon"),
            "sentinel_decay_half_life": row.get("sentinel_decay_half_life"),
            "sentinel_confidence": row.get("sentinel_confidence"),
            "sentinel_call_relevance": row.get("sentinel_call_relevance"),
            "sentinel_put_relevance": row.get("sentinel_put_relevance"),
            "sentinel_no_trade_relevance": row.get("sentinel_no_trade_relevance"),
            "sentinel_spot_effect": row.get("sentinel_spot_effect"),
            "sentinel_iv_effect": row.get("sentinel_iv_effect"),
            "council_risk_flags": row.get("council_risk_flags", []),
            "friction_gate_passed": row.get("friction_gate_passed"),
        },
        "context": {
            "regime": regime,
            "scan_settings": scan_settings,
            "model_modes": model_modes,
            "model_artifacts": model_artifacts,
            "ranker_mode": row.get("ranker_mode"),
            "ranker_artifact_sha256": row.get("ranker_artifact_sha256"),
            "path_model_mode": row.get("path_model_mode"),
            "path_model_artifact_sha256": row.get("path_model_artifact_sha256"),
        },
        "notes": row.get("notes", []),
        "outcomes": _prospective_outcome_template(),
    }


def _prospective_outcome_summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "picks": 0,
        "pending": 0,
        "partial": 0,
        "complete": 0,
        "with_any_mark": 0,
        "with_all_fixed_marks": 0,
        "missing_outcome_quotes": 0,
    }
    fixed_names = ("one_hour", "end_of_day", "next_day_close", "friday_close")
    for entry in entries:
        picks = entry.get("picks") if isinstance(entry, dict) and isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            summary["picks"] += 1
            outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
            status = str(outcomes.get("status") or "pending")
            if status in {"pending", "partial", "complete"}:
                summary[status] += 1
            fixed_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
            marked = [name for name in fixed_names if fixed_marks.get(name) is not None]
            if marked:
                summary["with_any_mark"] += 1
            if len(marked) == len(fixed_names):
                summary["with_all_fixed_marks"] += 1
            quote_verification = outcomes.get("quote_verification") if isinstance(outcomes.get("quote_verification"), dict) else {}
            if marked and not quote_verification.get("outcome_quotes_captured"):
                summary["missing_outcome_quotes"] += 1
    return summary


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
    manifest_path = MODEL_DIR / "artifact_manifest.json"
    manifest_artifacts: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_artifacts = loaded.get("artifacts", {}) if isinstance(loaded, dict) else {}
            manifest_artifacts = raw_artifacts if isinstance(raw_artifacts, dict) else {}
        except json.JSONDecodeError:
            manifest_artifacts = {}
    artifacts = {
        "scout_model": MODEL_DIR / "scout_model.pkl",
        "scout_scaler": MODEL_DIR / "scout_scaler.pkl",
        "scout_side_model": MODEL_DIR / "scout_side_model.pkl",
        "sentinel_model": MODEL_DIR / "sentinel_model.json",
        "payoff_model": MODEL_DIR / "payoff_model.pkl",
        "path_model": MODEL_DIR / "path_model.pkl",
        "scout_model_card": MODEL_DIR / "scout_model_card.json",
        "sentinel_model_card": MODEL_DIR / "sentinel_model_card.json",
        "payoff_model_card": MODEL_DIR / "payoff_model_card.json",
        "path_model_card": MODEL_DIR / "path_model_card.json",
    }
    return {
        name: {
            "present": path.exists(),
            "sha256": _sha256_file(path),
            "required": bool(
                manifest_artifacts.get(name, {}).get("required", True)
                if isinstance(manifest_artifacts.get(name), dict)
                else True
            ),
        }
        for name, path in artifacts.items()
    }


def _normalize_mode(raw: object, *, active_values: set[str], shadow_values: set[str], default: str) -> str:
    value = str(raw or "").strip().lower()
    if value in active_values:
        return "active"
    if value in shadow_values:
        return "shadow"
    return default


def _model_mode_status(artifacts: dict[str, Any] | None = None) -> dict[str, str]:
    artifact_status = artifacts or _model_artifact_status()
    scout_model = artifact_status.get("scout_model", {}) if isinstance(artifact_status, dict) else {}
    scout_scaler = artifact_status.get("scout_scaler", {}) if isinstance(artifact_status, dict) else {}
    side_model = artifact_status.get("scout_side_model", {}) if isinstance(artifact_status, dict) else {}
    payoff_model = artifact_status.get("payoff_model", {}) if isinstance(artifact_status, dict) else {}
    path_model = artifact_status.get("path_model", {}) if isinstance(artifact_status, dict) else {}
    directional_mode = (
        "artifact"
        if bool(scout_model.get("present")) and bool(scout_scaler.get("present"))
        else "heuristic_fallback"
    )
    return {
        "directional_scout": directional_mode,
        "side_aware_scout": _normalize_mode(
            os.getenv("OROGRAPHIC_SIDE_MODEL_MODE", "shadow"),
            active_values={"active", "live"},
            shadow_values={"shadow", "observe", "off"},
            default="shadow",
        ),
        "sentinel": _normalize_mode(
            os.getenv("OROGRAPHIC_SENTINEL_MODE", "shadow"),
            active_values={"active"},
            shadow_values={"shadow", "observe", "off"},
            default="shadow",
        ),
        "payoff_ranker": (
            _normalize_mode(
                os.getenv("OROGRAPHIC_PAYOFF_MODEL_MODE", "active"),
                active_values={"active", "live", "on"},
                shadow_values={"shadow", "observe", "off"},
                default="active",
            )
            if bool(payoff_model.get("present"))
            else "unavailable"
        ),
        "path_model": _normalize_mode(
            os.getenv("OROGRAPHIC_PATH_MODEL_MODE", "shadow"),
            active_values=set(),
            shadow_values={"shadow", "observe", "off"},
            default="shadow",
        ),
        "side_model_source": "artifact" if bool(side_model.get("present")) else "derived",
        "path_model_source": "artifact" if bool(path_model.get("present")) else "heuristic",
    }


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _diagnostic_source_path(payload: dict[str, Any], key: str, default_filename: str) -> Path | None:
    sources = payload.get("diagnostic_sources") if isinstance(payload.get("diagnostic_sources"), dict) else {}
    raw = sources.get(key)
    if isinstance(raw, (str, os.PathLike)):
        return Path(raw)
    if isinstance(raw, Path):
        return raw
    return None


def _new_pick_metric_accumulator() -> dict[str, float | int]:
    return {
        "recommendations": 0,
        "pending": 0,
        "partial": 0,
        "complete": 0,
        "fixed_marks": 0,
        "missing_quote_marks": 0,
        "friday_close_marks": 0,
        "friday_close_pnl_pct_sum": 0.0,
        "realized_trades": 0,
        "realized_pnl_sum": 0.0,
        "realized_pnl_pct_sum": 0.0,
    }


def _finalize_pick_metric_accumulator(accumulator: dict[str, float | int]) -> dict[str, Any]:
    friday_close_marks = _coerce_int(accumulator.get("friday_close_marks"))
    realized_trades = _coerce_int(accumulator.get("realized_trades"))
    fixed_marks = _coerce_int(accumulator.get("fixed_marks"))
    return {
        "recommendations": _coerce_int(accumulator.get("recommendations")),
        "pending": _coerce_int(accumulator.get("pending")),
        "partial": _coerce_int(accumulator.get("partial")),
        "complete": _coerce_int(accumulator.get("complete")),
        "fixed_marks": fixed_marks,
        "missing_quote_marks": _coerce_int(accumulator.get("missing_quote_marks")),
        "quote_coverage_pct": (
            round(
                1.0
                - (_coerce_int(accumulator.get("missing_quote_marks")) / max(fixed_marks, 1)),
                4,
            )
            if fixed_marks > 0
            else None
        ),
        "friday_close_marks": friday_close_marks,
        "friday_close_avg_pnl_pct": (
            round(float(accumulator.get("friday_close_pnl_pct_sum") or 0.0) / friday_close_marks, 4)
            if friday_close_marks > 0
            else None
        ),
        "realized_trades": realized_trades,
        "realized_pnl": round(float(accumulator.get("realized_pnl_sum") or 0.0), 4),
        "realized_avg_pnl_pct": (
            round(float(accumulator.get("realized_pnl_pct_sum") or 0.0) / realized_trades, 4)
            if realized_trades > 0
            else None
        ),
    }


def _summarize_pick_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    lane_totals: dict[str, dict[str, float | int]] = {}
    regime_totals: dict[str, dict[str, float | int]] = {}
    overall = _new_pick_metric_accumulator()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        regime = entry.get("regime") if isinstance(entry.get("regime"), dict) else {}
        regime_mode = str(regime.get("mode") or "unknown")
        picks = entry.get("picks") if isinstance(entry.get("picks"), list) else []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            lane = str(pick.get("lane") or "unknown")
            lane_acc = lane_totals.setdefault(lane, _new_pick_metric_accumulator())
            regime_acc = regime_totals.setdefault(regime_mode, _new_pick_metric_accumulator())
            outcomes = pick.get("outcomes") if isinstance(pick.get("outcomes"), dict) else {}
            status = str(outcomes.get("status") or "pending")
            fixed_exit_marks = outcomes.get("fixed_exit_marks") if isinstance(outcomes.get("fixed_exit_marks"), dict) else {}
            friday_close = fixed_exit_marks.get("friday_close") if isinstance(fixed_exit_marks.get("friday_close"), dict) else None
            quote_verification = outcomes.get("quote_verification") if isinstance(outcomes.get("quote_verification"), dict) else {}
            realized = outcomes.get("realized_if_traded") if isinstance(outcomes.get("realized_if_traded"), dict) else {}
            accumulators = (overall, lane_acc, regime_acc)

            for accumulator in accumulators:
                accumulator["recommendations"] = _coerce_int(accumulator.get("recommendations")) + 1
                if status in {"pending", "partial", "complete"}:
                    accumulator[status] = _coerce_int(accumulator.get(status)) + 1
                if friday_close is not None:
                    accumulator["fixed_marks"] = _coerce_int(accumulator.get("fixed_marks")) + 1
                    accumulator["friday_close_marks"] = _coerce_int(accumulator.get("friday_close_marks")) + 1
                    pnl_pct_from_emission = friday_close.get("pnl_pct_from_emission")
                    if isinstance(pnl_pct_from_emission, Number):
                        accumulator["friday_close_pnl_pct_sum"] = float(
                            accumulator.get("friday_close_pnl_pct_sum") or 0.0
                        ) + float(pnl_pct_from_emission)
                    if not bool(quote_verification.get("outcome_quotes_captured")):
                        accumulator["missing_quote_marks"] = _coerce_int(accumulator.get("missing_quote_marks")) + 1
                pnl = realized.get("pnl")
                if isinstance(pnl, Number):
                    accumulator["realized_trades"] = _coerce_int(accumulator.get("realized_trades")) + 1
                    accumulator["realized_pnl_sum"] = float(accumulator.get("realized_pnl_sum") or 0.0) + float(pnl)
                    pnl_pct = realized.get("pnl_pct")
                    if isinstance(pnl_pct, Number):
                        accumulator["realized_pnl_pct_sum"] = float(
                            accumulator.get("realized_pnl_pct_sum") or 0.0
                        ) + float(pnl_pct)

    return {
        "runs": _coerce_int(ledger.get("aggregate", {}).get("runs")),
        "aggregate": ledger.get("aggregate", {}) if isinstance(ledger.get("aggregate"), dict) else {},
        "outcome_summary": ledger.get("outcome_summary", {}) if isinstance(ledger.get("outcome_summary"), dict) else {},
        "overall": _finalize_pick_metric_accumulator(overall),
        "lanes": {lane: _finalize_pick_metric_accumulator(stats) for lane, stats in lane_totals.items()},
        "regimes": {regime: _finalize_pick_metric_accumulator(stats) for regime, stats in regime_totals.items()},
    }


def _canonical_performance_baseline() -> dict[str, Any]:
    backtest = _load_json_dict(REPO_ROOT / "web" / "data" / "backtest_results.json")
    walk_forward = _load_json_dict(REPO_ROOT / "web" / "data" / "walk_forward_results.json")
    return {
        "backtest": {
            "generated_at": backtest.get("generated_at"),
            "sharpe_ratio": backtest.get("sharpe_ratio"),
            "max_drawdown": backtest.get("max_drawdown"),
            "net_return_pct": backtest.get("net_return_pct"),
            "total_trades": backtest.get("total_trades"),
        },
        "walk_forward": {
            "generated_at": walk_forward.get("generated_at"),
            "sharpe_ratio": walk_forward.get("sharpe_ratio"),
            "max_drawdown": walk_forward.get("max_drawdown"),
            "net_return_pct": walk_forward.get("net_return_pct"),
            "total_trades": walk_forward.get("total_trades"),
        },
    }


def _build_profitability_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload.get("diagnostic_sources"), dict):
        return {}

    prospective = _summarize_pick_ledger(
        _load_json_dict(_diagnostic_source_path(payload, "prospective_ledger", "prospective_pick_ledger.json") or Path())
    )
    moonshot = _summarize_pick_ledger(
        _load_json_dict(_diagnostic_source_path(payload, "moonshot_ledger", "moonshot_prospective_ledger.json") or Path())
    )
    research = _load_json_dict(_diagnostic_source_path(payload, "research_ledger", "research_run_ledger.json") or Path())
    board_history = _load_json_dict(_diagnostic_source_path(payload, "board_history", "board_recommendation_history.json") or Path())
    side_shadow = _load_json_dict(_diagnostic_source_path(payload, "shadow_ledger", "side_aware_scout_shadow_ledger.json") or Path())

    prospective_lanes = prospective.get("lanes", {}) if isinstance(prospective.get("lanes"), dict) else {}
    live_lane = prospective_lanes.get("live", {})
    shadow_lane = prospective_lanes.get("shadow", {})
    holdout_lane = prospective_lanes.get("council_holdout", {})
    veto_lane = prospective_lanes.get("friction_veto", {})
    prospective_outcomes = prospective.get("outcome_summary", {}) if isinstance(prospective.get("outcome_summary"), dict) else {}

    return {
        "shadow_window_runs": _coerce_int(side_shadow.get("aggregate", {}).get("runs")),
        "disagreement_observations": _coerce_int(side_shadow.get("aggregate", {}).get("disagreements")),
        "directional_disagreements": _coerce_int(side_shadow.get("aggregate", {}).get("directional_disagreements")),
        "tracked_recommendation_runs": _coerce_int(prospective.get("runs")),
        "tracked_recommendations": _coerce_int(prospective.get("overall", {}).get("recommendations")),
        "tracked_live_recommendations": _coerce_int(live_lane.get("recommendations")),
        "tracked_shadow_recommendations": _coerce_int(shadow_lane.get("recommendations")),
        "tracked_holdouts": _coerce_int(holdout_lane.get("recommendations")),
        "tracked_friction_vetoes": _coerce_int(veto_lane.get("recommendations")),
        "outcomes_complete": _coerce_int(prospective_outcomes.get("complete")),
        "outcomes_partial": _coerce_int(prospective_outcomes.get("partial")),
        "outcomes_pending": _coerce_int(prospective_outcomes.get("pending")),
        "quote_coverage_pct": prospective.get("overall", {}).get("quote_coverage_pct"),
        "live_realized_trades": _coerce_int(live_lane.get("realized_trades")),
        "live_realized_pnl": live_lane.get("realized_pnl"),
        "live_realized_avg_pnl_pct": live_lane.get("realized_avg_pnl_pct"),
        "live_friday_close_avg_pnl_pct": live_lane.get("friday_close_avg_pnl_pct"),
        "shadow_realized_trades": _coerce_int(shadow_lane.get("realized_trades")),
        "shadow_realized_pnl": shadow_lane.get("realized_pnl"),
        "shadow_realized_avg_pnl_pct": shadow_lane.get("realized_avg_pnl_pct"),
        "shadow_friday_close_avg_pnl_pct": shadow_lane.get("friday_close_avg_pnl_pct"),
        "holdout_friday_close_avg_pnl_pct": holdout_lane.get("friday_close_avg_pnl_pct"),
        "friction_veto_friday_close_avg_pnl_pct": veto_lane.get("friday_close_avg_pnl_pct"),
        "research_runs": _coerce_int(research.get("aggregate", {}).get("runs")),
        "research_abstain_runs": _coerce_int(research.get("aggregate", {}).get("abstain_runs")),
        "board_runs": _coerce_int(board_history.get("aggregate", {}).get("runs")),
        "board_abstain_runs": _coerce_int(board_history.get("aggregate", {}).get("abstain_runs")),
        "moonshot_tracked_candidates": _coerce_int(moonshot.get("overall", {}).get("recommendations")),
        "moonshot_friday_close_avg_pnl_pct": (
            moonshot.get("lanes", {}).get("moonshot_pick", {}).get("friday_close_avg_pnl_pct")
            if isinstance(moonshot.get("lanes"), dict)
            else None
        ),
        "canonical_baseline": _canonical_performance_baseline(),
    }


def _validate_snapshot_contract(payload: dict[str, Any]) -> None:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    council_summary = council.get("summary") if isinstance(council.get("summary"), dict) else {}
    scout_signals = payload.get("scout_signals") if isinstance(payload.get("scout_signals"), list) else []
    forge_candidates = payload.get("forge_candidates") if isinstance(payload.get("forge_candidates"), list) else []
    live_board = council.get("live_board") if isinstance(council.get("live_board"), list) else []
    shadow_board = council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []
    moonshot_lane = payload.get("moonshot_lane") if isinstance(payload.get("moonshot_lane"), dict) else {}
    moonshot_summary = (
        moonshot_lane.get("summary")
        if isinstance(moonshot_lane.get("summary"), dict)
        else {}
    )
    moonshot_picks = moonshot_lane.get("picks") if isinstance(moonshot_lane.get("picks"), list) else []

    expected = {
        "scout_signal_count": len(scout_signals),
        "forge_candidate_count": len(forge_candidates),
        "candidate_count": len(forge_candidates),
        "live_count": len(live_board),
        "shadow_count": len(shadow_board),
        "moonshot_pick_count": len(moonshot_picks),
    }
    observed = {
        "scout_signal_count": _coerce_int(summary.get("scout_signal_count")),
        "forge_candidate_count": _coerce_int(summary.get("forge_candidate_count")),
        "candidate_count": _coerce_int(council_summary.get("candidate_count")),
        "live_count": _coerce_int(council_summary.get("live_count")),
        "shadow_count": _coerce_int(council_summary.get("shadow_count")),
        "moonshot_pick_count": _coerce_int(moonshot_summary.get("pick_count")),
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
    path_model = forge.get("path_model") if isinstance(forge.get("path_model"), dict) else {}
    ranker_modes = learned_ranker.get("mode_counts") if isinstance(learned_ranker.get("mode_counts"), dict) else {}
    path_modes = path_model.get("mode_counts") if isinstance(path_model.get("mode_counts"), dict) else {}
    model_modes = payload.get("model_modes") if isinstance(payload.get("model_modes"), dict) else {}
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
    profitability_evidence = _build_profitability_evidence(payload)
    shadow_window_runs = _coerce_int(profitability_evidence.get("shadow_window_runs"))
    disagreement_observations = _coerce_int(profitability_evidence.get("disagreement_observations"))
    tracked_recommendations = _coerce_int(profitability_evidence.get("tracked_recommendations"))
    quote_coverage_pct = profitability_evidence.get("quote_coverage_pct")
    live_realized_pnl = profitability_evidence.get("live_realized_pnl")
    shadow_realized_pnl = profitability_evidence.get("shadow_realized_pnl")
    tracked_live_recommendations = _coerce_int(profitability_evidence.get("tracked_live_recommendations"))
    tracked_shadow_recommendations = _coerce_int(profitability_evidence.get("tracked_shadow_recommendations"))
    live_friday_close_avg_pnl_pct = profitability_evidence.get("live_friday_close_avg_pnl_pct")
    shadow_friday_close_avg_pnl_pct = profitability_evidence.get("shadow_friday_close_avg_pnl_pct")
    holdout_friday_close_avg_pnl_pct = profitability_evidence.get("holdout_friday_close_avg_pnl_pct")
    friction_veto_friday_close_avg_pnl_pct = profitability_evidence.get("friction_veto_friday_close_avg_pnl_pct")
    payoff_ranker_mode = str(model_modes.get("payoff_ranker") or "").strip().lower()
    if payoff_ranker_mode not in {"active", "shadow", "unavailable"}:
        payoff_ranker_mode = "active" if "active" in ranker_modes else "shadow"

    gates = [
        {
            "name": "Disagreement P&L",
            "status": (
                "collecting_evidence"
                if disagreement_observations > 0 or tracked_recommendations > 0
                else "pending"
            ),
            "target": "Shadow beats active when they disagree, after costs.",
            "progress": (
                f"{disagreement_observations} disagreement observations logged; "
                f"{tracked_shadow_recommendations} tracked shadow recommendations and "
                f"{tracked_live_recommendations} tracked live recommendations."
                if disagreement_observations > 0 or tracked_recommendations > 0
                else "No disagreement outcome evidence logged yet."
            ),
        },
        {
            "name": "Live Shadow Window",
            "status": "pass" if shadow_window_runs >= 30 else ("collecting_evidence" if shadow_window_runs > 0 else "pending"),
            "target": "At least 30 trading days, preferably 60.",
            "progress": (
                f"{shadow_window_runs} shadow runs captured."
                if shadow_window_runs > 0
                else "No shadow run history captured yet."
            ),
        },
        {
            "name": "Backtest Windows",
            "status": "pending",
            "target": "Shadow beats active over 3, 6, and 12 month windows.",
            "progress": "Current pipeline still needs a fresh shadow-vs-active comparison across the canonical windows.",
        },
        {
            "name": "Calibration",
            "status": "collecting_evidence" if tracked_recommendations > 0 else "pending",
            "target": "Brier score improves or stays close while P&L improves.",
            "progress": (
                f"{tracked_recommendations} tracked recommendations; quote coverage "
                f"{quote_coverage_pct:.1%}."
                if tracked_recommendations > 0 and isinstance(quote_coverage_pct, Number)
                else (
                    f"{tracked_recommendations} tracked recommendations awaiting enough marked outcomes."
                    if tracked_recommendations > 0
                    else "No tracked recommendation history yet."
                )
            ),
        },
        {
            "name": "Risk Shape",
            "status": "collecting_evidence",
            "target": "Sharpe is no worse and drawdown does not materially increase.",
            "progress": (
                "Baseline backtest Sharpe "
                f"{profitability_evidence.get('canonical_baseline', {}).get('backtest', {}).get('sharpe_ratio')} / "
                "walk-forward Sharpe "
                f"{profitability_evidence.get('canonical_baseline', {}).get('walk_forward', {}).get('sharpe_ratio')}."
                if profitability_evidence
                else "No baseline risk artifact loaded."
            ),
        },
        {
            "name": "Coverage",
            "status": (
                "pass"
                if isinstance(quote_coverage_pct, Number) and quote_coverage_pct >= 0.99 and tracked_recommendations > 0
                else ("collecting_evidence" if tracked_recommendations > 0 else "pending")
            ),
            "target": "Option-chain coverage remains stable and representative.",
            "progress": (
                f"{tracked_recommendations} tracked recommendations with "
                f"{quote_coverage_pct:.1%} quote coverage."
                if tracked_recommendations > 0 and isinstance(quote_coverage_pct, Number)
                else (
                    f"{tracked_recommendations} tracked recommendations awaiting enough marked quote windows."
                    if tracked_recommendations > 0
                    else "No tracked recommendation coverage yet."
                )
            ),
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
            "shadow_window_runs": shadow_window_runs,
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
            "mode": payoff_ranker_mode or ("active" if "active" in ranker_modes else "shadow"),
            "role": "option payoff-aware ranking",
            "status": "production_monitor",
            "recommendation": "Monitor calibration and drift; this is the recovered edge-bearing model.",
            "observations": int(learned_ranker.get("scored_candidates") or 0),
            "mode_counts": ranker_modes,
            "avg_learned_rank_score": learned_ranker.get("avg_learned_rank_score"),
            "tracked_live_recommendations": tracked_live_recommendations,
            "live_realized_pnl": live_realized_pnl,
            "live_friday_close_avg_pnl_pct": live_friday_close_avg_pnl_pct,
            "promotion_step": "active",
        },
        {
            "name": "Path Quality Model",
            "mode": "shadow",
            "role": "hold-window quality, take-profit odds, and decay risk",
            "status": "collecting_evidence",
            "recommendation": "Keep shadow until path metrics improve disagreement handling without increasing decay-driven losers.",
            "observations": int(path_model.get("scored_candidates") or 0),
            "mode_counts": path_modes,
            "avg_holding_quality_score": path_model.get("avg_holding_quality_score"),
            "avg_early_profit_take_prob": path_model.get("avg_early_profit_take_prob"),
            "avg_decay_risk": path_model.get("avg_decay_risk"),
            "promotion_step": "shadow",
        },
        {
            "name": "Council Risk Intelligence",
            "mode": "observe",
            "role": "correlation, sector exposure, sizing, no-trade discipline",
            "status": "observe_only",
            "recommendation": "Keep warnings visible; compare live picks against holdouts and friction vetoes before promoting harder controls.",
            "observations": int(council_summary.get("candidate_count") or 0),
            "live_risk_flags": live_risk_flags,
            "shadow_risk_flags": shadow_risk_flags,
            "avg_pairwise_correlation": council_summary.get("avg_pairwise_correlation"),
            "live_sector_counts": council_summary.get("live_sector_counts", {}),
            "holdout_friday_close_avg_pnl_pct": holdout_friday_close_avg_pnl_pct,
            "friction_veto_friday_close_avg_pnl_pct": friction_veto_friday_close_avg_pnl_pct,
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
        "profitability_evidence": profitability_evidence,
        "profitability_summary": {
            "live_realized_pnl": live_realized_pnl,
            "shadow_realized_pnl": shadow_realized_pnl,
            "live_friday_close_avg_pnl_pct": live_friday_close_avg_pnl_pct,
            "shadow_friday_close_avg_pnl_pct": shadow_friday_close_avg_pnl_pct,
            "holdout_friday_close_avg_pnl_pct": holdout_friday_close_avg_pnl_pct,
            "tracked_recommendations": tracked_recommendations,
            "quote_coverage_pct": quote_coverage_pct,
        },
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
        "scan_settings": payload.get("scan_settings", {}),
        "model_modes": payload.get("model_modes", {}),
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


def build_research_run_ledger_entry(payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = _normalize_timestamp(payload.get("generated_at_utc")).replace(microsecond=0).isoformat()
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    council_summary = council.get("summary") if isinstance(council.get("summary"), dict) else {}
    attribution = (
        payload.get("attribution")
        if isinstance(payload.get("attribution"), dict)
        else build_live_shadow_attribution_artifact(payload)
    )
    live_board = council.get("live_board") if isinstance(council.get("live_board"), list) else []
    shadow_board = council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []
    vetoed_candidates = attribution.get("friction_vetoes") if isinstance(attribution.get("friction_vetoes"), list) else []
    council_holdouts = attribution.get("council_holdouts") if isinstance(attribution.get("council_holdouts"), list) else []
    pre_forge_rejections = attribution.get("pre_forge_rejections") if isinstance(attribution.get("pre_forge_rejections"), list) else []
    return {
        "run_generated_at_utc": generated_at,
        "regime": payload.get("regime", {}),
        "scan_settings": payload.get("scan_settings", {}),
        "model_modes": payload.get("model_modes", {}),
        "model_artifacts": payload.get("model_artifacts", {}),
        "abstain": bool(council.get("abstain", summary.get("abstain", False))),
        "summary": {
            "universe_size": _coerce_int(summary.get("universe_size")),
            "scout_signal_count": _coerce_int(summary.get("scout_signal_count")),
            "pre_forge_signal_count": _coerce_int(summary.get("pre_forge_signal_count")),
            "forge_candidate_count": _coerce_int(summary.get("forge_candidate_count")),
            "live_count": _coerce_int(council_summary.get("live_count")),
            "shadow_count": _coerce_int(council_summary.get("shadow_count")),
            "abstain_audit": council_summary.get("abstain_audit", {}),
            "friction_veto_count": len(vetoed_candidates),
            "council_holdout_count": len(council_holdouts),
            "pre_forge_rejection_count": len(pre_forge_rejections),
            "live_side_mix": _count_side_mix(live_board, key="option_type"),
            "shadow_side_mix": _count_side_mix(shadow_board, key="option_type"),
        },
        "live_board": _compact_attribution_contract_view(live_board),
        "shadow_board": _compact_attribution_contract_view(shadow_board),
        "vetoed_candidates": vetoed_candidates,
        "council_holdouts": council_holdouts,
        "pre_forge_rejections": pre_forge_rejections,
    }


def build_prospective_pick_ledger_entry(payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = _normalize_timestamp(payload.get("generated_at_utc")).replace(microsecond=0).isoformat()
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    live_board = council.get("live_board") if isinstance(council.get("live_board"), list) else []
    shadow_board = council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []
    forge_candidates = payload.get("forge_candidates") if isinstance(payload.get("forge_candidates"), list) else []
    scout_signals = payload.get("scout_signals") if isinstance(payload.get("scout_signals"), list) else []
    scout_spots = {
        str(row.get("symbol") or "").strip().upper(): float(row["spot"])
        for row in scout_signals
        if isinstance(row, dict)
        and str(row.get("symbol") or "").strip()
        and isinstance(row.get("spot"), Number)
    }
    attribution = (
        payload.get("attribution")
        if isinstance(payload.get("attribution"), dict)
        else build_live_shadow_attribution_artifact(payload)
    )
    friction_vetoes = attribution.get("friction_vetoes") if isinstance(attribution.get("friction_vetoes"), list) else []
    council_holdouts = attribution.get("council_holdouts") if isinstance(attribution.get("council_holdouts"), list) else []

    live_contracts = {str(row.get("contract_symbol") or "") for row in live_board if isinstance(row, dict)}
    shadow_contracts = {str(row.get("contract_symbol") or "") for row in shadow_board if isinstance(row, dict)}
    friction_contracts = {str(row.get("contract_symbol") or "") for row in friction_vetoes if isinstance(row, dict)}
    holdout_contracts = {str(row.get("contract_symbol") or "") for row in council_holdouts if isinstance(row, dict)}
    reasons_by_contract = {
        str(row.get("contract_symbol") or ""): str(
            row.get("reason")
            or row.get("primary_reason")
            or row.get("rejection_reason")
            or ""
        ).strip()
        for row in [*friction_vetoes, *council_holdouts]
        if isinstance(row, dict) and str(row.get("contract_symbol") or "")
    }

    def lane_for(row: dict[str, Any]) -> tuple[str, str]:
        contract = str(row.get("contract_symbol") or "")
        if contract in live_contracts:
            return "live", "selected_live_board"
        if contract in shadow_contracts:
            flags = row.get("council_risk_flags") if isinstance(row.get("council_risk_flags"), list) else []
            suffix = f":{','.join(str(flag) for flag in flags)}" if flags else ""
            return "shadow", f"selected_shadow_board{suffix}"
        if contract in friction_contracts or row.get("friction_gate_passed") is False:
            return "friction_veto", reasons_by_contract.get(contract) or "failed_friction_gate"
        if contract in holdout_contracts:
            return "council_holdout", reasons_by_contract.get(contract) or "not_selected_by_council"
        return "council_holdout", "not_selected_by_council"

    rows = []
    for row in forge_candidates:
        if not isinstance(row, dict) or not str(row.get("contract_symbol") or ""):
            continue
        lane, lane_reason = lane_for(row)
        symbol = str(row.get("symbol") or "").strip().upper()
        rows.append(
            _prospective_pick_row(
                row,
                lane=lane,
                lane_reason=lane_reason,
                run_generated_at_utc=generated_at,
                regime=payload.get("regime", {}),
                scan_settings=payload.get("scan_settings", {}),
                model_modes=payload.get("model_modes", {}),
                model_artifacts=payload.get("model_artifacts", {}),
                scout_spot=scout_spots.get(symbol),
            )
        )

    return {
        "run_generated_at_utc": generated_at,
        "regime": payload.get("regime", {}),
        "scan_settings": payload.get("scan_settings", {}),
        "model_modes": payload.get("model_modes", {}),
        "summary": {
            "pick_rows": len(rows),
            "live": sum(1 for row in rows if row["lane"] == "live"),
            "shadow": sum(1 for row in rows if row["lane"] == "shadow"),
            "council_holdout": sum(1 for row in rows if row["lane"] == "council_holdout"),
            "friction_veto": sum(1 for row in rows if row["lane"] == "friction_veto"),
        },
        "picks": rows,
    }


def build_moonshot_prospective_ledger_entry(payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = _normalize_timestamp(payload.get("generated_at_utc")).replace(microsecond=0).isoformat()
    moonshot_lane = payload.get("moonshot_lane") if isinstance(payload.get("moonshot_lane"), dict) else {}
    picks = moonshot_lane.get("picks") if isinstance(moonshot_lane.get("picks"), list) else []
    shadow = moonshot_lane.get("shadow") if isinstance(moonshot_lane.get("shadow"), list) else []
    scout_signals = payload.get("scout_signals") if isinstance(payload.get("scout_signals"), list) else []
    scout_spots = {
        str(row.get("symbol") or "").strip().upper(): float(row["spot"])
        for row in scout_signals
        if isinstance(row, dict)
        and str(row.get("symbol") or "").strip()
        and isinstance(row.get("spot"), Number)
    }

    rows: list[dict[str, Any]] = []
    for lane, source_rows in (("moonshot_pick", picks), ("moonshot_shadow", shadow)):
        for row in source_rows:
            if not isinstance(row, dict) or not str(row.get("contract_symbol") or ""):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            rendered = _prospective_pick_row(
                row,
                lane=lane,
                lane_reason="selected_moonshot_lane" if lane == "moonshot_pick" else "moonshot_shadow_observation",
                run_generated_at_utc=generated_at,
                regime=payload.get("regime", {}),
                scan_settings=payload.get("scan_settings", {}),
                model_modes=payload.get("model_modes", {}),
                model_artifacts=payload.get("model_artifacts", {}),
                scout_spot=scout_spots.get(symbol),
            )
            moonshot = row.get("moonshot") if isinstance(row.get("moonshot"), dict) else {}
            rendered["moonshot"] = {
                "tail_upside_score": moonshot.get("tail_upside_score"),
                "eligible": bool(moonshot.get("eligible", lane == "moonshot_pick")),
                "reasons": moonshot.get("reasons") if isinstance(moonshot.get("reasons"), list) else [],
                "policy": moonshot_lane.get("policy", {}),
            }
            rows.append(rendered)

    return {
        "run_generated_at_utc": generated_at,
        "regime": payload.get("regime", {}),
        "scan_settings": payload.get("scan_settings", {}),
        "model_modes": payload.get("model_modes", {}),
        "moonshot_policy": moonshot_lane.get("policy", {}),
        "summary": {
            "candidate_rows": len(rows),
            "moonshot_pick": sum(1 for row in rows if row["lane"] == "moonshot_pick"),
            "moonshot_shadow": sum(1 for row in rows if row["lane"] == "moonshot_shadow"),
            "eligible": sum(1 for row in rows if bool(row.get("moonshot", {}).get("eligible"))),
        },
        "picks": rows,
    }


def append_prospective_pick_ledger(
    path: str | Path,
    payload: dict[str, Any],
    *,
    max_entries: int = 500,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    entry = build_prospective_pick_ledger_entry(payload)
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
    entries = [
        row
        for row in entries
        if not (
            isinstance(row, dict)
            and str(row.get("run_generated_at_utc") or "") == str(entry.get("run_generated_at_utc") or "")
        )
    ]
    entries.append(entry)
    entries = entries[-max(max_entries, 1):]
    aggregate = {
        "runs": len(entries),
        "pick_rows": sum(_coerce_int(row.get("summary", {}).get("pick_rows")) for row in entries),
        "live": sum(_coerce_int(row.get("summary", {}).get("live")) for row in entries),
        "shadow": sum(_coerce_int(row.get("summary", {}).get("shadow")) for row in entries),
        "council_holdout": sum(_coerce_int(row.get("summary", {}).get("council_holdout")) for row in entries),
        "friction_veto": sum(_coerce_int(row.get("summary", {}).get("friction_veto")) for row in entries),
    }
    rendered = {
        "artifact": "prospective_pick_ledger",
        "schema_version": 2,
        "updated_at_utc": entry["run_generated_at_utc"],
        "max_entries": max(max_entries, 1),
        "outcome_policy": {
            "required_fixed_exits": ["one_hour", "end_of_day", "next_day_close", "friday_close"],
            "path_rules": ["take_profit_40_pct_before_stop_50_pct", "take_profit_25_pct_before_stop_50_pct"],
            "purpose": "Judge every emitted contract recommendation, whether traded or not.",
        },
        "aggregate": aggregate,
        "outcome_summary": _prospective_outcome_summary(entries),
        "entries": entries,
    }
    output.write_text(json.dumps(rendered, indent=2), encoding="utf-8")
    return output


def append_moonshot_prospective_ledger(
    path: str | Path,
    payload: dict[str, Any],
    *,
    max_entries: int = 500,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    entry = build_moonshot_prospective_ledger_entry(payload)
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
    entries = [
        row
        for row in entries
        if not (
            isinstance(row, dict)
            and str(row.get("run_generated_at_utc") or "") == str(entry.get("run_generated_at_utc") or "")
        )
    ]
    entries.append(entry)
    entries = entries[-max(max_entries, 1):]
    aggregate = {
        "runs": len(entries),
        "candidate_rows": sum(_coerce_int(row.get("summary", {}).get("candidate_rows")) for row in entries),
        "moonshot_pick": sum(_coerce_int(row.get("summary", {}).get("moonshot_pick")) for row in entries),
        "moonshot_shadow": sum(_coerce_int(row.get("summary", {}).get("moonshot_shadow")) for row in entries),
        "eligible": sum(_coerce_int(row.get("summary", {}).get("eligible")) for row in entries),
    }
    rendered = {
        "artifact": "moonshot_prospective_ledger",
        "schema_version": 2,
        "updated_at_utc": entry["run_generated_at_utc"],
        "max_entries": max(max_entries, 1),
        "outcome_policy": {
            "required_fixed_exits": ["one_hour", "end_of_day", "next_day_close", "friday_close"],
            "path_rules": ["take_profit_40_pct_before_stop_50_pct", "take_profit_25_pct_before_stop_50_pct"],
            "purpose": "Judge every dedicated moonshot pick and near-miss shadow candidate.",
        },
        "aggregate": aggregate,
        "outcome_summary": _prospective_outcome_summary(entries),
        "entries": entries,
    }
    output.write_text(json.dumps(rendered, indent=2), encoding="utf-8")
    return output


def append_research_run_ledger(
    path: str | Path,
    payload: dict[str, Any],
    *,
    max_entries: int = 500,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    entry = build_research_run_ledger_entry(payload)
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
        "abstain_runs": sum(1 for row in entries if bool(row.get("abstain"))),
        "live_picks_emitted": sum(_coerce_int(row.get("summary", {}).get("live_count")) for row in entries),
        "shadow_picks_emitted": sum(_coerce_int(row.get("summary", {}).get("shadow_count")) for row in entries),
        "friction_vetoes": sum(_coerce_int(row.get("summary", {}).get("friction_veto_count")) for row in entries),
        "council_holdouts": sum(_coerce_int(row.get("summary", {}).get("council_holdout_count")) for row in entries),
        "pre_forge_rejections": sum(_coerce_int(row.get("summary", {}).get("pre_forge_rejection_count")) for row in entries),
        "abstain_primary_reasons": abstain_reason_counts,
    }
    rendered = {
        "artifact": "research_run_ledger",
        "schema_version": 1,
        "updated_at_utc": entry["run_generated_at_utc"],
        "max_entries": max(max_entries, 1),
        "aggregate": aggregate,
        "entries": entries,
    }
    output.write_text(json.dumps(rendered, indent=2), encoding="utf-8")
    return output


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
    promotion_readiness = payload.get("promotion_readiness") or build_promotion_readiness(payload)

    return {
        "artifact": "forge_rejection_waterfall",
        "product": payload.get("product", "Orographic"),
        "generated_at_utc": generated_at_utc,
        "trading_day": trading_day,
        "timezone": "America/Chicago",
        "scan_settings": payload.get("scan_settings", {}),
        "model_modes": payload.get("model_modes", {}),
        "model_artifacts": payload.get("model_artifacts", {}),
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
            "deduplication": forge.get("deduplication", {}),
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
        "promotion_readiness": promotion_readiness,
        "profitability_evidence": (
            promotion_readiness.get("profitability_evidence", {})
            if isinstance(promotion_readiness, dict)
            else {}
        ),
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
    promotion_readiness = payload.get("promotion_readiness") if isinstance(payload.get("promotion_readiness"), dict) else {}

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
        "scan_settings": payload.get("scan_settings", {}),
        "model_modes": payload.get("model_modes", {}),
        "model_artifacts": payload.get("model_artifacts", {}),
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
            "live_avg_policy_score": _average_metric(live_board, "risk_adjusted_score"),
            "shadow_avg_policy_score": _average_metric(shadow_board, "risk_adjusted_score"),
            "live_avg_edge_after_friction_pct": _average_metric(live_board, "expected_edge_after_friction_pct"),
            "shadow_avg_edge_after_friction_pct": _average_metric(shadow_board, "expected_edge_after_friction_pct"),
            "live_avg_fill_quality_ok": _average_metric(live_board, "prob_fill_quality_ok"),
            "shadow_avg_fill_quality_ok": _average_metric(shadow_board, "prob_fill_quality_ok"),
            "live_avg_no_trade_prob": _average_metric(live_board, "prob_no_trade"),
            "shadow_avg_no_trade_prob": _average_metric(shadow_board, "prob_no_trade"),
            "live_avg_path_holding_quality_score": _average_metric(live_board, "path_holding_quality_score"),
            "shadow_avg_path_holding_quality_score": _average_metric(shadow_board, "path_holding_quality_score"),
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
                "path_model": forge.get("path_model", {}),
                "pre_council_gate": {
                    "kept": _coerce_int(friction_gate.get("kept")),
                    "dropped": _coerce_int(friction_gate.get("dropped")),
                    "min_expected_edge_after_friction_pct": friction_gate.get("min_expected_edge_after_friction_pct"),
                },
                "deduplication": {
                    "removed_candidates": _coerce_int(deduplication.get("removed_candidates")),
                    "kept_candidates": _coerce_int(deduplication.get("kept_candidates")),
                    "max_structures_per_symbol_side": deduplication.get("max_structures_per_symbol_side"),
                    "min_moneyness_gap": deduplication.get("min_moneyness_gap"),
                    "max_structures_per_symbol": deduplication.get("max_structures_per_symbol"),
                    "strong_ticker_moneyness_gap": deduplication.get("strong_ticker_moneyness_gap"),
                    "strong_ticker_delta_gap": deduplication.get("strong_ticker_delta_gap"),
                    "strong_ticker_min_score": deduplication.get("strong_ticker_min_score"),
                    "strong_ticker_min_edge_after_friction_pct": deduplication.get(
                        "strong_ticker_min_edge_after_friction_pct"
                    ),
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
        "profitability_evidence": promotion_readiness.get("profitability_evidence", {}),
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
        live_size = _config_int(config, "live_size", 3, minimum=1)
        shadow_size = _config_int(config, "shadow_size", 3, minimum=1)
        forge_intake = _config_int(config, "forge_intake", 12, minimum=1)
        minimum_days_to_expiry = _config_int(config, "minimum_days_to_expiry", 7, minimum=0)
        maximum_days_to_expiry = _config_int(config, "maximum_days_to_expiry", 14, minimum=0)
        minimum_live_score = _config_float(config, "minimum_live_score", 0.76, minimum=0.0, maximum=1.0)
        minimum_put_live_score = _config_float(config, "minimum_put_live_score", 0.84, minimum=0.0, maximum=1.0)
        max_live_extrinsic_ratio = _config_float(config, "max_live_extrinsic_ratio", 0.90, minimum=0.0, maximum=1.0)
        moonshot_size = _config_int(config, "moonshot_size", 1, minimum=0)
        moonshot_threshold = _config_float(config, "moonshot_threshold", 0.68, minimum=0.0, maximum=1.0)
        moonshot_max_cost_basis = _config_float(config, "moonshot_max_cost_basis", 225.0, minimum=0.0)
        enforce_pre_council_friction_gate = _config_bool(config, "enforce_pre_council_friction_gate", False)
        model_artifacts = _model_artifact_status()
        model_modes = _model_mode_status(model_artifacts)
        regime, scout_signals, scout_diagnostics = scan_symbols_with_diagnostics(config.universe)
        market_shock = classify_current_market_shock(regime)
        market_shock_control_mode = str(
            getattr(config, "market_shock_control_mode", os.getenv("OROGRAPHIC_MARKET_SHOCK_CONTROL_MODE", "active"))
            or "active"
        ).strip().lower()
        if market_shock_control_mode not in {"active", "shadow", "off"}:
            market_shock_control_mode = "active"
        council_market_shock = market_shock if market_shock_control_mode == "active" else None
        log.info("Scout signal generation complete. Evaluating candidates...")

        forge_input_signals, pre_forge_diagnostics = select_signals_for_forge(
            scout_signals,
            target_count=forge_intake,
            minimum_days_to_expiry=minimum_days_to_expiry,
            maximum_days_to_expiry=maximum_days_to_expiry,
        )
        log.info(
            "Pre-Forge liquidity gate selected %d/%d signals for contract ranking.",
            len(forge_input_signals),
            len(scout_signals),
        )
        prior_live_board_symbols = _load_prior_live_board_symbols(
            getattr(config, "board_history_path", None),
        )

        forge_candidates, forge_diagnostics = rank_contracts_with_diagnostics(
            forge_input_signals,
            regime,
            minimum_days_to_expiry=minimum_days_to_expiry,
            maximum_days_to_expiry=maximum_days_to_expiry,
            enforce_pre_council_friction_gate=enforce_pre_council_friction_gate,
            prior_live_board_symbols=prior_live_board_symbols,
        )
        log.info("Contract ranking complete. %d candidates found.", len(forge_candidates))

        council = select_board(
            forge_candidates,
            regime,
            live_size=live_size,
            shadow_size=shadow_size,
            minimum_live_score=minimum_live_score,
            minimum_put_live_score=minimum_put_live_score,
            max_live_extrinsic_ratio=max_live_extrinsic_ratio,
            prior_live_board_symbols=prior_live_board_symbols,
            market_shock=council_market_shock,
        )
        log.info("Council selection complete. Abstain: %s", council.abstain)
        moonshot_lane = select_moonshot_lane(
            forge_candidates,
            regime,
            slot_count=moonshot_size,
            threshold=moonshot_threshold,
            max_cost_basis=moonshot_max_cost_basis,
        )
        log.info(
            "Moonshot lane selected %d/%d eligible candidates.",
            moonshot_lane["summary"]["pick_count"],
            moonshot_lane["summary"]["eligible_count"],
        )

        live_avg_score = (
            round(sum(row.forge_score for row in council.live_board) / len(council.live_board), 4)
            if council.live_board
            else 0.0
        )

        payload = {
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "product": "Orographic",
            "scan_settings": {
                "live_size": live_size,
                "shadow_size": shadow_size,
                "forge_intake": forge_intake,
                "universe_size": len(config.universe),
                "minimum_days_to_expiry": minimum_days_to_expiry,
                "maximum_days_to_expiry": maximum_days_to_expiry,
                "minimum_live_score": minimum_live_score,
                "minimum_put_live_score": minimum_put_live_score,
                "max_live_extrinsic_ratio": max_live_extrinsic_ratio,
                "moonshot_size": moonshot_size,
                "moonshot_threshold": moonshot_threshold,
                "moonshot_max_cost_basis": moonshot_max_cost_basis,
                "enforce_pre_council_friction_gate": enforce_pre_council_friction_gate,
                "market_shock_control_mode": market_shock_control_mode,
            },
            "model_modes": model_modes,
            "regime": regime.to_dict(),
            "market_shock": market_shock.to_dict(),
            "scout_signals": [row.to_dict() for row in scout_signals],
            "forge_candidates": [row.to_dict() for row in forge_candidates],
            "council": council.to_dict(),
            "moonshot_lane": moonshot_lane,
            "diagnostics": {
                "scout": scout_diagnostics,
                "pre_forge": pre_forge_diagnostics,
                "forge": forge_diagnostics,
                "market_shock": {
                    "mode": market_shock_control_mode,
                    "applied": market_shock_control_mode == "active",
                    "policy": market_shock.to_dict(),
                },
            },
            "model_artifacts": model_artifacts,
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
                "moonshot_pick_count": moonshot_lane["summary"]["pick_count"],
                "moonshot_eligible_count": moonshot_lane["summary"]["eligible_count"],
                "forge_learned_ranker": forge_diagnostics.get("learned_ranker", {}),
                "prior_live_board_symbols": prior_live_board_symbols,
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
