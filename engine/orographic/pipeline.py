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
from .scout import scan_symbols


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
                "contract_cost": row.get("contract_cost"),
                "is_spread": bool(row.get("is_spread")),
            }
        )
    return compact


def build_forge_rejection_waterfall_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    generated_at = _normalize_timestamp(payload.get("generated_at_utc"))
    generated_at_utc = generated_at.replace(microsecond=0).isoformat()
    trading_day = generated_at.astimezone(DIAGNOSTIC_TIMEZONE).date().isoformat()

    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    council = payload.get("council") if isinstance(payload.get("council"), dict) else {}
    pre_forge = diagnostics.get("pre_forge") if isinstance(diagnostics.get("pre_forge"), dict) else {}
    forge = diagnostics.get("forge") if isinstance(diagnostics.get("forge"), dict) else {}
    per_symbol = forge.get("per_symbol") if isinstance(forge.get("per_symbol"), list) else []
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
        "forge": {
            "waterfall": forge.get("waterfall", {}),
            "settings": forge.get("settings", {}),
            "rejection_counts": _sorted_reason_counts(forge_rejections, reason_key="rejection_reason"),
            "per_symbol": per_symbol,
        },
        "final_board": {
            "abstain": bool(council.get("abstain", False)),
            "abstain_reasons": abstain_reasons,
            "council_notes": council_notes,
            "live_board": _compact_contract_view(
                council.get("live_board") if isinstance(council.get("live_board"), list) else []
            ),
            "shadow_board": _compact_contract_view(
                council.get("shadow_board") if isinstance(council.get("shadow_board"), list) else []
            ),
        },
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


def run_scan(config: PipelineConfig) -> dict[str, Any]:
    log.info("Orographic pipeline started with universe of %d symbols.", len(config.universe))
    try:
        regime, scout_signals = scan_symbols(config.universe)
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

        return {
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "product": "Orographic",
            "regime": regime.to_dict(),
            "scout_signals": [row.to_dict() for row in scout_signals[:8]],
            "forge_candidates": [row.to_dict() for row in forge_candidates[:10]],
            "council": council.to_dict(),
            "diagnostics": {
                "pre_forge": pre_forge_diagnostics,
                "forge": forge_diagnostics,
            },
            "summary": {
                "universe_size": len(config.universe),
                "scout_signal_count": len(scout_signals),
                "pre_forge_signal_count": len(forge_input_signals),
                "forge_candidate_count": len(forge_candidates),
                "abstain": council.abstain,
                "live_avg_score": live_avg_score,
                "forge_input_symbols": [row.symbol for row in forge_input_signals],
                "forge_waterfall": forge_diagnostics.get("waterfall", {}),
            },
        }
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
