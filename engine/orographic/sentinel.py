from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yfinance as yf


_MODEL_DIR = Path(__file__).parent / "models"
_SENTINEL_MODEL_PATH = _MODEL_DIR / "sentinel_model.json"
_DIRECTION_VALUES = {"up": 1.0, "down": -1.0, "neutral": 0.0}
_MAGNITUDE_VALUES = {"small": 0.35, "medium": 0.65, "large": 1.0, "unknown": 0.25}
_TIME_HORIZON_VALUES = {
    "intraday": 0.25,
    "one_day": 0.4,
    "one_to_three_days": 0.8,
    "three_to_five_days": 0.7,
    "one_week": 0.6,
    "longer": 0.45,
    "unknown": 0.35,
}
_DECAY_VALUES = {
    "intraday": 0.2,
    "one_day": 0.4,
    "three_days": 0.7,
    "one_week": 0.85,
    "longer": 0.55,
    "unknown": 0.35,
}
_DEFAULT_SENTINEL_MODEL: dict[str, Any] = {
    "artifact": "sentinel_model",
    "version": 1,
    "trained_at": "2026-06-27",
    "category_scores": {
        "source_reliability": {"high": 1.0, "medium": 0.75, "low": 0.4, "unknown": 0.5},
        "novelty": {"high": 1.0, "medium": 0.7, "low": 0.35, "unknown": 0.45},
        "magnitude_bucket": _MAGNITUDE_VALUES,
        "time_horizon": _TIME_HORIZON_VALUES,
        "decay_half_life": _DECAY_VALUES,
    },
    "event_profiles": {
        "earnings": {
            "call_bias": 0.16,
            "put_bias": 0.16,
            "no_trade_bias": 0.02,
            "confidence_boost": 0.08,
            "default_horizon": "one_to_three_days",
            "default_decay": "three_days",
        },
        "guidance": {
            "call_bias": 0.12,
            "put_bias": 0.12,
            "no_trade_bias": 0.03,
            "confidence_boost": 0.06,
            "default_horizon": "one_to_three_days",
            "default_decay": "three_days",
        },
        "analyst": {
            "call_bias": 0.08,
            "put_bias": 0.08,
            "no_trade_bias": 0.05,
            "confidence_boost": 0.03,
            "default_horizon": "one_day",
            "default_decay": "one_day",
        },
        "regulatory": {
            "call_bias": 0.08,
            "put_bias": 0.18,
            "no_trade_bias": 0.14,
            "confidence_boost": 0.05,
            "default_horizon": "one_week",
            "default_decay": "one_week",
        },
        "macro": {
            "call_bias": 0.1,
            "put_bias": 0.1,
            "no_trade_bias": 0.08,
            "confidence_boost": 0.04,
            "default_horizon": "one_week",
            "default_decay": "one_week",
        },
        "geopolitical": {
            "call_bias": 0.06,
            "put_bias": 0.16,
            "no_trade_bias": 0.12,
            "confidence_boost": 0.04,
            "default_horizon": "one_week",
            "default_decay": "one_week",
        },
        "clinical_trial": {
            "call_bias": 0.18,
            "put_bias": 0.18,
            "no_trade_bias": 0.06,
            "confidence_boost": 0.08,
            "default_horizon": "one_week",
            "default_decay": "one_week",
        },
        "contract": {
            "call_bias": 0.14,
            "put_bias": 0.08,
            "no_trade_bias": 0.04,
            "confidence_boost": 0.06,
            "default_horizon": "one_to_three_days",
            "default_decay": "three_days",
        },
        "news": {
            "call_bias": 0.08,
            "put_bias": 0.08,
            "no_trade_bias": 0.08,
            "confidence_boost": 0.02,
            "default_horizon": "one_day",
            "default_decay": "one_day",
        },
        "none": {
            "call_bias": 0.0,
            "put_bias": 0.0,
            "no_trade_bias": 0.18,
            "confidence_boost": 0.0,
            "default_horizon": "unknown",
            "default_decay": "unknown",
        },
    },
    "spot_vs_iv_effect": {
        "spot": {"call": 0.08, "put": 0.08, "no_trade": 0.02},
        "iv": {"call": 0.03, "put": 0.03, "no_trade": 0.08},
        "mixed": {"call": 0.05, "put": 0.05, "no_trade": 0.04},
        "unknown": {"call": 0.0, "put": 0.0, "no_trade": 0.02},
    },
    "context_weights": {
        "fnspid_sentiment_mean": 0.18,
        "fnspid_catalyst_density": 0.14,
        "fnspid_news_volume_3d": 0.05,
        "fnspid_novelty_score": 0.08,
        "edt_event_intensity": 0.08,
        "edt_guidance_score": 0.11,
        "edt_new_contract_score": 0.1,
        "edt_clinical_trial_score": 0.12,
        "edt_risk_warning_score": -0.12,
        "edt_violation_score": -0.15,
        "edt_rating_action_score": -0.08,
        "mirai_risk_on_score": 0.1,
        "mirai_risk_off_score": -0.12,
        "mirai_macro_shock_score": -0.1,
        "mirai_geopolitical_risk_score": -0.08,
        "sec_material_event_score": 0.1,
        "sec_material_event_score_5d": 0.08,
        "sec_offering_flag": -0.12,
        "sec_proxy_flag": -0.04,
        "stocktwits_bullish_ratio": 0.07,
        "stocktwits_bearish_ratio": -0.07,
    },
    "shadow_multiplier_bounds": [0.88, 1.12],
    "live_multiplier_bounds": [0.84, 1.16],
}


