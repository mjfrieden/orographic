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
    call_edge_prob: float | None = None
    put_edge_prob: float | None = None
    no_trade_prob: float | None = None
    scout_model_mode: str = "directional"
    sentinel_event: dict[str, Any] = field(default_factory=dict)
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
    scout_call_edge_prob: float | None = None
    scout_put_edge_prob: float | None = None
    scout_no_trade_prob: float | None = None
    short_strike: float | None = None
    short_ask: float | None = None
    short_bid: float | None = None
    is_spread: bool = False
    spread_cost: float | None = None
    allocation_weight: float = 1.0
    iv_rank: float = 0.5          # IV Rank percentile [0, 1]; 0=IV low, 1=IV high-cycle
    surface_atm_iv: float | None = None
    surface_skew_slope: float | None = None
    surface_curvature: float | None = None
    surface_put_call_wing_skew: float | None = None
    surface_term_slope_30d: float | None = None
    surface_fit_rmse: float | None = None
    surface_observation_count: int | None = None
    iv_relative_to_atm: float | None = None
    iv_minus_realized_vol: float | None = None
    quote_mid: float | None = None
    quote_spread_dollars: float | None = None
    chain_snapshot_at_utc: str | None = None
    last_trade_age_seconds: float | None = None
    entry_data_source: str = "real_chain"
    entry_quote_type: str = "ask"
    realized_vol_20d: float | None = None
    atr_pct_14d: float | None = None
    premium_pct_of_spot: float | None = None
    vrp_gap: float | None = None
    pre_payoff_forge_score: float | None = None
    directional_edge: float | None = None
    liquidity_score: float | None = None
    regime_alignment_score: float | None = None
    prob_positive_option_pnl: float | None = None
    payoff_edge_score: float | None = None
    expected_option_return_pct_model: float | None = None
    expected_option_return_pct_rank: float | None = None
    prob_exceeds_breakeven: float | None = None
    breakeven_edge_score: float | None = None
    max_favorable_excursion_before_expiry: float | None = None
    adverse_excursion_risk: float | None = None
    friction_buffer_pct: float | None = None
    expected_edge_after_friction_pct: float | None = None
    friction_gate_passed: bool | None = None
    utility_after_friction_score: float | None = None
    stability_adjustment: float | None = None
    turnover_risk_penalty: float | None = None
    prior_live_board_symbol: bool | None = None
    payoff_model_score: float | None = None
    payoff_shadow_prob_positive: float | None = None
    payoff_shadow_rank: float | None = None
    payoff_shadow_probability_delta: float | None = None
    payoff_shadow_rank_delta: float | None = None
    payoff_shadow_disagreement: bool | None = None
    payoff_shadow_mode: str | None = None
    payoff_shadow_artifact_sha256: str | None = None
    payoff_shadow_return_q10: float | None = None
    payoff_shadow_return_q50: float | None = None
    payoff_shadow_return_q90: float | None = None
    payoff_shadow_prob_fill_quality: float | None = None
    payoff_shadow_prob_target_before_stop: float | None = None
    payoff_shadow_conservative_utility: float | None = None
    final_candidate_score: float | None = None
    learned_rank_score: float | None = None
    ranker_mode: str = "heuristic"
    ranker_artifact_sha256: str | None = None
    call_selector_model_score: float | None = None
    call_selector_contract_score: float | None = None
    call_contract_selector_score: float | None = None
    call_contract_selector_mode: str | None = None
    risk_adjusted_score: float | None = None
    sector: str | None = None
    suggested_allocation_pct: float | None = None
    sentinel_holding_window_fit: float | None = None
    sentinel_holding_window_label: str | None = None
    sentinel_event_type: str | None = None
    sentinel_decay_half_life: str | None = None
    sentinel_time_horizon: str | None = None
    sentinel_confidence: float | None = None
    sentinel_source_reliability: str | None = None
    sentinel_novelty: str | None = None
    sentinel_call_relevance: float | None = None
    sentinel_put_relevance: float | None = None
    sentinel_no_trade_relevance: float | None = None
    sentinel_spot_effect: float | None = None
    sentinel_iv_effect: float | None = None
    prob_no_trade: float | None = None
    no_trade_score: float | None = None
    prob_fill_quality_ok: float | None = None
    fill_quality_score: float | None = None
    path_early_profit_take_prob: float | None = None
    path_expected_mfe_pct: float | None = None
    path_decay_risk: float | None = None
    path_holding_quality_score: float | None = None
    path_model_mode: str | None = None
    path_model_artifact_sha256: str | None = None
    path_hazard_target_probability: float | None = None
    path_hazard_stop_probability: float | None = None
    path_hazard_expiry_probability: float | None = None
    path_exit_shadow_action: str | None = None
    path_hazard_artifact_sha256: str | None = None
    council_risk_flags: list[str] = field(default_factory=list)
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
