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


def _clip(value: float, low: float = 0.0, high: float = 1.5) -> float:
    return max(low, min(high, value))


def fetch_ai_multiplier(
    symbol: str,
    *,
    direction: str | None = None,
    scout_score: float | None = None,
) -> SentinelScore:
    """
    Fetches the top 3 headlines for a symbol, routes them to the Cloudflare AI Sentinel edge route,
    and returns an asymmetric edge multiplier. Gracefully degrades to 1.0 (neutral) if anything fails.
    """
    default_score = SentinelScore(
        multiplier=1.0,
        catalyst="none",
        rationale="No AI intelligence gathered.",
        direction=direction,
        source="default",
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
                return SentinelScore(
                    multiplier=_clip(float(data.get("multiplier", 1.0))),
                    catalyst=data.get("catalyst", "none"),
                    rationale=data.get("rationale", ""),
                    sentiment_score=float(data.get("sentiment_score", 0.0) or 0.0),
                    direction=data.get("direction") or direction,
                    source=data.get("source", "cloudflare_ai"),
                )
    except Exception:
        # Graceful degradation ensures execution engine never halts
        pass
        
    return default_score
