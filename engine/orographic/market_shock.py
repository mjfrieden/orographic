from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from numbers import Number
from typing import Any

import pandas as pd

from .event_features import (
    GLOBAL_EVENT_SYMBOL,
    EventFeatureSnapshot,
    latest_event_feature_snapshot,
    load_event_feature_frame,
)
from .market_data import CrossAssetSnapshot, cross_asset_snapshot, history
from .schemas import MarketRegime


@dataclass(frozen=True)
class MarketShockInput:
    spy_bias_20d: float = 0.0
    spy_return_5d: float = 0.0
    qqq_return_5d: float = 0.0
    smh_return_5d: float = 0.0
    vix_level: float = 0.0
    vix_change_5d: float = 0.0
    macro_shock_score: float = 0.0
    geopolitical_risk_score: float = 0.0
    commodity_risk_score: float = 0.0
    risk_on_score: float = 0.0
    risk_off_score: float = 0.0
    event_feature_date: str | None = None
    event_dataset_tags: str = ""


@dataclass(frozen=True)
class MarketShockRegime:
    label: str
    severity: float
    stance: str
    global_abstain: bool
    live_score_buffer: float = 0.0
    put_score_buffer: float = 0.0
    max_extrinsic_ratio: float | None = None
    preferred_sides: list[str] = field(default_factory=list)
    blocked_sides: list[str] = field(default_factory=list)
    drivers: list[str] = field(default_factory=list)
    strategy_notes: list[str] = field(default_factory=list)
    source: str = "cross_asset_event_overlay"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


NEUTRAL_MARKET_SHOCK = MarketShockRegime(
    label="normal_crosscurrents",
    severity=0.0,
    stance="allow",
    global_abstain=False,
    strategy_notes=["Use standard Council score, extrinsic, and diversification gates."],
)


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(float(value), upper))


