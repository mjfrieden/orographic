from __future__ import annotations

import json
import urllib.request
import urllib.error
import socket
import yfinance as yf
import os
from dataclasses import dataclass
from typing import Any

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
    status: str = "no_news"
    error: str | None = None
    options_impact_label: str = "unknown"
    recommended_use: str = "observe"
    veto_reason: str | None = None
    tie_breaker_score: float = 0.0
    size_multiplier: float = 1.0


def _clip(value: float, low: float = 0.0, high: float = 1.5) -> float:
    return max(low, min(high, value))


def _signed_clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _bucket(value: str | None, allowed: set[str], fallback: str) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in allowed else fallback


def _event_context_value(context: dict[str, Any] | None, key: str) -> float:
    if not isinstance(context, dict):
        return 0.0
    try:
        return float(context.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _has_material_event_context(context: dict[str, Any] | None) -> bool:
    if not isinstance(context, dict) or not context:
        return False
    keys = (
        "fnspid_news_volume_1d",
        "fnspid_news_volume_3d",
        "fnspid_catalyst_density",
        "edt_event_intensity",
        "mirai_macro_shock_score",
        "mirai_geopolitical_risk_score",
        "mirai_risk_off_score",
        "mirai_risk_on_score",
        "sec_signal_count_1d",
        "sec_signal_count_5d",
        "sec_material_event_score",
        "stocktwits_message_count",
        "stocktwits_emotion_intensity",
    )
    return any(abs(_event_context_value(context, key)) > 0 for key in keys)


def _event_context_headlines(symbol: str, context: dict[str, Any] | None) -> list[str]:
    if not _has_material_event_context(context):
        return []
    tags = str((context or {}).get("dataset_tags") or "event_feature_store")
    headlines: list[str] = [f"{symbol} structured event-feature snapshot active: {tags}"]
    if _event_context_value(context, "sec_signal_count_1d") or _event_context_value(context, "sec_material_event_score"):
        headlines.append(f"{symbol} SEC filing signal present in daily event store")
    if _event_context_value(context, "fnspid_catalyst_density") or _event_context_value(context, "fnspid_news_volume_1d"):
        headlines.append(f"{symbol} news volume or catalyst density elevated in FNSPID-style event store")
    if _event_context_value(context, "mirai_risk_off_score") or _event_context_value(context, "mirai_geopolitical_risk_score"):
        headlines.append("Global macro/geopolitical risk-off signal present in event store")
    if _event_context_value(context, "stocktwits_emotion_intensity"):
        headlines.append(f"{symbol} social emotion intensity present in StockTwits-style event store")
    return headlines[:5]


def _options_impact_label(
    *,
    event_polarity: float,
    spot_vs_iv_effect: str,
    direction_3d: str,
    no_trade_relevance: float,
    event_context: dict[str, Any] | None,
) -> str:
    iv_heavy = spot_vs_iv_effect in {"iv", "mixed"} or _event_context_value(event_context, "fnspid_catalyst_density") > 0.6
    risk_off = _event_context_value(event_context, "mirai_risk_off_score") > _event_context_value(event_context, "mirai_risk_on_score")
    filing_risk = _event_context_value(event_context, "sec_offering_flag") or _event_context_value(event_context, "sec_material_event_score") > 0.7
    if no_trade_relevance >= 0.7 or filing_risk:
        return "pre_event_premium_risk" if iv_heavy else "post_event_decay_risk"
    if iv_heavy and abs(event_polarity) < 0.25:
        return "iv_expansion_only"
    if event_polarity > 0.15 or direction_3d == "up":
        return "spot_up_iv_down" if not iv_heavy else "pre_event_premium_risk"
    if event_polarity < -0.15 or direction_3d == "down" or risk_off:
        return "spot_down_iv_up" if iv_heavy or risk_off else "post_event_decay_risk"
    return "unknown"


def _recommended_use(
    *,
    event_type: str,
    novelty: str,
    confidence: float,
    no_trade_relevance: float,
    shadow_multiplier: float,
    options_impact_label: str,
) -> tuple[str, str | None, float, float]:
    if no_trade_relevance >= 0.75 or options_impact_label in {"pre_event_premium_risk", "post_event_decay_risk"}:
        return "veto_candidate", options_impact_label, 0.0, max(0.35, 1.0 - no_trade_relevance * 0.5)
    if event_type in {"earnings", "guidance", "legal_regulatory", "fraud_accounting", "financing"}:
        return "flag_event_risk", None, round((shadow_multiplier - 1.0) * confidence, 4), 1.0
    if novelty in {"new", "incremental"} and confidence >= 0.5 and abs(shadow_multiplier - 1.0) >= 0.015:
        return "tie_breaker", None, round((shadow_multiplier - 1.0) * confidence, 4), 1.0
    if no_trade_relevance >= 0.45:
        return "reduce_size", None, 0.0, max(0.5, 1.0 - no_trade_relevance * 0.35)
    return "observe", None, round((shadow_multiplier - 1.0) * confidence, 4), 1.0


def _fallback_from_event_context(
    symbol: str,
    *,
    direction: str | None,
    sentinel_mode: str,
    event_context: dict[str, Any] | None,
    status: str,
    error: str | None = None,
) -> SentinelScore:
    if not _has_material_event_context(event_context):
        return SentinelScore(
            multiplier=1.0,
            catalyst="none",
            rationale="No Sentinel headlines or material event-feature context available.",
            direction=direction,
            source=status,
            mode=sentinel_mode,
            headlines=[],
            event_context=event_context or {},
            status=status,
            error=error,
        )
    sentiment = _signed_clip(_event_context_value(event_context, "fnspid_sentiment_mean"))
    macro_risk = _event_context_value(event_context, "mirai_risk_off_score") - _event_context_value(event_context, "mirai_risk_on_score")
    sec_risk = _event_context_value(event_context, "sec_material_event_score")
    social_bull = _event_context_value(event_context, "stocktwits_bullish_ratio") - _event_context_value(event_context, "stocktwits_bearish_ratio")
    event_polarity = _signed_clip(sentiment + social_bull * 0.35 - macro_risk * 0.45 - sec_risk * 0.35)
    event_type = "macro" if macro_risk > 0 else "legal_regulatory" if sec_risk > 0 else "product"
    confidence = _clip(
        0.25
        + min(_event_context_value(event_context, "fnspid_news_volume_3d") / 10.0, 0.25)
        + min(_event_context_value(event_context, "sec_signal_count_5d") / 5.0, 0.20)
        + min(_event_context_value(event_context, "stocktwits_emotion_intensity") * 0.20, 0.20),
        0.0,
        0.85,
    )
    relevance = "call" if event_polarity > 0.15 else "put" if event_polarity < -0.15 else "neither"
    no_trade = _clip(0.15 + abs(macro_risk) * 0.35 + sec_risk * 0.30, 0.0, 1.0)
    spot_vs_iv = "iv" if no_trade >= 0.45 else "spot"
    direction3d = "up" if event_polarity > 0.15 else "down" if event_polarity < -0.15 else "neutral"
    shadow_multiplier = _clip(1.0 + event_polarity * confidence * 0.10, 0.0, 1.5)
    impact = _options_impact_label(
        event_polarity=event_polarity,
        spot_vs_iv_effect=spot_vs_iv,
        direction_3d=direction3d,
        no_trade_relevance=no_trade,
        event_context=event_context,
    )
    recommended, veto_reason, tie_breaker, size_multiplier = _recommended_use(
        event_type=event_type,
        novelty="incremental",
        confidence=confidence,
        no_trade_relevance=no_trade,
        shadow_multiplier=shadow_multiplier,
        options_impact_label=impact,
    )
    return SentinelScore(
        multiplier=shadow_multiplier if sentinel_mode == "active" else 1.0,
        catalyst=f"{symbol} dataset-backed event context",
        rationale=f"Deterministic Sentinel fallback from event-feature store after {status}.",
        sentiment_score=event_polarity,
        direction=direction,
        source="event_feature_fallback",
        event_type=event_type,
        event_polarity=event_polarity,
        directional_relevance=relevance,
        novelty="incremental",
        source_reliability="unknown",
        time_horizon="one_to_three_days",
        confidence=round(confidence, 4),
        shadow_multiplier=round(shadow_multiplier, 4),
        mode=sentinel_mode,
        direction_1d=direction3d,
        direction_3d=direction3d,
        direction_5d="neutral",
        magnitude_bucket="small",
        decay_half_life="three_days",
        spot_vs_iv_effect=spot_vs_iv,
        call_relevance=0.65 if relevance == "call" else 0.15,
        put_relevance=0.65 if relevance == "put" else 0.15,
        no_trade_relevance=round(no_trade, 4),
        headlines=_event_context_headlines(symbol, event_context),
        event_context=event_context or {},
        status=status,
        error=error,
        options_impact_label=impact,
        recommended_use=recommended,
        veto_reason=veto_reason,
        tie_breaker_score=tie_breaker,
        size_multiplier=round(size_multiplier, 4),
    )


def score_event_context(
    symbol: str,
    *,
    direction: str | None = None,
    event_context: dict[str, Any] | None = None,
    status: str = "ai_success_event",
    sentinel_mode: str = "shadow",
    error: str | None = None,
) -> SentinelScore:
    """
    Deterministic Sentinel score for replay/offline event snapshots.

    Historical backtests must not fetch current headlines or call the live AI
    endpoint, so this routes structured event-feature context through the same
    fallback model used when live collection fails.
    """
    mode = sentinel_mode.strip().lower()
    if mode not in {"active", "shadow"}:
        mode = "shadow"
    return _fallback_from_event_context(
        symbol,
        direction=direction,
        sentinel_mode=mode,
        event_context=event_context,
        status=status,
        error=error,
    )


def score_to_event_dict(score: SentinelScore, *, fallback_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "multiplier": round(float(score.multiplier), 4),
        "shadow_multiplier": round(float(score.shadow_multiplier), 4),
        "mode": score.mode,
        "catalyst": score.catalyst,
        "rationale": score.rationale,
        "sentiment_score": round(float(score.sentiment_score), 4),
        "event_type": score.event_type,
        "event_polarity": round(float(score.event_polarity), 4),
        "directional_relevance": score.directional_relevance,
        "novelty": score.novelty,
        "source_reliability": score.source_reliability,
        "time_horizon": score.time_horizon,
        "direction_1d": score.direction_1d,
        "direction_3d": score.direction_3d,
        "direction_5d": score.direction_5d,
        "magnitude_bucket": score.magnitude_bucket,
        "decay_half_life": score.decay_half_life,
        "spot_vs_iv_effect": score.spot_vs_iv_effect,
        "call_relevance": round(float(score.call_relevance), 4),
        "put_relevance": round(float(score.put_relevance), 4),
        "no_trade_relevance": round(float(score.no_trade_relevance), 4),
        "confidence": round(float(score.confidence), 4),
        "direction": score.direction,
        "source": score.source,
        "headlines": list(score.headlines or []),
        "event_context": dict(score.event_context or fallback_context or {}),
        "status": score.status,
        "error": score.error,
        "options_impact_label": score.options_impact_label,
        "recommended_use": score.recommended_use,
        "veto_reason": score.veto_reason,
        "tie_breaker_score": round(float(score.tie_breaker_score), 4),
        "size_multiplier": round(float(score.size_multiplier), 4),
    }


def fetch_ai_multiplier(
    symbol: str,
    *,
    direction: str | None = None,
    scout_score: float | None = None,
    event_context: dict[str, Any] | None = None,
) -> SentinelScore:
    """
    Fetch the top headlines, route them to the Sentinel event extractor, and
    return a neutral-by-default overlay. Sentinel is shadow mode unless
    OROGRAPHIC_SENTINEL_MODE=active is set, so LLM text extraction cannot
    silently steer live scoring.
    """
    sentinel_mode = os.getenv("OROGRAPHIC_SENTINEL_MODE", "shadow").strip().lower()
    if sentinel_mode not in {"active", "shadow"}:
        sentinel_mode = "shadow"
    default_score = SentinelScore(
        multiplier=1.0,
        catalyst="none",
        rationale="No Sentinel event intelligence gathered.",
        direction=direction,
        source="no_news",
        mode=sentinel_mode,
        headlines=[],
        event_context=event_context or {},
        status="no_news",
    )
    
    # ── Configuration ──
    # Default to the production Cloudflare Pages endpoint so it runs in both local and CI environments.
    # Can still be overridden by OROGRAPHIC_SENTINEL_URL if needed.
    url = os.getenv("OROGRAPHIC_SENTINEL_URL") or "https://orographic.pages.dev/api/ai/sentinel"

    try:
        # 1. Grab breaking news from yfinance
        try:
            news_items = yf.Ticker(symbol).news
        except Exception as exc:
            return _fallback_from_event_context(
                symbol,
                direction=direction,
                sentinel_mode=sentinel_mode,
                event_context=event_context,
                status="yf_error",
                error=str(exc),
            )
        if not news_items:
            context_headlines = _event_context_headlines(symbol, event_context)
            if not context_headlines:
                return default_score
            news_items = [{"title": title} for title in context_headlines]
            
        # Extract the titles of the top 3 most recent news items
        headlines = []
        for item in news_items[:3]:
            title = item.get("title") or item.get("content", {}).get("title")
            if title:
                headlines.append(title)
                
        if not headlines:
            return _fallback_from_event_context(
                symbol,
                direction=direction,
                sentinel_mode=sentinel_mode,
                event_context=event_context,
                status="no_news",
            )

        # 2. Dispatch to Cloudflare Workers AI endpoint
        payload = json.dumps(
            {
                "symbol": symbol,
                "headlines": headlines,
                "direction": direction,
                "scout_score": scout_score,
                "mode": sentinel_mode,
                "event_context": event_context or {},
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = os.getenv("OROGRAPHIC_SENTINEL_TOKEN") or os.getenv("OROGRAPHIC_INTERNAL_AI_TOKEN")
        if token:
            headers["X-Orographic-Internal-Token"] = token
        req = urllib.request.Request(
            url, 
            data=payload, 
            headers=headers,
        )

        with urllib.request.urlopen(req, timeout=3.0) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("ok"):
                shadow_multiplier = _clip(float(data.get("shadow_multiplier", data.get("multiplier", 1.0))))
                live_multiplier = _clip(float(data.get("multiplier", 1.0)))
                event_type = str(data.get("event_type", data.get("catalyst", "none")) or "none")
                event_polarity = _signed_clip(float(data.get("event_polarity", data.get("sentiment_score", 0.0)) or 0.0))
                confidence = float(data.get("confidence", 0.0) or 0.0)
                no_trade_relevance = _clip(float(data.get("no_trade_relevance", 1.0) or 0.0), 0.0, 1.0)
                spot_vs_iv = _bucket(data.get("spot_vs_iv_effect"), {"spot", "iv", "mixed", "unknown"}, "unknown")
                direction_3d = _bucket(data.get("direction_3d"), {"up", "down", "neutral"}, "neutral")
                status = str(data.get("status") or ("ai_success_event" if event_type not in {"none", "no_clear_event"} or abs(shadow_multiplier - 1.0) > 0.0001 else "ai_success_neutral"))
                impact = _bucket(
                    data.get("options_impact_label"),
                    {"spot_up_iv_down", "spot_down_iv_up", "iv_expansion_only", "post_event_decay_risk", "pre_event_premium_risk", "unknown"},
                    _options_impact_label(
                        event_polarity=event_polarity,
                        spot_vs_iv_effect=spot_vs_iv,
                        direction_3d=direction_3d,
                        no_trade_relevance=no_trade_relevance,
                        event_context=event_context,
                    ),
                )
                recommended, veto_reason, tie_breaker, size_multiplier = _recommended_use(
                    event_type=event_type,
                    novelty=str(data.get("novelty", "unknown") or "unknown"),
                    confidence=confidence,
                    no_trade_relevance=no_trade_relevance,
                    shadow_multiplier=shadow_multiplier,
                    options_impact_label=impact,
                )
                return SentinelScore(
                    multiplier=live_multiplier if sentinel_mode == "active" else 1.0,
                    catalyst=data.get("catalyst", "none"),
                    rationale=data.get("rationale", ""),
                    sentiment_score=_signed_clip(float(data.get("sentiment_score", 0.0) or 0.0)),
                    direction=data.get("direction") or direction,
                    source=status,
                    event_type=event_type,
                    event_polarity=event_polarity,
                    directional_relevance=str(data.get("directional_relevance", "neither") or "neither"),
                    novelty=str(data.get("novelty", "unknown") or "unknown"),
                    source_reliability=str(data.get("source_reliability", "unknown") or "unknown"),
                    time_horizon=str(data.get("time_horizon", "unknown") or "unknown"),
                    confidence=confidence,
                    shadow_multiplier=shadow_multiplier,
                    mode=str(data.get("mode", sentinel_mode) or sentinel_mode),
                    direction_1d=_bucket(data.get("direction_1d"), {"up", "down", "neutral"}, "neutral"),
                    direction_3d=direction_3d,
                    direction_5d=_bucket(data.get("direction_5d"), {"up", "down", "neutral"}, "neutral"),
                    magnitude_bucket=_bucket(data.get("magnitude_bucket"), {"small", "medium", "large", "unknown"}, "unknown"),
                    decay_half_life=_bucket(data.get("decay_half_life"), {"intraday", "one_day", "three_days", "one_week", "longer", "unknown"}, "unknown"),
                    spot_vs_iv_effect=spot_vs_iv,
                    call_relevance=_clip(float(data.get("call_relevance", 0.0) or 0.0), 0.0, 1.0),
                    put_relevance=_clip(float(data.get("put_relevance", 0.0) or 0.0), 0.0, 1.0),
                    no_trade_relevance=no_trade_relevance,
                    headlines=headlines,
                    event_context=event_context or {},
                    status=status,
                    options_impact_label=impact,
                    recommended_use=str(data.get("recommended_use") or recommended),
                    veto_reason=data.get("veto_reason") or veto_reason,
                    tie_breaker_score=float(data.get("tie_breaker_score", tie_breaker) or 0.0),
                    size_multiplier=float(data.get("size_multiplier", size_multiplier) or 1.0),
                )
            return _fallback_from_event_context(
                symbol,
                direction=direction,
                sentinel_mode=sentinel_mode,
                event_context=event_context,
                status="parse_error",
                error=str(data.get("error") or "Sentinel endpoint returned ok=false."),
            )
    except urllib.error.HTTPError as exc:
        status = "sentinel_401" if exc.code == 401 else "sentinel_503" if exc.code == 503 else "sentinel_http_error"
        return _fallback_from_event_context(
            symbol,
            direction=direction,
            sentinel_mode=sentinel_mode,
            event_context=event_context,
            status=status,
            error=str(exc),
        )
    except (TimeoutError, socket.timeout):
        return _fallback_from_event_context(
            symbol,
            direction=direction,
            sentinel_mode=sentinel_mode,
            event_context=event_context,
            status="timeout",
            error="Sentinel request timed out.",
        )
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        status = "timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else "sentinel_503"
        return _fallback_from_event_context(
            symbol,
            direction=direction,
            sentinel_mode=sentinel_mode,
            event_context=event_context,
            status=status,
            error=str(exc),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return _fallback_from_event_context(
            symbol,
            direction=direction,
            sentinel_mode=sentinel_mode,
            event_context=event_context,
            status="parse_error",
            error=str(exc),
        )
    except Exception as exc:
        return _fallback_from_event_context(
            symbol,
            direction=direction,
            sentinel_mode=sentinel_mode,
            event_context=event_context,
            status="sentinel_503",
            error=str(exc),
        )
        
    return default_score
