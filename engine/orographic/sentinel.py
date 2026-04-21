from __future__ import annotations

import json
import urllib.request
import urllib.error
import yfinance as yf
import os
from dataclasses import dataclass

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


def _clip(value: float, low: float = 0.0, high: float = 1.5) -> float:
    return max(low, min(high, value))


def fetch_ai_multiplier(
    symbol: str,
    *,
    direction: str | None = None,
    scout_score: float | None = None,
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
                    sentiment_score=float(data.get("sentiment_score", 0.0) or 0.0),
                    direction=data.get("direction") or direction,
                    source=data.get("source", "cloudflare_ai"),
                    event_type=str(data.get("event_type", data.get("catalyst", "none")) or "none"),
                    event_polarity=float(data.get("event_polarity", data.get("sentiment_score", 0.0)) or 0.0),
                    directional_relevance=str(data.get("directional_relevance", "neither") or "neither"),
                    novelty=str(data.get("novelty", "unknown") or "unknown"),
                    source_reliability=str(data.get("source_reliability", "unknown") or "unknown"),
                    time_horizon=str(data.get("time_horizon", "unknown") or "unknown"),
                    confidence=float(data.get("confidence", 0.0) or 0.0),
                    shadow_multiplier=shadow_multiplier,
                    mode=str(data.get("mode", sentinel_mode) or sentinel_mode),
                )
    except Exception:
        # Graceful degradation ensures execution engine never halts
        pass
        
    return default_score
