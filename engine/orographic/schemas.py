from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MarketRegime:
    mode: str
    bias: float
    source_symbol: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScoutSignal:
    symbol: str
    direction: str
    spot: float
    momentum_5d: float
    momentum_20d: float
    rsi_14: float
    realized_vol_20d: float
    atr_pct_14d: float
    technical_score: float
    empirical_score: float
    scout_score: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContractCandidate:
    symbol: str
    contract_symbol: str
    option_type: str
    expiry: str
    strike: float
    bid: float
    ask: float
    last: float
    premium: float
    contract_cost: float
    spread_pct: float
    open_interest: int
    volume: int
    implied_volatility: float
    delta: float | None
    moneyness: float
    projected_move_pct: float
    breakeven_move_pct: float
    expected_return_pct: float
    extrinsic_ratio: float
    scout_score: float
    forge_score: float
    short_strike: float | None = None
    short_ask: float | None = None
    short_bid: float | None = None
    is_spread: bool = False
    spread_cost: float | None = None
    allocation_weight: float = 1.0
    iv_rank: float = 0.5          # IV Rank percentile [0, 1]; 0=IV low, 1=IV high-cycle
    entry_data_source: str = "real_chain"
    entry_quote_type: str = "ask"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CouncilResult:
    live_board: list[ContractCandidate]
    shadow_board: list[ContractCandidate]
    abstain: bool
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "live_board": [row.to_dict() for row in self.live_board],
            "shadow_board": [row.to_dict() for row in self.shadow_board],
            "abstain": self.abstain,
            "summary": self.summary,
        }