def _safe_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, Number):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _return_5d(symbol: str) -> float:
    frame = history(symbol, period="1mo")
    close = pd.to_numeric(frame.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
    if len(close) < 6:
        return 0.0
    return float(close.iloc[-1] / close.iloc[-6] - 1.0)


def _input_from_event_snapshot(
    *,
    cross_asset: CrossAssetSnapshot,
    event_snapshot: EventFeatureSnapshot | None,
) -> MarketShockInput:
    values = event_snapshot.values if event_snapshot else {}
    return MarketShockInput(
        spy_bias_20d=float(cross_asset.spy_bias),
        spy_return_5d=_return_5d("SPY"),
        qqq_return_5d=_return_5d("QQQ"),
        smh_return_5d=_return_5d("SMH"),
        vix_level=float(cross_asset.vix_level),
        vix_change_5d=float(cross_asset.vix_change_5d),
        macro_shock_score=_safe_float(values.get("mirai_macro_shock_score")),
        geopolitical_risk_score=_safe_float(values.get("mirai_geopolitical_risk_score")),
        commodity_risk_score=_safe_float(values.get("mirai_commodity_risk_score")),
        risk_on_score=_safe_float(values.get("mirai_risk_on_score")),
        risk_off_score=_safe_float(values.get("mirai_risk_off_score")),
        event_feature_date=event_snapshot.as_of.isoformat() if event_snapshot and event_snapshot.as_of else None,
        event_dataset_tags=event_snapshot.dataset_tags if event_snapshot else "",
    )


def classify_market_shock(features: MarketShockInput, regime: MarketRegime | None = None) -> MarketShockRegime:
    drivers: list[str] = []
    regime_mode = str(getattr(regime, "mode", "") or "").lower()

    vix_stress = 0.0
    if features.vix_level >= 30.0:
        vix_stress += 0.7
        drivers.append("vix_above_30")
    elif features.vix_level >= 24.0:
        vix_stress += 0.45
        drivers.append("vix_above_24")
    elif features.vix_level >= 20.0:
        vix_stress += 0.25
        drivers.append("vix_above_20")
    if features.vix_change_5d >= 0.35:
        vix_stress += 0.45
        drivers.append("vix_5d_spike")
    elif features.vix_change_5d >= 0.20:
        vix_stress += 0.25
        drivers.append("vix_5d_rising")

    drawdown_stress = 0.0
    if features.spy_return_5d <= -0.035:
        drawdown_stress += 0.35
        drivers.append("spy_5d_selloff")
    if features.spy_bias_20d <= -0.06:
        drawdown_stress += 0.25
        drivers.append("spy_20d_drawdown")

    tech_unwind = (
        features.qqq_return_5d <= -0.035
        and features.smh_return_5d <= -0.045
        and features.qqq_return_5d < features.spy_return_5d - 0.01
    )
    if tech_unwind:
        drivers.append("ai_semiconductor_unwind")

    event_risk = max(
        features.macro_shock_score,
        features.geopolitical_risk_score,
        features.commodity_risk_score,
        features.risk_off_score,
    )
    if features.macro_shock_score >= 0.5:
        drivers.append("macro_shock_headlines")
    if features.geopolitical_risk_score >= 0.5:
        drivers.append("geopolitical_risk_headlines")
    if features.commodity_risk_score >= 0.5:
        drivers.append("commodity_supply_headlines")
    if features.risk_off_score > features.risk_on_score and features.risk_off_score >= 0.5:
        drivers.append("headline_risk_off")

    severity = _clip(vix_stress + drawdown_stress + 0.25 * event_risk + (0.25 if tech_unwind else 0.0))

    if regime_mode == "extreme_vol" or severity >= 0.82:
        return MarketShockRegime(
            label="extreme_vol_deleveraging",
            severity=round(max(severity, 0.82), 4),
            stance="abstain",
            global_abstain=True,
            live_score_buffer=0.12,
            put_score_buffer=0.12,
            max_extrinsic_ratio=0.72,
            preferred_sides=[],
            blocked_sides=["call", "put"],
            drivers=drivers or ["extreme_vol_regime"],
            strategy_notes=[
                "Stand down from short-dated single-name options until volatility shock cools.",
                "Collect shadow outcomes for retraining; do not promote fresh live picks.",
            ],
        )

    if tech_unwind:
        return MarketShockRegime(
            label="ai_tech_unwind",
            severity=round(max(severity, 0.55), 4),
            stance="tighten",
            global_abstain=False,
            live_score_buffer=0.08,
            put_score_buffer=0.04,
            max_extrinsic_ratio=0.78,
            preferred_sides=["put", "observe"],
            blocked_sides=[],
            drivers=drivers,
            strategy_notes=[
                "Require stronger evidence for calls in mega-cap tech and semiconductors.",
                "Prefer low-extrinsic hedged or put-side setups only when payoff calibration is strong.",
            ],
        )

    if event_risk >= 0.7 and features.risk_off_score >= features.risk_on_score:
        return MarketShockRegime(
            label="geopolitical_macro_risk_off",
            severity=round(max(severity, 0.62), 4),
            stance="hedged_only",
            global_abstain=False,
            live_score_buffer=0.07,
            put_score_buffer=0.03,
            max_extrinsic_ratio=0.80,
            preferred_sides=["put", "observe"],
            drivers=drivers,
            strategy_notes=[
                "Avoid naked call chasing while headlines dominate index direction.",
                "Allow only high-confidence, low-extrinsic picks; keep rejected candidates in shadow.",
            ],
        )

    if features.risk_on_score >= 0.7 and features.spy_bias_20d > 0.03 and features.vix_level < 20.0:
        return MarketShockRegime(
            label="melt_up_fomo",
            severity=round(max(0.35, features.risk_on_score * 0.5), 4),
            stance="tighten",
            global_abstain=False,
            live_score_buffer=0.03,
            put_score_buffer=0.05,
            max_extrinsic_ratio=0.84,
            preferred_sides=["call", "observe"],
            drivers=drivers or ["headline_risk_on"],
            strategy_notes=[
                "Risk-on is favorable, but weekly calls can be overpriced during crowding.",
                "Keep extrinsic and path-decay gates tight even in good markets.",
            ],
        )

    if regime_mode == "risk_off" or severity >= 0.45:
        return MarketShockRegime(
            label="orderly_risk_off",
            severity=round(max(severity, 0.45), 4),
            stance="tighten",
            global_abstain=False,
            live_score_buffer=0.05,
            put_score_buffer=0.02,
            max_extrinsic_ratio=0.84,
            preferred_sides=["put", "observe"],
            drivers=drivers or ["risk_off_regime"],
            strategy_notes=[
                "Tighten live promotion while risk-off pressure is present.",
                "Do not treat put candidates as automatically safe; require post-friction edge.",
            ],
        )

    if regime_mode == "risk_on":
        return MarketShockRegime(
            label="constructive_risk_on",
            severity=round(min(0.25, severity), 4),
            stance="allow",
            global_abstain=False,
            max_extrinsic_ratio=0.90,
            preferred_sides=["call", "observe"],
            drivers=drivers,
            strategy_notes=["Allow standard live selection with normal high-extrinsic discipline."],
        )

    return NEUTRAL_MARKET_SHOCK


def classify_current_market_shock(
    regime: MarketRegime | None = None,
    *,
    as_of: date | datetime | None = None,
) -> MarketShockRegime:
    event_frame = load_event_feature_frame()
    lookup = as_of or datetime.now(timezone.utc)
    event_snapshot = latest_event_feature_snapshot(
        GLOBAL_EVENT_SYMBOL,
        event_frame,
        as_of=lookup,
    )
    if event_snapshot and event_snapshot.as_of:
        lookup_date = lookup.date() if isinstance(lookup, datetime) else lookup
        if (lookup_date - event_snapshot.as_of).days > 7:
            event_snapshot = None
    features = _input_from_event_snapshot(
        cross_asset=cross_asset_snapshot(),
        event_snapshot=event_snapshot,
    )
    return classify_market_shock(features, regime)