@dataclass
class SentinelScore:
    multiplier: float
    catalyst: str
    rationale: str
    sentiment_score: float = 0.0
    direction: str | None = None
    source: str = "neutral"
    event_type: str = "none"
    event_polarity: float = 0.0
    directional_relevance: str = "neither"
    novelty: str = "unknown"
    source_reliability: str = "unknown"
    time_horizon: str = "unknown"
    confidence: float = 0.0
    shadow_multiplier: float = 1.0
    mode: str = "shadow"
    direction_1d: str = "neutral"
    direction_3d: str = "neutral"
    direction_5d: str = "neutral"
    magnitude_bucket: str = "unknown"
    decay_half_life: str = "unknown"
    spot_vs_iv_effect: str = "unknown"
    call_relevance: float = 0.0
    put_relevance: float = 0.0
    no_trade_relevance: float = 1.0
    headlines: list[str] | None = None
    event_context: dict[str, Any] | None = None
    model_mode: str = "structured_event_model"
    model_artifact_sha256: str | None = None
    structured_event_score: float = 0.0


def _clip(value: float, low: float = 0.0, high: float = 1.5) -> float:
    return max(low, min(high, value))


def _signed_clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _bucket(value: object, allowed: set[str], fallback: str) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in allowed else fallback


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _load_sentinel_model() -> tuple[dict[str, Any], str | None, str]:
    if _SENTINEL_MODEL_PATH.exists():
        payload = json.loads(_SENTINEL_MODEL_PATH.read_text(encoding="utf-8"))
        artifact = payload if isinstance(payload, dict) else dict(_DEFAULT_SENTINEL_MODEL)
        return artifact, _sha256_file(_SENTINEL_MODEL_PATH), "artifact"
    return dict(_DEFAULT_SENTINEL_MODEL), None, "builtin"


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        result = float(value)
        if not (-1e18 < result < 1e18):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _score_lookup(mapping: dict[str, float], key: object, fallback: float = 0.0) -> float:
    cleaned = str(key or "").strip().lower()
    return float(mapping.get(cleaned, fallback))


def _effect_adjustments(model: dict[str, Any], effect: object) -> dict[str, float]:
    mapping = model.get("spot_vs_iv_effect", {})
    cleaned = _bucket(effect, {"spot", "iv", "mixed", "unknown"}, "unknown")
    values = mapping.get(cleaned, mapping.get("unknown", {}))
    return {
        "call": _safe_float(values.get("call")),
        "put": _safe_float(values.get("put")),
        "no_trade": _safe_float(values.get("no_trade")),
    }


def _direction_signal(value: object) -> float:
    return float(_DIRECTION_VALUES.get(str(value or "").strip().lower(), 0.0))


