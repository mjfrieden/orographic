from __future__ import annotations

import json
import urllib.request
import urllib.error
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


def _clip(value: float, low: float = 0.0, high: float = 1.5) -> float:
    return max(low, min(high, value))


def _signed_clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _bucket(value: str | None, allowed: set[str], fallback: str) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in allowed else fallback


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
        source="default",
        mode=sentinel_mode,
        headlines=[],
    )
    
    # ── Configuration ──
    # Default to the production Cloudflare Pages endpoint so it runs in both local and CI environments.
    # Can still be overridden by OROGRAPHIC_SENTINEL_URL if needed.
    url = os.getenv("OROGRAPHIC_SENTINEL_URL") or "https://orographic.pages.dev/api/ai/sentinel"

    try:
        # 1. Grab breaking news from yfinance
        news_items = yf.Ticker(symbol).news
        if not news_items:
            return default_score
            
        # Extract the titles of the top 3 most recent news items
        headlines = []
        for item in news_items[:3]:
            title = item.get("title") or item.get("content", {}).get("title")
            if title:
                headlines.append(title)
                
        if not headlines:
            return default_score

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
                return SentinelScore(
                    multiplier=live_multiplier if sentinel_mode == "active" else 1.0,
                    catalyst=data.get("catalyst", "none"),
                    rationale=data.get("rationale", ""),
                    sentiment_score=_signed_clip(float(data.get("sentiment_score", 0.0) or 0.0)),
                    direction=data.get("direction") or direction,
                    source=data.get("source", "cloudflare_ai"),
                    event_type=str(data.get("event_type", data.get("catalyst", "none")) or "none"),
                    event_polarity=_signed_clip(float(data.get("event_polarity", data.get("sentiment_score", 0.0)) or 0.0)),
                    directional_relevance=str(data.get("directional_relevance", "neither") or "neither"),
                    novelty=str(data.get("novelty", "unknown") or "unknown"),
                    source_reliability=str(data.get("source_reliability", "unknown") or "unknown"),
                    time_horizon=str(data.get("time_horizon", "unknown") or "unknown"),
                    confidence=float(data.get("confidence", 0.0) or 0.0),
                    shadow_multiplier=shadow_multiplier,
                    mode=str(data.get("mode", sentinel_mode) or sentinel_mode),
                    direction_1d=_bucket(data.get("direction_1d"), {"up", "down", "neutral"}, "neutral"),
                    direction_3d=_bucket(data.get("direction_3d"), {"up", "down", "neutral"}, "neutral"),
                    direction_5d=_bucket(data.get("direction_5d"), {"up", "down", "neutral"}, "neutral"),
                    magnitude_bucket=_bucket(data.get("magnitude_bucket"), {"small", "medium", "large", "unknown"}, "unknown"),
                    decay_half_life=_bucket(data.get("decay_half_life"), {"intraday", "one_day", "three_days", "one_week", "longer", "unknown"}, "unknown"),
                    spot_vs_iv_effect=_bucket(data.get("spot_vs_iv_effect"), {"spot", "iv", "mixed", "unknown"}, "unknown"),
                    call_relevance=_clip(float(data.get("call_relevance", 0.0) or 0.0), 0.0, 1.0),
                    put_relevance=_clip(float(data.get("put_relevance", 0.0) or 0.0), 0.0, 1.0),
                    no_trade_relevance=_clip(float(data.get("no_trade_relevance", 1.0) or 0.0), 0.0, 1.0),
                    headlines=headlines,
                    event_context=event_context or {},
                )
    except Exception:
        # Graceful degradation ensures execution engine never halts
        pass
        
    return default_score
