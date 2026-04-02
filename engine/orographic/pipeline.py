from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from .council import select_board
from .forge import rank_contracts
from .scout import scan_symbols


DEFAULT_UNIVERSE = [
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


def run_scan(config: PipelineConfig) -> dict[str, Any]:
    regime, scout_signals = scan_symbols(config.universe)
    forge_candidates = rank_contracts(scout_signals[: min(len(scout_signals), 6)], regime)
    council = select_board(
        forge_candidates,
        regime,
        live_size=config.live_size,
        shadow_size=config.shadow_size,
    )

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
        "summary": {
            "universe_size": len(config.universe),
            "scout_signal_count": len(scout_signals),
            "forge_candidate_count": len(forge_candidates),
            "abstain": council.abstain,
            "live_avg_score": live_avg_score,
        },
    }


def load_universe(universe_file: str | None) -> list[str]:
    if not universe_file:
        return list(DEFAULT_UNIVERSE)
    path = Path(universe_file)
    if not path.exists():
        raise FileNotFoundError(f"Universe file not found: {path}")
    symbols: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip().upper()
        if cleaned and not cleaned.startswith("#"):
            symbols.append(cleaned)
    return symbols or list(DEFAULT_UNIVERSE)


def write_snapshot(path: str, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