def _derive_event_type_from_context(event_context: dict[str, Any]) -> str:
    if _safe_float(event_context.get("edt_clinical_trial_score")) > 0.0:
        return "clinical_trial"
    if _safe_float(event_context.get("edt_new_contract_score")) > 0.0:
        return "contract"
    if _safe_float(event_context.get("edt_guidance_score")) > 0.0:
        return "guidance"
    if _safe_float(event_context.get("sec_material_event_score")) > 0.0 or _safe_float(event_context.get("sec_signal_count_1d")) > 0.0:
        return "regulatory"
    if max(
        _safe_float(event_context.get("mirai_macro_shock_score")),
        _safe_float(event_context.get("mirai_geopolitical_risk_score")),
        _safe_float(event_context.get("mirai_risk_off_score")),
        _safe_float(event_context.get("mirai_risk_on_score")),
    ) > 0.0:
        return "macro"
    if _safe_float(event_context.get("fnspid_news_volume_1d")) > 0.0:
        return "news"
    return "none"


def _context_net_signal(model: dict[str, Any], event_context: dict[str, Any]) -> float:
    weights = model.get("context_weights", {})
    total = 0.0
    for feature, weight in weights.items():
        total += _safe_float(weight) * _safe_float(event_context.get(feature))
    return _signed_clip(total, -1.0, 1.0)


def _default_novelty(event_context: dict[str, Any]) -> str:
    novelty = _safe_float(event_context.get("fnspid_novelty_score"))
    if novelty >= 0.75:
        return "high"
    if novelty >= 0.45:
        return "medium"
    if novelty > 0.0:
        return "low"
    return "unknown"


def _default_source_reliability(event_context: dict[str, Any]) -> str:
    tags = str(event_context.get("dataset_tags") or "")
    tag_count = len([token for token in tags.split(",") if token.strip()])
    if tag_count >= 2:
        return "high"
    if tag_count == 1 or _safe_float(event_context.get("sec_signal_count_1d")) > 0.0:
        return "medium"
    return "unknown"


def _default_time_horizon(event_type: str, profile: dict[str, Any], event_context: dict[str, Any]) -> str:
    if event_type == "macro" and _safe_float(event_context.get("mirai_macro_shock_score")) > 0.0:
        return "one_week"
    return str(profile.get("default_horizon", "unknown") or "unknown")


def _default_decay_half_life(profile: dict[str, Any]) -> str:
    return str(profile.get("default_decay", "unknown") or "unknown")


def _blend(raw_value: float, derived_value: float, raw_present: bool, raw_weight: float = 0.65) -> float:
    if not raw_present:
        return derived_value
    return raw_weight * raw_value + (1.0 - raw_weight) * derived_value


def _build_structured_score(
    raw: dict[str, Any],
    *,
    symbol: str,
    headlines: list[str],
    direction: str | None,
    scout_score: float | None,
    event_context: dict[str, Any],
    sentinel_mode: str,
) -> SentinelScore:
    model, artifact_sha, model_source = _load_sentinel_model()
    category_scores = model.get("category_scores", {})
    event_type = str(raw.get("event_type") or raw.get("catalyst") or _derive_event_type_from_context(event_context) or "none").strip().lower()
    profiles = model.get("event_profiles", {})
    profile = profiles.get(event_type, profiles.get("none", {}))
    novelty = _bucket(
        raw.get("novelty") or _default_novelty(event_context),
        set(category_scores.get("novelty", {}).keys()) | {"unknown"},
        "unknown",
    )
    source_reliability = _bucket(
        raw.get("source_reliability") or _default_source_reliability(event_context),
        set(category_scores.get("source_reliability", {}).keys()) | {"unknown"},
        "unknown",
    )
    magnitude_bucket = _bucket(raw.get("magnitude_bucket"), set(_MAGNITUDE_VALUES.keys()), "unknown")
    time_horizon = _bucket(
        raw.get("time_horizon") or _default_time_horizon(event_type, profile, event_context),
        set(_TIME_HORIZON_VALUES.keys()),
        "unknown",
    )
    decay_half_life = _bucket(
        raw.get("decay_half_life") or _default_decay_half_life(profile),
        set(_DECAY_VALUES.keys()),
        "unknown",
    )
    spot_vs_iv_effect = _bucket(raw.get("spot_vs_iv_effect"), {"spot", "iv", "mixed", "unknown"}, "unknown")
    context_net = _context_net_signal(model, event_context)
    direction_1d = _bucket(raw.get("direction_1d"), set(_DIRECTION_VALUES.keys()), "neutral")
    direction_3d = _bucket(raw.get("direction_3d"), set(_DIRECTION_VALUES.keys()), "neutral")
    direction_5d = _bucket(raw.get("direction_5d"), set(_DIRECTION_VALUES.keys()), "neutral")
    derived_polarity = _signed_clip(
        0.45 * _direction_signal(direction_1d)
        + 0.35 * _direction_signal(direction_3d)
        + 0.20 * _direction_signal(direction_5d)
        + 0.30 * context_net
        + 0.10 * _signed_clip(_safe_float(scout_score), -1.0, 1.0),
        -1.0,
        1.0,
    )
    raw_polarity_present = raw.get("event_polarity") is not None or raw.get("sentiment_score") is not None
    event_polarity = _signed_clip(
        _signed_clip(_safe_float(raw.get("event_polarity", raw.get("sentiment_score"))), -1.0, 1.0)
        if raw_polarity_present
        else derived_polarity,
        -1.0,
        1.0,
    )
    reliability_score = _score_lookup(category_scores.get("source_reliability", {}), source_reliability, 0.5)
    novelty_score = _score_lookup(category_scores.get("novelty", {}), novelty, 0.45)
    magnitude_score = _score_lookup(category_scores.get("magnitude_bucket", {}), magnitude_bucket, 0.25)
    horizon_score = _score_lookup(category_scores.get("time_horizon", {}), time_horizon, 0.35)
    decay_score = _score_lookup(category_scores.get("decay_half_life", {}), decay_half_life, 0.35)
    effect_adj = _effect_adjustments(model, spot_vs_iv_effect)
    derived_confidence = _clip(
        0.20
        + 0.26 * abs(event_polarity)
        + 0.18 * reliability_score
        + 0.14 * novelty_score
        + 0.12 * magnitude_score
        + 0.08 * abs(context_net)
        + 0.08 * _safe_float(profile.get("confidence_boost")),
        0.0,
        1.0,
    )
    raw_confidence_present = raw.get("confidence") is not None
    confidence = _clip(
        _clip(_safe_float(raw.get("confidence")), 0.0, 1.0) if raw_confidence_present else derived_confidence,
        0.0,
        1.0,
    )
    directional_relevance = str(raw.get("directional_relevance") or "").strip().lower()
    if directional_relevance not in {"call", "put", "both", "neither"}:
        if event_polarity > 0.12:
            directional_relevance = "call"
        elif event_polarity < -0.12:
            directional_relevance = "put"
        else:
            directional_relevance = "neither"

    structured_event_score = _signed_clip(
        0.40 * event_polarity
        + 0.20 * context_net
        + 0.15 * (_direction_signal(direction_1d) + _direction_signal(direction_3d)) / 2.0
        + 0.10 * (magnitude_score - 0.5)
        + 0.10 * (reliability_score - 0.5)
        + 0.05 * (novelty_score - 0.5),
        -1.0,
        1.0,
    )
    neutral_event = (
        event_type == "none"
        and abs(context_net) < 0.05
        and not raw_polarity_present
        and raw.get("call_relevance") is None
        and raw.get("put_relevance") is None
        and raw.get("no_trade_relevance") is None
    )
    directional_strength = _clip(
        0.24 * abs(event_polarity)
        + 0.20 * confidence
        + 0.12 * magnitude_score
        + 0.10 * reliability_score
        + 0.10 * novelty_score
        + 0.08 * horizon_score
        + 0.06 * decay_score
        + 0.10 * abs(context_net),
        0.0,
        1.0,
    )
    uncertainty = _clip(1.0 - abs(structured_event_score), 0.0, 1.0)
    call_bias = _safe_float(profile.get("call_bias"))
    put_bias = _safe_float(profile.get("put_bias"))
    no_trade_bias = _safe_float(profile.get("no_trade_bias"))
    derived_call = _clip(
        0.14
        + call_bias
        + 0.54 * max(structured_event_score, 0.0)
        + 0.18 * directional_strength
        + effect_adj["call"]
        + (0.08 if directional_relevance in {"call", "both"} else 0.0),
        0.0,
        1.0,
    )
    derived_put = _clip(
        0.14
        + put_bias
        + 0.54 * max(-structured_event_score, 0.0)
        + 0.18 * directional_strength
        + effect_adj["put"]
        + (0.08 if directional_relevance in {"put", "both"} else 0.0),
        0.0,
        1.0,
    )
    derived_no_trade = _clip(
        0.18
        + no_trade_bias
        + 0.34 * uncertainty
        + 0.16 * (1.0 - reliability_score)
        + 0.12 * (1.0 - novelty_score)
        + effect_adj["no_trade"]
        - 0.16 * directional_strength,
        0.0,
        1.0,
    )
    if neutral_event:
        structured_event_score = 0.0
        confidence = 0.0 if not raw_confidence_present else confidence
        derived_call = 0.0
        derived_put = 0.0
        derived_no_trade = 1.0
    call_present = raw.get("call_relevance") is not None
    put_present = raw.get("put_relevance") is not None
    no_trade_present = raw.get("no_trade_relevance") is not None
    call_relevance = _clip(_clip(_safe_float(raw.get("call_relevance")), 0.0, 1.0) if call_present else derived_call, 0.0, 1.0)
    put_relevance = _clip(_clip(_safe_float(raw.get("put_relevance")), 0.0, 1.0) if put_present else derived_put, 0.0, 1.0)
    no_trade_relevance = _clip(
        _clip(_safe_float(raw.get("no_trade_relevance", 1.0)), 0.0, 1.0) if no_trade_present else derived_no_trade,
        0.0,
        1.0,
    )
    desired_direction = str(direction or raw.get("direction") or "").strip().lower()
    support = call_relevance if desired_direction == "call" else put_relevance if desired_direction == "put" else max(call_relevance, put_relevance)
    oppose = put_relevance if desired_direction == "call" else call_relevance if desired_direction == "put" else min(call_relevance, put_relevance)
    derived_shadow = _clip(
        1.0 + 0.16 * (support - max(oppose, no_trade_relevance * 0.85)),
        _safe_float(model.get("shadow_multiplier_bounds", [0.88, 1.12])[0], 0.88),
        _safe_float(model.get("shadow_multiplier_bounds", [0.88, 1.12])[1], 1.12),
    )
    raw_shadow_present = raw.get("shadow_multiplier") is not None or raw.get("multiplier") is not None
    shadow_multiplier = _clip(
        _clip(_safe_float(raw.get("shadow_multiplier", raw.get("multiplier", 1.0))), 0.0, 1.5)
        if raw_shadow_present
        else derived_shadow,
        0.0,
        1.5,
    )
    derived_live = _clip(
        1.0 + 0.18 * (support - max(oppose, no_trade_relevance * 0.80)),
        _safe_float(model.get("live_multiplier_bounds", [0.84, 1.16])[0], 0.84),
        _safe_float(model.get("live_multiplier_bounds", [0.84, 1.16])[1], 1.16),
    )
    raw_live_present = raw.get("multiplier") is not None
    live_multiplier = _clip(
        _clip(_safe_float(raw.get("multiplier", 1.0)), 0.0, 1.5) if raw_live_present else derived_live,
        0.0,
        1.5,
    )
    catalyst = str(raw.get("catalyst") or event_type or "none")
    rationale = str(raw.get("rationale") or "").strip()
    if not rationale:
        rationale = (
            f"Structured Sentinel model rated {event_type} as {time_horizon} / {decay_half_life} "
            f"with confidence {confidence:.2f}."
        )
    source = str(raw.get("source") or "")
    if not source:
        source = "structured_event_model"
    elif model_source == "artifact":
        source = f"{source}+structured_event_model"

    return SentinelScore(
        multiplier=live_multiplier if sentinel_mode == "active" else 1.0,
        catalyst=catalyst,
        rationale=rationale,
        sentiment_score=event_polarity,
        direction=str(raw.get("direction") or direction or "").strip().lower() or direction,
        source=source,
        event_type=event_type,
        event_polarity=event_polarity,
        directional_relevance=directional_relevance,
        novelty=novelty,
        source_reliability=source_reliability,
        time_horizon=time_horizon,
        confidence=confidence,
        shadow_multiplier=shadow_multiplier,
        mode=str(raw.get("mode", sentinel_mode) or sentinel_mode),
        direction_1d=direction_1d,
        direction_3d=direction_3d,
        direction_5d=direction_5d,
        magnitude_bucket=magnitude_bucket,
        decay_half_life=decay_half_life,
        spot_vs_iv_effect=spot_vs_iv_effect,
        call_relevance=call_relevance,
        put_relevance=put_relevance,
        no_trade_relevance=no_trade_relevance,
        headlines=headlines,
        event_context=event_context or {"symbol": symbol},
        model_mode=f"structured_event_{model_source}",
        model_artifact_sha256=artifact_sha,
        structured_event_score=structured_event_score,
    )


def _fetch_remote_payload(
    *,
    symbol: str,
    headlines: list[str],
    direction: str | None,
    scout_score: float | None,
    event_context: dict[str, Any],
    sentinel_mode: str,
) -> dict[str, Any]:
    url = os.getenv("OROGRAPHIC_SENTINEL_URL") or "https://orographic.pages.dev/api/ai/sentinel"
    payload = json.dumps(
        {
            "symbol": symbol,
            "headlines": headlines,
            "direction": direction,
            "scout_score": scout_score,
            "mode": sentinel_mode,
            "event_context": event_context,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("OROGRAPHIC_SENTINEL_TOKEN") or os.getenv("OROGRAPHIC_INTERNAL_AI_TOKEN")
    if token:
        headers["X-Orographic-Internal-Token"] = token
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=3.0) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def fetch_ai_multiplier(
    symbol: str,
    *,
    direction: str | None = None,
    scout_score: float | None = None,
    event_context: dict[str, Any] | None = None,
) -> SentinelScore:
    """
    Fetch the top headlines, route them to Sentinel extraction, and then run a
    local structured-event model so Sentinel can still produce stable fields and
    abstain-aware scores when remote extraction is partial or unavailable.
    """
    sentinel_mode = os.getenv("OROGRAPHIC_SENTINEL_MODE", "active").strip().lower()
    if sentinel_mode not in {"active", "shadow"}:
        sentinel_mode = "shadow"
    event_context = dict(event_context or {})
    default_score = _build_structured_score(
        {},
        symbol=symbol,
        headlines=[],
        direction=direction,
        scout_score=scout_score,
        event_context=event_context,
        sentinel_mode=sentinel_mode,
    )
    default_score.catalyst = "none"
    default_score.rationale = "No Sentinel event intelligence gathered."
    default_score.direction = direction
    default_score.source = "default+structured_event_model"
    default_score.mode = sentinel_mode

    try:
        news_items = yf.Ticker(symbol).news
        if not news_items:
            return default_score

        headlines: list[str] = []
        for item in news_items[:3]:
            title = item.get("title") or item.get("content", {}).get("title")
            if title:
                headlines.append(title)
        if not headlines:
            return default_score

        raw = _fetch_remote_payload(
            symbol=symbol,
            headlines=headlines,
            direction=direction,
            scout_score=scout_score,
            event_context=event_context,
            sentinel_mode=sentinel_mode,
        )
        if raw.get("ok"):
            return _build_structured_score(
                raw,
                symbol=symbol,
                headlines=headlines,
                direction=direction,
                scout_score=scout_score,
                event_context=event_context,
                sentinel_mode=sentinel_mode,
            )
        return _build_structured_score(
            {},
            symbol=symbol,
            headlines=headlines,
            direction=direction,
            scout_score=scout_score,
            event_context=event_context,
            sentinel_mode=sentinel_mode,
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return default_score
    except Exception:
        return default_score
